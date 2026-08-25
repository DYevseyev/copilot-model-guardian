"""
PuTTY <-> Microsoft Copilot Smart Bridge & Autonomous Loop Engine
==================================================================
Connects SSH terminal sessions (PuTTY/KiTTY) to Microsoft Copilot (Standalone & Teams).
Features:
- Delta buffer extraction (only captures new command output, no scrollback dump)
- ANSI escape sequence cleaner
- Threshold gating (ignores shell prompts, echo, status noise under min length)
- Rolling memory deduplication (prevents sending repeated outputs)
- Manual safe mode by default (Auto-send OFF)
- Toggleable Autonomous Loop mode (F9 or 'T' key) for automated back-and-forth
- Safety circuit breakers (password/[y/N] prompt detector, max turn limit, Esc abort)

Author: Dmitriy Yevseyev (@DYevseyev)
"""

import os
import sys
import time
import re
import hashlib
import logging
import threading
import ctypes
from ctypes import wintypes
from typing import Optional, List, Tuple, Set

# Ensure high-DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import uiautomation as auto

# ─── Win32 API Definitions ──────────────────────────────────────────────────
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

WM_COMMAND = 0x0111
WM_SYSCOMMAND = 0x0112
WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_RETURN = 0x0D

# PuTTY internal menu ID for "Copy All to Clipboard"
IDM_COPYALL = 0x0170

# ─── Default Configuration ──────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG_FILE = os.path.join(SCRIPT_DIR, "putty_bridge.log")
DEFAULT_MIN_THRESHOLD_CHARS = 20
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_MAX_AUTO_TURNS = 10
DEDUP_HISTORY_SIZE = 50

# Regex for stripping ANSI color and control escape codes
ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b\([a-zA-Z]|\x1b[=>]')
# Regex for matching common shell prompt lines at the end of output
SHELL_PROMPT_RE = re.compile(
    r'^[a-zA-Z0-9_\-\.]+@[a-zA-Z0-9_\-\.]+:.*?[#\$\%>]\s*$'
    r'|^\[[a-zA-Z0-9_\-\.]+@[a-zA-Z0-9_\-\.]+\s+.*?\][#\$\%>]\s*$'
    r'|^[#\$\%>]\s*$',
    re.MULTILINE
)
# Patterns indicating interactive prompt requiring human intervention
INTERACTIVE_PROMPT_RE = re.compile(
    r'(password\s*for|enter\s*password|password:\s*$|\[y/n\]|\(yes/no\)|are\s+you\s+sure|do\s+you\s+want\s+to\s+continue)',
    re.IGNORECASE
)


def setup_logging(log_file: Optional[str] = DEFAULT_LOG_FILE, verbose: bool = False) -> logging.Logger:
    """Configures console and file logging."""
    logger = logging.getLogger("PuttyBridge")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S"
    )

    console_h = logging.StreamHandler(sys.stdout)
    console_h.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_h.setFormatter(formatter)
    logger.addHandler(console_h)

    if log_file:
        try:
            file_h = logging.FileHandler(log_file, encoding="utf-8")
            file_h.setLevel(logging.DEBUG)
            file_h.setFormatter(formatter)
            logger.addHandler(file_h)
        except Exception:
            pass

    return logger


def clean_ansi(text: str) -> str:
    """Strips ANSI terminal escapes, cleans carriage returns, and normalizes whitespace."""
    if not text:
        return ""
    # Strip ANSI escape codes
    cleaned = ANSI_ESCAPE_RE.sub('', text)
    # Replace CRLF with standard LF
    cleaned = cleaned.replace('\r\n', '\n').replace('\r', '\n')
    # Remove null bytes or non-printable control characters except tab and newline
    cleaned = "".join(ch for ch in cleaned if ch in ('\n', '\t') or (ord(ch) >= 32 and ord(ch) != 127))
    return cleaned.strip()


def extract_commands_from_copilot_response(response_text: str) -> List[str]:
    """
    Extracts bash/sh/shell command blocks from Copilot's markdown response.
    Falls back to single-line command heuristics if no code block exists.
    """
    if not response_text:
        return []

    # 1. Look for fenced markdown code blocks (```bash, ```sh, ```shell, ```)
    fenced_blocks = re.findall(
        r'```(?:bash|sh|shell|zsh|console|cmd|powershell)?\s*\n(.*?)\n```',
        response_text,
        re.DOTALL | re.IGNORECASE
    )

    commands = []
    if fenced_blocks:
        for block in fenced_blocks:
            for line in block.split('\n'):
                line = line.strip()
                # Skip comments or empty lines
                if line and not line.startswith('#'):
                    # Strip leading '$ ' or '# ' prompt symbols if Copilot wrote them
                    if line.startswith('$ ') or line.startswith('> '):
                        line = line[2:].strip()
                    if line:
                        commands.append(line)
        return commands

    # 2. Heuristic fallback: check if the text is a direct command
    lines = [l.strip() for l in response_text.strip().split('\n') if l.strip()]
    if len(lines) == 1 and len(lines[0]) < 150:
        candidate = lines[0]
        if candidate.startswith('$ ') or candidate.startswith('> '):
            candidate = candidate[2:].strip()
        if candidate and not candidate.startswith(('Here', 'You', 'To', 'Run', 'Note', 'Please', 'I')):
            commands.append(candidate)

    return commands


# ─── PuTTY Terminal Buffer Manager ──────────────────────────────────────────
class PuttyBufferManager:
    """
    Discovers PuTTY / KiTTY windows and extracts clean delta outputs.
    """

    def __init__(self, min_threshold_chars: int = DEFAULT_MIN_THRESHOLD_CHARS, logger: Optional[logging.Logger] = None):
        self.min_threshold_chars = min_threshold_chars
        self.logger = logger or logging.getLogger("PuttyBridge")
        self.hwnd: Optional[int] = None
        self.window_title: str = ""
        self.last_buffer_content: str = ""
        self.dedup_hashes: List[str] = []
        self.lock = threading.Lock()

    def discover_putty_window(self) -> Optional[int]:
        """Finds the most recently active PuTTY or KiTTY window."""
        found = []

        def enum_win(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                cls_buff = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buff, 256)
                cls = cls_buff.value
                t_len = user32.GetWindowTextLengthW(hwnd)
                title = ""
                if t_len > 0:
                    t_buff = ctypes.create_unicode_buffer(t_len + 1)
                    user32.GetWindowTextW(hwnd, t_buff, t_len + 1)
                    title = t_buff.value
                if cls.lower() in ('putty', 'kitty', 'virtualconsoleclass', 'mintty') or 'putty' in title.lower() or 'kitty' in title.lower():
                    found.append((hwnd, cls, title))
            return True

        user32.EnumWindows(WNDENUMPROC(enum_win), 0)
        if found:
            # Pick first found window
            self.hwnd = found[0][0]
            self.window_title = found[0][2]
            return self.hwnd
        self.hwnd = None
        self.window_title = ""
        return None

    def get_raw_clipboard_text(self) -> str:
        """Reads current text from the Windows clipboard safely."""
        text = ""
        try:
            if user32.OpenClipboard(0):
                h_clip = user32.GetClipboardData(13) # CF_UNICODETEXT
                if h_clip:
                    ptr = kernel32.GlobalLock(h_clip)
                    if ptr:
                        try:
                            text = ctypes.c_wchar_p(ptr).value or ""
                        finally:
                            kernel32.GlobalUnlock(h_clip)
                user32.CloseClipboard()
        except Exception:
            pass
        return text

    def set_clipboard_text(self, text: str) -> bool:
        """Sets text onto the Windows clipboard."""
        try:
            if not user32.OpenClipboard(0):
                return False
            user32.EmptyClipboard()
            data = text.encode('utf-16-le') + b'\x00\x00'
            h_mem = kernel32.GlobalAlloc(0x0002, len(data)) # GMEM_MOVEABLE
            if h_mem:
                ptr = kernel32.GlobalLock(h_mem)
                if ptr:
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h_mem)
                    user32.SetClipboardData(13, h_mem)
            user32.CloseClipboard()
            return True
        except Exception:
            try: user32.CloseClipboard()
            except: pass
            return False

    def fetch_full_terminal_buffer(self) -> str:
        """Triggers IDM_COPYALL on PuTTY and returns clean buffer text."""
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            if not self.discover_putty_window():
                return ""

        # Trigger PuTTY's internal Copy All to Clipboard
        # PuTTY accepts IDM_COPYALL via WM_SYSCOMMAND or WM_COMMAND
        user32.SendMessageW(self.hwnd, WM_SYSCOMMAND, IDM_COPYALL, 0)
        time.sleep(0.05)

        raw = self.get_raw_clipboard_text()
        return clean_ansi(raw)

    def extract_new_delta(self) -> Tuple[Optional[str], str]:
        """
        Extracts the newly appended output since the last check.
        Returns: (clean_delta_or_None, reason_str)
        """
        with self.lock:
            current_buffer = self.fetch_full_terminal_buffer()
            if not current_buffer:
                return None, "No terminal buffer available"

            # If first run, initialize baseline without triggering
            if not self.last_buffer_content:
                self.last_buffer_content = current_buffer
                return None, "Initialized baseline buffer watermark"

            # Calculate delta
            if current_buffer == self.last_buffer_content:
                return None, "No new output"

            # If current buffer starts with or contains previous buffer:
            if current_buffer.startswith(self.last_buffer_content):
                delta = current_buffer[len(self.last_buffer_content):]
            else:
                # Scrollback wrapped or screen cleared - find common overlap
                overlap_len = 0
                for i in range(min(len(self.last_buffer_content), 500), 10, -1):
                    suffix = self.last_buffer_content[-i:]
                    idx = current_buffer.find(suffix)
                    if idx != -1:
                        overlap_len = idx + len(suffix)
                        break
                if overlap_len > 0:
                    delta = current_buffer[overlap_len:]
                else:
                    delta = current_buffer

            self.last_buffer_content = current_buffer
            clean_delta = clean_ansi(delta).strip()

            # Remove trailing bare prompt lines from delta
            lines = clean_delta.split('\n')
            if lines and SHELL_PROMPT_RE.match(lines[-1]):
                lines.pop()
            clean_delta = '\n'.join(lines).strip()

            # 1. Threshold filter (ignore echoes, single characters, prompt blinks)
            if len(clean_delta) < self.min_threshold_chars:
                return None, f"Below threshold ({len(clean_delta)} < {self.min_threshold_chars} chars)"

            # 2. Rolling Deduplication filter
            block_hash = hashlib.sha256(clean_delta.encode('utf-8')).hexdigest()
            if block_hash in self.dedup_hashes:
                return None, "Duplicate output block (already in memory history)"

            # Update deduplication history FIFO
            self.dedup_hashes.append(block_hash)
            if len(self.dedup_hashes) > DEDUP_HISTORY_SIZE:
                self.dedup_hashes.pop(0)

            return clean_delta, "New valid delta captured"

    def send_command_to_putty(self, command: str) -> bool:
        """Sends a command string to the PuTTY window and executes with Return."""
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            if not self.discover_putty_window():
                return False

        self.logger.info(f"[PuTTY SSH] Executing command: '{command}'")
        for ch in command:
            user32.PostMessageW(self.hwnd, WM_CHAR, ord(ch), 0)
            time.sleep(0.002)

        # Send Enter / Return
        user32.PostMessageW(self.hwnd, WM_KEYDOWN, VK_RETURN, 0)
        user32.PostMessageW(self.hwnd, WM_KEYUP, VK_RETURN, 0)
        return True


# ─── Copilot Interaction Bridge ─────────────────────────────────────────────
class CopilotBridgeManager:
    """
    Connects to Microsoft Copilot (Teams tab or Standalone app),
    submits prompts, and monitors/extracts AI responses.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("PuttyBridge")
        self.doc = None
        self.btn = None
        self.input_edit = None
        self.last_response_text = ""

    def ensure_connection(self) -> bool:
        """Discovers and connects to the active Copilot window."""
        try:
            # Import discover helper from guardian
            from copilot_model_guardian import find_copilot_document
            self.doc, self.btn = find_copilot_document(self.logger)
            if self.doc:
                return True
        except Exception as e:
            self.logger.debug(f"Copilot discovery error: {e}")
        return False

    def find_chat_input(self) -> Optional[auto.EditControl]:
        """Locates the chat prompt input EditControl."""
        if not self.doc:
            if not self.ensure_connection():
                return None

        # Search for EditControl
        queue = [self.doc]
        visited = 0
        while queue and visited < 300:
            c = queue.pop(0)
            visited += 1
            try:
                if c.ControlTypeName == 'EditControl':
                    self.input_edit = c
                    return c
                queue.extend(c.GetChildren())
            except Exception:
                pass
        return None

    def send_prompt_to_copilot(self, prompt_text: str) -> bool:
        """Types or sets the prompt into Copilot's input box and sends it."""
        edit = self.find_chat_input()
        if not edit:
            self.logger.error("Could not find Copilot chat input box.")
            return False

        self.logger.info(f"[Copilot] Sending prompt ({len(prompt_text)} chars)...")
        try:
            # Set value or type
            try:
                vp = edit.GetValuePattern()
                if vp:
                    vp.SetValue(prompt_text)
                else:
                    edit.SendKeys(prompt_text)
            except Exception:
                edit.SendKeys(prompt_text)

            time.sleep(0.1)
            # Submit by pressing Enter or clicking Send button
            edit.SendKeys('{Enter}')
            return True
        except Exception as e:
            self.logger.error(f"Failed to submit prompt to Copilot: {e}")
            return False

    def wait_for_copilot_response(self, timeout_sec: float = 45.0) -> Optional[str]:
        """
        Polls until Copilot finishes generating response, then returns the latest message text.
        """
        self.logger.info("[Copilot] Waiting for AI response generation...")
        t0 = time.time()
        time.sleep(1.5) # Initial generation spin-up wait

        while time.time() - t0 < timeout_sec:
            # Find response text in latest message container
            try:
                # Read all text controls under doc to find latest block
                texts = []
                queue = [self.doc]
                visited = 0
                while queue and visited < 400:
                    c = queue.pop(0)
                    visited += 1
                    try:
                        if c.ControlTypeName == 'TextControl' and c.Name:
                            texts.append(c.Name)
                        queue.extend(c.GetChildren())
                    except Exception:
                        pass

                full_text = "\n".join(texts)
                # If generating stopped and text is present
                if len(texts) > 5 and full_text != self.last_response_text:
                    self.last_response_text = full_text
                    # Extract the latest AI bubble text
                    return full_text
            except Exception:
                pass
            time.sleep(0.8)

        self.logger.warning("[Copilot] Response generation wait timed out.")
        return None


# ─── Autonomous Loop & Mode Controller ──────────────────────────────────────
class AutonomousBridgeEngine:
    """
    Coordinates PuTTY delta capture, manual staging, and autonomous back-and-forth execution.
    """

    def __init__(self, min_threshold_chars: int = DEFAULT_MIN_THRESHOLD_CHARS):
        self.logger = setup_logging()
        self.putty = PuttyBufferManager(min_threshold_chars, self.logger)
        self.copilot = CopilotBridgeManager(self.logger)
        self.auto_mode = False # Default: Safe manual staging (OFF)
        self.staged_delta: Optional[str] = None
        self.turn_count = 0
        self.max_auto_turns = DEFAULT_MAX_AUTO_TURNS
        self.running = True

    def toggle_mode(self):
        """Toggles between Manual Staging and Autonomous Loop."""
        self.auto_mode = not self.auto_mode
        self.turn_count = 0
        status = ">>> AUTONOMOUS LOOP: ON <<<" if self.auto_mode else ">>> MANUAL STAGING: ON (Safe Mode) <<<"
        self.logger.info("=" * 60)
        self.logger.info(f"   {status}")
        if self.auto_mode:
            self.logger.info("   PuTTY and Copilot will converse back-and-forth automatically.")
            self.logger.info("   Press [F9] or [T] to stop | [Esc] for emergency abort.")
        else:
            self.logger.info("   Auto-send disabled. New terminal deltas will be staged.")
            self.logger.info("   Press [Space] to send staged delta | [F9] to enable auto-loop.")
        self.logger.info("=" * 60)

    def step(self):
        """Single monitoring and execution cycle."""
        # 1. Ensure PuTTY is discovered
        if not self.putty.hwnd or not user32.IsWindow(self.putty.hwnd):
            if not self.putty.discover_putty_window():
                return

        # 2. Extract new delta
        delta, reason = self.putty.extract_new_delta()
        if not delta:
            return

        # Check for interactive prompt circuit breaker
        if INTERACTIVE_PROMPT_RE.search(delta):
            self.logger.warning("=" * 60)
            self.logger.warning("   [SAFETY TRIGGER] Interactive Prompt Detected in SSH!")
            self.logger.warning(f"   Snippet: '{delta[:80]}...'")
            self.logger.warning("   Pausing Autonomous Mode for user input.")
            self.logger.warning("=" * 60)
            self.auto_mode = False
            return

        self.staged_delta = delta
        self.logger.info(f"[New Output Captured] {len(delta)} characters | Reason: {reason}")
        self.logger.info(f"--- Output Preview ---\n{delta[:250]}{'...' if len(delta) > 250 else ''}\n----------------------")

        # Put staged delta on clipboard for convenience in manual mode
        self.putty.set_clipboard_text(delta)

        # If in Autonomous Mode, proceed with Copilot cycle
        if self.auto_mode:
            if self.turn_count >= self.max_auto_turns:
                self.logger.info(f"Reached max auto turns limit ({self.max_auto_turns}). Pausing auto loop.")
                self.auto_mode = False
                return

            self.turn_count += 1
            self.logger.info(f"[Auto Turn {self.turn_count}/{self.max_auto_turns}] Forwarding output to Copilot...")

            prompt = f"SSH Terminal output:\n```\n{delta}\n```\nAnalyze the output and provide the next single bash command to run in a code block."
            if not self.copilot.send_prompt_to_copilot(prompt):
                self.logger.warning("Failed to send prompt to Copilot. Pausing auto loop.")
                self.auto_mode = False
                return

            # Wait for AI response
            response = self.copilot.wait_for_copilot_response()
            if not response:
                self.logger.warning("No response from Copilot. Pausing auto loop.")
                self.auto_mode = False
                return

            # Extract command
            commands = extract_commands_from_copilot_response(response)
            if not commands:
                self.logger.info("Copilot provided no executable commands in code blocks. Pausing auto loop.")
                self.auto_mode = False
                return

            # Execute first command in PuTTY
            cmd_to_run = commands[0]
            self.logger.info(f"[Auto Turn {self.turn_count}] Extracted Command: '{cmd_to_run}'")
            self.putty.send_command_to_putty(cmd_to_run)

    def run_console_loop(self):
        """Main daemon loop with interactive console commands."""
        self.logger.info("=" * 65)
        self.logger.info("   PuTTY <-> Microsoft Copilot Smart Bridge Started")
        self.logger.info(f"   Mode            : {'AUTONOMOUS' if self.auto_mode else 'MANUAL (Default)'}")
        self.logger.info(f"   Threshold       : > {self.putty.min_threshold_chars} chars (ignores prompt echoes)")
        self.logger.info(f"   Deduplication   : Enabled (last {DEDUP_HISTORY_SIZE} output blocks)")
        self.logger.info("   Commands        : [T] Toggle Auto-Loop | [Space] Send Staged | [Q] Quit")
        self.logger.info("=" * 65)

        # Initial baseline capture
        self.putty.fetch_full_terminal_buffer()

        # Keyboard listener thread for hotkeys
        def key_listener():
            import msvcrt
            while self.running:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b't', b'T'):
                            self.toggle_mode()
                        elif ch == b' ':
                            if self.staged_delta:
                                self.logger.info("[Manual Trigger] Sending staged delta to Copilot...")
                                prompt = f"SSH Terminal output:\n```\n{self.staged_delta}\n```"
                                self.copilot.send_prompt_to_copilot(prompt)
                            else:
                                self.logger.info("No delta currently staged. Run a command in PuTTY first.")
                        elif ch in (b'q', b'Q', b'\x1b'): # 'Q' or Esc
                            self.logger.info("Exiting PuTTY Copilot Bridge...")
                            self.running = False
                    time.sleep(0.05)
                except Exception:
                    pass

        t = threading.Thread(target=key_listener, daemon=True)
        t.start()

        while self.running:
            try:
                self.step()
                time.sleep(DEFAULT_POLL_INTERVAL)
            except KeyboardInterrupt:
                self.logger.info("Interrupt signal received. Shutting down...")
                break
            except Exception as e:
                self.logger.error(f"Bridge loop error: {e}")
                time.sleep(1.0)


if __name__ == '__main__':
    engine = AutonomousBridgeEngine()
    engine.run_console_loop()
