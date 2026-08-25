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

# Enable per-monitor DPI awareness so UIA coordinates match physical screen pixels
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TARGET_MODEL = "GPT 5.6 Think deeper"
DEFAULT_POLL_INTERVAL = 0.2
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
    """
    Locate the Copilot UI root and gptModeSwitcher button.

    Tries two strategies in order:
    Strategy A — Standalone Copilot app (Copilot.exe / M365Copilot.exe):
        Finds windows with 'copilot' in title/class, then looks for a
        Chrome_RenderWidgetHostHWND child as the UIA root.
    Strategy B — Microsoft Teams (any context):
        Scans ALL TeamsWebView windows for the gptModeSwitcher button.
        Covers the dedicated Copilot tab AND Copilot inside any Teams chat
        or channel (where the window title is the chat name, not 'Copilot').
        Windows with 'copilot' in the title are checked first as a fast path.
    """
    hdesk = ensure_desktop_station(logger)

    standalone_hwnds  = []   # Strategy A candidates
    teams_copilot_tab = []   # Strategy B — dedicated Copilot tab (fast path)
    teams_other       = []   # Strategy B — all other TeamsWebView windows

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

            low_title = title.lower()
            low_cls   = cls.lower()

            if cls == "TeamsWebView":
                # Prioritise the dedicated Copilot tab by checking it first
                if "copilot" in low_title:
                    teams_copilot_tab.append(hwnd)
                else:
                    teams_other.append(hwnd)
            elif "copilot" in low_title or "copilot" in low_cls:
                standalone_hwnds.append(hwnd)
        except Exception:
            pass
        return True

    cb = WNDENUMPROC(enum_top)
    if hdesk:
        user32.EnumDesktopWindows(hdesk, cb, 0)

    def check_handle_for_switcher(h):
        try:
            ctrl = auto.ControlFromHandle(h)
            btn = ctrl.ButtonControl(AutomationId='gptModeSwitcher')
            if btn.Exists(0, 0):
                return ctrl, btn
            btn2 = ctrl.ButtonControl(Name='Model Selector')
            if btn2.Exists(0, 0):
                return ctrl, btn2
        except Exception:
            pass
        return None, None

    # ── Strategy A: Standalone Copilot (M365 Copilot, Edge/PWA Copilot) ─────
    for top_h in standalone_hwnds:
        # Check child render widgets first
        child_hwnds = []
        def enum_child(ch, lparam):
            try:
                cls_buff = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(ch, cls_buff, 256)
                if cls_buff.value in ('Chrome_RenderWidgetHostHWND', 'Intermediate D3D Window'):
                    child_hwnds.append(ch)
            except Exception:
                pass
            return True
        user32.EnumChildWindows(top_h, WNDENUMPROC(enum_child), 0)

        for ch in (child_hwnds + [top_h]):
            ctrl, btn = check_handle_for_switcher(ch)
            if ctrl and btn:
                return ctrl, btn

    # ── Strategy B: Teams (Dedicated tab first, then chat channels) ─────────
    for th in (teams_copilot_tab + teams_other):
        child_hwnds = []
        def enum_teams_child(ch, lparam):
            try:
                cls_buff = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(ch, cls_buff, 256)
                if cls_buff.value in ('Chrome_RenderWidgetHostHWND', 'Intermediate D3D Window'):
                    child_hwnds.append(ch)
            except Exception:
                pass
            return True
        user32.EnumChildWindows(th, WNDENUMPROC(enum_teams_child), 0)

        for ch in (child_hwnds + [th]):
            ctrl, btn = check_handle_for_switcher(ch)
            if ctrl and btn:
                return ctrl, btn

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

def get_web_root(btn, doc):
    """
    Find the closest DocumentControl ancestor of the button (the web rendering container).
    Searching within this subtree is 50-100x faster than traversing the whole top-level window.
    """
    try:
        p = btn.GetParentControl()
        while p:
            if p.ControlTypeName == 'DocumentControl':
                return p
            p = p.GetParentControl()
    except Exception:
        pass
    return doc

def find_all_controls(root, matcher, max_visit=400):
    """Fast bounded breadth-first search across UI controls."""
    results = []
    queue = [root]
    visited = 0
    while queue and visited < max_visit:
        curr = queue.pop(0)
        visited += 1
        try:
            if matcher(curr):
                results.append(curr)
            queue.extend(curr.GetChildren())
        except Exception:
            pass
    return results

def enforce_model_selection(doc, btn, target_model=DEFAULT_TARGET_MODEL, logger=None):
    """
    Checks if target_model is selected. If not, opens the dropdown and selects it.
    Executes in ~0.35s via direct popup hierarchy traversal.
    """
    if logger is None:
        logger = logging.getLogger("CopilotGuardian")

    try:
        current_text = get_current_model_name(btn)

        # Check if target is already active
        if is_target_model_active(current_text, target_model):
            return True, current_text

        logger.warning(f"Detected non-target model: '{current_text or 'Auto/Unknown'}'. Switching to '{target_model}'...")

        web_root = get_web_root(btn, doc)

        # ── Step 1: Open the model switcher dropdown ─────────────────────────
        opened = False
        try:
            ec = btn.GetExpandCollapsePattern()
            if ec:
                if ec.ExpandCollapseState == 1:
                    ec.Collapse(waitTime=0.02)
                ec.Expand(waitTime=0.05)
                opened = True
        except Exception:
            pass
        if not opened:
            btn.Click(simulateMove=False, waitTime=0.05)

        # ── Step 2: Find GPT Section Trigger in Popups ────────────────────────
        gpt_trigger = None
        for _ in range(15):
            children = web_root.GetChildren()
            popup_nodes = children[-3:] if len(children) >= 3 else children
            for c in reversed(popup_nodes):
                queue = [c]
                while queue:
                    curr = queue.pop(0)
                    typ = curr.ControlTypeName
                    name = (curr.Name or '').lower()
                    aid = curr.AutomationId or ''
                    if typ == 'MenuItemControl' and ('gpt' in name or 'openai' in name):
                        gpt_trigger = curr
                        break
                    if typ == 'GroupControl' and aid.startswith(('menur', 'menu_r')) and not curr.Name:
                        gpt_trigger = curr
                        break
                    try: queue.extend(curr.GetChildren())
                    except: pass
                if gpt_trigger:
                    break
            if gpt_trigger:
                break
            time.sleep(0.01)

        # Fallback search if direct popup check missed
        if not gpt_trigger:
            btn_rect = btn.BoundingRectangle
            gpt_headers = find_all_controls(
                web_root,
                lambda el: (el.ControlTypeName in ('GroupControl', 'MenuItemControl')) and
                           (
                               (el.AutomationId or '').startswith(('menur', 'menu_r')) or
                               ('gpt' in (el.Name or '').lower() or 'openai' in (el.Name or '').lower())
                           ) and
                           el.BoundingRectangle.top > btn_rect.bottom,
                max_visit=200
            )
            if gpt_headers:
                gpt_trigger = min(gpt_headers, key=lambda el: el.BoundingRectangle.top)

        if not gpt_trigger:
            logger.error("Could not find GPT section trigger in the dropdown.")
            return False, current_text

        # ── Step 3: Expand GPT Section ───────────────────────────────────────
        try:
            t_ec = gpt_trigger.GetExpandCollapsePattern()
            if t_ec:
                t_ec.Expand(waitTime=0.05)
            else:
                gpt_trigger.Click(simulateMove=False, waitTime=0.05)
        except Exception:
            try: gpt_trigger.Click(simulateMove=False, waitTime=0.05)
            except: pass

        # ── Step 4: Multi-Level Search for Target Model Row ──────────────────
        target_row = None
        for _ in range(15):
            children = web_root.GetChildren()
            popup_nodes = children[-3:] if len(children) >= 3 else children
            for c in reversed(popup_nodes):
                queue = [c]
                while queue:
                    curr = queue.pop(0)
                    try:
                        name = curr.Name or ""
                        if ('5.6' in name or '5.5' in name) and is_target_model_active(name, target_model):
                            target_row = curr
                            break
                        for sub in curr.GetChildren():
                            if sub.ControlTypeName == 'TextControl' and ('5.6' in (sub.Name or '') or '5.5' in (sub.Name or '')):
                                if is_target_model_active(sub.Name, target_model):
                                    target_row = curr
                                    break
                        if target_row:
                            break
                        queue.extend(curr.GetChildren())
                    except Exception:
                        pass
                if target_row:
                    break
            if target_row:
                break
            time.sleep(0.01)

        # Fallback search if direct popup check missed
        if not target_row:
            gpt_menus = find_all_controls(
                web_root,
                lambda el: el.ControlTypeName in ('RadioButtonControl', 'MenuItemControl', 'GroupControl') and
                           ('5.6' in (el.Name or '') or '5.5' in (el.Name or '')) and
                           is_target_model_active(el.Name or '', target_model),
                max_visit=200
            )
            if gpt_menus:
                target_row = gpt_menus[0]

        if not target_row:
            logger.error(f"Could not locate target model row '{target_model}'.")
            return False, current_text

        # ── Step 5: Safe Activation Pipeline ─────────────────────────────────
        logger.info(f"Activating target model row: [{target_row.ControlTypeName}] '{target_row.Name}'")
        activated = False
        try:
            p_sel = target_row.GetSelectionItemPattern()
            if p_sel:
                p_sel.Select(waitTime=0.02)
                activated = True
        except Exception:
            pass

        if not activated:
            try:
                p = target_row.GetLegacyIAccessiblePattern()
                if p:
                    p.DoDefaultAction(waitTime=0.02)
                    activated = True
            except Exception:
                pass

        if not activated:
            try:
                target_row.Click(simulateMove=False, waitTime=0.02)
            except Exception:
                pass

        # ── Step 6: Adaptive Verification Loop ───────────────────────────────
        for _ in range(15):
            time.sleep(0.015)
            updated_text = get_current_model_name(btn)
            if is_target_model_active(updated_text, target_model):
                logger.info(f"Model selection verified active: '{updated_text}'")
                return True, updated_text

        updated_text = get_current_model_name(btn)
        logger.info(f"Model selection applied. Active: '{updated_text}'")
        return is_target_model_active(updated_text, target_model), updated_text

    except Exception as e:
        logger.error(f"Enforce model selection error: {e}")
        return False, ""



def monitor_loop(target_model=DEFAULT_TARGET_MODEL, poll_interval=DEFAULT_POLL_INTERVAL, log_file=DEFAULT_LOG_FILE, verbose=False):
    """Continuous monitoring loop with cached element handles for fast polling."""
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
    cached_doc = None
    cached_btn = None

    while running:
        try:
            # Validate cached handle; re-discover only if missing or closed
            valid = False
            if cached_doc and cached_btn:
                try:
                    if cached_btn.Exists(0, 0):
                        valid = True
                except Exception:
                    valid = False

            if not valid:
                cached_doc, cached_btn = find_copilot_document(logger)

            if cached_doc and cached_btn:
                current_mode = get_current_model_name(cached_btn)
                if current_mode and not is_target_model_active(current_mode, target_model):
                    logger.info(f"Copilot model changed to '{current_mode}'. Enforcing '{target_model}'...")
                    enforce_model_selection(cached_doc, cached_btn, target_model, logger)
                    last_status = "UPDATED"
                elif not current_mode:
                    # Handle is stale/disconnected — re-discover immediately without delay
                    cached_doc, cached_btn = find_copilot_document(logger)
                    if cached_btn:
                        current_mode = get_current_model_name(cached_btn)
                        if current_mode and not is_target_model_active(current_mode, target_model):
                            logger.info(f"Copilot model changed to '{current_mode}'. Enforcing '{target_model}'...")
                            enforce_model_selection(cached_doc, cached_btn, target_model, logger)
                            last_status = "UPDATED"
                else:
                    if last_status != "OK" and current_mode:
                        logger.info(f"Copilot protected. Model '{current_mode}' is active.")
                        last_status = "OK"
            else:
                if last_status != "NOT_FOUND":
                    logger.info("Monitoring active. Waiting for Copilot window / conversation...")
                    last_status = "NOT_FOUND"
        except Exception as e:
            logger.debug(f"Monitoring loop iteration error: {e}")
            cached_doc = None
            cached_btn = None
        
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

