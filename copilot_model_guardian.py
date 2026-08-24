"""
==============================================================================
 Microsoft Copilot Model Guardian
 Monitors Microsoft Copilot and ensures your preferred AI model is always
 selected — even when Copilot resets it after a new chat or session switch.

 Author: Dmitriy Yevseyev (@DYevseyev)
 License: MIT
==============================================================================
"""

import os
import sys
import time
import signal
import argparse
import logging
import ctypes
from ctypes import wintypes
import uiautomation as auto

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGET_MODEL = "GPT 5.6 Think deeper"
DEFAULT_POLL_INTERVAL = 1.5
DEFAULT_LOG_FILE = os.path.join(SCRIPT_DIR, "copilot_guardian.log")

def is_target_model_active(current_text: str, target_model: str) -> bool:
    """
    Returns True if the currently displayed model text matches the target.
    Uses a flexible substring match so truncated button labels (e.g. 'GPT 5.6 Think')
    still correctly match a full target like 'GPT 5.6 Think deeper'.
    """
    current = current_text.lower().strip()
    target = target_model.lower().strip()
    if not current:
        return False
    # Match if either is a prefix/substring of the other (handles button truncation)
    return target in current or current in target

# Win32 desktop API definitions for reliable background / interactive integration
user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, WNDENUMPROC, wintypes.LPARAM]
user32.EnumDesktopWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

def setup_logging(log_file=DEFAULT_LOG_FILE, verbose=False):
    """Configure logging for both console and pythonw background execution."""
    log_format = '[%(asctime)s] [%(levelname)s] %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    level = logging.DEBUG if verbose else logging.INFO

    handlers = []
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    
    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            handlers.append(logging.FileHandler(log_file, encoding='utf-8', mode='a'))
        except Exception:
            pass

    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(level=level, format=log_format, datefmt=date_format, handlers=handlers, force=True)
    return logging.getLogger("CopilotGuardian")

def ensure_desktop_station(logger=None):
    """Ensure process and thread have access to interactive WinSta0\\Default desktop."""
    try:
        hwinsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)
        hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
        return hdesk
    except Exception as e:
        if logger:
            logger.debug(f"Desktop station binding notice: {e}")
        return None

def find_copilot_document(logger=None):
    """Locate the Copilot Chromium/Edge Render Widget inside active Copilot windows."""
    hdesk = ensure_desktop_station(logger)
    top_hwnds = []

    def enum_top(hwnd, lparam):
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                t_buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, t_buff, length + 1)
                title = t_buff.value
            cls_buff = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buff, 256)
            cls = cls_buff.value

            if "copilot" in title.lower() or "copilot" in cls.lower():
                top_hwnds.append(hwnd)
        except Exception:
            pass
        return True

    cb = WNDENUMPROC(enum_top)
    if hdesk:
        user32.EnumDesktopWindows(hdesk, cb, 0)

    render_hwnds = []
    def enum_child(hwnd, lparam):
        try:
            cls_buff = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls_buff, 256)
            if cls_buff.value == 'Chrome_RenderWidgetHostHWND':
                render_hwnds.append(hwnd)
        except Exception:
            pass
        return True

    child_cb = WNDENUMPROC(enum_child)
    for top_h in top_hwnds:
        user32.EnumChildWindows(top_h, child_cb, 0)

    for rh in render_hwnds:
        try:
            ctrl = auto.ControlFromHandle(rh)
            btn = ctrl.ButtonControl(AutomationId='gptModeSwitcher')
            if btn.Exists(0, 0):
                return ctrl, btn
        except Exception:
            pass

    return None, None

def get_current_model_name(btn):
    """Retrieve the text currently displayed on the gptModeSwitcher button."""
    try:
        text_pieces = []
        for sub in btn.GetChildren():
            try:
                if sub.Name:
                    text_pieces.append(sub.Name)
                for s in sub.GetChildren():
                    try:
                        if s.Name:
                            text_pieces.append(s.Name)
                    except Exception:
                        pass
            except Exception:
                pass
        return " ".join(text_pieces).strip()
    except Exception:
        return ""

def enforce_model_selection(doc, btn, target_model=DEFAULT_TARGET_MODEL, logger=None):
    """
    Checks if target_model is selected. If not, automatically opens the dropdown and selects it.
    """
    if logger is None:
        logger = logging.getLogger("CopilotGuardian")

    try:
        current_text = get_current_model_name(btn)
        
        # Check if target is already active
        if is_target_model_active(current_text, target_model):
            return True, current_text

        logger.warning(f"Detected non-target model: '{current_text or 'Auto/Unknown'}'. Switching to target: '{target_model}'...")

        root_web = doc.DocumentControl()
        if not root_web.Exists(0, 0):
            root_web = doc

        # 1. Expand the model switcher dropdown
        try:
            ec = btn.GetExpandCollapsePattern()
            if ec and ec.ExpandCollapseState == 0:  # Collapsed
                ec.Expand()
                time.sleep(0.3)
        except Exception:
            btn.Click(simulateMove=False)
            time.sleep(0.3)

        # 2. Find the 'GPT OpenAI' menu item to open sub-menu using BFS
        gpt_menu = None
        queue = [root_web]
        while queue:
            curr = queue.pop(0)
            try:
                if curr.ControlTypeName == 'MenuItemControl' and 'gpt' in curr.Name.lower() and 'openai' in curr.Name.lower():
                    gpt_menu = curr
                    break
                queue.extend(curr.GetChildren())
            except Exception:
                pass

        if gpt_menu:
            try:
                gpt_ec = gpt_menu.GetExpandCollapsePattern()
                if gpt_ec and gpt_ec.ExpandCollapseState == 0:
                    gpt_ec.Expand()
                else:
                    gpt_menu.MoveCursorToMyCenter()
            except Exception:
                gpt_menu.MoveCursorToMyCenter()
            time.sleep(0.3)

        # 3. Locate target radio button using BFS
        target_radio = None
        queue = [root_web]
        while queue:
            curr = queue.pop(0)
            try:
                if curr.ControlTypeName == 'RadioButtonControl':
                    if is_target_model_active(curr.Name, target_model):
                        target_radio = curr
                        break
                queue.extend(curr.GetChildren())
            except Exception:
                pass

        if target_radio:
            try:
                pat = target_radio.GetSelectionItemPattern()
                if pat:
                    pat.Select()
                else:
                    target_radio.Click(simulateMove=False)
            except Exception:
                target_radio.Click(simulateMove=False)
            
            time.sleep(0.3)
            updated_text = get_current_model_name(btn)
            logger.info(f"Target model selected successfully! Active status: '{updated_text}'")
            return True, updated_text
        else:
            logger.error(f"Could not locate '{target_model}' in flyout menu.")
            return False, current_text

    except Exception as e:
        logger.error(f"Enforce model selection error: {e}")
        return False, ""

def monitor_loop(target_model=DEFAULT_TARGET_MODEL, poll_interval=DEFAULT_POLL_INTERVAL, log_file=DEFAULT_LOG_FILE, verbose=False):
    """Continuous monitoring loop."""
    logger = setup_logging(log_file, verbose)

    logger.info("=" * 65)
    logger.info("   MICROSOFT COPILOT MODEL GUARDIAN ACTIVE")
    logger.info(f"   Target Model : {target_model}")
    logger.info(f"   Poll Interval: {poll_interval}s")
    if log_file:
        logger.info(f"   Log File     : {os.path.abspath(log_file)}")
    logger.info("=" * 65)

    running = True

    def sig_handler(signum, frame):
        nonlocal running
        logger.info("Shutdown signal received. Exiting...")
        running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    last_status = None

    while running:
        try:
            doc, btn = find_copilot_document(logger)
            if doc and btn:
                current_mode = get_current_model_name(btn)
                if not is_target_model_active(current_mode, target_model):
                    logger.info(f"Copilot model changed to '{current_mode}'. Enforcing '{target_model}'...")
                    enforce_model_selection(doc, btn, target_model, logger)
                    last_status = "UPDATED"
                else:
                    if last_status != "OK":
                        logger.info(f"Copilot protected. Model '{current_mode}' is active.")
                        last_status = "OK"
            else:
                if last_status != "NOT_FOUND":
                    logger.info("Monitoring active. Waiting for Copilot window / conversation...")
                    last_status = "NOT_FOUND"
        except Exception as e:
            logger.debug(f"Monitoring loop iteration error: {e}")
        
        time.sleep(poll_interval)

    logger.info("Copilot Model Guardian stopped.")

def main():
    parser = argparse.ArgumentParser(description="Microsoft Copilot Model Guardian")
    parser.add_argument("--model", default=DEFAULT_TARGET_MODEL, help=f"Target model to enforce (default: '{DEFAULT_TARGET_MODEL}')")
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL, help=f"Polling check interval in seconds (default: {DEFAULT_POLL_INTERVAL})")
    parser.add_argument("--log-file", default=DEFAULT_LOG_FILE, help=f"Path to log file (default: '{DEFAULT_LOG_FILE}')")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    monitor_loop(target_model=args.model, poll_interval=args.interval, log_file=args.log_file, verbose=args.verbose)

if __name__ == '__main__':
    main()
