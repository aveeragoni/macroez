"""The Mac side of MacroEZ: pressing keys, clicking, scrolling, recording, hotkeys and app detection, done the
macOS way (Quartz events and AppKit). macroez.py stays the same on both systems; install() swaps these in.

Keys keep MacroEZ's Windows-style identities everywhere (scan codes for keys a macro presses, virtual-key numbers
for hotkeys), so macros and presets made on Windows work on a Mac and the other way round. This file translates.
"""
import fcntl
import os
import threading
import time
from pathlib import Path

import AppKit
import Quartz

try:  # Accessibility check lives in ApplicationServices (HIServices)
    from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
except ImportError:  # older PyObjC layouts
    from HIServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

OWN_PID = os.getpid()
HID = Quartz.kCGEventSourceStateHIDSystemState

# MacroEZ key names -> Mac key codes (kVK_* from Carbon's Events.h). Windows keys are Command, Alt is Option.
MAC_KEYS = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09,
    "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F, "u": 0x20, "i": 0x22,
    "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19, "0": 0x1D,
    "=": 0x18, "-": 0x1B, "]": 0x1E, "[": 0x21, "'": 0x27, ";": 0x29, "\\": 0x2A, ",": 0x2B, "/": 0x2C, ".": 0x2F,
    "`": 0x32,
    "enter": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33, "esc": 0x35, "capslock": 0x39,
    "shift": 0x38, "right shift": 0x3C, "ctrl": 0x3B, "right ctrl": 0x3E, "alt": 0x3A, "right alt": 0x3D,
    "left win": 0x37, "right win": 0x36, "menu": 0x6E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64, "f9": 0x65,
    "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "printscreen": 0x69, "scrolllock": 0x6B,  # F13 and F14 sit where those keys are on a Mac keyboard
    "insert": 0x72, "delete": 0x75, "home": 0x73, "end": 0x77, "pageup": 0x74, "pagedown": 0x79,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "numlock": 0x47, "num/": 0x4B, "num*": 0x43, "num-": 0x4E, "num+": 0x45, "numenter": 0x4C, "num.": 0x41,
    "num0": 0x52, "num1": 0x53, "num2": 0x54, "num3": 0x55, "num4": 0x56, "num5": 0x57, "num6": 0x58,
    "num7": 0x59, "num8": 0x5B, "num9": 0x5C,
}
PAUSE_KEY = 0x71  # F15, where Pause sits on a Mac keyboard
MODIFIER_FLAGS = {  # key code -> the flag apps read to know it's held
    0x38: Quartz.kCGEventFlagMaskShift, 0x3C: Quartz.kCGEventFlagMaskShift,
    0x3B: Quartz.kCGEventFlagMaskControl, 0x3E: Quartz.kCGEventFlagMaskControl,
    0x3A: Quartz.kCGEventFlagMaskAlternate, 0x3D: Quartz.kCGEventFlagMaskAlternate,
    0x37: Quartz.kCGEventFlagMaskCommand, 0x36: Quartz.kCGEventFlagMaskCommand,
    0x39: Quartz.kCGEventFlagMaskAlphaShift,
}
BUTTONS = {  # MacroEZ button -> (Quartz button number, down event, up event, drag event)
    "left": (0, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGEventLeftMouseDragged),
    "right": (1, Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGEventRightMouseDragged),
    "middle": (2, Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGEventOtherMouseDragged),
    "back": (3, Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGEventOtherMouseDragged),
    "forward": (4, Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp, Quartz.kCGEventOtherMouseDragged),
}
BUTTON_FOR_NUMBER = {number: name for name, (number, *_rest) in BUTTONS.items()}
MOUSE_VKS = {0x01: 0, 0x02: 1, 0x04: 2, 0x05: 3, 0x06: 4}  # Windows mouse virtual keys -> Quartz button numbers

g = {}  # macroez's globals, filled by install()
_held_flags = 0  # modifiers MacroEZ is holding down right now
_held_buttons = set()
_last_click = {"time": 0.0, "button": None, "point": (0, 0), "count": 0}
_scroll_rest = {}  # leftover part-notches per direction, so 1/4 notches add up
SCAN_TO_MAC = {}  # (scan, extended) -> Mac key code
MAC_TO_SCAN = {}  # Mac key code -> (scan, extended)
VK_TO_MAC = {}  # hotkey virtual key -> Mac key code
MAC_TO_VK = {}


def _build_tables():
    for name, code in MAC_KEYS.items():
        scan = g["SCAN_CODES"].get(name)
        if scan is None:
            continue
        pair = (scan, name in g["EXTENDED_KEYS"])
        SCAN_TO_MAC[pair] = code
        MAC_TO_SCAN.setdefault(code, pair)
    for *_shape, press, hot in g["KEYBOARD"]:
        vk = g["HOTKEY_KEYS"].get(hot)
        if not vk:
            continue
        code = PAUSE_KEY if hot == "Pause" else MAC_KEYS.get(press)
        if code is not None:
            VK_TO_MAC.setdefault(vk, code)
            MAC_TO_VK.setdefault(code, vk)


def _post(event):
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


# ---------------------------------------------------------------- keys
def send_scan(code, extended=False, up=False):
    """Press or release a key, named by its Windows scan code."""
    global _held_flags
    mac = SCAN_TO_MAC.get((code, bool(extended)))
    if mac is None:
        return 0
    vk = MAC_TO_VK.get(mac)
    if vk:
        g["_sent_at"][vk] = time.monotonic()  # MacroEZ's own presses never trigger its hotkeys
    flag = MODIFIER_FLAGS.get(mac)
    if flag and mac != 0x39:
        _held_flags = (_held_flags & ~flag) if up else (_held_flags | flag)
    event = Quartz.CGEventCreateKeyboardEvent(None, mac, not up)
    Quartz.CGEventSetFlags(event, _held_flags)
    _post(event)
    return 1


def key_is_down(vk):
    if vk in MOUSE_VKS:
        return bool(Quartz.CGEventSourceButtonState(HID, MOUSE_VKS[vk]))
    mac = VK_TO_MAC.get(vk)
    return mac is not None and bool(Quartz.CGEventSourceKeyState(HID, mac))


def key_name(scan, extended):
    return g["NAME_FOR_SCAN"].get((scan, extended), f"key {scan}")


# ---------------------------------------------------------------- mouse
def cursor_position():
    point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return int(round(point.x)), int(round(point.y))


def _mouse_event(kind, point, button_number=0):
    event = Quartz.CGEventCreateMouseEvent(None, kind, point, button_number)
    Quartz.CGEventSetFlags(event, _held_flags)
    return event


def _move_event_type():
    """Moving while a button is held is a drag on a Mac; apps ignore plain moves then."""
    for name in ("left", "right", "middle", "back", "forward"):
        if name in _held_buttons:
            number, _down, _up, drag = BUTTONS[name]
            return drag, number
    return Quartz.kCGEventMouseMoved, 0


def set_cursor(x, y):
    kind, number = _move_event_type()
    _post(_mouse_event(kind, (x, y), number))


def nudge_mouse():
    x, y = cursor_position()
    set_cursor(x + 1, y)
    set_cursor(x, y)


def move_to(x, y):
    """Put the cursor there and wiggle it a pixel, so the app notices the hover. Takes about MOVE_LEAD."""
    set_cursor(x, y)
    time.sleep(0.03)
    set_cursor(x + 1, y)
    time.sleep(0.02)
    set_cursor(x, y)
    time.sleep(0.03)


def mouse_button(button, up=False):
    number, down_type, up_type, _drag = BUTTONS.get(button, BUTTONS["left"])
    point = cursor_position()
    event = _mouse_event(up_type if up else down_type, point, number)
    if not up:  # double-clicks count up, the way a Mac expects them
        last = _last_click
        near = abs(point[0] - last["point"][0]) <= 4 and abs(point[1] - last["point"][1]) <= 4
        if last["button"] == button and near and time.monotonic() - last["time"] < 0.5:
            last["count"] += 1
        else:
            last["count"] = 1
        last.update(time=time.monotonic(), button=button, point=point)
        _held_buttons.add(button)
    else:
        _held_buttons.discard(button)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, max(_last_click["count"], 1))
    _post(event)


def scroll_wheel(direction, amount):
    """Turn the wheel at the cursor. 120 is one notch, as on Windows; part-notches add up."""
    key = direction
    total = _scroll_rest.get(key, 0.0) + amount / g["WHEEL_NOTCH"]
    lines = int(total)
    _scroll_rest[key] = total - lines
    if not lines:
        return
    vertical = {"up": lines, "down": -lines}.get(direction, 0)
    horizontal = {"left": lines, "right": -lines}.get(direction, 0)
    event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 2, vertical, horizontal)
    _post(event)


# ---------------------------------------------------------------- apps and windows
def _app_name(app):
    url = app.executableURL()
    return (url.lastPathComponent() if url else None) or app.localizedName() or ""


def foreground_exe():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return _app_name(app) if app else ""


def foreground_is_ours():
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return bool(app) and app.processIdentifier() == OWN_PID


def point_is_ours(x, y):
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
    for window in windows or []:  # front to back
        if window.get("kCGWindowLayer", 0) != 0 or window.get("kCGWindowAlpha", 1) == 0:
            continue
        bounds = window.get("kCGWindowBounds", {})
        if bounds.get("X", 0) <= x < bounds.get("X", 0) + bounds.get("Width", 0) and \
                bounds.get("Y", 0) <= y < bounds.get("Y", 0) + bounds.get("Height", 0):
            return window.get("kCGWindowOwnerPID") == OWN_PID
    return False


def open_apps():
    """Apps with a window, like the Dock shows them: {app name: its display name}."""
    found = {}
    for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != AppKit.NSApplicationActivationPolicyRegular or app.processIdentifier() == OWN_PID:
            continue
        name = _app_name(app)
        if name and name.lower() not in ("finder",):
            found.setdefault(name, app.localizedName() or name)
    return dict(sorted(found.items(), key=lambda item: item[0].lower()))


# ---------------------------------------------------------------- recording
class Recorder:
    """Watches real key presses, clicks and scrolls with a listen-only event tap. Same events as on Windows."""

    def __init__(self, skip_vks=(), allow_injected=False):
        self.skip = set(skip_vks)
        self.allow_injected = allow_injected
        self.events = []
        self.down = set()
        self.loop = None
        self.ready = threading.Event()
        self.error = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(2)
        if self.error:
            raise OSError(self.error)

    def stop(self):
        if self.loop is not None:
            Quartz.CFRunLoopStop(self.loop)
        return list(self.events)

    def _injected(self, event):
        return Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == OWN_PID

    def _key(self, code, up):
        if MAC_TO_VK.get(code) in self.skip or code not in MAC_TO_SCAN:
            return
        physical = MAC_TO_SCAN[code]
        if not up and physical in self.down:
            return  # a held key repeats; keep only the first press
        if up and physical not in self.down:
            return  # let go of a key that was already down when recording started
        if foreground_is_ours():
            return
        (self.down.discard if up else self.down.add)(physical)
        self.events.append((time.perf_counter(), "key", up, physical))

    def _callback(self, _proxy, kind, event, _refcon):
        try:
            if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self.tap, True)
                return event
            if self._injected(event) and not self.allow_injected:
                return event
            if kind in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                if not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                    self._key(code, kind == Quartz.kCGEventKeyUp)
            elif kind == Quartz.kCGEventFlagsChanged:  # Shift, Control, Option, Command
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                flag = MODIFIER_FLAGS.get(code)
                if flag:
                    self._key(code, not (Quartz.CGEventGetFlags(event) & flag) if code != 0x39 else
                              MAC_TO_SCAN.get(code) in self.down)
            elif kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGEventRightMouseDown,
                          Quartz.kCGEventRightMouseUp, Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp):
                number = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber)
                button = BUTTON_FOR_NUMBER.get(number)
                up = kind in (Quartz.kCGEventLeftMouseUp, Quartz.kCGEventRightMouseUp, Quartz.kCGEventOtherMouseUp)
                point = Quartz.CGEventGetLocation(event)
                x, y = int(round(point.x)), int(round(point.y))
                vk = {0: 0x01, 1: 0x02, 2: 0x04, 3: 0x05, 4: 0x06}.get(number)
                if button and vk not in self.skip and not point_is_ours(x, y) and (not up or button in self.down):
                    (self.down.discard if up else self.down.add)(button)
                    self.events.append((time.perf_counter(), "mouse", up, (button, x, y)))
            elif kind == Quartz.kCGEventScrollWheel:
                point = Quartz.CGEventGetLocation(event)
                x, y = int(round(point.x)), int(round(point.y))
                if not point_is_ours(x, y):
                    for axis, (positive, negative) in ((Quartz.kCGScrollWheelEventDeltaAxis1, ("up", "down")),
                                                       (Quartz.kCGScrollWheelEventDeltaAxis2, ("left", "right"))):
                        delta = Quartz.CGEventGetIntegerValueField(event, axis)
                        if delta:
                            self.events.append((time.perf_counter(), "scroll", False,
                                                (positive if delta > 0 else negative, abs(delta) * 120, x, y)))
        except Exception:
            pass
        return event

    def _run(self):
        kinds = (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp, Quartz.kCGEventFlagsChanged,
                 Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp, Quartz.kCGEventRightMouseDown,
                 Quartz.kCGEventRightMouseUp, Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp,
                 Quartz.kCGEventScrollWheel)
        mask = 0
        for kind in kinds:
            mask |= Quartz.CGEventMaskBit(kind)
        self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                           Quartz.kCGEventTapOptionListenOnly, mask, self._callback, None)
        if not self.tap:
            self.error = ("macOS didn't let MacroEZ watch the keyboard and mouse. Allow MacroEZ in System Settings → "
                          "Privacy & Security → Input Monitoring and Accessibility, then try again.")
            self.ready.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        self.loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self.loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        self.ready.set()
        Quartz.CFRunLoopRun()
        Quartz.CGEventTapEnable(self.tap, False)


# ---------------------------------------------------------------- the rest
def fine_timer(_on):
    """Mac timers are already precise."""


def register_link_handler():
    """macroez:// links are registered by the app bundle's Info.plist; nothing to do here."""


_lock_file = None


def already_running():
    global _lock_file
    lock = g["APP_DIR"] / ".macroez.lock"
    _lock_file = open(lock, "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True


SETTINGS_PAGES = {
    "Accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "Input Monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "Screen Recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}


def missing_permissions():
    """What macOS still has to allow: clicking and pressing keys, watching hotkeys, and seeing the screen."""
    missing = []
    if not AXIsProcessTrusted():
        missing.append("Accessibility")
    if hasattr(Quartz, "CGPreflightListenEventAccess") and not Quartz.CGPreflightListenEventAccess():
        missing.append("Input Monitoring")
    if hasattr(Quartz, "CGPreflightScreenCaptureAccess") and not Quartz.CGPreflightScreenCaptureAccess():
        missing.append("Screen Recording")
    return missing


def screen_capture_blocked():
    """True when macOS hides other apps from screenshots, so a snip would only show the wallpaper.
    macOS only rechecks this when MacroEZ starts, so after allowing it MacroEZ has to be reopened."""
    try:
        if hasattr(Quartz, "CGPreflightScreenCaptureAccess"):
            return not Quartz.CGPreflightScreenCaptureAccess()
    except Exception:
        pass
    return False


_screen_asked = [False]


def screen_permission_help(app):
    """A small window that sends the user to Screen Recording, with a button to quit after allowing it."""
    if not _screen_asked[0] and hasattr(Quartz, "CGRequestScreenCaptureAccess"):
        _screen_asked[0] = True
        try:
            Quartz.CGRequestScreenCaptureAccess()  # puts MacroEZ in the Screen Recording list
        except Exception:
            pass
    old = getattr(app, "screen_help_window", None)
    if old is not None and old.winfo_exists():
        old.deiconify()
        old.lift()
        return
    tk, ttk = g["tk"], g["ttk"]
    top = tk.Toplevel(app.root)
    top.title(g["APP_NAME"])
    top.configure(bg=g["BG"])
    body = ttk.Frame(top, style="Card.TFrame", padding=22)
    body.pack(fill="both", expand=True, padx=14, pady=14)
    ttk.Label(body, text="Let MacroEZ see your screen", style="Section.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(body, text="Right now macOS only shows MacroEZ your wallpaper, not your apps, so snips and "
                         "picture searches can't work.\n\n"
                         "1. Click Open Screen Recording settings.\n"
                         "2. Turn MacroEZ on. If it's already on, turn it off and on again.\n"
                         "3. Quit MacroEZ and open it again. macOS only applies this after a restart.",
              style="Muted.TLabel", wraplength=440, justify="left").grid(row=1, column=0, sticky="w", pady=(8, 14))
    ttk.Button(body, text="Open Screen Recording settings",
               command=lambda: g["webbrowser"].open(SETTINGS_PAGES["Screen Recording"])
               ).grid(row=2, column=0, sticky="ew", pady=3)
    ttk.Button(body, text="Quit MacroEZ", command=app.close).grid(row=3, column=0, sticky="ew", pady=3)
    ttk.Button(body, text="Not now", style="Accent.TButton", command=top.destroy).grid(
        row=4, column=0, sticky="e", pady=(14, 0))
    top.transient(app.root)
    app.screen_help_window = top


def ask_permissions():
    """Let macOS show its own prompts, which also adds MacroEZ to each list in System Settings."""
    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    if hasattr(Quartz, "CGRequestListenEventAccess"):
        Quartz.CGRequestListenEventAccess()
    if hasattr(Quartz, "CGRequestScreenCaptureAccess"):
        Quartz.CGRequestScreenCaptureAccess()


def permissions_window(app, missing):
    tk, ttk = g["tk"], g["ttk"]
    top = tk.Toplevel(app.root)
    top.title(g["APP_NAME"])
    top.configure(bg=g["BG"])
    body = ttk.Frame(top, style="Card.TFrame", padding=22)
    body.pack(fill="both", expand=True, padx=14, pady=14)
    ttk.Label(body, text="MacroEZ needs your OK", style="Section.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(body, text="macOS keeps apps from clicking, pressing keys or seeing the screen until you allow it. "
                         "Turn MacroEZ on in each of these, then quit and reopen MacroEZ.", style="Muted.TLabel",
              wraplength=420, justify="left").grid(row=1, column=0, sticky="w", pady=(8, 14))
    for row, name in enumerate(missing, start=2):
        ttk.Button(body, text=f"Open {name} settings", command=lambda n=name: g["webbrowser"].open(SETTINGS_PAGES[n])
                   ).grid(row=row, column=0, sticky="ew", pady=3)
    ttk.Button(body, text="Done", style="Accent.TButton", command=top.destroy).grid(
        row=len(missing) + 2, column=0, sticky="e", pady=(14, 0))
    top.transient(app.root)
    app.permissions_window = top


def attach(app):
    """Mac-only touches once the window exists: links from the website, the app menu, and permissions."""
    root = app.root

    def open_links(*urls):
        for url in urls:
            preset_id = g["preset_link_id"](url)
            if preset_id:
                root.deiconify()
                root.lift()
                app.download_preset(preset_id)

    root.createcommand("::tk::mac::LaunchURL", open_links)
    root.createcommand("::tk::mac::ReopenApplication", lambda *a: (root.deiconify(), root.lift()))
    root.createcommand("::tk::mac::Quit", app.close)
    missing = missing_permissions()
    if missing:
        ask_permissions()
        root.after(900, lambda: permissions_window(app, missing))


MAC_ICONS = {"image": "▣", "classic": "⌨", "hybrid": "⇄", "reaction": "ϟ", "autoclick": "◉", "presets": "☰",
             "profiles": "☷", "settings": "⚙"}
MAC_LABELS = {"Win": "⌘", "Alt": "⌥", "Ctrl": "⌃", "Menu": "Menu", "PrtSc": "F13", "ScrLk": "F14", "Pause": "F15",
              "Ins": "Help", "Backspace": "Delete", "Del": "Del ⌦", "Num": "Clear"}
MAC_NAMES = {"Left Windows": "Left Command", "Right Windows": "Right Command", "Left Alt": "Left Option",
             "Right Alt": "Right Option", "Left Ctrl": "Left Control", "Right Ctrl": "Right Control",
             "Print Screen": "F13", "Scroll Lock": "F14", "Pause": "F15", "Insert": "Help"}


def install(namespace):
    """Swap the Mac versions into macroez's globals."""
    g.update(namespace)
    _build_tables()
    for name in ("send_scan", "key_is_down", "key_name", "cursor_position", "set_cursor", "nudge_mouse", "move_to",
                 "mouse_button", "scroll_wheel", "foreground_exe", "foreground_is_ours", "point_is_ours",
                 "open_apps", "Recorder", "fine_timer", "register_link_handler", "screen_capture_blocked",
                 "screen_permission_help"):
        namespace[name] = globals()[name]
    namespace["OWN_PID"] = OWN_PID
    info = namespace["MODE_INFO"]
    for mode, icon in MAC_ICONS.items():
        if mode in info:
            info[mode] = (info[mode][0], icon, info[mode][2])
    keyboard = namespace["KEYBOARD"]
    for index, (x, y, w, h, label, press, hot) in enumerate(keyboard):
        keyboard[index] = (x, y, w, h, MAC_LABELS.get(label, label), press, hot)
    labels = namespace["PRESS_LABEL"]
    for press, label in list(labels.items()):
        labels[press] = MAC_NAMES.get(label, label)
    old_key_label = namespace["key_label"]

    def key_label(name, kind="press"):
        shown = old_key_label(name, kind)
        return MAC_NAMES.get(shown, shown) if kind == "hotkey" else shown
    namespace["key_label"] = key_label
