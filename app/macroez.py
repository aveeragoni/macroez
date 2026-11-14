"""MacroEZ: image, classic, hybrid, reaction and autoclicker macros for Windows and Mac."""
APP_NAME = "MacroEZ"
import copy
import ctypes
import io
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
import collections
from collections import namedtuple
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    # The Windows parts below are set up but never called on a Mac: macroez_mac.py replaces each of them.
    class _NotWindows:
        def __getattr__(self, name):
            return _NotWindowsCall()

    class _NotWindowsCall:
        argtypes = restype = None

        def __call__(self, *args, **kwargs):
            return 0

    ctypes.WinDLL = lambda *a, **k: _NotWindows()
    ctypes.windll = _NotWindows()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    ctypes.get_last_error = lambda: 0
    winreg = None
else:
    import winreg
try:
    from ctypes import wintypes
except (ImportError, ValueError):  # some Python builds can't load the Windows types off Windows
    import types as _types

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MSG(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                    ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("pt", _POINT)]

    wintypes = _types.SimpleNamespace(
        LONG=ctypes.c_long, DWORD=ctypes.c_ulong, WORD=ctypes.c_ushort, UINT=ctypes.c_uint, BOOL=ctypes.c_int,
        HWND=ctypes.c_void_p, HANDLE=ctypes.c_void_p, HINSTANCE=ctypes.c_void_p, HMODULE=ctypes.c_void_p,
        LPARAM=ctypes.c_ssize_t, WPARAM=ctypes.c_size_t, LPCWSTR=ctypes.c_wchar_p, LPWSTR=ctypes.c_wchar_p,
        POINT=_POINT, MSG=_MSG)

if not IS_MAC:
    # Must happen before any window or capture so all coordinates are real pixels.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        ctypes.windll.user32.SetProcessDPIAware()

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import cv2
import mss
import numpy as np
from PIL import Image, ImageDraw, ImageGrab, ImageTk

# A packaged .exe unpacks to a temp folder, so keep user data beside the .exe instead.
APP_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
if IS_MAC and getattr(sys, "frozen", False):  # a Mac app can't write inside itself: use the usual place
    APP_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
    APP_DIR.mkdir(parents=True, exist_ok=True)
IMAGES = APP_DIR / "images"
CONFIG = APP_DIR / "config.json"
CRASH_LOG = APP_DIR / "crash.log"
# Files packed inside the .exe (the logo) unpack to a temp folder; when run as a script they sit beside it.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
LOGO = BUNDLE_DIR / "macroez_logo.png"

ROBLOX_EXE = "RobloxPlayerBeta.exe"

# ---------------------------------------------------------------- Win32 input
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSE_MOVE, MOUSE_LEFT_DOWN, MOUSE_LEFT_UP = 0x0001, 0x0002, 0x0004
MOUSE_BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040),
                 "back": (0x0080, 0x0100), "forward": (0x0080, 0x0100)}  # back and forward are the side buttons
MOUSE_XDATA = {"back": 1, "forward": 2}  # which side button
MOUSE_TITLE = {"left": "Left", "right": "Right", "middle": "Middle", "back": "Mouse 4", "forward": "Mouse 5"}
MOUSE_LABEL = {"left": "Left", "right": "Right", "middle": "Middle (wheel)", "back": "Mouse 4 (back)",
               "forward": "Mouse 5 (forward)"}
MOUSE_VK = {"left": 0x01, "right": 0x02, "middle": 0x04, "back": 0x05, "forward": 0x06}
KEY_SCANCODE, KEY_UP, KEY_EXTENDED = 0x0008, 0x0002, 0x0001
MOUSE_WHEEL, MOUSE_HWHEEL, WHEEL_NOTCH = 0x0800, 0x1000, 120
SCROLL_DIRECTIONS = {"up": (MOUSE_WHEEL, 1), "down": (MOUSE_WHEEL, -1),
                     "left": (MOUSE_HWHEEL, -1), "right": (MOUSE_HWHEEL, 1)}  # direction -> wheel, sign

SCAN_CODES = {"space": 0x39, "enter": 0x1C, "tab": 0x0F, "shift": 0x2A, "ctrl": 0x1D, "alt": 0x38, "esc": 0x01,
              "backspace": 0x0E, "capslock": 0x3A}
for row, start in (("1234567890-=", 0x02), ("qwertyuiop[]", 0x10), ("asdfghjkl;'`", 0x1E), ("zxcvbnm,./", 0x2C)):
    for offset, char in enumerate(row):
        SCAN_CODES[char] = start + offset
SCAN_CODES["\\"] = 0x2B
for number in range(1, 11):
    SCAN_CODES[f"f{number}"] = 0x3A + number
SCAN_CODES.update({"f11": 0x57, "f12": 0x58})
EXTENDED_KEYS = {"up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D, "insert": 0x52, "delete": 0x53,
                 "home": 0x47, "end": 0x4F, "pageup": 0x49, "pagedown": 0x51}
SCAN_CODES.update(EXTENDED_KEYS)
SCAN_CODES.update({"right shift": 0x36, "numlock": 0x45, "scrolllock": 0x46, "num*": 0x37, "num-": 0x4A,
                   "num+": 0x4E, "num.": 0x53})
SCAN_CODES.update({f"num{n}": code for n, code in enumerate((0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48,
                                                              0x49))})
_MORE_EXTENDED = {"right ctrl": 0x1D, "right alt": 0x38, "left win": 0x5B, "right win": 0x5C, "menu": 0x5D,
                  "printscreen": 0x37, "num/": 0x35, "numenter": 0x1C}
EXTENDED_KEYS.update(_MORE_EXTENDED)
SCAN_CODES.update(_MORE_EXTENDED)
KEY_NAMES = list(SCAN_CODES)

# Keys that can start/stop the macro and so on. Read straight from the keyboard state, which works even when
# a game or another app has grabbed the key, and also allows mouse side buttons.
HOTKEY_KEYS = {f"F{n}": 0x6F + n for n in range(1, 13)}
HOTKEY_KEYS.update({"Insert": 0x2D, "Home": 0x24, "End": 0x23, "Page Up": 0x21, "Page Down": 0x22,
                    "Delete": 0x2E, "Pause": 0x13, "Scroll Lock": 0x91, "` (backtick)": 0xC0,
                    "Mouse side button 1": 0x05, "Mouse side button 2": 0x06, "Middle mouse": 0x04})
HOTKEY_KEYS.update({f"Numpad {n}": 0x60 + n for n in range(10)})
HOTKEY_KEYS.update({"Esc": 0x1B, "Print Screen": 0x2C, "Tab": 0x09, "Caps Lock": 0x14, "Backspace": 0x08,
                    "Enter": 0x0D, "Space": 0x20, "Left Shift": 0xA0, "Right Shift": 0xA1, "Left Ctrl": 0xA2,
                    "Right Ctrl": 0xA3, "Left Alt": 0xA4, "Right Alt": 0xA5, "Left Windows": 0x5B,
                    "Right Windows": 0x5C, "Menu": 0x5D, "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27,
                    "Num Lock": 0x90, "Numpad /": 0x6F, "Numpad *": 0x6A, "Numpad -": 0x6D, "Numpad +": 0x6B,
                    "Numpad .": 0x6E, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA, "'": 0xDE,
                    ",": 0xBC, ".": 0xBE, "/": 0xBF})
HOTKEY_KEYS.update({c: ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})
HOTKEY_KEYS["None"] = 0
MOUSE_HOTKEYS = ("Middle mouse", "Mouse side button 1", "Mouse side button 2")
# keys that type text, so they never fire a hotkey while you type in one of MacroEZ's own boxes
TYPING_VKS = {0x08, 0x0D, 0x20, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E, *range(0x30, 0x3A), *range(0x41, 0x5B),
              *range(0x60, 0x70), *range(0xBA, 0xC1), *range(0xDB, 0xE0)}


def _keyboard():
    """A full-size (100%) US keyboard: x, y, width, height in key units, label, press name, hotkey name."""
    keys = []

    def add(x, y, label, press, hot, w=1.0, h=1.0):
        keys.append((x, y, w, h, label, press, hot))

    add(0, 0, "Esc", "esc", "Esc")
    for n in range(1, 13):
        add(n + 1 + (n - 1) // 4 * 0.5, 0, f"F{n}", f"f{n}", f"F{n}")
    add(15.25, 0, "PrtSc", "printscreen", "Print Screen")
    add(16.25, 0, "ScrLk", "scrolllock", "Scroll Lock")
    add(17.25, 0, "Pause", None, "Pause")
    y = 1.5
    for i, ch in enumerate("`1234567890-="):
        add(i, y, ch, ch, "` (backtick)" if ch == "`" else ch)
    add(13, y, "Backspace", "backspace", "Backspace", w=2)
    add(15.25, y, "Ins", "insert", "Insert")
    add(16.25, y, "Home", "home", "Home")
    add(17.25, y, "PgUp", "pageup", "Page Up")
    for i, (label, press, hot) in enumerate((("Num", "numlock", "Num Lock"), ("/", "num/", "Numpad /"),
                                             ("*", "num*", "Numpad *"), ("-", "num-", "Numpad -"))):
        add(18.5 + i, y, label, press, hot)
    y = 2.5
    add(0, y, "Tab", "tab", "Tab", w=1.5)
    for i, ch in enumerate("qwertyuiop[]"):
        add(1.5 + i, y, ch.upper(), ch, ch.upper())
    add(13.5, y, "\\", "\\", "\\", w=1.5)
    add(15.25, y, "Del", "delete", "Delete")
    add(16.25, y, "End", "end", "End")
    add(17.25, y, "PgDn", "pagedown", "Page Down")
    for i, n in enumerate((7, 8, 9)):
        add(18.5 + i, y, str(n), f"num{n}", f"Numpad {n}")
    add(21.5, y, "+", "num+", "Numpad +", h=2)
    y = 3.5
    add(0, y, "Caps", "capslock", "Caps Lock", w=1.75)
    for i, ch in enumerate("asdfghjkl;'"):
        add(1.75 + i, y, ch.upper(), ch, ch.upper())
    add(12.75, y, "Enter", "enter", "Enter", w=2.25)
    for i, n in enumerate((4, 5, 6)):
        add(18.5 + i, y, str(n), f"num{n}", f"Numpad {n}")
    y = 4.5
    add(0, y, "Shift", "shift", "Left Shift", w=2.25)
    for i, ch in enumerate("zxcvbnm,./"):
        add(2.25 + i, y, ch.upper(), ch, ch.upper())
    add(12.25, y, "Shift", "right shift", "Right Shift", w=2.75)
    add(16.25, y, "\u2191", "up", "Up")
    for i, n in enumerate((1, 2, 3)):
        add(18.5 + i, y, str(n), f"num{n}", f"Numpad {n}")
    add(21.5, y, "Enter", "numenter", "Enter", h=2)
    y, x = 5.5, 0.0
    for label, press, hot, w in (("Ctrl", "ctrl", "Left Ctrl", 1.25), ("Win", "left win", "Left Windows", 1.25),
                                 ("Alt", "alt", "Left Alt", 1.25), ("Space", "space", "Space", 6.25),
                                 ("Alt", "right alt", "Right Alt", 1.25), ("Win", "right win", "Right Windows", 1.25),
                                 ("Menu", "menu", "Menu", 1.25), ("Ctrl", "right ctrl", "Right Ctrl", 1.25)):
        add(x, y, label, press, hot, w=w)
        x += w
    add(15.25, y, "\u2190", "left", "Left")
    add(16.25, y, "\u2193", "down", "Down")
    add(17.25, y, "\u2192", "right", "Right")
    add(18.5, y, "0", "num0", "Numpad 0", w=2)
    add(20.5, y, ".", "num.", "Numpad .")
    return keys


KEYBOARD = _keyboard()
KEYBOARD_SIZE = (22.5, 6.5)  # key units
PRESS_LABEL = {press: hot for *_rest, press, hot in KEYBOARD if press}  # "num7" -> "Numpad 7"
PRESS_LABEL.update({"numenter": "Numpad Enter", "`": "` (backtick)"})


def key_label(name, kind="press"):
    """How a key's saved name is shown: "num7" -> "Numpad 7". Hotkey names are already readable."""
    if kind == "mouse":
        return MOUSE_LABEL.get(name, name)
    return PRESS_LABEL.get(name, name) if kind == "press" else {"Mouse side button 1": "Mouse 4",
                                                                 "Mouse side button 2": "Mouse 5"}.get(name, name)


# The drawn mouse: shapes in units where the body is 160 x 312, side buttons on its left.
MOUSE_SHAPES = {
    "left": ("poly", [(4, 112), (4, 112), (4, 48), (22, 10), (77, 4), (77, 4), (77, 112), (77, 112)]),
    "right": ("poly", [(156, 112), (156, 112), (156, 48), (138, 10), (83, 4), (83, 4), (83, 112), (83, 112)]),
    "middle": ("rect", (71, 28, 89, 82)),
    "forward": ("rect", (-18, 128, -3, 170)),
    "back": ("rect", (-18, 178, -3, 220)),
}
MOUSE_TEXT = {"left": (40, 72, "Left"), "right": (120, 72, "Right"), "middle": (80, -14, "Middle"),
              "forward": (-26, 149, "Mouse 5\nforward"), "back": (-26, 199, "Mouse 4\nback")}
MOUSE_BODY = [(0, 96), (0, 46), (20, 6), (80, 0), (140, 6), (160, 46), (160, 96), (160, 236), (140, 296), (80, 312),
              (20, 296), (0, 236)]
MOUSE_HOTKEY_NAMES = {"middle": "Middle mouse", "back": "Mouse side button 1", "forward": "Mouse side button 2"}
DEFAULT_HOTKEYS = {"start": "F6", "snip": "F7", "test": "F8", "zone": "F9", "record": "F7"}
SHARED_HOTKEYS = {"snip", "record"}  # these may share a key: Record works in Classic and Hybrid, Snip elsewhere
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
_sent_at = {}  # virtual key -> when this app last pressed it, so its own presses never trigger a hotkey


def _send(item):
    return user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))


def send_mouse(flags, dx=0, dy=0, data=0):
    return _send(INPUT(INPUT_MOUSE, _INPUTUNION(mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0))))


def send_scan(code, extended=False, up=False):
    """Press or release a key by its scan code (the physical key), which games read reliably."""
    flags = KEY_SCANCODE | (KEY_UP if up else 0) | (KEY_EXTENDED if extended else 0)
    _sent_at[user32.MapVirtualKeyW(code | (0xE000 if extended else 0), 3)] = time.monotonic()
    return _send(INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, flags, 0, 0))))


def send_key(name, up=False):
    return send_scan(SCAN_CODES[name], name in EXTENDED_KEYS, up)


def tap_key(name, hold=0.03, flag=None):
    send_key(name)
    try:
        if flag is None:
            time.sleep(min(max(hold, 0.01), 60.0))
        else:
            wait_exact(flag, max(hold, 0.01))  # a long hold ends the moment the macro is stopped
    finally:
        send_key(name, up=True)  # always let go, even if something went wrong


try:
    _winmm = ctypes.WinDLL("winmm")
except OSError:
    _winmm = None


def fine_timer(on):
    """Windows normally wakes sleeping threads only every ~15 ms; ask for 1 ms while a macro plays."""
    if _winmm:
        (_winmm.timeBeginPeriod if on else _winmm.timeEndPeriod)(1)


def wait_exact(flag, seconds):
    """Wait this long to within about a millisecond, or until stopped. True if stopped."""
    end = time.perf_counter() + seconds
    while not flag.is_set():
        left = end - time.perf_counter()
        if left <= 0:
            return False
        if left > 0.04:
            # A plain wait can wake up to ~15 ms late when Windows ignores the 1 ms timer request (Windows 11
            # does that for apps in the background), so it stops well short of the moment.
            flag.wait(min(left - 0.025, 3600.0))  # waited in pieces: a huge wait would overflow the timer
        elif left > 0.0008:
            time.sleep(min(left - 0.0005, 0.01))  # Python's sleep uses a precise timer of its own
        else:
            time.sleep(0)  # the last moment is spun out for accuracy
    return True


def cursor_position():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def set_cursor(x, y):
    user32.SetCursorPos(x, y)


def nudge_mouse():
    """A one-pixel wiggle, so the app under the cursor notices it arrived."""
    send_mouse(MOUSE_MOVE, 1, 0)
    send_mouse(MOUSE_MOVE, -1, 0)


def key_is_down(vk):
    """Whether a key or mouse button (by Windows virtual-key number) is held right now."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def foreground_is_ours():
    return _window_pid(user32.GetForegroundWindow()) == OWN_PID


def screen_capture_blocked():
    """Whether the system only lets MacroEZ see the wallpaper instead of other apps (a Mac without Screen Recording)."""
    return False


def screen_permission_help(app):
    """Explain how to let MacroEZ see the screen (only needed on a Mac)."""


def point_is_ours(x, y):
    """Whether one of MacroEZ's own windows is under this point on the screen."""
    return _window_pid(user32.WindowFromPoint(wintypes.POINT(x, y))) == OWN_PID


def move_to(x, y):
    """Put the cursor there and wiggle it a pixel, so games notice the hover."""
    user32.SetCursorPos(x, y)
    time.sleep(0.03)
    send_mouse(MOUSE_MOVE, 1, 0)
    time.sleep(0.02)
    send_mouse(MOUSE_MOVE, -1, 0)
    time.sleep(0.03)


def mouse_button(button, up=False):
    down_flag, up_flag = MOUSE_BUTTONS.get(button, MOUSE_BUTTONS["left"])
    send_mouse(up_flag if up else down_flag, data=MOUSE_XDATA.get(button, 0))


def click_at(x, y, button="left", hold=0.04, flag=None):
    """Move, wiggle so Roblox registers the hover, then click. A stopped macro doesn't click at all, and a long
    hold lets go the moment it's stopped."""
    if flag is not None and flag.is_set():
        return
    move_to(x, y)
    if flag is not None and flag.is_set():
        return
    mouse_button(button)
    try:
        if flag is None:
            time.sleep(min(max(hold, 0.01), 60.0))
        else:
            wait_exact(flag, max(hold, 0.01))  # hold as long as you like: stopping lets go straight away
    finally:
        mouse_button(button, up=True)  # always let go, even if something went wrong


def scroll_wheel(direction, amount):
    """Turn the wheel at the cursor. 120 is one notch."""
    wheel, sign = SCROLL_DIRECTIONS.get(direction, SCROLL_DIRECTIONS["down"])
    send_mouse(wheel, data=sign * amount)


# ---------------------------------------------------------------- Recording
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


KEY_MESSAGES = {0x100: False, 0x104: False, 0x101: True, 0x105: True}  # message -> is it a key release
MOUSE_MESSAGES = {0x201: ("left", False), 0x202: ("left", True), 0x204: ("right", False), 0x205: ("right", True),
                  0x207: ("middle", False), 0x208: ("middle", True)}
WHEEL_MESSAGES = {0x20A: ("up", "down"), 0x20E: ("right", "left")}  # message -> direction when positive, negative
NAME_FOR_SCAN = {(code, name in EXTENDED_KEYS): name for name, code in SCAN_CODES.items()}
OWN_PID = os.getpid()


def key_name(scan, extended):
    """A short readable name for a physical key, e.g. "w", "space", "Right Shift"."""
    if (scan, extended) in NAME_FOR_SCAN:
        return NAME_FOR_SCAN[(scan, extended)]
    buffer = ctypes.create_unicode_buffer(32)
    if user32.GetKeyNameTextW((scan << 16) | (1 << 24 if extended else 0), buffer, 32):
        return buffer.value
    return f"key {scan}"


def _window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


class Recorder:
    """Watches every real key press, mouse click and scroll (not ones this app sends) until stopped.

    events: (seconds, "key"/"mouse"/"scroll", up?, details) where details is (scan, extended), (button, x, y)
    or (direction, amount, x, y).
    """

    def __init__(self, skip_vks=(), allow_injected=False):
        self.skip = set(skip_vks)  # the hotkeys themselves are never recorded
        self.allow_injected = allow_injected  # tests only
        self.events = []
        self.down = set()
        self.thread_id = None
        self.ready = threading.Event()
        self.error = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(2)
        if self.error:
            raise OSError(self.error)

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT ends the hook thread
        return list(self.events)

    def _ours(self, hwnd):
        return bool(hwnd) and _window_pid(hwnd) == OWN_PID

    def _on_key(self, code, message, data):
        try:
            if code == 0 and message in KEY_MESSAGES:
                info = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                injected = info.flags & 0x10
                up = KEY_MESSAGES[message]
                physical = (info.scanCode, bool(info.flags & 0x01))
                if info.vkCode in self.skip or (injected and not self.allow_injected):
                    pass
                elif up or physical not in self.down:  # holding a key repeats key-down; keep only the first
                    if up and physical not in self.down:
                        pass  # released a key that was already down when recording started
                    elif not self._ours(user32.GetForegroundWindow()):
                        (self.down.discard if up else self.down.add)(physical)
                        self.events.append((time.perf_counter(), "key", up, physical))
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, message, data)

    def _on_mouse(self, code, message, data):
        try:
            if code == 0 and (message in MOUSE_MESSAGES or message in (0x20B, 0x20C)):
                info = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                injected = info.flags & 0x01
                if message in MOUSE_MESSAGES:
                    button, up = MOUSE_MESSAGES[message]
                else:  # a side button: which one is in the high word
                    button, up = "back" if (info.mouseData >> 16) == 1 else "forward", message == 0x20C
                if MOUSE_VK[button] in self.skip:
                    pass
                elif (not injected or self.allow_injected) and not self._ours(user32.WindowFromPoint(info.pt)):
                    if not up or button in self.down:
                        (self.down.discard if up else self.down.add)(button)
                        self.events.append((time.perf_counter(), "mouse", up, (button, info.pt.x, info.pt.y)))
            elif code == 0 and message in WHEEL_MESSAGES:
                info = ctypes.cast(data, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short(info.mouseData >> 16).value
                if delta and (not info.flags & 0x01 or self.allow_injected) and not self._ours(
                        user32.WindowFromPoint(info.pt)):
                    direction = WHEEL_MESSAGES[message][delta < 0]
                    self.events.append((time.perf_counter(), "scroll", False,
                                        (direction, abs(delta), info.pt.x, info.pt.y)))
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, message, data)

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        keyboard_proc, mouse_proc = HOOKPROC(self._on_key), HOOKPROC(self._on_mouse)  # must stay alive
        module = kernel32.GetModuleHandleW(None)
        keyboard = user32.SetWindowsHookExW(13, ctypes.cast(keyboard_proc, ctypes.c_void_p), module, 0)
        mouse = user32.SetWindowsHookExW(14, ctypes.cast(mouse_proc, ctypes.c_void_p), module, 0)
        if not keyboard or not mouse:
            self.error = f"Windows refused to let MacroEZ watch the keyboard and mouse (error {ctypes.get_last_error()})."
        self.ready.set()
        message = wintypes.MSG()
        while not self.error and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            pass
        for hook in (keyboard, mouse):
            if hook:
                user32.UnhookWindowsHookEx(hook)


MOVE_LEAD = 0.08  # how long move_to takes before a button goes down
DRAG_TOLERANCE = 3  # pixels a button may move while held and still count as a plain click
SCROLL_BURST = 0.25  # wheel turns closer together than this, in one direction, become one scroll step


def recording_to_steps(events, left=0, top=0):
    """Turn recorded events into steps that replay them exactly, including the time between them.

    A press and its release with nothing in between become one step with that hold time. Anything that
    overlaps (holding W while pressing Space, dragging the mouse) is kept as separate down and up steps.
    A run of wheel turns becomes one scroll step, spread over the time it took.
    """
    steps, previous_end = [], None
    index = 0
    while index < len(events):
        moment, device, up, details = events[index]
        if device == "scroll":
            direction, amount, x, y = details
            last = index
            while last + 1 < len(events):
                when, kind, _up, more = events[last + 1]
                if (kind != "scroll" or more[0] != direction or when - events[last][0] > SCROLL_BURST
                        or abs(more[2] - x) > DRAG_TOLERANCE or abs(more[3] - y) > DRAG_TOLERANCE):
                    break
                amount += more[1]
                last += 1
            if previous_end is not None:
                gap = round((moment - previous_end) * 1000)
                if gap > 0:
                    steps.append(Action("wait", ms=gap, ms_max=gap))
            steps.append(Action("scroll", direction=direction, notches=round(amount / WHEEL_NOTCH, 2), x=x - left,
                                y=y - top, scroll_at=SCROLL_AT_SPOT,
                                spread_ms=round((events[last][0] - moment) * 1000)))
            previous_end = events[last][0]
            index = last + 1
            continue
        following = events[index + 1] if index + 1 < len(events) else None
        merged = (following is not None and not up and following[2] and following[1] == device
                  and (following[3] == details if device == "key" else
                       following[3][0] == details[0] and abs(following[3][1] - details[1]) <= DRAG_TOLERANCE
                       and abs(following[3][2] - details[2]) <= DRAG_TOLERANCE))
        if previous_end is not None:
            gap = round((moment - previous_end) * 1000)
            if gap > 0:
                steps.append(Action("wait", ms=gap, ms_max=gap))
        hold = round((following[0] - moment) * 1000) if merged else 0
        if device == "key":
            scan, extended = details
            kind = "key" if merged else ("key_up" if up else "key_down")
            steps.append(Action(kind, key=key_name(scan, extended), scan=scan, extended=extended,
                                hold_ms=max(hold, 1) if merged else 30))
        else:
            button, x, y = details
            kind = "click" if merged else ("mouse_up" if up else "mouse_down")
            steps.append(Action(kind, button=button, x=x - left, y=y - top, hold_ms=max(hold, 1) if merged else 30))
        previous_end = following[0] if merged else moment
        index += 2 if merged else 1
    return steps


def process_exe(pid):
    """The .exe file name of a running program, e.g. "RobloxPlayerBeta.exe", or "" if it can't be read."""
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def foreground_exe():
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return process_exe(pid.value)


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
try:
    _dwmapi = ctypes.WinDLL("dwmapi")
except OSError:
    _dwmapi = None
SKIP_APPS = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
             "shellexperiencehost.exe", "searchhost.exe", "startmenuexperiencehost.exe", "lockapp.exe",
             "macroez.exe"}


def open_apps():
    """Programs with a window on screen right now: {exe name: one of its window titles}."""
    found = {}

    def each(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            if user32.GetWindowLongW(hwnd, -20) & 0x80 or user32.GetWindow(hwnd, 4):  # tool window, or owned
                return True
            if _dwmapi:  # hidden "cloaked" windows, like apps suspended in the background
                cloaked = wintypes.DWORD()
                _dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
                if cloaked.value:
                    return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return True
            exe = process_exe(pid.value)
            if exe and exe.lower() not in SKIP_APPS:
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                found.setdefault(exe, title.value)
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(each), 0)
    return dict(sorted(found.items(), key=lambda item: item[0].lower()))


# -------------------------------------------------------------- Image matching
Hit = namedtuple("Hit", "score x y w h scale backup", defaults=(False,))
SCALES = (1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15, 0.8, 1.2, 0.75, 1.25, 0.7, 1.3)
SWEEP_SECONDS = 4.0
SWEEP_GAP = 2.0  # the slow size search runs at most this often across all images, so scans stay quick
NORMAL_EVERY = 0.5  # with Priority images set, the rest are checked this often (seconds)
REARM_SECONDS = 10.0  # after a finished reconnect, ignore step 1 for this long
BACKUP_SECONDS = 1.0  # how often each image may use the slower backup detection
BACKUP_MIN_POINTS = 8  # fewest lined-up points that count as a find
BACKUP_MIN_SHARE = 0.30  # lets a real button through but not a lookalike with the same frame
BACKUP_PER_PASS = 2  # slow detections allowed per pass when it isn't set to run on every scan
STRONG_MATCH = 0.70  # below this, a hit only counts if nothing stronger is on screen
STILL_THERE = 0.60  # in real menus a row that stayed scored 0.78+, one that left scored 0.40 or less
MERGE_PAD = 60  # how far snips may sit from each other when lining them up
MERGE_MIN_ALIGN = 0.55  # how well a snip's edges must line up with the biggest snip
MERGE_MAX_SPREAD = 40  # a pixel counts as part of the button if it varies less than this across snips
MERGE_MAX_KEPT = 0.60  # keeping more than this means the snips share a background
MERGE_MIN_PIECE = 40  # specks smaller than this are coincidence
MERGE_MIN_PIXELS = 1500  # fewer than this isn't enough to recognise a button


def read_image(path):
    data = np.fromfile(str(path), dtype=np.uint8)  # handles any characters in the path
    image = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if image is None:
        raise ValueError(f"could not read image: {path}")
    return image


def read_mask(path):
    """Which pixels of a PNG to compare, or None if the whole image counts."""
    raw = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.ndim != 3 or raw.shape[2] != 4 or raw.dtype != np.uint8 or raw[..., 3].min() >= 128:
        return None
    return ((raw[..., 3] >= 128) * 255).astype(np.uint8)


def score_map(screen, template, scale, mask=None):
    """Match score at every position, plus the size the template was resized to."""
    if scale == 1.0:
        sized, sized_mask = template, mask
    else:
        method = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        sized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=method)
        sized_mask = None if mask is None else cv2.resize(mask, (sized.shape[1], sized.shape[0]),
                                                          interpolation=cv2.INTER_NEAREST)
    h, w = sized.shape[:2]
    if h < 4 or w < 4 or h > screen.shape[0] or w > screen.shape[1]:
        return None, w, h
    result = cv2.matchTemplate(screen, sized, cv2.TM_CCOEFF_NORMED, mask=sized_mask)
    return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0), w, h  # flat-colour snips


def match_at_scale(screen, template, scale, mask=None):
    result, w, h = score_map(screen, template, scale, mask)
    if result is None:
        return None
    _, score, _, loc = cv2.minMaxLoc(result)
    return Hit(float(score), loc[0], loc[1], w, h, scale)


_half = (None, None)


def half_size(screen, grey=False):
    """The screen at half size (in colour and grey), made once per frame and shared by every image."""
    global _half
    cached = _half
    if cached[0] is not screen:
        small = cv2.resize(screen, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        cached = (screen, (small, cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)))
        _half = cached
    return cached[1][1 if grey else 0]


ROUGH_MIN_SIZE = 24  # smaller images lose too much detail at half size, so they get the full search
ROUGH_SPOTS = 4  # best spots from the half-size search that get checked at full size


class Matcher:
    """Remembers the size an image was last found at, so most scans stay cheap."""

    def __init__(self, template, mask=None):
        self.template = template
        self.grey = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        self.mask = mask
        self.scale = None
        self.next_sweep = 0.0
        self.points = None
        self.next_backup = 0.0
        self.last_probe = (None, None)  # (frame, hit) so one frame is never searched twice
        self.last_detect = None  # frame the backup detection last ran on, with "every scan" on

    def probe(self, screen):
        return self._at_scale(screen, self.scale or 1.0)

    def _at_scale(self, screen, scale):
        """Search a half-size screen, then score the best few spots at full size.

        A full-size search of a 1080p screen takes about 125 ms per image; this takes a fraction of that.
        """
        height, width = self.template.shape[:2]
        if min(height, width) * scale < ROUGH_MIN_SIZE:
            return match_at_scale(screen, self.template, scale, self.mask)
        # the rough search is in grey (3x less work); the final score below is still in full colour
        rough, _, _ = score_map(half_size(screen, grey=True), self.grey, scale * 0.5, self.mask)
        if rough is None:
            return match_at_scale(screen, self.template, scale, self.mask)
        full_w, full_h = round(width * scale), round(height * scale)
        best = None
        for _ in range(ROUGH_SPOTS):
            _, score, _, (x, y) = cv2.minMaxLoc(rough)
            if score <= 0:
                break
            # blank out this spot so the next round finds a different one
            rough[max(y - full_h // 4, 0):y + full_h // 4 + 1, max(x - full_w // 4, 0):x + full_w // 4 + 1] = 0
            left, top = max(x * 2 - 6, 0), max(y * 2 - 6, 0)
            hit = match_at_scale(screen[top:top + full_h + 12, left:left + full_w + 12], self.template, scale, self.mask)
            if hit and (best is None or hit.score > best.score):
                best = hit._replace(x=hit.x + left, y=hit.y + top)
        return best or Hit(0.0, 0, 0, full_w, full_h, scale)

    def sweep_due(self):
        return self.scale is None and time.monotonic() >= self.next_sweep

    def sweep(self, screen, threshold):
        """Try every size at half resolution, then confirm the best two at full resolution."""
        self.next_sweep = time.monotonic() + SWEEP_SECONDS
        factor = 0.5 if min(self.template.shape[:2]) >= 24 else 1.0
        small, template = (half_size(screen, grey=True), self.grey) if factor < 1 else (screen, self.template)
        coarse = []
        for scale in SCALES:
            hit = match_at_scale(small, template, scale * factor, self.mask)
            if hit:
                coarse.append((hit.score, scale))
        coarse.sort(reverse=True)
        hits = [self._at_scale(screen, scale) for _, scale in coarse[:2]]
        best = max(filter(None, hits), key=lambda h: h.score, default=None)
        if best and best.score >= threshold:
            self.scale = best.scale
        return best

    def find(self, screen, threshold, multiscale=True):
        best = self.probe(screen)
        if multiscale and (not best or best.score < threshold):
            swept = self.sweep(screen, threshold)
            if swept and (not best or swept.score > best.score):
                best = swept
        return best


class BackupDetector:
    """Finds an image by distinctive points, so leftover map background in a snip doesn't matter."""

    def __init__(self):
        self.image_points = cv2.SIFT_create(contrastThreshold=0.02, edgeThreshold=12)
        self.screen_points = cv2.SIFT_create(nfeatures=6000, contrastThreshold=0.02, edgeThreshold=12)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.cached = (None, None, None)

    def _screen_features(self, screen):
        if self.cached[0] is not screen:  # every image in a pass shares one analysis of the frame
            keypoints, descriptors = self.screen_points.detectAndCompute(cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY), None)
            self.cached = (screen, keypoints, descriptors)
        return self.cached[1], self.cached[2]

    def find(self, matcher, screen):
        if matcher.points is None:
            matcher.points = self.image_points.detectAndCompute(cv2.cvtColor(matcher.template, cv2.COLOR_BGR2GRAY),
                                                                matcher.mask)
        points, descriptors = matcher.points
        if descriptors is None or len(points) < BACKUP_MIN_POINTS:
            return None
        screen_points, screen_descriptors = self._screen_features(screen)
        if screen_descriptors is None or len(screen_points) < 2:
            return None
        pairs = [p for p in self.matcher.knnMatch(descriptors, screen_descriptors, k=2) if len(p) == 2]
        good = [a for a, b in pairs if a.distance < 0.8 * b.distance]
        if len(good) < BACKUP_MIN_POINTS:
            return None
        source = np.float32([points[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        target = np.float32([screen_points[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        transform, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=4.0)
        if transform is None:
            return None
        lined_up = int(inliers.sum())
        share = lined_up / len(points)
        scale = math.hypot(transform[0, 0], transform[1, 0])
        rotation = abs(math.degrees(math.atan2(transform[1, 0], transform[0, 0])))
        if lined_up < BACKUP_MIN_POINTS or share < BACKUP_MIN_SHARE or not 0.6 <= scale <= 1.6 or rotation > 6:
            return None
        height, width = matcher.template.shape[:2]
        centre_x, centre_y = transform @ np.array([width / 2, height / 2, 1.0])
        w, h = round(width * scale), round(height * scale)
        return Hit(min(share, 1.0), round(centre_x - w / 2), round(centre_y - h / 2), w, h, scale, True)


def describe(hit):
    return "backup detection" if hit.backup else f"{hit.score:.0%} match"


def edge_map(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))


def learn_outline(images, names):
    """Line up snips of one button and keep the pixels that match in all of them.

    Returns (pixels, mask). Raises ValueError with a message for the user if they can't be merged.
    """
    biggest = max(range(len(images)), key=lambda i: images[i].shape[0] * images[i].shape[1])
    reference = cv2.copyMakeBorder(edge_map(images[biggest]), MERGE_PAD, MERGE_PAD, MERGE_PAD, MERGE_PAD,
                                   cv2.BORDER_CONSTANT, value=0)
    height, width = reference.shape
    places = []
    for index, image in enumerate(images):
        if index == biggest:
            places.append((MERGE_PAD, MERGE_PAD))
            continue
        h, w = image.shape[:2]
        left, right, top, bottom = int(w * 0.1), int(w * 0.9), int(h * 0.15), int(h * 0.85)
        if bottom - top > height or right - left > width:
            raise ValueError(f"{names[index]} doesn't line up with {names[biggest]}.")
        middle = edge_map(image)[top:bottom, left:right]
        result = np.nan_to_num(cv2.matchTemplate(reference, middle, cv2.TM_CCOEFF_NORMED),
                               nan=0.0, posinf=0.0, neginf=0.0)
        # Over the empty padding the score is meaningless, so only spots with real detail may win.
        detail = cv2.boxFilter(reference, -1, middle.shape[::-1], anchor=(0, 0), normalize=True,
                               borderType=cv2.BORDER_CONSTANT)[:result.shape[0], :result.shape[1]]
        result[detail < 0.25 * middle.mean()] = 0.0
        _, score, _, found = cv2.minMaxLoc(result)
        if score < MERGE_MIN_ALIGN:
            raise ValueError(f"{names[index]} doesn't look like the same button as {names[biggest]}.")
        places.append((found[0] - left, found[1] - top))

    total = np.zeros((height, width, 3), np.float32)
    low = np.full((height, width, 3), np.inf, np.float32)
    high = np.full((height, width, 3), -np.inf, np.float32)
    count = np.zeros((height, width), np.int32)
    for image, (x, y) in zip(images, places):
        x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + image.shape[1], width), min(y + image.shape[0], height)
        piece = image[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
        total[y0:y1, x0:x1] += piece
        low[y0:y1, x0:x1] = np.minimum(low[y0:y1, x0:x1], piece)
        high[y0:y1, x0:x1] = np.maximum(high[y0:y1, x0:x1], piece)
        count[y0:y1, x0:x1] += 1
    covered = count >= max(2, math.ceil(len(images) * 0.55))  # half-row snips can't vote on the other half
    same = covered & ((high - low).max(axis=2) < MERGE_MAX_SPREAD)
    if covered.sum() and same.sum() / covered.sum() > MERGE_MAX_KEPT:
        raise ValueError("almost nothing differs between these. Snip the button on different backgrounds first.")

    pieces, labels, stats, _ = cv2.connectedComponentsWithStats(same.astype(np.uint8), connectivity=8)
    keep = np.isin(labels, [i for i in range(1, pieces) if stats[i, cv2.CC_STAT_AREA] >= MERGE_MIN_PIECE])
    if keep.sum() < MERGE_MIN_PIXELS:
        raise ValueError("too little stayed the same. Make sure every snip is of the same button.")
    rows, columns = np.where(keep)
    top, bottom, left, right = rows.min(), rows.max() + 1, columns.min(), columns.max() + 1
    mask = (keep[top:bottom, left:right] * 255).astype(np.uint8)
    average = total[top:bottom, left:right] / np.maximum(count[top:bottom, left:right, None], 1)
    pixels = average.round().astype(np.uint8)
    pixels[mask == 0] = 0
    return pixels, mask


# ----------------------------------------------------------------------- State
# Autoclicker: what a Smart stop image does when it's seen, where clicks go, and how the Start key works
WATCH_STOP, WATCH_PAUSE, WATCH_ONLY = "Stop clicking", "Pause while it's on screen", "Only click while it's on screen"
WATCH_ACTIONS = (WATCH_STOP, WATCH_PAUSE, WATCH_ONLY)
WATCH_SHORT = {WATCH_STOP: "Stop", WATCH_PAUSE: "Pause", WATCH_ONLY: "Only while"}
AC_AT_MOUSE, AC_ROUTE = "Wherever the mouse is", "My route of spots"
AC_TOGGLE, AC_HOLD = "Press on, press off", "Hold to click"


@dataclass
class Target:
    name: str
    file: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    enabled: bool = True
    threshold: float = 0.85
    cooldown: float = 3.0
    delay_after: float = 0.25
    timeout: float = 90.0  # reconnect steps only
    uncropped: str = ""  # the file from before cropping, if any
    priority: bool = False  # checked on every scan, ahead of the rest (farm list only)
    key: str = "space"  # Reaction mode: the key pressed when this image shows up
    hold_ms: int = 30  # Reaction mode: how long that key is held down
    watch: str = WATCH_STOP  # Autoclicker Smart stop: what happens when this image is seen


ACTION_KINDS = {  # kind -> label in the list; image steps only run in Hybrid
    "key": "Press key", "click": "Click", "wait": "Wait", "click_image": "Click image", "wait_image": "Wait for image",
    "key_down": "Hold key down", "key_up": "Let go of key", "mouse_down": "Hold mouse button",
    "mouse_up": "Let go of mouse button", "scroll": "Scroll"}
IMAGE_ACTIONS = ("click_image", "wait_image")
KEY_ACTIONS = ("key", "key_down", "key_up")
MOUSE_ACTIONS = ("click", "mouse_down", "mouse_up")
SCROLL_AT_MOUSE, SCROLL_AT_SPOT = "Wherever the mouse is", "A set spot"


@dataclass
class Action:
    """One step of a Classic or Hybrid macro."""
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    enabled: bool = True
    key: str = "space"
    hold_ms: int = 30
    button: str = "left"
    x: int = 0
    y: int = 0
    ms: int = 500  # wait: shortest
    ms_max: int = 500  # wait: longest (a random time in between)
    name: str = ""  # image steps
    file: str = ""
    threshold: float = 0.85
    timeout: float = 10.0  # image steps: skip after this many seconds
    uncropped: str = ""
    scan: int = 0  # recorded keys: the exact physical key (0 means use the key name)
    extended: bool = False
    direction: str = "down"  # scroll steps
    notches: float = 3.0  # how far: one click of a normal mouse wheel is one notch
    spread_ms: int = 150  # the turns are spaced out over this long
    scroll_at: str = SCROLL_AT_MOUSE

    def key_code(self):
        if self.scan:
            return self.scan, self.extended
        if self.key in SCAN_CODES:
            return SCAN_CODES[self.key], self.key in EXTENDED_KEYS
        return None

    def describe(self):
        label = self.key.upper() if len(self.key) == 1 else PRESS_LABEL.get(self.key, self.key.title())
        if self.kind == "key":
            return f"Press {label}" + (f" (hold {self.hold_ms} ms)" if self.hold_ms > 30 else "")
        if self.kind == "key_down":
            return f"Hold {label} down"
        if self.kind == "key_up":
            return f"Let go of {label}"
        if self.kind == "click":
            held = f" (hold {self.hold_ms} ms)" if self.hold_ms > 60 else ""
            return f"{MOUSE_TITLE.get(self.button, self.button.title())} click at {self.x}, {self.y}{held}"
        button = MOUSE_TITLE.get(self.button, self.button).lower()
        if self.kind == "mouse_down":
            return f"Hold {button} button at {self.x}, {self.y}"
        if self.kind == "mouse_up":
            return f"Let go of {button} button at {self.x}, {self.y}"
        if self.kind == "scroll":
            where = f"at {self.x}, {self.y}" if self.scroll_at == SCROLL_AT_SPOT else "at the mouse"
            return f"Scroll {self.direction} {self.notches:g} notch{"" if self.notches == 1 else "es"} {where}"
        if self.kind == "wait":
            return f"Wait {self.ms} ms" if self.ms_max <= self.ms else f"Wait {self.ms}-{self.ms_max} ms"
        return f"{ACTION_KINDS[self.kind]}: {self.name or 'no image'}"


DEFAULT_SETTINGS = {
    "mode": "image",  # image, classic, hybrid, reaction or autoclick
    "classic_loops": 0,  # 0 repeats forever
    "hybrid_loops": 0,
    "spam_enabled": True,
    "spam_key": "space",
    "spam_min_ms": 120,
    "spam_max_ms": 180,
    "scan_interval_ms": 150,
    "monitor": 1,
    "only_process": "",  # used by any mode that hasn't picked its own app yet
    "run_in": {},  # mode -> the .exe that mode only works in ("" means any app)
    "mode_keys": {},  # mode -> {"start": key, "stop": key}; missing means the main key / same as start
    "profile_key": "",  # starts and stops this whole profile from anywhere, even while another one is open
    "multiscale": True,
    "reconnect_enabled": True,
    "backup_enabled": True,
    "backup_every_scan": False,
    "farm_in_order": False,
    "order_timeout": 15,
    "safe_zones": [],  # [left, top, right, bottom] as fractions of the screen
    "ac_button": "left",  # Autoclicker
    "ac_double": False,
    "ac_cps": 10.0,  # clicks per second
    "ac_variation": 0,  # percent each gap may differ, so it doesn't look robotic
    "ac_use_rhythm": False,
    "ac_rhythm": [],  # ms between the clicks of a tapped beat, played in a loop
    "ac_where": AC_AT_MOUSE,
    "ac_spots": [],  # [x, y] on the chosen screen, clicked in turn
    "ac_per_spot": 1,
    "ac_wobble": 0,  # pixels each route click may land away from its spot
    "ac_trigger": AC_TOGGLE,
    "ac_max_clicks": 0,  # 0 means no limit
    "ac_max_minutes": 0.0,
}


DEFAULT_PROFILE = "Default"
MODES = ("image", "classic", "hybrid", "reaction", "autoclick")
SIMPLE_HIDDEN = {"hybrid", "reaction", "profiles"}  # what Simple mode hides
DEFAULT_PREFS = {"simple": False, "asked_simple": False, "snip_shape": "box"}  # app-wide, shared by every profile
ANY_APP = "Any app"


def run_in(settings, mode=None):
    """The .exe a mode only works in, or "" for any app."""
    mode = mode or settings.get("mode", "image")
    chosen = settings.get("run_in") or {}
    return chosen[mode] if mode in chosen else settings.get("only_process", "")


LISTS = (("targets", Target), ("reconnect", Target), ("reactions", Target), ("classic", Action), ("hybrid", Action),
         ("watches", Target))


def macro_problem(state):
    """Why this profile can't start yet, or "" if it's ready."""
    s = state.settings
    mode = s["mode"]
    if mode == "image":
        if not (s["spam_enabled"] or any(t.enabled for t in state.targets + state.reconnect)):
            return "Nothing to do yet: add an image or turn on key spam."
    elif mode == "reaction":
        if not any(t.enabled for t in state.reactions):
            return "Add a trigger image first, then pick the key it presses."
    elif mode == "autoclick":
        if s["ac_where"] == AC_ROUTE and not s["ac_spots"]:
            return "Add a spot on the Route tab first, or turn off Use this route."
        if s["ac_use_rhythm"] and not s["ac_rhythm"]:
            return "Tap a rhythm first, or turn off Click to my rhythm."
    else:
        steps = state.classic if mode == "classic" else state.hybrid
        if not any(a.enabled and (mode == "hybrid" or a.kind not in IMAGE_ACTIONS) for a in steps):
            return "Add some steps first."
    return ""


class NamedEvents:
    """A background profile's messages, with its name in front of each log line."""

    def __init__(self, name, events):
        self.name, self.events = name, events

    def put(self, item):
        if item[0] == "log":
            item = ("log", f"{self.name}: {item[1]}")
        self.events.put(item)


class State:
    def __init__(self):
        self.targets = []
        self.reconnect = []
        self.reactions = []
        self.classic = []
        self.hybrid = []
        self.watches = []  # Autoclicker Smart stop images
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.hotkeys = dict(DEFAULT_HOTKEYS)  # shared by every profile
        self.prefs = dict(DEFAULT_PREFS)  # app-wide choices like Simple mode
        self.active_profile = DEFAULT_PROFILE
        self.profiles = {}  # every saved macro except the one loaded right now
        try:
            raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        prefs = raw.get("app")
        if isinstance(prefs, dict):
            self.prefs.update({k: bool(v) for k, v in prefs.items() if k in DEFAULT_PREFS and k != "snip_shape"})
            if prefs.get("snip_shape") in SNIP_SHAPES:
                self.prefs["snip_shape"] = prefs["snip_shape"]
        keys = raw.get("hotkeys")
        if isinstance(keys, dict):
            self.hotkeys.update({k: v for k, v in keys.items() if k in DEFAULT_HOTKEYS and v in HOTKEY_KEYS})
        self._load(raw)
        name = raw.get("active_profile")
        if isinstance(name, str) and name:
            self.active_profile = name
        saved = raw.get("profiles")
        if isinstance(saved, dict):
            self.profiles = {n: p for n, p in saved.items() if isinstance(p, dict) and n != self.active_profile}

    def _load(self, raw):
        """Fill in place, because the running macro and the image lists hold these same objects."""
        saved = raw.get("settings", {})
        self.settings.update({**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}})
        if self.settings["mode"] not in MODES:
            self.settings["mode"] = "image"
        chosen = self.settings.get("run_in")  # a fresh dict, so profiles never share one
        self.settings["run_in"] = {m: v for m, v in chosen.items() if m in MODES and isinstance(v, str)} \
            if isinstance(chosen, dict) else {}
        keys = self.settings.get("mode_keys")
        self.settings["mode_keys"] = {
            m: {w: n for w, n in v.items() if w in ("start", "stop") and n in HOTKEY_KEYS and n != "None"}
            for m, v in keys.items() if m in MODES and isinstance(v, dict)} if isinstance(keys, dict) else {}
        if self.settings["profile_key"] not in HOTKEY_KEYS or self.settings["profile_key"] == "None":
            self.settings["profile_key"] = ""
        try:  # fresh lists, never the shared defaults, and only sensible values
            self.settings["ac_spots"] = [[int(x), int(y)] for x, y in self.settings["ac_spots"]]
            self.settings["ac_rhythm"] = [min(max(int(ms), 10), 10000) for ms in self.settings["ac_rhythm"]]
        except (TypeError, ValueError):
            self.settings["ac_spots"], self.settings["ac_rhythm"] = [], []
        for key, allowed in (("ac_where", (AC_AT_MOUSE, AC_ROUTE)), ("ac_trigger", (AC_TOGGLE, AC_HOLD)),
                             ("ac_button", tuple(MOUSE_BUTTONS))):
            if self.settings[key] not in allowed:
                self.settings[key] = DEFAULT_SETTINGS[key]
        for key, kind in LISTS:
            known = {f.name for f in fields(kind)}
            loaded = []
            for item in raw.get(key, []):
                try:
                    loaded.append(kind(**{k: v for k, v in item.items() if k in known}))
                except TypeError:
                    continue
            if kind is Action:
                loaded = [a for a in loaded if a.kind in ACTION_KINDS]
            getattr(self, key)[:] = loaded

    def _snapshot(self):
        return {"settings": copy.deepcopy(self.settings),
                **{key: [asdict(item) for item in getattr(self, key)] for key, _ in LISTS}}

    def profile_names(self):
        return sorted([*self.profiles, self.active_profile], key=str.lower)

    def switch_profile(self, name):
        self.profiles[self.active_profile] = self._snapshot()
        self._load(self.profiles.pop(name))
        self.active_profile = name
        self.save()

    def new_profile(self, name, blank):
        """Start a profile from the current macro, or with empty lists but the same settings."""
        self.profiles[self.active_profile] = self._snapshot()
        self.active_profile = name
        self.settings["profile_key"] = ""  # keys can't be shared, so a new profile starts without one
        if blank:
            for key, _ in LISTS:
                getattr(self, key)[:] = []
        self.save()

    def rename_profile(self, name):
        self.active_profile = name
        self.save()

    def profile_settings(self, name):
        """A profile's settings: the loaded one's live dict, or the saved copy of any other."""
        if name == self.active_profile:
            return self.settings
        return self.profiles.get(name, {}).setdefault("settings", {})

    def profile_key(self, name):
        key = self.profile_settings(name).get("profile_key", "")
        return key if key in HOTKEY_KEYS and key != "None" else ""

    def profile_mode(self, name):
        mode = self.profile_settings(name).get("mode", "image")
        return mode if mode in MODES else "image"

    def view(self, name):
        """A separate copy of a saved profile, for running it in the background."""
        copy_ = State.__new__(State)
        copy_.targets, copy_.reconnect, copy_.reactions, copy_.classic, copy_.hybrid, copy_.watches = \
            [], [], [], [], [], []
        copy_.settings = copy.deepcopy(DEFAULT_SETTINGS)
        copy_.hotkeys, copy_.active_profile, copy_.profiles = self.hotkeys, name, {}
        copy_._load(copy.deepcopy(self.profiles[name]))
        return copy_

    def delete_profile(self):
        """Drop the loaded profile and load the first of the others."""
        name = next(n for n in self.profile_names() if n != self.active_profile)
        self._load(self.profiles.pop(name))
        self.active_profile = name
        self.save()

    def save(self):
        data = {"active_profile": self.active_profile, "hotkeys": self.hotkeys, "app": self.prefs,
                **self._snapshot(),
                "profiles": self.profiles}
        temp = CONFIG.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, CONFIG)


def pick_monitor(sct, index):
    return sct.monitors[index] if 1 <= index < len(sct.monitors) else sct.monitors[1]


def grab_bgr(sct, monitor):
    frame = np.array(sct.grab(monitor))[:, :, :3]
    if frame.shape[1] != monitor["width"] or frame.shape[0] != monitor["height"]:
        frame = cv2.resize(frame, (monitor["width"], monitor["height"]), interpolation=cv2.INTER_AREA)
    return frame


def zone_boxes(zones, shape):
    height, width = shape[:2]
    return [(int(l * width), int(t * height), int(r * width), int(b * height)) for l, t, r, b in zones]


def mask_zones(screen, boxes):
    """Black out safe zones so an icon inside one can't outscore the real button elsewhere."""
    if not boxes:
        return screen
    screen = screen.copy()
    for left, top, right, bottom in boxes:
        screen[top:bottom, left:right] = 0
    return screen


# ---------------------------------------------------------------------- Engine
class Engine:
    def __init__(self, state, events):
        self.state = state
        self.events = events
        self.stop_flag = None
        self.status = "STOPPED"
        self.clicks = 0
        self.scores = {}
        self.matchers = {}
        self.broken_files = set()
        self.swept = False
        self.backups_left = BACKUP_PER_PASS
        self.next_sweep = 0.0
        self.next_normal = 0.0
        self.sequence_step = 0  # 0 means idle and watching for step 1
        self.sequence_deadline = 0.0
        self.sequence_rearm = 0.0
        self.farm_step = 0
        self.farm_deadline = 0.0
        self.detector = BackupDetector()
        self.ac_block = None  # why the autoclicker is holding off right now, set by Smart stop
        self.ac_done = 0  # clicks this run
        self.ac_started = 0.0
        self.ac_elapsed = 0.0
        self.ac_times = collections.deque(maxlen=400)  # when recent clicks happened, for the live speed

    @property
    def running(self):
        return self.stop_flag is not None and not self.stop_flag.is_set()

    @property
    def reconnecting(self):
        return self.sequence_step > 0

    def log(self, message):
        self.events.put(("log", message))

    def start(self):
        if self.running:
            return
        flag = threading.Event()
        self.stop_flag = flag
        self.matchers.clear()  # relearn sizes in case the game resolution changed
        self.sequence_step = 0
        self.sequence_rearm = 0.0
        self.farm_step = 0
        self.status = "RUNNING"
        mode = self.state.settings["mode"]
        if mode == "image":
            threading.Thread(target=self._guarded, args=(self._scan_loop, flag), daemon=True).start()
            threading.Thread(target=self._guarded, args=(self._spam_loop, flag), daemon=True).start()
        elif mode == "reaction":
            threading.Thread(target=self._guarded, args=(self._reaction_loop, flag), daemon=True).start()
        elif mode == "autoclick":
            watching = any(t.enabled for t in self.state.watches)
            self.ac_block = "CHECKING THE SCREEN" if watching else None  # look once before the first click
            self.ac_done, self.ac_elapsed = 0, 0.0
            self.ac_started = time.monotonic()
            self.ac_times.clear()
            threading.Thread(target=self._guarded, args=(self._autoclick_loop, flag), daemon=True).start()
            if watching:
                threading.Thread(target=self._guarded, args=(self._watch_loop, flag), daemon=True).start()
        else:
            threading.Thread(target=self._guarded, args=(lambda f: self._steps_loop(f, mode), flag),
                             daemon=True).start()

    def stop(self):
        if self.stop_flag is not None:
            self.stop_flag.set()
        self.status = "STOPPED"

    def _guarded(self, loop, flag):
        try:
            loop(flag)
        except Exception as error:  # surface it in the app instead of dying silently
            self.log(f"Macro stopped because of an error: {error}")
            CRASH_LOG.write_text(traceback.format_exc(), encoding="utf-8")
            flag.set()
            self.status = "STOPPED"

    def in_game(self):
        wanted = run_in(self.state.settings).strip().lower()
        return not wanted or foreground_exe().lower() == wanted

    def matcher_for(self, target):
        if target.file in self.matchers:
            return self.matchers[target.file]
        problem = None
        try:
            template = read_image(IMAGES / target.file)
            mask = read_mask(IMAGES / target.file)
            visible = template if mask is None else template[mask > 0]
            if visible.size == 0 or visible.std() < 2:  # scores 100% on any solid area of the screen
                problem = "it's one solid colour, so snip something with more detail"
        except (OSError, ValueError):
            problem = "its image file is missing or unreadable"
        if problem:
            if target.file not in self.broken_files:
                self.broken_files.add(target.file)
                self.log(f"Skipping '{target.name}': {problem}.")
            return None
        matcher = Matcher(template, mask)
        self.matchers[target.file] = matcher
        return matcher

    def _next_spam_wait(self):
        """Seconds to wait so press-to-press time lands in the range (a tap itself takes 30 ms)."""
        settings = self.state.settings
        low, high = sorted((max(settings["spam_min_ms"], 20), max(settings["spam_max_ms"], 20)))
        return max(random.uniform(low, high) / 1000 - 0.03, 0.001)

    def _spam_loop(self, flag):
        settings = self.state.settings
        while not flag.wait(self._next_spam_wait()):
            if settings["spam_enabled"] and not self.reconnecting and settings["spam_key"] in SCAN_CODES \
                    and self.in_game():
                tap_key(settings["spam_key"])

    def _scan_loop(self, flag):
        settings = self.state.settings
        last_click = {}
        with mss.MSS() as sct:
            while not flag.wait(max(settings["scan_interval_ms"], 30) / 1000):
                if not self.in_game():
                    self.status = "WAITING FOR APP"
                    continue
                monitor = pick_monitor(sct, settings["monitor"])
                screen = self._capture(sct, monitor)
                self.swept = False  # at most one slow size search per pass keeps reactions quick
                self.backups_left = BACKUP_PER_PASS
                if self._reconnect_pass(flag, screen, monitor):
                    continue
                self.status = "RUNNING"
                if settings["farm_in_order"]:
                    self._ordered_pass(flag, sct, screen, monitor, last_click)
                    continue
                choice = self._choose_target(flag, screen, last_click)
                if choice is None or not self.in_game():
                    continue
                target, hit = choice
                if not self._still_there(hit, screen, sct, monitor):
                    continue  # the menu moved on while this pass ran, so look again
                self._click(hit, monitor)
                last_click[target.id] = time.monotonic()
                self.log(f"Clicked {target.name} ({describe(hit)})")
                flag.wait(target.delay_after)

    def _choose_target(self, flag, screen, last_click):
        """The first confident hit in list order, or else the first weak one."""
        weak = None
        missed = []
        # Quick check of every image first; the slow searches only run when that finds nothing sure.
        for extras in (False, True):
            for target, matcher in (missed if extras else self._enabled_matchers()):
                if flag.is_set():
                    return None
                hit = self._locate(target, matcher, screen, extras)
                if hit is None:
                    if not extras:
                        missed.append((target, matcher))
                    continue
                if time.monotonic() - last_click.get(target.id, -1e9) < target.cooldown:
                    continue
                if hit.backup or hit.score >= STRONG_MATCH:
                    return target, hit
                weak = weak or (target, hit)
        return weak

    def _enabled_matchers(self):
        """★ images first, on every scan. If any are set, the others only get checked every NORMAL_EVERY s."""
        targets = [t for t in list(self.state.targets) if t.enabled]
        starred = [t for t in targets if t.priority]
        rest = [t for t in targets if not t.priority]
        now = time.monotonic()
        if starred and rest:
            if now < self.next_normal:
                rest = []
            else:
                self.next_normal = now + NORMAL_EVERY
        for target in starred + rest:
            matcher = self.matcher_for(target)
            if matcher:
                yield target, matcher

    def _still_there(self, hit, screen, sct, monitor):
        """A pass can outlive the menu page it looked at, so recheck that spot right before clicking."""
        fresh = self._capture(sct, monitor)
        if fresh is screen:
            return True
        left, top = max(hit.x, 0), max(hit.y, 0)
        before = screen[top:hit.y + hit.h, left:hit.x + hit.w]
        after = fresh[top:hit.y + hit.h, left:hit.x + hit.w]
        if before.size == 0 or before.shape != after.shape or np.array_equal(before, after):
            return True
        result = cv2.matchTemplate(after, before, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0).max()) >= STILL_THERE

    def _ordered_pass(self, flag, sct, screen, monitor, last_click):
        """Look only for the next farm image, starting over after the last one."""
        targets = [t for t in self.state.targets if t.enabled and self.matcher_for(t)]
        if self.farm_step >= len(targets):
            self.farm_step = 0
        if not targets:
            return
        index = self.farm_step
        target = targets[index]
        hit = self._locate(target, self.matcher_for(target), screen)
        now = time.monotonic()
        if hit is None:
            if index > 0 and now >= self.farm_deadline:
                self.log(f"Order: gave up waiting for {target.name}. Starting over from the top.")
                self.farm_step = 0
            return
        if now - last_click.get(target.id, -1e9) < target.cooldown or not self.in_game():
            return
        if not self._still_there(hit, screen, sct, monitor):
            return
        self._click(hit, monitor)
        last_click[target.id] = time.monotonic()
        self.log(f"Clicked {target.name} ({index + 1}/{len(targets)}, {describe(hit)})")
        self.farm_step = (index + 1) % len(targets)
        self.farm_deadline = time.monotonic() + target.delay_after + self.state.settings["order_timeout"]
        flag.wait(target.delay_after)

    def _capture(self, sct, monitor):
        screen = grab_bgr(sct, monitor)
        return mask_zones(screen, zone_boxes(self.state.settings["safe_zones"], screen.shape))

    def in_safe_zone(self, hit, shape):
        x, y = hit.x + hit.w // 2, hit.y + hit.h // 2
        return any(l <= x < r and t <= y < b for l, t, r, b in zone_boxes(self.state.settings["safe_zones"], shape))

    def _locate(self, target, matcher, screen, extras=True):
        """Record the target's score and return its Hit if it clears the minimum match.

        With extras off it only does the quick check; the size search and backup detection are skipped.
        """
        every_scan = self.state.settings["backup_enabled"] and self.state.settings["backup_every_scan"]
        if every_scan and matcher.last_detect is not screen:  # detection goes first, once per frame
            matcher.last_detect = screen
            found = self.detector.find(matcher, screen)
            if found and not self.in_safe_zone(found, screen.shape):
                self.scores[target.id] = found.score
                return found
        if matcher.last_probe[0] is screen:  # already checked this frame in the quick round
            hit = matcher.last_probe[1]
        else:
            hit = matcher.probe(screen)
            matcher.last_probe = (screen, hit)
        now = time.monotonic()
        if extras and (not hit or hit.score < target.threshold) and self.state.settings["multiscale"] \
                and not self.swept and now >= self.next_sweep and matcher.sweep_due():
            self.swept = True
            self.next_sweep = now + SWEEP_GAP
            wider = matcher.sweep(screen, target.threshold)
            if wider and (not hit or wider.score > hit.score):
                hit = wider
        if extras and (not hit or hit.score < target.threshold) and self.state.settings["backup_enabled"] \
                and not every_scan and self.backups_left > 0 and time.monotonic() >= matcher.next_backup:
            self.backups_left -= 1
            matcher.next_backup = time.monotonic() + BACKUP_SECONDS
            hit = self.detector.find(matcher, screen) or hit
        if hit and self.in_safe_zone(hit, screen.shape):
            hit = None
        self.scores[target.id] = hit.score if hit else 0.0
        if not hit or (hit.score < target.threshold and not hit.backup):
            return None
        if not hit.backup:
            matcher.scale = hit.scale  # size learned, no more searching for this image
        return hit

    def _click(self, hit, monitor):
        if self.stop_flag is not None and self.stop_flag.is_set():
            return
        click_at(monitor["left"] + hit.x + hit.w // 2, monitor["top"] + hit.y + hit.h // 2, flag=self.stop_flag)
        self.clicks += 1

    def _reconnect_pass(self, flag, screen, monitor):
        """Advance the reconnect steps. Returns True while farming should stay paused."""
        steps = [t for t in self.state.reconnect if t.enabled] if self.state.settings["reconnect_enabled"] else []
        if self.sequence_step >= len(steps):
            self.sequence_step = 0
        if not steps:
            return False
        now = time.monotonic()
        if not self.reconnecting and now < self.sequence_rearm:
            return False
        index = self.sequence_step
        step = steps[index]
        matcher = self.matcher_for(step)
        hit = self._locate(step, matcher, screen) if matcher else None
        if hit is None:
            if not self.reconnecting:
                return False
            if now < self.sequence_deadline:
                self.status = f"RECOVERING {index + 1}/{len(steps)}"
                return True
            self.log(f"Recovery: gave up waiting for {step.name}. Watching for step 1 again.")
            self.sequence_step = 0
            return False
        if not self.in_game():
            return self.reconnecting
        self._click(hit, monitor)
        self.log(f"Recovery {index + 1}/{len(steps)}: clicked {step.name} ({describe(hit)})")
        if index + 1 == len(steps):
            self.sequence_rearm = time.monotonic() + REARM_SECONDS
            self.sequence_step = 0
            self.log("Recovery finished. Back to the click list.")
        else:
            self.sequence_deadline = time.monotonic() + step.delay_after + steps[index + 1].timeout
            self.sequence_step = index + 1  # also pauses the key spam from here on
            self.status = f"RECOVERING {index + 2}/{len(steps)}"
        flag.wait(step.delay_after)
        return True

    # ---- Reaction mode: an image on screen presses a key
    def _reaction_loop(self, flag):
        settings = self.state.settings
        last_press = {}
        with mss.MSS() as sct:
            while not flag.wait(max(settings["scan_interval_ms"], 30) / 1000):
                if not self.in_game():
                    self.status = "WAITING FOR APP"
                    continue
                self.status = "RUNNING"
                screen = self._capture(sct, pick_monitor(sct, settings["monitor"]))
                self.swept = False
                self.backups_left = BACKUP_PER_PASS
                for rule in list(self.state.reactions):
                    if flag.is_set():
                        return
                    if not rule.enabled or rule.key not in SCAN_CODES:
                        continue
                    matcher = self.matcher_for(rule)
                    hit = self._locate(rule, matcher, screen) if matcher else None
                    if hit is None or time.monotonic() - last_press.get(rule.id, -1e9) < rule.cooldown:
                        continue
                    if not self.in_game():
                        break
                    tap_key(rule.key, rule.hold_ms / 1000, flag)
                    last_press[rule.id] = time.monotonic()
                    self.clicks += 1
                    self.log(f"Saw {rule.name} ({describe(hit)}), pressed {rule.key.upper()}")
                    flag.wait(rule.delay_after)

    # ---- Autoclicker: clicks on a timer, with Smart stop watching the screen beside it
    def _ac_interval(self, turn):
        """Seconds until the next click: the tapped rhythm or the steady speed, varied a little if asked."""
        s = self.state.settings
        rhythm = s["ac_rhythm"] if s["ac_use_rhythm"] else []
        base = rhythm[turn % len(rhythm)] / 1000 if rhythm else 1 / min(max(s["ac_cps"], 0.1), 100)
        spread = min(max(s["ac_variation"], 0), 90) / 100
        return max(base * random.uniform(1 - spread, 1 + spread), 0.002)

    def _ac_point(self, monitor, spot):
        """Where the next click goes on the whole desktop, or None if it must be skipped."""
        s = self.state.settings
        if s["ac_where"] == AC_ROUTE:
            spots = s["ac_spots"]
            if not spots:
                return None
            x, y = spots[spot % len(spots)]
            wobble = max(int(s["ac_wobble"]), 0)
            if wobble:
                angle, reach = random.uniform(0, 2 * math.pi), wobble * math.sqrt(random.random())
                x, y = x + round(reach * math.cos(angle)), y + round(reach * math.sin(angle))
            point = (monitor["left"] + x, monitor["top"] + y)
        else:
            point = cursor_position()
        if point_is_ours(*point):
            return None  # never click MacroEZ itself, like its own Stop button
        x, y = point[0] - monitor["left"], point[1] - monitor["top"]
        if 0 <= x < monitor["width"] and 0 <= y < monitor["height"] and self.in_safe_zone(
                Hit(1.0, x, y, 1, 1, 1.0), (monitor["height"], monitor["width"])):
            return None
        return point

    def _ac_finish(self, flag, message):
        self.log(message)
        flag.set()
        self.status = "STOPPED"

    def _autoclick_loop(self, flag):
        s = self.state.settings
        turn = spot = on_spot = 0
        last_point = None
        fine_timer(True)
        try:
            with mss.MSS() as sct:
                next_at = time.perf_counter()  # after setting up the screen grabber, which takes a moment
                while not flag.is_set():
                    if s["ac_max_clicks"] and self.ac_done >= s["ac_max_clicks"]:
                        self._ac_finish(flag, f"Autoclicker finished: {self.ac_done} clicks.")
                        break
                    if s["ac_max_minutes"] and time.monotonic() - self.ac_started >= s["ac_max_minutes"] * 60:
                        self._ac_finish(flag, f"Autoclicker finished: {s['ac_max_minutes']:g} min, "
                                              f"{self.ac_done} clicks.")
                        break
                    block = self.ac_block or (None if self.in_game() else "WAITING FOR APP")
                    if block:
                        self.status = block
                        flag.wait(0.05)
                        next_at = time.perf_counter()  # pick up at normal speed afterwards, not with a burst
                        continue
                    self.status = "CLICKING"
                    if wait_exact(flag, next_at - time.perf_counter()):
                        break
                    interval = self._ac_interval(turn)
                    turn += 1
                    next_at = max(next_at + interval, time.perf_counter() - 0.05)
                    if self.ac_block:  # Smart stop saw something during the wait
                        continue
                    monitor = pick_monitor(sct, s["monitor"])
                    point = self._ac_point(monitor, spot)
                    if s["ac_where"] == AC_ROUTE:
                        on_spot += 1
                        if on_spot >= max(int(s["ac_per_spot"]), 1):
                            spot, on_spot = spot + 1, 0
                        if point is not None:
                            set_cursor(*point)
                            if point != last_point:  # a small nudge so the app notices the mouse arrived
                                nudge_mouse()
                                time.sleep(0.004)
                                last_point = point
                    if point is None:
                        continue
                    presses = 2 if s["ac_double"] else 1
                    hold = min(0.012, interval * 0.3)
                    for press in range(presses):
                        if press:
                            time.sleep(min(0.03, interval * 0.2))
                        mouse_button(s["ac_button"])
                        time.sleep(hold)
                        mouse_button(s["ac_button"], up=True)
                    self.ac_done += 1
                    self.clicks += presses
                    self.ac_times.append(time.monotonic())
        finally:
            fine_timer(False)
            self.ac_elapsed = time.monotonic() - self.ac_started

    def _watch_loop(self, flag):
        """Smart stop: look for the chosen images and stop, pause or hold off the clicking."""
        settings = self.state.settings
        with mss.MSS() as sct:
            while not flag.is_set():
                rules = [t for t in self.state.watches if t.enabled]
                if not rules:
                    self.ac_block = None
                    flag.wait(0.25)
                    continue
                screen = self._capture(sct, pick_monitor(sct, settings["monitor"]))
                self.swept = False
                self.backups_left = BACKUP_PER_PASS
                seen = []
                for rule in rules:
                    matcher = self.matcher_for(rule)
                    if matcher and self._locate(rule, matcher, screen) is not None:
                        seen.append(rule)
                if flag.is_set():
                    return
                stop = next((r for r in seen if r.watch == WATCH_STOP), None)
                if stop:
                    self.ac_block = "STOPPING"
                    self._ac_finish(flag, f"Saw {stop.name}, so the autoclicker stopped after {self.ac_done} clicks.")
                    return
                pause = next((r for r in seen if r.watch == WATCH_PAUSE), None)
                needed = [r for r in rules if r.watch == WATCH_ONLY]
                if pause:
                    self.ac_block = f"PAUSED: {pause.name}"
                elif needed and not any(r in seen for r in needed):
                    self.ac_block = f"WAITING FOR {needed[0].name}"
                else:
                    self.ac_block = None
                flag.wait(max(settings["scan_interval_ms"], 30) / 1000)

    # ---- Classic and Hybrid modes: a list of steps, played top to bottom
    def _steps_loop(self, flag, mode):
        settings = self.state.settings
        steps_list = self.state.classic if mode == "classic" else self.state.hybrid
        loops = int(settings[f"{mode}_loops"])
        done = 0
        self.held = set()  # keys and buttons a step pressed down and hasn't let go of yet
        fine_timer(True)
        self.late = 0.0  # seconds playback has fallen behind, taken back out of the next wait
        try:
            with mss.MSS() as sct:
                while not flag.is_set() and (loops <= 0 or done < loops):
                    steps = [a for a in steps_list if a.enabled and (mode == "hybrid" or a.kind not in IMAGE_ACTIONS)]
                    if not steps:
                        self.log("No steps to play. Add some first.")
                        break
                    for index, step in enumerate(steps):
                        if not self._wait_for_game(flag):
                            return
                        self.status = f"STEP {index + 1}/{len(steps)}" + (
                            f"  LOOP {done + 1}/{loops}" if loops > 0 else "")
                        following = steps[index + 1] if index + 1 < len(steps) else steps[0]
                        self._play(flag, step, sct, following)
                        if flag.is_set():
                            return
                    done += 1
                    flag.wait(0.01)  # an empty-wait loop must still let the app breathe
        finally:
            self._let_go()
            fine_timer(False)
        if not flag.is_set():
            self.log(f"Finished {done} loop{'s' if done != 1 else ''}.")
            flag.set()
            self.status = "STOPPED"

    def _wait_for_game(self, flag):
        """Pause while the chosen game isn't focused. False once stopped."""
        while not self.in_game():
            self.status = "WAITING FOR APP"
            if flag.wait(0.2):
                return False
        return not flag.is_set()

    def _let_go(self):
        """Release anything still held, so stopping never leaves a key or button stuck down."""
        for item in list(getattr(self, "held", ())):
            if item[0] == "key":
                send_scan(item[1], item[2], up=True)
            else:
                mouse_button(item[1], up=True)
        self.held = set()

    def _play(self, flag, step, sct, following=None):
        started = time.perf_counter()
        planned = 0.0  # how long the step is meant to take; anything beyond that is made up in the next wait
        if step.kind in KEY_ACTIONS:
            code = step.key_code()
            if code is None:
                return
            if step.kind == "key":
                send_scan(*code)
                planned = max(step.hold_ms, 1) / 1000
                try:
                    wait_exact(flag, planned)
                finally:
                    send_scan(*code, up=True)
                self.clicks += 1
            elif step.kind == "key_down":
                send_scan(*code)
                self.held.add(("key", *code))
                self.clicks += 1
            else:
                send_scan(*code, up=True)
                self.held.discard(("key", *code))
        elif step.kind in MOUSE_ACTIONS:
            monitor = pick_monitor(sct, self.state.settings["monitor"])
            x, y = monitor["left"] + step.x, monitor["top"] + step.y
            if step.kind != "mouse_up" and self.in_safe_zone(Hit(1.0, step.x, step.y, 1, 1, 1.0),
                                                             (monitor["height"], monitor["width"])):
                self.log(f"Skipped a click at {step.x}, {step.y}: it's inside a safe zone.")
                return
            if step.kind == "click":
                hold = max(step.hold_ms, 1) / 1000
                planned = hold + MOVE_LEAD
                click_at(x, y, step.button, hold, flag)
                self.clicks += 1
            elif step.kind == "mouse_down":
                planned = MOVE_LEAD
                move_to(x, y)
                mouse_button(step.button)
                self.held.add(("mouse", step.button))
                self.clicks += 1
            else:
                set_cursor(x, y)  # where the drag ended
                mouse_button(step.button, up=True)
                self.held.discard(("mouse", step.button))
        elif step.kind == "scroll":
            planned = self._scroll(flag, step, sct)
            if planned is None:
                return
            self.clicks += 1
        elif step.kind == "wait":
            low, high = sorted((max(step.ms, 0), max(step.ms_max, step.ms, 0)))
            wanted = random.uniform(low, high) / 1000
            if following is not None and (following.kind in ("click", "mouse_down") or (
                    following.kind == "scroll" and following.scroll_at == SCROLL_AT_SPOT)):
                wanted = max(wanted - MOVE_LEAD, 0.0)  # moving the mouse there takes this long; start it early
            owed = min(self.late, wanted)
            self.late -= owed
            wait_exact(flag, wanted - owed)
            return
        else:
            self._image_step(flag, step, sct)
            self.late = 0.0  # an image step waits for the screen, so earlier timing no longer matters
            return
        self.late += max(time.perf_counter() - started - planned, 0.0)

    def _scroll(self, flag, step, sct):
        """Turn the wheel, spaced out over the step's time. Returns how long that should take, or None if skipped."""
        total = round(max(step.notches, 0) * WHEEL_NOTCH)
        if not total:
            return None
        planned = max(step.spread_ms, 0) / 1000
        if step.scroll_at == SCROLL_AT_SPOT:
            monitor = pick_monitor(sct, self.state.settings["monitor"])
            if self.in_safe_zone(Hit(1.0, step.x, step.y, 1, 1, 1.0), (monitor["height"], monitor["width"])):
                self.log(f"Skipped a scroll at {step.x}, {step.y}: it's inside a safe zone.")
                return None
            move_to(monitor["left"] + step.x, monitor["top"] + step.y)
        turns = (total + WHEEL_NOTCH - 1) // WHEEL_NOTCH
        gap = planned / max(turns - 1, 1)
        for turn in range(turns):
            scroll_wheel(step.direction, total * (turn + 1) // turns - total * turn // turns)
            if (turn < turns - 1 or turns == 1) and wait_exact(flag, gap):
                break
        return planned + (MOVE_LEAD if step.scroll_at == SCROLL_AT_SPOT else 0)

    def _image_step(self, flag, step, sct):
        matcher = self.matcher_for(step)
        if matcher is None:
            return
        deadline = time.monotonic() + step.timeout
        settings = self.state.settings
        while not flag.is_set():
            monitor = pick_monitor(sct, settings["monitor"])
            screen = self._capture(sct, monitor)
            self.swept = False
            self.backups_left = BACKUP_PER_PASS
            hit = self._locate(step, matcher, screen)
            if hit is not None and self.in_game():
                if step.kind == "click_image":
                    self._click(hit, monitor)
                    self.log(f"Clicked {step.name} ({describe(hit)})")
                else:
                    self.log(f"Saw {step.name} ({describe(hit)})")
                return
            if time.monotonic() >= deadline:
                self.log(f"{step.name} didn't show up within {step.timeout:g} s, so that step was skipped.")
                return
            flag.wait(max(settings["scan_interval_ms"], 30) / 1000)

    def test_scan(self):
        threading.Thread(target=self._guarded, args=(self._test, threading.Event()), daemon=True).start()

    def _test(self, _flag):
        settings = self.state.settings
        mode = settings["mode"]
        if mode == "image":
            targets = self.state.targets + self.state.reconnect
        elif mode == "reaction":
            targets = list(self.state.reactions)
        elif mode == "hybrid":
            targets = [a for a in self.state.hybrid if a.kind in IMAGE_ACTIONS and a.file]
        elif mode == "autoclick":
            targets = list(self.state.watches)
        else:
            targets = []
        targets = [t for t in targets if t.enabled]
        if not targets:
            self.log("Test scan: this mode has no images to check." if mode == "classic"
                     else "Test scan: add an image first.")
            return
        with mss.MSS() as sct:
            screen = self._capture(sct, pick_monitor(sct, settings["monitor"]))
        detector = BackupDetector()  # this runs beside the macro, so it gets its own
        for target in targets:
            matcher = self.matcher_for(target)
            if matcher is None:
                continue
            hit = matcher.find(screen, target.threshold, settings["multiscale"])
            score = hit.score if hit and not self.in_safe_zone(hit, screen.shape) else 0.0
            self.scores[target.id] = score
            found = "would notice it" if mode == "autoclick" else "would click"
            verdict = found if score >= target.threshold else f"below your {target.threshold:.0%} minimum"
            if score < target.threshold and settings["backup_enabled"]:
                backup = detector.find(matcher, screen)
                if backup and not self.in_safe_zone(backup, screen.shape):
                    verdict += f", but backup detection found it and {found}"
            self.log(f"Test: {target.name} scored {score:.0%}, {verdict}")


# -------------------------------------------------------------------------- UI
BG, SIDEBAR, PANEL, FIELD, LINE = "#0b0e13", "#07090d", "#12171e", "#1a212b", "#2b3542"
HOVER, RAISED, EDGE, NAV_ON, CHIP, SELECTED = "#10151c", "#232c38", "#1d2530", "#151c26", "#11303a", "#163640"
GLASS_2 = "#1aa9d6"  # the darker end of the accent gradient
TEXT, MUTED, GLASS, DANGER, AMBER, GOOD = "#e6edf3", "#8b98a9", "#2dd4ea", "#ff5d6c", "#f5b94f", "#3ddc97"
GLASS_HOVER, DANGER_HOVER, ON_ACCENT = "#7ae6f3", "#ff8792", "#062229"
HOTKEY_ACTIONS = (("start", "Main start / stop"), ("record", "Record (Classic)"), ("snip", "Snip an image"),
                  ("test", "Test scan"), ("zone", "Draw a safe zone"))
MODE_INFO = {  # title, icon (Segoe Fluent Icons), what it does
    "image": ("Image", "", "Spots buttons on your screen, like Accept, Next or OK, and clicks them for you."),
    "classic": ("Classic", "", "Record a task once, like filling in a form or copying rows, and replay it as often as you need."),
    "hybrid": ("Hybrid", "", "Recorded steps that also wait for a window or button to appear before carrying on."),
    "reaction": ("Reaction", "", "Presses a key the moment something shows up, like a popup or a finished export."),
    "autoclick": ("Autoclicker", "", "Clicks for you at any speed or rhythm, and can stop itself when something "
                                           "shows up."),
    "presets": ("Presets", "\ue8f1", "Ready-made macros shared by other people. Preview one, add it as a profile, "
                                     "and share your own."),
    "profiles": ("Profiles", "\ue716", "Run several profiles at once. Give each one its own key, then press it "
                                        "to start or stop that profile from anywhere."),
    "settings": ("Settings", "", "Detection, safe zones and hotkeys. Hotkeys are shared by every profile."),
}


def parse_number(text, fallback, low=0.0):
    try:
        return max(low, float(text))
    except (TypeError, ValueError):
        return fallback


def save_image(image, name):
    IMAGES.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")[:40] or "image"
    filename = f"{slug}-{uuid.uuid4().hex[:6]}.png"
    image.convert("RGBA" if image.has_transparency_data else "RGB").save(IMAGES / filename)
    return filename


# ---------------------------------------------------------------- Presets
PRESET_FORMAT = "macroez-preset"
PRESET_SITE = "https://macroez.com"
PRESET_REPO = "aveeragoni/macroez"
PRESET_LIMITS = {"bytes": 15_000_000, "images": 200, "steps": 5000, "pixels": 4000}
# settings that belong to one PC, not to the macro: they're never shared and never taken from a preset
PRESET_PRIVATE_SETTINGS = {"run_in", "only_process", "monitor", "profile_key", "mode_keys", "safe_zones"}
PRESET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
PENDING_PRESET = APP_DIR / "open-preset.txt"  # a macroez:// link handed over to the copy that's already open
WIN_SCANS = {(0x5B, True), (0x5C, True)}


class PresetError(ValueError):
    pass


def _preset_images(snapshot):
    names = set()
    for key, _kind in LISTS:
        for item in snapshot.get(key, []):
            for attr in ("file", "uncropped"):
                if item.get(attr):
                    names.add(item[attr])
    return sorted(names)


def export_preset(state, name, description=""):
    """The open profile as a preset file (zip bytes): its steps, images and settings, minus anything personal."""
    snapshot = state._snapshot()
    settings = {k: v for k, v in snapshot["settings"].items() if k not in PRESET_PRIVATE_SETTINGS}
    data = {"format": PRESET_FORMAT, "version": 1, "name": name.strip()[:60], "description": description.strip()[:500],
            "mode": settings.get("mode", "image"), "settings": settings,
            **{key: snapshot[key] for key, _kind in LISTS}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("preset.json", json.dumps(data, indent=2))
        for filename in _preset_images(snapshot):
            if (IMAGES / filename).is_file():
                archive.write(IMAGES / filename, f"images/{filename}")
    return buffer.getvalue()


def read_preset(blob):
    """Check a preset file and return what's in it. Anything odd is refused with PresetError."""
    if len(blob) > PRESET_LIMITS["bytes"]:
        raise PresetError("That preset is too big.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        entries = archive.infolist()
        if sum(e.file_size for e in entries) > PRESET_LIMITS["bytes"] * 3:
            raise PresetError("That preset is too big.")
        data = json.loads(archive.read("preset.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, ValueError) as problem:
        if isinstance(problem, PresetError):
            raise
        raise PresetError("That isn't a MacroEZ preset file.") from None
    if not isinstance(data, dict) or data.get("format") != PRESET_FORMAT or not isinstance(data.get("version"), int):
        raise PresetError("That isn't a MacroEZ preset file.")
    if data["version"] > 1:
        raise PresetError("That preset needs a newer version of MacroEZ.")
    if data.get("mode") not in MODES:
        raise PresetError("That preset uses a mode this version of MacroEZ doesn't have.")
    lists = {}
    for key, _kind in LISTS:
        items = data.get(key, [])
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise PresetError("That preset file is damaged.")
        lists[key] = items
    if sum(len(v) for v in lists.values()) > PRESET_LIMITS["steps"]:
        raise PresetError("That preset has too many steps.")
    images = {}
    for entry in entries:
        if entry.filename == "preset.json" or entry.is_dir():
            continue
        name = entry.filename[len("images/"):] if entry.filename.startswith("images/") else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.png", name) or ".." in name:
            raise PresetError("That preset contains a file it shouldn't.")
        raw = archive.read(entry)
        try:
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format != "PNG" or max(picture.size) > PRESET_LIMITS["pixels"]:
                    raise PresetError("That preset contains an image it shouldn't.")
                picture.load()
        except (OSError, ValueError) as problem:
            if isinstance(problem, PresetError):
                raise
            raise PresetError("That preset contains a damaged image.") from None
        images[name] = raw
    if len(images) > PRESET_LIMITS["images"]:
        raise PresetError("That preset has too many images.")
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    settings = {k: v for k, v in settings.items() if k in DEFAULT_SETTINGS and k not in PRESET_PRIVATE_SETTINGS}
    settings["mode"] = data["mode"]
    return {"name": str(data.get("name") or "Preset")[:60].strip() or "Preset",
            "description": str(data.get("description") or "")[:500], "mode": data["mode"],
            "settings": settings, "lists": lists, "images": images}


def preset_steps(preset):
    """The preset's steps, as the app would show them, for the preview."""
    snapshot = {"settings": preset["settings"], **preset["lists"]}
    view = State.__new__(State)
    view.targets, view.reconnect, view.reactions, view.classic, view.hybrid, view.watches = [], [], [], [], [], []
    view.settings = copy.deepcopy(DEFAULT_SETTINGS)
    view.hotkeys, view.active_profile, view.profiles, view.prefs = dict(DEFAULT_HOTKEYS), "", {}, dict(DEFAULT_PREFS)
    view._load(copy.deepcopy(snapshot))
    return view


def preset_warnings(preset):
    """Things to look at before running someone else's macro."""
    view = preset_steps(preset)
    s, mode = view.settings, view.settings["mode"]
    keys = []  # (name, scan, extended, kind)
    if mode in ("classic", "hybrid"):
        keys += [(a.key, a.scan, a.extended, a.kind) for a in getattr(view, mode) if a.kind in KEY_ACTIONS]
    if mode == "reaction":
        keys += [(t.key, 0, False, "key") for t in view.reactions]
    if mode == "image" and s.get("spam_enabled"):
        keys.append((s.get("spam_key", ""), 0, False, "key"))
    names = [k[0].lower() for k in keys]
    warnings = []
    if any(n in ("left win", "right win") or (scan, ext) in WIN_SCANS for n, scan, ext, _ in keys):
        warnings.append("It presses the Windows key, which can open the Start menu or run Windows shortcuts.")
    if any(kind == "key_down" and ("ctrl" in n.lower() or "alt" in n.lower() or scan in (0x1D, 0x38))
           for n, scan, ext, kind in keys):
        warnings.append("It holds Ctrl or Alt down, so it uses keyboard shortcuts.")
    if "menu" in names:
        warnings.append("It presses the Menu key.")
    typed = sum(1 for n in names if len(n) == 1 and n.isalnum())
    if typed >= 12:
        warnings.append(f"It types text ({typed} letter and number presses).")
    return warnings


def preset_summary(preset):
    view = preset_steps(preset)
    mode = view.settings["mode"]
    count = {"image": len(view.targets), "classic": len(view.classic), "hybrid": len(view.hybrid),
             "reaction": len(view.reactions), "autoclick": len(view.watches)}[mode]
    unit = {"image": "button", "reaction": "trigger", "autoclick": "Smart stop image"}.get(mode, "step")
    text = f"{MODE_INFO[mode][0]} mode" + (f", {count} {unit}{'s' if count != 1 else ''}"
                                           if count or mode != "autoclick" else "")
    images = len(preset["images"])
    return text + (f", {images} image{'s' if images != 1 else ''}" if images else "")


def preset_lines(preset, limit=400):
    view = preset_steps(preset)
    mode = view.settings["mode"]
    if mode in ("classic", "hybrid"):
        lines = [f"{n}. {a.describe()}" for n, a in enumerate(getattr(view, mode), start=1)]
    elif mode == "image":
        lines = [f"Click {t.name}" for t in view.targets] + [f"Recovery: click {t.name}" for t in view.reconnect]
        if view.settings.get("spam_enabled"):
            lines.append(f"Press {key_label(view.settings.get('spam_key', ''))} over and over between clicks")
    elif mode == "reaction":
        lines = [f"When {t.name} shows up, press {key_label(t.key)}" for t in view.reactions]
    else:
        s = view.settings
        lines = [f"Click {MOUSE_TITLE.get(s['ac_button'], s['ac_button']).lower()} at {s['ac_cps']:g} clicks per second"
                 + (", to a tapped rhythm" if s["ac_use_rhythm"] else "")
                 + (", only while you hold the start key" if s["ac_trigger"] == AC_HOLD else "")]
        lines += [f"Smart stop: {t.name} ({t.watch.lower()})" for t in view.watches]
    if len(lines) > limit:
        lines = lines[:limit] + [f"… and {len(lines) - limit} more"]
    return lines


def install_preset(state, preset, profile_name):
    """Add a preset as a new saved profile (not opened, not started). Its images get fresh file names."""
    renamed = {}
    for old, raw in preset["images"].items():
        with Image.open(io.BytesIO(raw)) as picture:
            renamed[old] = save_image(picture, Path(old).stem.rsplit("-", 1)[0] or "preset")
    lists = copy.deepcopy(preset["lists"])
    for items in lists.values():
        for item in items:
            for attr in ("file", "uncropped"):
                if item.get(attr):
                    item[attr] = renamed.get(item[attr], "")
            item.pop("id", None)
    settings = {**copy.deepcopy(preset["settings"]), "profile_key": "", "mode_keys": {}, "run_in": {}}
    state.profiles[profile_name] = {"settings": settings, **lists}
    state.save()
    return profile_name


def fetch(url, limit=PRESET_LIMITS["bytes"]):
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME} app"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise PresetError("That download is too big.")
    return data


def preset_link_id(text):
    """macroez://preset/<id> -> id, or None."""
    match = re.fullmatch(r"macroez://preset/([a-z0-9-]+)/?", (text or "").strip().lower())
    return match.group(1) if match and PRESET_ID.match(match.group(1)) else None


def register_link_handler():
    """Let "Open in MacroEZ" buttons on macroez.com open this app (only for the installed .exe, just this user)."""
    if not getattr(sys, "frozen", False):
        return
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\macroez") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:MacroEZ preset")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\macroez\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{sys.executable}" "%1"')
    except OSError:
        pass


# Shapes a snip can be cut to. Everything outside the shape is saved see-through and ignored when searching.
SNIP_SHAPES = {"box": "▭ Box", "circle": "◯ Circle", "triangle": "△ Triangle", "diamond": "◇ Diamond",
               "hexagon": "⬡ Hexagon", "star": "☆ Star", "draw": "✎ Draw your own"}
MIN_SHAPE_PIXELS = 60  # fewer kept pixels than this can't be told apart from anything else on screen


def shape_outline(shape, left, top, right, bottom):
    """The corners of a shape that fills this box, as (x, y) points. Circles get enough points to look round."""
    cx, cy, rx, ry = (left + right) / 2, (top + bottom) / 2, (right - left) / 2, (bottom - top) / 2

    def around(count, radius=lambda i: 1.0, start=-math.pi / 2):
        return [(cx + rx * radius(i) * math.cos(start + 2 * math.pi * i / count),
                 cy + ry * radius(i) * math.sin(start + 2 * math.pi * i / count)) for i in range(count)]

    if shape == "circle":
        return around(72)
    if shape == "triangle":
        return [(cx, top), (right, bottom), (left, bottom)]
    if shape == "diamond":
        return [(cx, top), (right, cy), (cx, bottom), (left, cy)]
    if shape == "hexagon":
        return around(6, start=0.0)  # flat top and bottom, points left and right
    if shape == "star":
        return around(10, radius=lambda i: 1.0 if i % 2 == 0 else 0.45)
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def cut_shape(image, points):
    """Crop the image to the outline's box and make everything outside the outline see-through.
    Returns (cut image, box), or (None, reason) if too little is left to search for."""
    xs, ys = [x for x, _ in points], [y for _, y in points]
    box = (max(0, round(min(xs))), max(0, round(min(ys))),
           min(image.width, round(max(xs))), min(image.height, round(max(ys))))
    if box[2] - box[0] < 6 or box[3] - box[1] < 6:
        return None, "That area was too small, try again."
    piece = image.crop(box)
    mask = Image.new("L", piece.size, 0)
    ImageDraw.Draw(mask).polygon([(x - box[0], y - box[1]) for x, y in points], fill=255)
    if piece.mode == "RGBA":  # cutting a picture that already has see-through parts keeps them see-through
        mask = Image.fromarray(np.minimum(np.array(mask), np.array(piece.getchannel("A"))))
    kept = int((np.array(mask) >= 128).sum())
    if kept < MIN_SHAPE_PIXELS:
        return None, "That shape is too thin to search for. Draw around more of it."
    if kept == piece.width * piece.height:
        return piece.convert("RGB"), box
    piece = piece.convert("RGBA")
    piece.putalpha(mask)
    return piece, box


def shape_bar(parent, current, pick):
    """A row of shape buttons; pick(shape) runs when one is chosen. Returns the frame and a function to
    show which one is chosen."""
    bar = ttk.Frame(parent, style="Bg.TFrame")
    buttons = {}
    for number, (shape, label) in enumerate(SNIP_SHAPES.items(), start=1):
        buttons[shape] = ttk.Button(bar, text=label, command=lambda s=shape: pick(s), takefocus=False)
        buttons[shape].pack(side="left", padx=(0 if number == 1 else 4, 0))

    def show(chosen):
        for shape, button in buttons.items():
            button.configure(style="Accent.TButton" if shape == chosen else "TButton")

    show(current)
    return bar, show


def drag_outline(shape, start, end, square=False):
    """The outline for a drag from start to end. Shift (square=True) keeps it as wide as it is tall."""
    (x0, y0), (x1, y1) = start, end
    if square:
        side = max(abs(x1 - x0), abs(y1 - y0))
        x1 = x0 + side * (1 if x1 >= x0 else -1)
        y1 = y0 + side * (1 if y1 >= y0 else -1)
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    return shape_outline(shape, left, top, right, bottom)


def flat(points):
    """Points as x1, y1, x2, y2, ... for a canvas polygon, which needs at least three points."""
    points = list(points) + [points[-1]] * max(0, 3 - len(points))
    return [value for point in points for value in point]


def see_through(image, size=8):
    """A picture with see-through parts drawn over a dark checkerboard, so its shape shows in lists."""
    if image.mode != "RGBA":
        return image.convert("RGB")
    board = Image.new("RGB", image.size, "#1a212b")
    draw = ImageDraw.Draw(board)
    for y in range(0, image.height, size):
        for x in range((y // size) % 2 * size, image.width, size * 2):
            draw.rectangle((x, y, x + size - 1, y + size - 1), fill="#232c38")
    board.paste(image, (0, 0), image)
    return board


def thumbnail(item, box):
    try:
        with Image.open(IMAGES / item.file) as image:
            copy_ = see_through(image.convert("RGBA") if image.has_transparency_data else image.convert("RGB"))
    except (OSError, ValueError):
        return None
    copy_.thumbnail(box)
    return ImageTk.PhotoImage(copy_)


def crop_window(app, target, set_file):
    """Let the user drag a box over the image; set_file(target, new_file, original_file) on Apply."""
    try:
        with Image.open(IMAGES / target.file) as opened:
            image = opened.convert("RGBA" if opened.has_transparency_data else "RGB")
    except (OSError, ValueError):
        app.log(f"Can't crop {target.name}: its image file is missing or unreadable.")
        return
    zoom = min(800 / image.width, 500 / image.height, 8)
    shown = image.resize((round(image.width * zoom), round(image.height * zoom)), Image.NEAREST)
    window = tk.Toplevel(app.root)
    window.title(f"Crop {target.name}")
    window.configure(bg=BG, padx=18, pady=16)
    window.resizable(False, False)
    window.transient(app.root)
    message = tk.StringVar(value="Pick a shape, then drag around the part to keep. Leave out the background.")
    tk.Label(window, textvariable=message, bg=BG, fg=TEXT, font=(app.font, 10)).pack(anchor="w", pady=(0, 10))
    drag = {"x": 0, "y": 0, "box": None, "points": None, "shape": app.state.prefs.get("snip_shape", "box")}

    def pick(shape):
        drag["shape"] = shape
        app.state.prefs["snip_shape"] = shape
        show_shape(shape)
        message.set("Hold the mouse and draw around the part to keep." if shape == "draw" else
                    "Drag around the part to keep. Hold Shift to keep it even, like a perfect circle.")

    bar, show_shape = shape_bar(window, drag["shape"], pick)
    bar.pack(anchor="w", pady=(0, 10))
    canvas = tk.Canvas(window, width=shown.width, height=shown.height, highlightthickness=0, cursor="crosshair",
                       bg=FIELD)
    canvas.pack()
    canvas.photo = ImageTk.PhotoImage(see_through(shown))
    canvas.create_image(0, 0, image=canvas.photo, anchor="nw")

    def clamp(event):
        return min(max(event.x, 0), shown.width), min(max(event.y, 0), shown.height)

    def press(event):
        drag["x"], drag["y"] = clamp(event)
        drag["points"] = None
        if drag["box"]:
            canvas.delete(drag["box"])
        drag["box"] = None
        if drag["shape"] == "draw":
            drag["points"] = [clamp(event)]

    def motion(event):
        end = clamp(event)
        if drag["shape"] == "draw":
            last = drag["points"][-1]
            if abs(end[0] - last[0]) + abs(end[1] - last[1]) >= 3:
                drag["points"].append(end)
        else:
            drag["points"] = drag_outline(drag["shape"], (drag["x"], drag["y"]), end, bool(event.state & 0x0001))
        if drag["points"]:
            if drag["box"]:
                canvas.coords(drag["box"], *flat(drag["points"]))
            else:
                drag["box"] = canvas.create_polygon(*flat(drag["points"]), outline=GLASS, fill="", width=2)

    def apply():
        if not drag["points"] or len(drag["points"]) < 3:
            message.set("Drag around the part to keep first.")
            return
        piece, box = cut_shape(image, [(x / zoom, y / zoom) for x, y in drag["points"]])
        if piece is None:
            message.set(box)
            return
        filename = save_image(piece, target.name)
        set_file(target, filename, target.uncropped or target.file)
        app.log(f"Cropped {target.name}. Press {app.hotkey_name('test')} while it's on screen to check the match.")
        window.destroy()

    def reset():
        set_file(target, target.uncropped, "")
        app.log(f"Put {target.name} back to its original image.")
        window.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", motion)
    buttons = ttk.Frame(window, style="Bg.TFrame")
    buttons.pack(fill="x", pady=(14, 0))
    ttk.Button(buttons, text="Apply crop", style="Accent.TButton", command=apply).pack(side="left")
    if target.uncropped:
        ttk.Button(buttons, text="Reset to original", command=reset).pack(side="left", padx=6)
    ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right")


def card(parent, title=None, hint=None, padding=16):
    frame = ttk.Frame(parent, style="Card.TFrame", padding=padding)
    frame.columnconfigure(0, weight=1)
    if title:
        ttk.Label(frame, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
    if hint:
        label = ttk.Label(frame, text=hint, style="Muted.TLabel", wraplength=560, justify="left")
        label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        # wrap to the card's real width, so the text never runs off the edge
        frame.bind("<Configure>", lambda e: label.configure(wraplength=max(e.width - 2 * padding - 4, 160)), add="+")
    return frame


class KeyBox(ttk.Combobox):
    """Looks like a dropdown, opens the keyboard picker. The variable holds the key's saved name."""

    def __init__(self, parent, app, var, kind, specials=(), title="Choose a key", **options):
        self.shown = tk.StringVar()
        super().__init__(parent, textvariable=self.shown, state="readonly", values=(), **options)
        self.app, self.var, self.kind, self.specials, self.title = app, var, kind, specials, title
        self.watch = var.trace_add("write", lambda *_a: self.refresh())
        self.bind("<Destroy>", self._forget, add="+")
        for sequence in ("<Button-1>", "<space>", "<Down>"):
            self.bind(sequence, self.open)
        self.bind("<MouseWheel>", lambda _e: "break")
        self.refresh()

    def _forget(self, event):
        if event.widget is self:
            try:
                self.var.trace_remove("write", self.watch)
            except (tk.TclError, ValueError):
                pass

    def choices(self):
        return self.specials() if callable(self.specials) else tuple(self.specials)

    def refresh(self):
        name = self.var.get()
        self.shown.set(next((text for value, text in self.choices() if value == name), None) or
                       key_label(name, self.kind))

    def open(self, _event=None):
        picker = self.app.pick_mouse if self.kind == "mouse" else self.app.pick_key
        picker(self.kind, self.var.get(), self.var.set, self.choices(), self.title)
        return "break"


class NavItem:
    """One entry in the left sidebar."""

    def __init__(self, parent, icon, text, command, icon_font, font):
        self.frame = ttk.Frame(parent, style="Nav.TFrame", cursor="hand2", padding=(12, 9))
        self.icon = tk.Label(self.frame, text=icon, font=icon_font, bg=SIDEBAR, fg=MUTED, width=2)
        self.icon.pack(side="left", padx=(2, 10))
        self.label = tk.Label(self.frame, text=text, font=font, bg=SIDEBAR, fg=MUTED, anchor="w")
        self.label.pack(side="left", fill="x", expand=True)
        self.badge = tk.Label(self.frame, text="", font=(font[0], 7), bg=SIDEBAR, fg=GLASS)  # the mode Start runs
        self.badge.pack(side="right", padx=(0, 14))
        self.selected = False
        for widget in (self.frame, self.icon, self.label, self.badge):
            widget.bind("<Button-1>", lambda _e: command())
            widget.bind("<Enter>", lambda _e: self._paint(hover=True))
            widget.bind("<Leave>", lambda _e: self._paint())

    def set(self, selected):
        self.selected = selected
        self._paint()

    def _paint(self, hover=False):
        bg = NAV_ON if self.selected else HOVER if hover else SIDEBAR
        self.frame.configure(style="NavOn.TFrame" if self.selected else "NavHover.TFrame" if hover else "Nav.TFrame")
        for widget in (self.icon, self.label, self.badge):
            widget.configure(bg=bg)
        self.icon.configure(fg=GLASS if self.selected else TEXT if hover else MUTED)
        self.label.configure(fg=TEXT if self.selected or hover else MUTED)


class ImageList:
    """A list of images (Image mode's two tabs, or Reaction mode), plus the editor for the selected one.

    kind is "farm", "reconnect", "reaction" or "watch" (Autoclicker Smart stop).
    """

    def __init__(self, app, items, parent, editor_parent, tab, title, hint, kind="farm"):
        self.app = app
        self.items = items
        self.kind = kind
        self.sequence = kind == "reconnect"
        self.thumbs = {}
        self.loading = False
        self.wait_attr = "timeout" if self.sequence else "cooldown"
        self._build_panel(parent, tab, title, hint)
        self._build_editor(editor_parent)
        self.reload()

    _save_image = staticmethod(save_image)

    def reload(self):
        """Redraw the list from scratch after a different profile is loaded."""
        self.tree.delete(*self.tree.get_children())
        self.thumbs.clear()
        for target in self.items:
            self._insert_row(target)
        self.apply_order_view()
        if self.items:
            self.tree.selection_set(self.items[0].id)
        self.load_editor()

    def _build_panel(self, parent, tab, title, hint):
        app = self.app
        panel = card(parent, title, hint)
        if isinstance(parent, ttk.Notebook):
            parent.add(panel, text=tab)
        else:
            panel.grid(row=0, column=0, sticky="nsew")
        panel.rowconfigure(2, weight=1)
        self.hint = hint
        self.hint_label = panel.grid_slaves(row=1, column=0)[0]
        if self.kind == "reconnect":
            ttk.Checkbutton(panel, text="Recovery on", variable=app.v_reconnect).grid(row=0, column=0, sticky="e")
        elif self.kind == "farm":
            order = ttk.Frame(panel, style="Inner.TFrame")
            order.grid(row=0, column=0, sticky="e")
            ttk.Checkbutton(order, text="Click in order", variable=app.v_order).pack(side="left")
            ttk.Label(order, text="Restart if stuck (s)", style="Muted.TLabel").pack(side="left", padx=(14, 6))
            ttk.Spinbox(order, from_=1, to=600, increment=1, width=5, textvariable=app.v_order_timeout).pack(side="left")
        holder = ttk.Frame(panel, style="Inner.TFrame")
        holder.grid(row=2, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, columns=("step", "on", "pri", "key", "name", "score"),
                                 show="tree headings", selectmode="extended")
        for column, text, width in (("step", "Step", 50), ("on", "On", 44), ("pri", "Priority", 66),
                                    ("key", "Key", 70), ("score", "Match", 70)):
            self.tree.heading(column, text=text)
            self.tree.column(column, width=width, stretch=False, anchor="center")
        self.tree.heading("#0", text="Image")
        self.tree.column("#0", width=96, stretch=False, anchor="center")
        if self.kind == "watch":
            self.tree.heading("key", text="When seen")
            self.tree.column("key", width=96)
        self.tree.heading("name", text="Name", anchor="w")
        self.tree.column("name", width=200, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.load_editor())
        self.tree.bind("<Button-1>", self._tree_click)

        bar = ttk.Frame(panel, style="Inner.TFrame")
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        bar.columnconfigure(1, weight=1)
        adding = ttk.Frame(bar, style="Inner.TFrame")
        adding.grid(row=0, column=0, sticky="w")
        self.snip_button = ttk.Button(adding, text="+ Snip", style="Accent.TButton", command=app.snip)
        self.snip_button.pack(side="left")
        ttk.Button(adding, text="Add files", style="Small.TButton", command=app.add_files).pack(side="left", padx=5)
        ttk.Button(adding, text="Paste", style="Small.TButton", command=app.paste).pack(side="left")
        ttk.Button(adding, text="Test", style="Small.TButton", command=app.engine.test_scan).pack(side="left", padx=5)
        editing = ttk.Frame(bar, style="Inner.TFrame")
        ttk.Button(editing, text="Remove all", style="Danger.TButton", command=self.remove_all).pack(side="right")
        ttk.Button(editing, text="Remove", style="Danger.TButton", command=self.remove).pack(side="right", padx=(0, 5))
        ttk.Button(editing, text="↓", width=2, style="Small.TButton", command=lambda: self.move(1)).pack(
            side="right", padx=5)
        ttk.Button(editing, text="↑", width=2, style="Small.TButton", command=lambda: self.move(-1)).pack(side="right")

        def fit(_event=None):
            """Side by side when there's room, otherwise the editing buttons go on a second line."""
            room = panel.winfo_width() - 40
            two_lines = adding.winfo_reqwidth() + editing.winfo_reqwidth() + 12 > room
            where = {"row": 1, "column": 0, "columnspan": 2, "pady": (8, 0)} if two_lines else                 {"row": 0, "column": 2, "columnspan": 1, "pady": 0}
            if editing.grid_info().get("row") != where["row"]:
                editing.grid(sticky="e", **where)
        editing.grid(row=0, column=2, sticky="e")
        panel.bind("<Configure>", fit, add="+")

    def _build_editor(self, editor_parent):
        app = self.app
        self.editor = card(editor_parent)
        self.editor.grid(row=0, column=0, sticky="new")
        self.editor.columnconfigure(1, weight=1)
        heading = {"reconnect": "Selected step", "reaction": "Selected trigger"}.get(self.kind, "Selected image")
        ttk.Label(self.editor, text=heading, style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.preview = ttk.Label(self.editor, text="Nothing selected", style="Preview.TLabel", anchor="center")
        self.preview.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8, ipady=6)
        self.v_name, self.v_thr = tk.StringVar(), tk.DoubleVar(value=0.85)
        self.v_wait, self.v_delay = tk.StringVar(), tk.StringVar()
        self.v_key, self.v_hold, self.v_when = tk.StringVar(), tk.StringVar(), tk.StringVar()
        app._field(self.editor, 2, "Name", lambda p: ttk.Entry(p, textvariable=self.v_name))
        self.thr_text = tk.StringVar(value="Min match 85%")
        ttk.Label(self.editor, textvariable=self.thr_text).grid(row=3, column=0, sticky="w", pady=4)
        ttk.Scale(self.editor, from_=0.3, to=1.0, variable=self.v_thr).grid(row=3, column=1, sticky="ew")
        row = 4
        if self.kind == "reaction":
            app._field(self.editor, row, "Key to press", lambda p: KeyBox(
                p, app, self.v_key, "press", title="Key to press"))
            app._field(self.editor, row + 1, "Hold key (ms)", lambda p: ttk.Spinbox(
                p, from_=10, to=5000, increment=10, textvariable=self.v_hold))
            row += 2
        if self.kind == "watch":
            app._field(self.editor, row, "When it's seen", lambda p: ttk.Combobox(
                p, textvariable=self.v_when, values=list(WATCH_ACTIONS), state="readonly"))
            ttk.Label(self.editor, text="Use \"Only click while\" for something like a Collect button that comes "
                                        "and goes.", style="Muted.TLabel", wraplength=290, justify="left").grid(
                row=row + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        else:
            wait_label = {"reconnect": "Give up after (s)", "reaction": "Wait before re-press (s)"}.get(
                self.kind, "Wait before reclick (s)")
            limit = 3600 if self.sequence else 120
            step_size = 5 if self.sequence else 0.5
            app._field(self.editor, row, wait_label,
                       lambda p: ttk.Spinbox(p, from_=0, to=limit, increment=step_size, textvariable=self.v_wait))
            app._field(self.editor, row + 1,
                       "Pause after press (s)" if self.kind == "reaction" else "Pause after click (s)",
                       lambda p: ttk.Spinbox(p, from_=0, to=30, increment=0.05, textvariable=self.v_delay))
        tools = ttk.Frame(self.editor, style="Inner.TFrame")
        tools.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(tools, text="Crop image", command=self.open_crop).pack(side="left", fill="x", expand=True)
        ttk.Button(tools, text="Merge selected", command=self.merge_selected).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        for var in (self.v_name, self.v_thr, self.v_wait, self.v_delay, self.v_key, self.v_hold, self.v_when):
            var.trace_add("write", self._editor_changed)

    def selected(self):
        chosen = self.tree.selection()
        return next((t for t in self.items if chosen and t.id == chosen[0]), None)

    def next_name(self):
        word = {"reconnect": "Step", "reaction": "Trigger", "watch": "Watch"}.get(self.kind, "Snip")
        return f"{word} {len(self.items) + 1}"

    def _thumbnail(self, target, box):
        return thumbnail(target, box)

    def _insert_row(self, target):
        thumb = self._thumbnail(target, (80, 42))
        self.thumbs[target.id] = thumb
        row = {"on": "✓" if target.enabled else "", "pri": "★" if target.priority else "", "name": target.name,
               "key": WATCH_SHORT.get(target.watch, "Stop") if self.kind == "watch" else target.key.upper(),
               "score": "-", "step": ""}
        self.tree.insert("", "end", iid=target.id, image=thumb or "",
                         values=[row[column] for column in self.tree["columns"]])

    @property
    def ordered(self):
        return self.sequence or (self.kind == "farm" and bool(self.app.v_order.get()))

    @property
    def shown_columns(self):
        """Step only when order matters; Priority only on the farm list when it isn't clicking in order."""
        if self.kind in ("reaction", "watch"):
            return ("on", "key", "name", "score")
        if self.ordered:
            return ("step", "on", "name", "score")
        return ("on", "pri", "name", "score")

    def _column_at(self, x):
        index = self.tree.identify_column(x)  # "#1" is the first shown column after the picture
        shown = self.shown_columns
        return shown[int(index[1:]) - 1] if index.startswith("#") and 0 < int(index[1:] or 0) <= len(shown) else None

    def apply_order_view(self):
        """Show the Step column only when this list's order actually matters."""
        self.tree["displaycolumns"] = self.shown_columns
        if self.kind == "farm":
            ordered_hint = "Clicked one at a time in this order, then it starts over."
            self.hint_label.configure(text=ordered_hint if self.ordered else self.hint)
        self.renumber()

    def renumber(self):
        number = 0
        for target in self.items:
            number += target.enabled
            self.tree.set(target.id, "step", number if target.enabled else "")

    def _set_file(self, target, filename, uncropped):
        target.file, target.uncropped = filename, uncropped
        thumb = self._thumbnail(target, (80, 42))
        self.thumbs[target.id] = thumb
        self.tree.item(target.id, image=thumb or "")
        self.app.engine.matchers.pop(filename, None)
        self.load_editor()
        self.app.state.save()

    def open_crop(self):
        target = self.selected()
        if not target:
            self.app.log("Select an image to crop first.")
            return
        crop_window(self.app, target, self._set_file)

    def merge_selected(self):
        """Replace several snips of one button with an outline that ignores the map behind it."""
        chosen = set(self.tree.selection())
        group = [t for t in self.items if t.id in chosen]
        if len(group) < 3:
            self.app.log("To merge: Ctrl-click 3 or more snips of the same button, taken on different backgrounds.")
            return
        images = []
        for target in group:
            try:
                images.append(read_image(IMAGES / target.file))
                if read_mask(IMAGES / target.file) is not None:
                    self.app.log(f"{target.name} is already an outline, so select only plain snips.")
                    return
            except (OSError, ValueError):
                self.app.log(f"Can't merge {target.name}: its image file is missing or unreadable.")
                return
        try:
            pixels, mask = learn_outline(images, [t.name for t in group])
        except ValueError as problem:
            self.app.log(f"Couldn't merge these: {problem}")
            return
        first = group[0]
        name = f"{first.name} outline"
        filename = save_image(Image.fromarray(np.dstack([pixels[..., ::-1], mask])), name)
        merged = Target(name=name, file=filename, cooldown=first.cooldown, delay_after=first.delay_after,
                        timeout=first.timeout, key=first.key, hold_ms=first.hold_ms, watch=first.watch)
        self.items.insert(self.items.index(first), merged)
        for target in group:
            target.enabled = False
        self.reload()
        self.tree.selection_set(merged.id)
        self.tree.see(merged.id)
        self.load_editor()
        self.app.state.save()
        self.app.log(f"Merged {len(group)} snips into {name} and turned the originals off. "
                     f"Press {self.app.hotkey_name('test')} while the button is on screen to check it.")

    def add(self, image, name):
        filename = save_image(image, name)
        if self.kind == "reaction":
            target = Target(name=name, file=filename, cooldown=1.0, delay_after=0.0)
        elif self.kind == "watch":
            target = Target(name=name, file=filename, cooldown=0.0, delay_after=0.0)
            self.app.ac_tabs.select(self.app.ac_tab_frames["watch"])
        else:
            target = Target(name=name, file=filename, delay_after=1.0 if self.sequence else 0.25)
        self.items.append(target)
        self._insert_row(target)
        self.renumber()
        self.tree.selection_set(target.id)
        self.tree.see(target.id)
        self.app.state.save()
        where = {"reconnect": " to Recovery steps", "reaction": " to Reaction", "watch": " to Smart stop"}.get(self.kind, "")
        extra = {"reaction": " Pick the key it should press on the right.",
                 "watch": " Pick what happens when it's seen on the right."}.get(self.kind, "")
        self.app.log(f"Added {name}{where}. Press {self.app.hotkey_name('test')} while it's on screen to check "
                     f"how well it matches.{extra}")

    def remove_all(self):
        if not self.items:
            return
        what = {"reconnect": "recovery step", "reaction": "trigger", "watch": "Smart stop image"}.get(self.kind, "image")
        count = len(self.items)
        if not messagebox.askyesno(APP_NAME, f"Remove all {count} {what}{'s' if count != 1 else ''} from this list? "
                                             "This can't be undone.", parent=self.app.root):
            return
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.thumbs.clear()
        self.renumber()
        self.load_editor()
        self.app.state.save()
        self.app.log(f"Removed all {count} {what}{'s' if count != 1 else ''}.")

    def remove(self):
        target = self.selected()
        if not target:
            return
        self.items.remove(target)
        self.tree.delete(target.id)
        self.thumbs.pop(target.id, None)
        self.renumber()
        self.load_editor()
        self.app.state.save()

    def move(self, step):
        target = self.selected()
        if not target:
            return
        old = self.items.index(target)
        new = old + step
        if 0 <= new < len(self.items):
            self.items[old], self.items[new] = self.items[new], self.items[old]
            self.tree.move(target.id, "", new)
            self.renumber()
            self.app.state.save()

    def _tree_click(self, event):
        row = self.tree.identify_row(event.y)
        column = self._column_at(event.x) if row else None
        if column == "on":
            target = next(t for t in self.items if t.id == row)
            target.enabled = not target.enabled
            self.tree.set(row, "on", "✓" if target.enabled else "")
            self.renumber()
            self.app.state.save()
        elif column == "pri":
            target = next(t for t in self.items if t.id == row)
            target.priority = not target.priority
            self.tree.set(row, "pri", "★" if target.priority else "")
            self.app.state.save()

    def load_editor(self):
        target = self.selected()
        self.loading = True
        if target:
            self.v_name.set(target.name)
            self.v_thr.set(target.threshold)
            self.v_wait.set(str(getattr(target, self.wait_attr)))
            self.v_delay.set(str(target.delay_after))
            self.v_key.set(target.key)
            self.v_hold.set(str(target.hold_ms))
            self.v_when.set(target.watch)
            self.thr_text.set(f"Min match {target.threshold:.0%}")
            photo = self._thumbnail(target, (300, 90))
            self.preview.configure(image=photo or "", text="" if photo else "Image file missing")
            self.preview.image = photo
        else:
            for var in (self.v_name, self.v_wait, self.v_delay, self.v_key, self.v_hold, self.v_when):
                var.set("")
            self.preview.configure(image="", text="Nothing selected")
            self.preview.image = None
        self.loading = False

    def _editor_changed(self, *_):
        target = self.selected()
        if self.loading or not target:
            return
        target.name = self.v_name.get().strip() or target.name
        target.threshold = round(float(self.v_thr.get()), 2)
        setattr(target, self.wait_attr, parse_number(self.v_wait.get(), getattr(target, self.wait_attr)))
        target.delay_after = parse_number(self.v_delay.get(), target.delay_after)
        if self.kind == "reaction":
            target.key = self.v_key.get() if self.v_key.get() in SCAN_CODES else target.key
            target.hold_ms = int(parse_number(self.v_hold.get(), target.hold_ms, 10))
            self.tree.set(target.id, "key", target.key.upper())
        if self.kind == "watch" and self.v_when.get() in WATCH_ACTIONS:
            target.watch = self.v_when.get()
            self.tree.set(target.id, "key", WATCH_SHORT[target.watch])
        self.thr_text.set(f"Min match {target.threshold:.0%}")
        self.tree.set(target.id, "name", target.name)
        self.app.state.save()


class ActionList:
    """The steps of a Classic or Hybrid macro, plus the editor for the selected step."""

    def __init__(self, app, items, parent, editor_parent, mode):
        self.app = app
        self.items = items
        self.mode = mode
        self.hybrid = mode == "hybrid"
        self.pending = None  # what the next snip is for: a new image step, or "replace" for the selected one
        self.loading = False
        self.keep = []  # variables and pictures of the editor, kept alive while it is shown
        self.thumbs = {}
        self._build_panel(parent)
        self._build_editor(editor_parent)
        self.reload()

    def _build_panel(self, parent):
        app = self.app
        if self.hybrid:
            hint = ("Played top to bottom, then it starts over. Image steps wait until the image shows up "
                    "(or give up after their time limit).")
        else:
            hint = ("Played top to bottom, then it starts over. Hit Record, play normally, then stop: every key "
                    "press, click and scroll is saved with its exact spot and timing.")
        panel = card(parent, "Steps", hint)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.rowconfigure(3, weight=1)
        adds = ttk.Frame(panel, style="Inner.TFrame")
        adds.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        rows = [[("+ Key", "key"), ("+ Click", "click"), ("+ Scroll", "scroll"), ("+ Wait", "wait")]]
        if self.hybrid:  # on their own row, so all of them still fit in the smallest window
            rows.append([("+ Click image", "click_image"), ("+ Wait for image", "wait_image")])
        for number, row in enumerate(rows):
            line = ttk.Frame(adds, style="Inner.TFrame")
            line.pack(anchor="w", pady=(6 if number else 0, 0))
            for index, (text, kind) in enumerate(row):
                ttk.Button(line, text=text, style="Accent.TButton" if index == number == 0 else "TButton",
                           command=lambda k=kind: self.add_step(k)).pack(side="left", padx=(0 if index == 0 else 6, 0))
        self.record_button = ttk.Button(panel, text="", style="Record.TButton", command=app.toggle_recording)
        self.record_button.grid(row=0, column=0, sticky="e")
        self.show_recording(False)
        holder = ttk.Frame(panel, style="Inner.TFrame")
        holder.grid(row=3, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(holder, columns=("num", "on", "what"), show="headings", style="Steps.Treeview",
                                 selectmode="browse")
        self.tree.heading("num", text="#")
        self.tree.heading("on", text="On")
        self.tree.heading("what", text="Step", anchor="w")
        self.tree.column("num", width=44, stretch=False, anchor="center")
        self.tree.column("on", width=44, stretch=False, anchor="center")
        self.tree.column("what", width=300, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.load_editor())
        self.tree.bind("<Button-1>", self._tree_click)
        bar = ttk.Frame(panel, style="Inner.TFrame")
        bar.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        if self.hybrid:
            ttk.Button(bar, text="Test", command=app.engine.test_scan).pack(side="left")
        ttk.Button(bar, text="Duplicate", command=self.duplicate).pack(side="left", padx=(6 if self.hybrid else 0, 0))
        ttk.Button(bar, text="Remove all", style="Danger.TButton", command=self.remove_all).pack(side="right")
        ttk.Button(bar, text="Remove", style="Danger.TButton", command=self.remove).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="↓", width=3, style="Small.TButton", command=lambda: self.move(1)).pack(
            side="right", padx=6)
        ttk.Button(bar, text="↑", width=3, style="Small.TButton", command=lambda: self.move(-1)).pack(side="right")

    def _build_editor(self, editor_parent):
        self.editor = card(editor_parent, "Selected step")
        self.editor.grid(row=0, column=0, sticky="new")
        self.fields = ttk.Frame(self.editor, style="Inner.TFrame")
        self.fields.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.fields.columnconfigure(1, weight=1)

    # ---- list
    def reload(self):
        self.tree.delete(*self.tree.get_children())
        for step in self.items:
            self.tree.insert("", "end", iid=step.id, values=("", "✓" if step.enabled else "", step.describe()))
        self.renumber()
        if self.items:
            self.tree.selection_set(self.items[0].id)
        self.load_editor()

    def renumber(self):
        number = 0
        for step in self.items:
            usable = step.enabled and (self.hybrid or step.kind not in IMAGE_ACTIONS)
            number += usable
            self.tree.set(step.id, "num", number if usable else "")

    def selected(self):
        chosen = self.tree.selection()
        return next((s for s in self.items if chosen and s.id == chosen[0]), None)

    def next_name(self):
        return f"Image {sum(s.kind in IMAGE_ACTIONS for s in self.items) + 1}"

    def _insert(self, step):
        current = self.selected()
        index = self.items.index(current) + 1 if current else len(self.items)
        self.items.insert(index, step)
        self.tree.insert("", index, iid=step.id, values=("", "✓", step.describe()))
        self.renumber()
        self.tree.selection_set(step.id)
        self.tree.see(step.id)
        self.app.state.save()

    def add_step(self, kind):
        if kind in IMAGE_ACTIONS:
            self.pending = kind
            self.app.snip()
            return
        step = Action(kind=kind)
        if kind in ("click", "scroll"):
            step.x, step.y = self.app.cursor_on_screen()
        self._insert(step)
        if kind == "click":
            self.app.log("Added a click where your mouse is. Use Pick spot to choose another place.")

    def add(self, image, name):
        """A snipped, pasted or opened image: a new image step, or the new picture for the selected one."""
        if not self.hybrid:
            self.app.log("Classic macros don't use images. Use Hybrid mode to mix in image steps.")
            return
        pending, self.pending = self.pending, None
        filename = save_image(image, name)
        step = self.selected()
        if pending == "replace" and step and step.kind in IMAGE_ACTIONS:
            step.file, step.uncropped = filename, ""
            self.app.engine.matchers.pop(filename, None)
            self.tree.set(step.id, "what", step.describe())
            self.load_editor()
            self.app.state.save()
            self.app.log(f"New picture for {step.name}.")
            return
        kind = pending if pending in IMAGE_ACTIONS else "click_image"
        self._insert(Action(kind=kind, name=name, file=filename))
        self.app.log(f"Added {ACTION_KINDS[kind].lower()} step {name}. "
                     f"Press {self.app.hotkey_name('test')} while it's on screen to check the match.")

    def show_recording(self, recording):
        keys = self.app.state.hotkeys
        key = keys.get("record", "None")
        snips_here = self.hybrid and key == keys.get("snip")  # in Hybrid a shared key snips instead
        hint = "" if key == "None" or snips_here else f"  ({key})"
        self.record_button.configure(text=("■  Stop recording" if recording else "●  Record") + hint)

    def add_recorded(self, steps):
        """Recorded steps go after the selected step, or at the end."""
        current = self.selected()
        index = self.items.index(current) + 1 if current else len(self.items)
        self.items[index:index] = steps
        self.reload()
        self.tree.selection_set(steps[0].id)
        self.tree.see(steps[-1].id)
        self.tree.see(steps[0].id)
        self.app.state.save()

    def duplicate(self):
        step = self.selected()
        if step:
            self._insert(Action(**{**asdict(step), "id": uuid.uuid4().hex[:10]}))

    def remove_all(self):
        if not self.items:
            return
        count = len(self.items)
        if not messagebox.askyesno(APP_NAME, f"Remove all {count} step{'s' if count != 1 else ''}? This can't be "
                                             "undone.", parent=self.app.root):
            return
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.renumber()
        self.load_editor()
        self.app.state.save()
        self.app.log(f"Removed all {count} step{'s' if count != 1 else ''}.")

    def remove(self):
        step = self.selected()
        if not step:
            return
        index = self.items.index(step)
        self.items.remove(step)
        self.tree.delete(step.id)
        self.renumber()
        if self.items:
            self.tree.selection_set(self.items[min(index, len(self.items) - 1)].id)
        self.load_editor()
        self.app.state.save()

    def move(self, offset):
        step = self.selected()
        if not step:
            return
        old = self.items.index(step)
        new = old + offset
        if 0 <= new < len(self.items):
            self.items[old], self.items[new] = self.items[new], self.items[old]
            self.tree.move(step.id, "", new)
            self.renumber()
            self.app.state.save()

    def _tree_click(self, event):
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_column(event.x) == "#2":
            step = next(s for s in self.items if s.id == row)
            step.enabled = not step.enabled
            self.tree.set(row, "on", "✓" if step.enabled else "")
            self.renumber()
            self.app.state.save()

    # ---- editor
    def _var(self, step, attr, variable=tk.StringVar):
        initial = getattr(step, attr)
        var = variable(value=f"{initial:g}" if variable is tk.StringVar and isinstance(initial, float) else initial)
        self.keep.append(var)

        def changed(*_):
            if self.loading:
                return
            current = getattr(step, attr)
            try:
                raw = var.get()
            except (tk.TclError, ValueError):
                return
            if isinstance(current, float):
                value = round(parse_number(raw, current), 2)
            elif isinstance(current, int):
                value = int(parse_number(raw, current))
            else:
                value = str(raw).strip() or current
            setattr(step, attr, value)
            if self.tree.exists(step.id):
                self.tree.set(step.id, "what", step.describe())
            if attr == "threshold":
                self.thr_text.set(f"Min match {value:.0%}")
            if attr == "file":
                self.app.engine.matchers.pop(value, None)
            if attr == "key":  # a key picked from the list replaces the exact recorded one
                step.scan, step.extended = 0, False
            self.app.state.save()

        var.trace_add("write", changed)
        return var

    def _row(self, row, label, factory):
        return self.app._field(self.fields, row, label, factory)

    def load_editor(self):
        for child in self.fields.winfo_children():
            child.destroy()
        self.keep = []
        step = self.selected()
        self.loading = True
        try:
            if step is None:
                ttk.Label(self.fields, text="Nothing selected. Add a step on the left.", style="Muted.TLabel").grid(
                    row=0, column=0, columnspan=2, sticky="w")
                return
            ttk.Label(self.fields, text=ACTION_KINDS[step.kind], style="Chip.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
            if step.kind in KEY_ACTIONS:
                key = self._var(step, "key")
                self._row(1, "Key", lambda p: KeyBox(p, self.app, key, "press", title="Key to press"))
                if step.kind == "key":
                    hold = self._var(step, "hold_ms")
                    self._row(2, "Hold (ms)", lambda p: ttk.Spinbox(
                        p, from_=1, to=60000, increment=10, textvariable=hold))
            elif step.kind in MOUSE_ACTIONS:
                button = self._var(step, "button")
                self._row(1, "Button", lambda p: KeyBox(p, self.app, button, "mouse", title="Mouse button"))
                x, y = self._var(step, "x"), self._var(step, "y")
                self._row(2, "X", lambda p: ttk.Spinbox(p, from_=-10000, to=10000, increment=1, textvariable=x))
                self._row(3, "Y", lambda p: ttk.Spinbox(p, from_=-10000, to=10000, increment=1, textvariable=y))
                if step.kind == "click":
                    hold = self._var(step, "hold_ms")
                    self._row(5, "Hold (ms)", lambda p: ttk.Spinbox(
                        p, from_=1, to=60000, increment=10, textvariable=hold))
                ttk.Button(self.fields, text="Pick spot on screen", style="Accent.TButton",
                           command=lambda: self.pick_spot(step)).grid(row=4, column=0, columnspan=2, sticky="ew",
                                                                      pady=(10, 0))
            elif step.kind == "scroll":
                direction = self._var(step, "direction")
                self._row(1, "Direction", lambda p: ttk.Combobox(
                    p, textvariable=direction, values=list(SCROLL_DIRECTIONS), state="readonly"))
                notches = self._var(step, "notches")
                self._row(2, "Notches", lambda p: ttk.Spinbox(
                    p, from_=0.25, to=1000, increment=1, textvariable=notches))
                spread = self._var(step, "spread_ms")
                self._row(3, "Spread over (ms)", lambda p: ttk.Spinbox(
                    p, from_=0, to=60000, increment=50, textvariable=spread))
                where = self._var(step, "scroll_at")
                self._row(4, "Scroll at", lambda p: ttk.Combobox(
                    p, textvariable=where, values=[SCROLL_AT_MOUSE, SCROLL_AT_SPOT], state="readonly")).bind(
                    "<<ComboboxSelected>>", lambda _e: self.app.root.after_idle(self.load_editor))
                if step.scroll_at == SCROLL_AT_SPOT:
                    x, y = self._var(step, "x"), self._var(step, "y")
                    self._row(5, "X", lambda p: ttk.Spinbox(p, from_=-10000, to=10000, increment=1, textvariable=x))
                    self._row(6, "Y", lambda p: ttk.Spinbox(p, from_=-10000, to=10000, increment=1, textvariable=y))
                    ttk.Button(self.fields, text="Pick spot on screen", style="Accent.TButton",
                               command=lambda: self.pick_spot(step)).grid(row=7, column=0, columnspan=2, sticky="ew",
                                                                          pady=(10, 0))
            elif step.kind == "wait":
                low, high = self._var(step, "ms"), self._var(step, "ms_max")
                self._row(1, "Wait at least (ms)", lambda p: ttk.Spinbox(
                    p, from_=0, to=600000, increment=50, textvariable=low))
                self._row(2, "Wait at most (ms)", lambda p: ttk.Spinbox(
                    p, from_=0, to=600000, increment=50, textvariable=high))
                ttk.Label(self.fields, text="A random time in between each time, so it looks less robotic.",
                          style="Muted.TLabel", wraplength=280).grid(row=3, column=0, columnspan=2, sticky="w",
                                                                    pady=(6, 0))
            else:
                photo = thumbnail(step, (300, 90))
                self.keep.append(photo)
                ttk.Label(self.fields, image=photo or "", text="" if photo else "Image file missing",
                          style="Preview.TLabel", anchor="center").grid(row=1, column=0, columnspan=2, sticky="ew",
                                                                         pady=(0, 8), ipady=6)
                name = self._var(step, "name")
                self._row(2, "Name", lambda p: ttk.Entry(p, textvariable=name))
                self.thr_text = tk.StringVar(value=f"Min match {step.threshold:.0%}")
                self.keep.append(self.thr_text)
                ttk.Label(self.fields, textvariable=self.thr_text).grid(row=3, column=0, sticky="w", pady=4)
                threshold = self._var(step, "threshold", tk.DoubleVar)
                ttk.Scale(self.fields, from_=0.3, to=1.0, variable=threshold).grid(row=3, column=1, sticky="ew")
                timeout = self._var(step, "timeout")
                self._row(4, "Give up after (s)", lambda p: ttk.Spinbox(
                    p, from_=0.5, to=3600, increment=1, textvariable=timeout))
                tools = ttk.Frame(self.fields, style="Inner.TFrame")
                tools.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
                ttk.Button(tools, text="Snip new picture", command=self.replace_image).pack(
                    side="left", fill="x", expand=True)
                ttk.Button(tools, text="Crop", command=lambda: crop_window(self.app, step, self._set_file)).pack(
                    side="left", fill="x", expand=True, padx=(6, 0))
        finally:
            self.loading = False

    def _set_file(self, step, filename, uncropped):
        step.file, step.uncropped = filename, uncropped
        self.app.engine.matchers.pop(filename, None)
        self.load_editor()
        self.app.state.save()

    def replace_image(self):
        self.pending = "replace"
        self.app.snip()

    def pick_spot(self, step):
        def chosen(x, y):
            step.x, step.y = x, y
            if self.tree.exists(step.id):
                self.tree.set(step.id, "what", step.describe())
            self.load_editor()
            self.app.state.save()
            self.app.log(f"Spot set to {x}, {y}.")
        self.app.pick_point(chosen)


class App:
    def __init__(self):
        IMAGES.mkdir(parents=True, exist_ok=True)
        self.state = State()
        self.events = queue.Queue()
        self.engine = Engine(self.state, self.events)
        self.snipping = False
        self.key_window = None  # the keyboard picker, while it's open
        self.preset_list, self.preset_loading = [], False  # what macroez.com lists
        self.recorder = None  # set while Classic/Hybrid is recording
        self.background = {}  # profile name -> the Engine running it while another profile is open
        self.closing = False
        self.showing_profile = False  # true while the settings boxes are being refilled
        self.page = self.state.settings["mode"]
        self.root = tk.Tk()
        self.root.report_callback_exception = self._callback_error
        self.root.title(APP_NAME)
        self.root.geometry("1320x880")
        self.root.minsize(1160, 780)
        self.root.configure(bg=BG)
        self._load_logo()
        self._style()
        self._build()
        threading.Thread(target=self._hotkey_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(120, self._pump)
        self.log(f"Ready. Pick a mode on the left, set it up, then press {self.hotkey_name('start')} to start.")
        self.simple_window = None
        if not self.state.prefs["asked_simple"] and not os.environ.get("MACROEZ_NO_WELCOME"):  # tests skip it
            self.root.after(500, self._simple_dialog)

    # ---- construction
    def _load_logo(self):
        self.logo_small = None
        try:
            with Image.open(LOGO) as logo:
                logo = logo.convert("RGBA")
                self.window_icon = ImageTk.PhotoImage(logo.resize((64, 64), Image.LANCZOS))
                self.logo_small = ImageTk.PhotoImage(logo.resize((34, 34), Image.LANCZOS))
            self.root.iconphoto(True, self.window_icon)
        except (OSError, ValueError):
            pass

    def _round(self, width, height, radius, fill, bg, outline=None, top_only=False, gradient=None, knob=None):
        """A smooth rounded shape as an image, drawn 4x bigger and shrunk so the edges are anti-aliased."""
        key = (width, height, radius, fill, bg, outline, top_only, gradient, knob)
        if key in self._shapes:
            return self._shapes[key]
        corner = radius * 2 + 4
        if not gradient and not knob and (width > corner or height > corner):
            # Draw only a small piece carefully, then stretch its plain middle: same look, far less work.
            small = ImageTk.getimage(self._round(min(width, corner), min(height, corner), radius, fill, bg,
                                                 outline, top_only)).convert("RGB")
            sw, sh = small.size
            mx, my = sw // 2, sh // 2
            out = Image.new("RGB", (width, height))
            cols = [(0, mx, 0, mx), (mx, mx + 1, mx, width - (sw - mx - 1)), (mx + 1, sw, width - (sw - mx - 1), width)]
            rows = [(0, my, 0, my), (my, my + 1, my, height - (sh - my - 1)), (my + 1, sh, height - (sh - my - 1), height)]
            for x0, x1, dx0, dx1 in cols:
                for y0, y1, dy0, dy1 in rows:
                    if x1 > x0 and y1 > y0 and dx1 > dx0 and dy1 > dy0:
                        out.paste(small.crop((x0, y0, x1, y1)).resize((dx1 - dx0, dy1 - dy0), Image.NEAREST),
                                  (dx0, dy0))
            image = ImageTk.PhotoImage(out)
            self._shapes[key] = image
            return image
        scale = 4
        big = Image.new("RGBA", (width * scale, height * scale), bg)
        shape = Image.new("RGBA", big.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(shape)
        box = (0, 0, width * scale - 1, height * scale - 1 + (radius * scale if top_only else 0))
        if gradient:  # left-to-right colour blend, clipped to the rounded shape
            mask = Image.new("L", big.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(box, radius * scale, fill=255)
            start, end = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in gradient)
            ramp = Image.new("RGBA", big.size)
            for x in range(big.width):
                t = x / max(big.width - 1, 1)
                ImageDraw.Draw(ramp).line([(x, 0), (x, big.height)],
                                          fill=tuple(round(a + (b - a) * t) for a, b in zip(start, end)) + (255,))
            shape.paste(ramp, (0, 0), mask)
        else:
            draw.rounded_rectangle(box, radius * scale, fill=fill,
                                   outline=outline, width=scale if outline else 0)
        if knob:  # toggle switch: (x centre as a fraction, knob colour)
            where, colour = knob
            d = (height - 6) * scale
            cx = int((width * scale - height * scale) * where + height * scale / 2)
            cy = height * scale // 2
            draw.ellipse((cx - d // 2, cy - d // 2, cx + d // 2, cy + d // 2), fill=colour)
        big.alpha_composite(shape)
        image = ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS).convert("RGB"))
        self._shapes[key] = image
        return image

    def _image_element(self, style, name, radius, states, size=None, padding=None):
        """states: [(state spec or "", image args...)] -> a stretchable rounded element called name."""
        # Tk fills big areas by repeating the middle of the image, so a large middle means few, fast draws
        side = size or (radius * 2 + 240, radius * 2 + 120)
        images = [self._round(side[0], side[1], radius, *args) for _spec, args in states]
        spec = [images[0]]
        for (state, _args), image in zip(states[1:], images[1:]):
            spec.append((state, image))
        small = radius * 2 + 4  # the size a control asks for; the bigger image only makes filling faster
        style.element_create(name, "image", *spec, border=radius + 1, sticky="nsew", width=min(small, side[0]),
                             height=min(small, side[1]), padding=padding if padding is not None else radius // 2 + 2)

    def _style(self):
        families = set(tkfont.families(self.root))
        pick = lambda *names: next((n for n in names if n in families), "Segoe UI")
        self.font = pick("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", ".AppleSystemUIFont", "Helvetica Neue")
        self.display_font = pick("Segoe UI Variable Display", "Segoe UI", "SF Pro Display", ".AppleSystemUIFont",
                                 "Helvetica Neue")
        self.icon_font = pick("Segoe Fluent Icons", "Segoe MDL2 Assets", "Segoe UI Symbol", "Apple Symbols",
                              "Apple Color Emoji")
        self._shapes = {}
        f = self.font
        style = ttk.Style(self.root)
        if "macroez" not in style.theme_names():
            style.theme_create("macroez", parent="clam")
        style.theme_use("macroez")
        self.root.option_add("*TCombobox*Listbox.background", RAISED)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", GLASS)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)
        self.root.option_add("*TCombobox*Listbox.font", (f, 10))
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.root.option_add("*Menu.font", (f, 10))
        style.configure(".", background=PANEL, foreground=TEXT, font=(f, 10), bordercolor=LINE, focuscolor=GLASS)
        style.configure("Bg.TFrame", background=BG)
        style.configure("Inner.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=(f, 9))
        style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=(self.display_font, 12, "bold"))

        # cards: rounded panels with a hairline edge
        self._image_element(style, "Card.bg", 14, [("", (PANEL, BG, EDGE))], padding=0)
        style.layout("Card.TFrame", [("Card.bg", {"sticky": "nsew"})])
        style.configure("Card.TFrame", background=PANEL)
        self._image_element(style, "Status.bg", 12, [("", (PANEL, SIDEBAR, EDGE))], padding=0)
        style.layout("Status.TFrame", [("Status.bg", {"sticky": "nsew"})])
        for name, fill in (("Nav", SIDEBAR), ("NavHover", HOVER), ("NavOn", NAV_ON)):
            self._image_element(style, f"{name}.bg", 10, [("", (fill, SIDEBAR))], padding=0)
            style.layout(f"{name}.TFrame", [(f"{name}.bg", {"sticky": "nsew"})])

        # little rounded labels
        self._image_element(style, "Preview.bg", 10, [("", (FIELD, PANEL))])
        style.layout("Preview.TLabel", [("Preview.bg", {"sticky": "nsew", "children": [
            ("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]})])
        style.configure("Preview.TLabel", background=FIELD, foreground=MUTED)
        self._image_element(style, "Chip.bg", 10, [("", (CHIP, PANEL))], size=(160, 30))
        style.layout("Chip.TLabel", [("Chip.bg", {"sticky": "nsew", "children": [
            ("Label.padding", {"sticky": "nsew", "children": [("Label.label", {"sticky": "nsew"})]})]})])
        style.configure("Chip.TLabel", background=CHIP, foreground=GLASS, font=(f, 9, "bold"), padding=(8, 1))

        # buttons
        buttons = {
            "TButton": [("", (FIELD, PANEL)), ("pressed", (LINE, PANEL)), ("active", (RAISED, PANEL))],
            "Accent.TButton": [("", (None, PANEL, None, False, (GLASS, GLASS_2))),
                               ("pressed", (GLASS, PANEL)), ("active", (None, PANEL, None, False, (GLASS_HOVER, GLASS)))],
            "Danger.TButton": [("", (FIELD, PANEL)), ("pressed", ("#4a2129", PANEL)), ("active", ("#3a1f25", PANEL))],
            "Ghost.TButton": [("", (FIELD, BG)), ("pressed", (LINE, BG)), ("active", (RAISED, BG))],
            "Start.TButton": [("", (None, BG, None, False, (GLASS, GLASS_2))),
                              ("pressed", (GLASS, BG)), ("active", (None, BG, None, False, (GLASS_HOVER, GLASS)))],
            "Stop.TButton": [("", (None, BG, None, False, (DANGER, "#e0465a"))),
                             ("pressed", (DANGER, BG)), ("active", (None, BG, None, False, (DANGER_HOVER, DANGER)))],
        }
        for name, states in buttons.items():
            element = name.replace(".", "") + ".bg"
            self._image_element(style, element, 12 if name in ("Start.TButton", "Stop.TButton") else 9, states)
            style.layout(name, [(element, {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
        style.configure("TButton", foreground=TEXT, padding=(12, 7), anchor="center")
        style.configure("Accent.TButton", foreground=ON_ACCENT, font=(f, 10, "bold"))
        style.configure("Danger.TButton", foreground=DANGER)
        style.configure("Ghost.TButton", background=BG)
        style.configure("Small.TButton", padding=(9, 7))
        style.configure("Record.TButton", foreground=DANGER, font=(f, 10, "bold"))
        style.layout("Record.TButton", style.layout("Danger.TButton"))
        for name in ("Start.TButton", "Stop.TButton"):
            style.configure(name, foreground=ON_ACCENT if name == "Start.TButton" else "#ffffff",
                            font=(f, 11, "bold"), padding=(26, 9), background=BG)
        style.map("TButton", foreground=[("disabled", MUTED)])

        # text boxes, number boxes and dropdowns
        field = [("", (FIELD, PANEL)), ("focus", (FIELD, PANEL, GLASS)), ("hover", (FIELD, PANEL, LINE))]
        self._image_element(style, "Field.bg", 8, field, padding=(8, 3))
        field_bg = [("", (FIELD, BG)), ("focus", (FIELD, BG, GLASS)), ("hover", (FIELD, BG, LINE))]
        self._image_element(style, "FieldOnBg.bg", 8, field_bg, padding=(8, 3))
        style.layout("TEntry", [("Field.bg", {"sticky": "nsew", "children": [
            ("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})])
        style.layout("TSpinbox", [("Field.bg", {"sticky": "nsew", "children": [
            ("null", {"side": "right", "sticky": "ns", "children": [
                ("Spinbox.uparrow", {"side": "top", "sticky": "e"}),
                ("Spinbox.downarrow", {"side": "bottom", "sticky": "e"})]}),
            ("Spinbox.padding", {"sticky": "nsew", "children": [("Spinbox.textarea", {"sticky": "nsew"})]})]})])
        for name, element in (("TCombobox", "Field.bg"), ("Header.TCombobox", "FieldOnBg.bg")):
            style.layout(name, [(element, {"sticky": "nsew", "children": [
                ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]})]})])
        for name in ("TEntry", "TSpinbox", "TCombobox", "Header.TCombobox"):
            style.configure(name, fieldbackground=FIELD, foreground=TEXT, background=FIELD, arrowcolor=MUTED,
                            bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD, troughcolor=FIELD,
                            insertcolor=TEXT, padding=(4, 4), arrowsize=11, selectbackground=GLASS_2,
                            selectforeground=ON_ACCENT)
            style.map(name, arrowcolor=[("active", TEXT)], fieldbackground=[("readonly", FIELD)],
                      foreground=[("readonly", TEXT)], background=[("active", FIELD), ("pressed", FIELD)],
                      selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)])

        # on/off switches instead of tick boxes
        switch = [("", (LINE, PANEL, None, False, None, (0.0, MUTED))),
                  ("selected", (GLASS_2, PANEL, None, False, None, (1.0, "#ffffff"))),
                  ("active !selected", (RAISED, PANEL, None, False, None, (0.0, TEXT)))]
        images = [self._round(38, 22, 11, *args) for _s, args in switch]
        style.element_create("Switch.indicator", "image", images[0], ("selected", images[1]),
                             ("active !selected", images[2]), sticky="", padding=(0, 0, 10, 0))
        style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
            ("Switch.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nsew"})]})])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, padding=(0, 3), labelmargins=(10, 0, 0, 0))
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", MUTED)])

        # slider
        style.element_create("Slider.trough", "image", self._round(240, 6, 3, LINE, PANEL), border=4, sticky="ew",
                             width=20)
        knob = self._round(18, 18, 9, None, PANEL, None, False, None, (0.0, GLASS))
        knob_hot = self._round(18, 18, 9, None, PANEL, None, False, None, (0.0, GLASS_HOVER))
        style.element_create("Slider.slider", "image", knob, ("active", knob_hot), sticky="")
        style.layout("Horizontal.TScale", [("Slider.trough", {"sticky": "ew", "children": [
            ("Slider.slider", {"side": "left", "sticky": ""})]})])
        style.configure("Horizontal.TScale", background=PANEL)

        # thin scrollbars
        style.element_create("Thin.trough", "image", self._round(10, 240, 5, PANEL, PANEL), border=5, sticky="ns",
                             height=20)
        style.element_create("Thin.thumb", "image", self._round(8, 160, 4, LINE, PANEL),
                             ("active", self._round(8, 160, 4, MUTED, PANEL)), border=4, sticky="nsew", height=20)
        style.layout("Vertical.TScrollbar", [("Thin.trough", {"sticky": "ns", "children": [
            ("Thin.thumb", {"expand": "1", "sticky": "nsew"})]})])

        # tabs: rounded on top, the open one joins the card below it
        style.element_create("Tab.bg", "image", self._round(160, 50, 10, PANEL, BG, None, True),
                             ("!selected active", self._round(160, 50, 10, HOVER, BG, None, True)),
                             ("!selected", self._round(160, 50, 10, BG, BG, None, True)),
                             border=(11, 11, 11, 2), sticky="nsew", padding=(18, 9), width=26, height=26)
        style.layout("TNotebook.Tab", [("Tab.bg", {"sticky": "nsew", "children": [
            ("Notebook.label", {"sticky": ""})]})])
        style.element_create("Client.bg", "image", self._round(240, 240, 0, BG, BG), border=1, sticky="nsew",
                             width=4, height=4)
        style.layout("TNotebook", [("Client.bg", {"sticky": "nsew"})])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, font=(f, 10, "bold"))
        style.map("TNotebook.Tab", foreground=[("selected", GLASS), ("active", TEXT)])
        style.configure("Notebook.client", background=BG)

        # lists
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=52, borderwidth=0, font=(f, 10))
        style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
        style.configure("Steps.Treeview", rowheight=38)
        style.configure("Treeview.Heading", background=PANEL, foreground=MUTED, relief="flat", padding=(6, 8),
                        font=(f, 9, "bold"), borderwidth=0, lightcolor=PANEL, darkcolor=PANEL, bordercolor=PANEL)
        style.map("Treeview.Heading", background=[("active", PANEL)], foreground=[("active", TEXT)])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no border around the list

    def _build(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._make_vars()
        self._build_sidebar()
        main = tk.Frame(self.root, bg=BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self._build_header(main)
        holder = tk.Frame(main, bg=BG)
        holder.grid(row=1, column=0, sticky="nsew", padx=28)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.pages = {name: tk.Frame(holder, bg=BG) for name in MODE_INFO}
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self._build_log(main)
        self._build_image_page(self.pages["image"])
        self.classic = self._build_steps_page(self.pages["classic"], "classic")
        self.hybrid = self._build_steps_page(self.pages["hybrid"], "hybrid")
        self._build_reaction_page(self.pages["reaction"])
        self._build_autoclick_page(self.pages["autoclick"])
        self._build_settings(self.pages["settings"])
        self._build_profiles_page(self.pages["profiles"])
        self._build_presets_page(self.pages["presets"])
        self.lists = (self.farm, self.auto, self.reactions, self.watch)
        for var in (*self.setting_vars.values(), self.v_monitor):
            var.trace_add("write", self._settings_changed)
        self.select_page(self.state.settings["mode"])

    def _make_vars(self):
        s = self.state.settings
        self.v_reconnect = tk.BooleanVar(value=s["reconnect_enabled"])
        self.v_order = tk.BooleanVar(value=s["farm_in_order"])
        self.v_order_timeout = tk.StringVar(value=str(s["order_timeout"]))
        self.v_spam = tk.BooleanVar(value=s["spam_enabled"])
        self.v_key = tk.StringVar(value=s["spam_key"])
        self.v_spam_min = tk.StringVar(value=str(s["spam_min_ms"]))
        self.v_spam_max = tk.StringVar(value=str(s["spam_max_ms"]))
        self.v_scan_ms = tk.StringVar(value=str(s["scan_interval_ms"]))
        self.v_process = tk.StringVar(value=s["only_process"])
        self.v_multi = tk.BooleanVar(value=s["multiscale"])
        self.v_backup = tk.BooleanVar(value=s["backup_enabled"])
        self.v_backup_every = tk.BooleanVar(value=s["backup_every_scan"])
        self.v_classic_loops = tk.StringVar(value=str(s["classic_loops"]))
        self.v_hybrid_loops = tk.StringVar(value=str(s["hybrid_loops"]))
        self.v_ac_button = tk.StringVar(value=s["ac_button"])
        self.v_ac_double = tk.BooleanVar(value=s["ac_double"])
        self.v_ac_cps = tk.StringVar(value=f"{s['ac_cps']:g}")
        self.v_ac_variation = tk.StringVar(value=str(s["ac_variation"]))
        self.v_ac_rhythm_on = tk.BooleanVar(value=s["ac_use_rhythm"])
        self.v_ac_where = tk.StringVar(value=s["ac_where"])
        self.v_ac_per_spot = tk.StringVar(value=str(s["ac_per_spot"]))
        self.v_ac_wobble = tk.StringVar(value=str(s["ac_wobble"]))
        self.v_ac_trigger = tk.StringVar(value=s["ac_trigger"])
        self.v_ac_max_clicks = tk.StringVar(value=str(s["ac_max_clicks"]))
        self.v_ac_max_minutes = tk.StringVar(value=f"{s['ac_max_minutes']:g}")
        with mss.MSS() as sct:
            self.screens = [f"{i}: {m['width']}x{m['height']}" for i, m in enumerate(sct.monitors) if i > 0]
        self.v_monitor = tk.StringVar(value=self._monitor_text(s["monitor"]))
        self.v_zones = tk.StringVar()
        self.v_run, self.run_boxes = {}, {}  # mode -> its "Runs in" dropdown
        self.v_mode_keys, self.mode_key_boxes = {}, []  # (mode, "start"/"stop") -> its own key
        self.setting_vars = {
            "spam_enabled": self.v_spam, "spam_key": self.v_key, "spam_min_ms": self.v_spam_min,
            "spam_max_ms": self.v_spam_max, "scan_interval_ms": self.v_scan_ms, "only_process": self.v_process,
            "multiscale": self.v_multi, "reconnect_enabled": self.v_reconnect, "farm_in_order": self.v_order,
            "order_timeout": self.v_order_timeout, "backup_enabled": self.v_backup,
            "backup_every_scan": self.v_backup_every, "classic_loops": self.v_classic_loops,
            "hybrid_loops": self.v_hybrid_loops, "ac_button": self.v_ac_button, "ac_double": self.v_ac_double,
            "ac_cps": self.v_ac_cps, "ac_variation": self.v_ac_variation, "ac_use_rhythm": self.v_ac_rhythm_on,
            "ac_where": self.v_ac_where, "ac_per_spot": self.v_ac_per_spot, "ac_wobble": self.v_ac_wobble,
            "ac_trigger": self.v_ac_trigger, "ac_max_clicks": self.v_ac_max_clicks,
            "ac_max_minutes": self.v_ac_max_minutes}

    def _show_nav(self):
        """Sidebar entries, minus the advanced ones while Simple mode is on."""
        simple = self.state.prefs["simple"]
        for widget, _options, _name in self.nav_order:
            widget.pack_forget()
        for widget, options, name in self.nav_order:
            if not (simple and name in SIMPLE_HIDDEN):
                widget.pack(**options)

    def _build_sidebar(self):
        side = tk.Frame(self.root, bg=SIDEBAR, width=232)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=20, pady=(22, 26))
        if self.logo_small:
            tk.Label(brand, image=self.logo_small, bg=SIDEBAR).pack(side="left")
        tk.Label(brand, text=APP_NAME, bg=SIDEBAR, fg=TEXT, font=(self.display_font, 17, "bold")).pack(
            side="left", padx=(10, 0))
        tk.Label(side, text="MODES", bg=SIDEBAR, fg=MUTED, font=(self.font, 8, "bold")).pack(
            anchor="w", padx=22, pady=(0, 6))
        self.nav = {}
        self.nav_order = []  # (widget, pack options, page name) in sidebar order
        for name in MODE_INFO:
            if name == "presets":
                line = tk.Frame(side, bg=LINE, height=1)
                self.nav_order.append((line, {"fill": "x", "padx": 18, "pady": 12}, None))
            title, icon, _ = MODE_INFO[name]
            item = NavItem(side, icon, title, lambda n=name: self.select_page(n), (self.icon_font, 13),
                           (self.font, 11))
            self.nav_order.append((item.frame, {"fill": "x", "padx": 12, "pady": 2}, name))
            self.nav[name] = item
        self._show_nav()
        status = ttk.Frame(side, style="Status.TFrame", padding=(14, 12))
        status.pack(side="bottom", fill="x", padx=14, pady=16)
        top = tk.Frame(status, bg=PANEL)
        top.pack(fill="x")
        self.status_dot = tk.Canvas(top, width=10, height=10, bg=PANEL, highlightthickness=0)
        self.status_dot.pack(side="left")
        self.status_dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="", tags="dot")
        self.status_label = tk.Label(top, text="STOPPED", bg=PANEL, fg=MUTED, font=(self.font, 10, "bold"))
        self.status_label.pack(side="left", padx=(8, 0))
        self.clicks_label = tk.Label(status, text="0 actions", bg=PANEL, fg=MUTED, font=(self.font, 9))
        self.clicks_label.pack(anchor="w", pady=(4, 0))
        self.running_mode_label = tk.Label(status, text="", bg=PANEL, fg=MUTED, font=(self.font, 9),
                                           justify="left")
        self.running_mode_label.pack(anchor="w")

    def _build_header(self, main):
        header = tk.Frame(main, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 18))
        header.columnconfigure(0, weight=1)
        titles = tk.Frame(header, bg=BG)
        titles.grid(row=0, column=0, sticky="w")
        self.title_label = tk.Label(titles, text="", bg=BG, fg=TEXT, font=(self.display_font, 22, "bold"))
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(titles, text="", bg=BG, fg=MUTED, font=(self.font, 10), justify="left")
        self.subtitle_label.pack(anchor="w")
        right = tk.Frame(header, bg=BG)
        right.grid(row=0, column=1, sticky="e")
        tk.Label(right, text="Profile", bg=BG, fg=MUTED, font=(self.font, 9, "bold")).pack(side="left", padx=(0, 8))
        self.v_profile = tk.StringVar()
        self.profile_box = ttk.Combobox(right, textvariable=self.v_profile, state="readonly", width=22,
                                        style="Header.TCombobox")
        self.profile_box.pack(side="left")
        self.profile_box.bind("<<ComboboxSelected>>", self.switch_profile)
        self.profile_box.bind("<MouseWheel>", lambda _e: "break")  # a stray scroll must not swap macros
        menu = tk.Menu(self.root, tearoff=False, bg=PANEL, fg=TEXT, activebackground=GLASS,
                       activeforeground=ON_ACCENT, bd=0)
        menu.add_command(label="New empty profile", command=self.new_profile)
        menu.add_command(label="Save as a copy", command=self.save_profile_as)
        menu.add_command(label="Rename", command=self.rename_profile)
        menu.add_separator()
        menu.add_command(label="Delete", command=self.delete_profile)
        self.profile_menu = menu
        more = ttk.Button(right, text="⋯", width=3, style="Ghost.TButton", command=lambda: menu.tk_popup(
            more.winfo_rootx(), more.winfo_rooty() + more.winfo_height()))
        more.pack(side="left", padx=(6, 18))
        self.start_button = ttk.Button(right, text="", command=self.toggle, style="Start.TButton", cursor="hand2")
        self.start_button.pack(side="left")
        # in a narrow window the description wraps onto a second line instead of being cut off
        header.bind("<Configure>", lambda e: self.subtitle_label.configure(
            wraplength=max(e.width - right.winfo_reqwidth() - 24, 240)), add="+")
        self._show_profile_names()

    def _build_log(self, main):
        logs = tk.Frame(main, bg=BG)
        logs.grid(row=2, column=0, sticky="ew", padx=28, pady=(16, 22))
        logs.columnconfigure(0, weight=1)
        tk.Label(logs, text="ACTIVITY", bg=BG, fg=MUTED, font=(self.font, 8, "bold")).grid(row=0, column=0, sticky="w")
        log_card = ttk.Frame(logs, style="Card.TFrame", padding=(14, 10))
        log_card.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        log_card.columnconfigure(0, weight=1)
        self.log_box = tk.Text(log_card, height=5, bg=PANEL, fg=MUTED, bd=0, padx=0, pady=0, highlightthickness=0,
                               font=("Cascadia Mono", 9) if "Cascadia Mono" in tkfont.families(self.root)
                               else ("Menlo", 11) if IS_MAC else ("Consolas", 9), state="disabled", wrap="word")
        self.log_box.grid(row=0, column=0, sticky="ew")

    def _two_columns(self, page, mode):
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, minsize=340)
        page.rowconfigure(0, weight=1)
        side = tk.Frame(page, bg=BG)
        side.grid(row=0, column=1, sticky="nsew", padx=(18, 0))
        side.columnconfigure(0, weight=1)
        self._build_run_in(side, mode)
        editors = tk.Frame(side, bg=BG)
        editors.grid(row=1, column=0, sticky="new", pady=(16, 0))
        editors.columnconfigure(0, weight=1)
        return side, editors

    # ---- which app each mode works in
    def _build_run_in(self, side, mode):
        box = card(side, padding=(16, 12))
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Runs in", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        var = tk.StringVar(value=self._app_label(run_in(self.state.settings, mode)))
        self.v_run[mode] = var
        combo = ttk.Combobox(box, textvariable=var, state="readonly",
                             postcommand=lambda: combo.configure(values=self._app_choices(mode)))
        combo.grid(row=0, column=1, sticky="ew")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._run_in_chosen(mode))
        combo.bind("<MouseWheel>", lambda _e: "break")
        self.run_boxes[mode] = combo
        ttk.Label(box, text="It only clicks and presses keys while this app is in front.", style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 4))
        ttk.Label(box, text="Start / stop").grid(row=2, column=0, sticky="w", pady=4, padx=(0, 12))
        pair = ttk.Frame(box, style="Inner.TFrame")
        pair.grid(row=2, column=1, sticky="ew", pady=4)
        for column, (which, label) in enumerate((("start", "Start key"), ("stop", "Stop key"))):
            pair.columnconfigure(column, weight=1, uniform="keys")
            var = tk.StringVar(value=self.state.settings["mode_keys"].get(mode, {}).get(which, ""))
            self.v_mode_keys[(mode, which)] = var
            if which == "start":
                specials = lambda: (("", f"Main ({self.state.hotkeys.get('start', 'None')})"),)
            else:
                specials = lambda m=mode: (("", f"Same ({self.start_key(m)})"),)
            key_box = KeyBox(pair, self, var, "hotkey", specials, title=f"{MODE_INFO[mode][0]} mode: {label}",
                             width=8)
            key_box.grid(row=0, column=column, sticky="ew", padx=(0, 6) if column == 0 else 0)
            self.mode_key_boxes.append(key_box)
            var.trace_add("write", lambda *_a, m=mode, w=which: self._mode_key_changed(m, w))

    def _app_label(self, exe, title=""):
        if not exe:
            return ANY_APP
        return f"{exe}  \u00b7  {title[:34]}" if title else exe

    def _app_choices(self, mode):
        """Any app, the app already picked, then every app with a window open right now."""
        current = run_in(self.state.settings, mode)
        apps = open_apps()
        choices = [ANY_APP]
        if current and current.lower() not in {a.lower() for a in apps}:
            choices.append(self._app_label(current, "not open right now"))
        choices += [self._app_label(exe, title) for exe, title in apps.items()]
        return choices

    def _run_in_chosen(self, mode):
        label = self.v_run[mode].get()
        exe = "" if label == ANY_APP else label.split("  \u00b7  ")[0].strip()
        self.state.settings["run_in"][mode] = exe
        self.v_run[mode].set(self._app_label(exe))
        self.state.save()
        where = exe or "any app"
        self.log(f"{MODE_INFO[mode][0]} mode now runs in {where}.")

    def _show_run_in(self):
        for mode, var in self.v_run.items():
            var.set(self._app_label(run_in(self.state.settings, mode)))
        self._show_mode_keys()

    def _show_mode_keys(self):
        was, self.showing_profile = self.showing_profile, True
        try:
            for (mode, which), var in self.v_mode_keys.items():
                var.set(self.state.settings["mode_keys"].get(mode, {}).get(which, ""))
            for box in self.mode_key_boxes:
                box.refresh()
        finally:
            self.showing_profile = was
        self._paint_start()

    # ---- which keys start and stop what
    def start_key(self, mode=None):
        mode = mode or self.state.settings["mode"]
        return self.state.settings["mode_keys"].get(mode, {}).get("start") or self.state.hotkeys.get("start", "None")

    def stop_key(self, mode=None):
        return self.state.settings["mode_keys"].get(mode or self.state.settings["mode"], {}).get("stop") or \
            self.start_key(mode)

    def _own_stop_key(self, mode=None):
        return bool(self.state.settings["mode_keys"].get(mode or self.state.settings["mode"], {}).get("stop"))

    def _key_jobs(self):
        """Every key bound right now: (kind, action, mode, which) -> its name."""
        jobs = {("global", action): name for action, name in list(self.state.hotkeys.items())}
        simple = self.state.prefs["simple"]
        for mode, keys in list(self.state.settings.get("mode_keys", {}).items()):
            if simple and mode in SIMPLE_HIDDEN:
                continue
            for which, name in list(keys.items()):
                jobs[("mode", mode, which)] = name
        for profile in ([] if simple else self.state.profile_names()):
            key = self.state.profile_key(profile)
            if key:
                jobs[("profile", profile)] = key
        return jobs

    def _key_bindings(self):
        """virtual key -> the hotkey actions it fires, like "start", "snip" or "stop:classic"."""
        wanted = {}
        for job, name in self._key_jobs().items():
            if HOTKEY_KEYS.get(name):
                action = job[1] if job[0] == "global" else f"profile:{job[1]}" if job[0] == "profile" else \
                    f"{job[2]}:{job[1]}"
                wanted.setdefault(HOTKEY_KEYS[name], []).append(action)
        return wanted

    def _job_text(self, job):
        if job[0] == "global":
            return dict(HOTKEY_ACTIONS)[job[1]].lower()
        if job[0] == "profile":
            return f"start / stop the {job[1]} profile"
        return f"{job[2]} {MODE_INFO[job[1]][0]} mode"

    def _free_key(self, name, keep):
        """One key does one job: take this key away from anything else that had it."""
        if not name or name == "None":
            return
        for job, other in self._key_jobs().items():
            if other != name or job == keep:
                continue
            if job[0] == "global" and keep[0] == "global" and {job[1], keep[1]} == SHARED_HOTKEYS:
                continue
            if job[0] == "global":
                self.state.hotkeys[job[1]] = "None"
                self.hotkey_vars[job[1]].set("None")
            elif job[0] == "profile":
                self.state.profile_settings(job[1])["profile_key"] = ""
                if hasattr(self, "profile_tree"):
                    self._profiles_reload()
            else:
                self.state.settings["mode_keys"][job[1]].pop(job[2], None)
            self.log(f"{name} no longer does {self._job_text(job)}. It's now the key for {self._job_text(keep)}.")
        self._show_mode_keys()

    def _mode_key_changed(self, mode, which):
        if self.showing_profile:
            return
        name = self.v_mode_keys[(mode, which)].get()
        if name not in HOTKEY_KEYS or name == "None":
            name = ""
        if which == "start" and name == self.state.hotkeys.get("start"):
            name = ""  # the main key already starts it
        if which == "stop" and name == self.start_key(mode):
            name = ""
        keys = self.state.settings["mode_keys"].setdefault(mode, {})
        if name:
            keys[which] = name
            self._free_key(name, ("mode", mode, which))
        else:
            keys.pop(which, None)
        if which == "start" and keys.get("stop") == self.start_key(mode):
            keys.pop("stop")
        self.state.save()
        self._show_mode_keys()
        title = MODE_INFO[mode][0]
        if which == "start":
            self.log(f"{self.start_key(mode)} now starts {title} mode, from any mode." if name else
                     f"{title} mode now starts with the main key, {self.start_key(mode)}.")
        else:
            self.log(f"{name} now stops {title} mode." if name else
                     f"{title} mode now stops with its start key, {self.start_key(mode)}.")

    def _mode_hotkey(self, which, mode):
        """A mode's own key: start switches to that mode and runs it; stop only stops it."""
        if self.state.prefs["simple"] and mode in SIMPLE_HIDDEN:
            return
        s = self.state.settings
        running, current = self.engine.running, s["mode"]
        hold = mode == "autoclick" and s["ac_trigger"] == AC_HOLD
        if which == "stop":
            if running and current == mode:
                self.engine.stop()
                self.log("Stopped.")
            return
        if self.recorder:
            self.toggle_recording()
            return
        if running and current == mode:
            if not hold:
                self.toggle()
            return
        if running:
            self.engine.stop()
        if current != mode:
            self.select_page(mode)
        self.toggle(quiet=hold)

    def _typing_here(self, action):
        """True when a typing key's hotkey is pressed while you type in one of MacroEZ's own boxes."""
        if action.startswith("profile:"):
            job = ("profile", action[len("profile:"):])
        else:
            job = ("global", action) if ":" not in action else ("mode", *reversed(action.split(":")))
        if HOTKEY_KEYS.get(self._key_jobs().get(job, "None"), 0) not in TYPING_VKS:
            return False
        try:
            focus = self.root.focus_get()
        except (KeyError, tk.TclError):
            return False
        return focus is not None and focus.winfo_class() in ("TEntry", "TSpinbox", "Entry", "Spinbox") and \
            foreground_is_ours()

    # ---- the keyboard picker
    def _picking_key(self):
        return self.key_window is not None and self.key_window.winfo_exists()

    def pick_key(self, kind, current, on_pick, specials=(), title="Choose a key"):
        """A full-size keyboard. Click a key, or press it. kind is "press" (keys a macro presses) or "hotkey"."""
        if self._picking_key():
            self.key_window.destroy()
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        hint = "Click a key, or press it on your keyboard. Esc closes this without changing anything."
        if kind == "hotkey":
            hint += " Letter and number keys also fire while you type in other apps, so F-keys, the numpad or " \
                    "mouse side buttons are the safest."
        body = card(top, title, hint)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        for label in body.grid_slaves(row=1):
            label.grid_configure(columnspan=3)
        unit, gap, pad = 38, 4, 2
        width, height = KEYBOARD_SIZE[0] * unit, KEYBOARD_SIZE[1] * unit
        canvas = tk.Canvas(body, width=round(width + 2 * pad), height=round(height + 2 * pad), bg=PANEL,
                           highlightthickness=0)
        canvas.grid(row=2, column=0, columnspan=2, pady=(4, 0))
        body.columnconfigure(0, weight=1)
        slot = 5 if kind == "press" else 6
        self.key_mouse, mouse_paint = None, []
        chosen = {"done": False}
        tags = {}  # name -> canvas tags of every key with that name

        def paint(tag, name, hover=False):
            on = name == current or chosen.get("flash") == name
            fill = GLASS if on else (SELECTED if hover else RAISED)
            canvas.itemconfigure(f"{tag}bg", fill=fill, outline=GLASS if hover and not on else fill)
            canvas.itemconfigure(f"{tag}tx", fill=ON_ACCENT if on else TEXT)

        def finish(name):
            if chosen["done"]:
                return
            chosen["done"] = True
            if top.winfo_exists():
                top.destroy()
            self.key_window = None
            on_pick(name)

        def choose(name, pressed=False):
            if chosen["done"] or chosen.get("flash"):
                return
            if not pressed:
                finish(name)
                return
            chosen["flash"] = name  # show which key was heard, then close
            for tag in tags.get(name, ()):
                paint(tag, name)
            for part, value in MOUSE_HOTKEY_NAMES.items():
                if value == name and mouse_paint:
                    mouse_paint[0](part)
            top.after(220, lambda: finish(name))

        for number, entry in enumerate(KEYBOARD):
            x, y, w, h, label, name = *entry[:5], entry[slot]
            x1, y1 = pad + x * unit, pad + y * unit
            x2, y2 = x1 + w * unit - gap, y1 + h * unit - gap
            tag = f"k{number}"
            self._round_rect(canvas, x1, y1, x2, y2, 6, tags=(tag, f"{tag}bg"),
                             fill=RAISED if name else FIELD, outline=RAISED if name else FIELD)
            size = 9 if len(label) <= 3 else 8
            canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=label, tags=(tag, f"{tag}tx"),
                               fill=TEXT if name else LINE, font=(self.font, size, "bold" if len(label) == 1 else "normal"))
            if not name:
                continue
            tags.setdefault(name, []).append(tag)
            paint(tag, name)
            canvas.tag_bind(tag, "<Button-1>", lambda _e, n=name: choose(n))
            canvas.tag_bind(tag, "<Enter>", lambda _e, t=tag, n=name: (paint(t, n, True),
                                                                        canvas.configure(cursor="hand2")))
            canvas.tag_bind(tag, "<Leave>", lambda _e, t=tag, n=name: (paint(t, n), canvas.configure(cursor="")))

        bar = ttk.Frame(body, style="Inner.TFrame")
        bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        extras = []
        if kind == "hotkey":
            mouse = tk.Canvas(body, width=round(262 * 0.78), height=round(max(height + 2 * pad, 334 * 0.78)),
                              bg=PANEL,
                              highlightthickness=0)
            mouse.grid(row=2, column=2, sticky="n", padx=(18, 0), pady=(4, 0))
            mouse_paint.append(self._draw_mouse(mouse, 92 * 0.78, 16, 0.78, MOUSE_HOTKEY_NAMES, current,
                                                lambda n: chosen.get("flash"), choose))
            self.key_mouse = mouse
        extras += list(specials)
        for value, text in extras:
            ttk.Button(bar, text=text, takefocus=False, style="Accent.TButton" if value == current else "TButton",
                       command=lambda v=value: choose(v)).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Cancel", takefocus=False, command=top.destroy).pack(side="right")
        shown = ttk.Label(body, text=f"Now: {next((t for v, t in specials if v == current), None) or key_label(current, kind)}",
                          style="Muted.TLabel")
        shown.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # Pressing a real key. Read straight from the keyboard, so left and right Shift, Ctrl and Alt and the
        # numpad all come out as themselves. Enter is told apart from numpad Enter by the window's own event.
        by_vk = {}
        for entry in KEYBOARD:
            name, hot = entry[slot], entry[6]
            if name and hot not in ("Enter", "Esc"):
                by_vk[HOTKEY_KEYS[hot]] = name
        if kind == "hotkey":
            by_vk.update({HOTKEY_KEYS[name]: name for name in MOUSE_HOTKEYS})
        held = {vk for vk in by_vk if key_is_down(vk)}

        def poll():
            if not top.winfo_exists() or chosen["done"]:
                return
            ours = foreground_is_ours()
            for vk, name in by_vk.items():
                down = key_is_down(vk)
                if down and vk not in held:
                    held.add(vk)
                    if ours:
                        choose(name, pressed=True)
                        return
                elif not down:
                    held.discard(vk)
            top.after(15, poll)

        top.bind("<KeyPress-Return>", lambda _e: choose("enter" if kind == "press" else "Enter", True) or "break")
        top.bind("<KeyPress-KP_Enter>", lambda _e: choose("numenter" if kind == "press" else "Enter", True) or "break")
        top.bind("<KeyPress-Escape>", lambda _e: top.destroy())
        top.bind("<Destroy>", lambda e: setattr(self, "key_window", None) if e.widget is top else None, add="+")
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_force()
        self.key_window, self.key_choose, self.key_canvas = top, choose, canvas  # tests reach in through these
        top.after(15, poll)

    def _draw_mouse(self, canvas, ox, oy, scale, names, current, flashing, choose):
        """Draw the mouse. names maps each part (left, right, middle, back, forward) to what picking it sets;
        parts missing from names are drawn dimmed."""
        at = lambda x, y: (ox + x * scale, oy + y * scale)
        body = [c for point in MOUSE_BODY for c in at(*point)]
        canvas.create_polygon(body, smooth=True, fill=EDGE, outline=LINE, width=1)
        canvas.create_line(*at(80, 4), *at(80, 112), fill=EDGE, width=3)

        def paint(part, hover=False):
            name = names.get(part)
            if not name:
                return
            on = name == current or flashing(name) == name
            fill = GLASS if on else (SELECTED if hover else RAISED)
            canvas.itemconfigure(f"m_{part}_bg", fill=fill, outline=GLASS if hover and not on else
                                 LINE if part == "middle" and not on else fill)
            canvas.itemconfigure(f"m_{part}_tx", fill=(ON_ACCENT if on else TEXT) if part in ("left", "right")
                                 else (GLASS if on else TEXT))

        for part, (shape, points) in MOUSE_SHAPES.items():
            enabled = part in names
            tag = f"m_{part}"
            fill = RAISED if enabled else FIELD
            if shape == "poly":
                canvas.create_polygon([c for point in points for c in at(*point)], smooth=True, fill=fill,
                                      outline=fill, tags=(tag, f"{tag}_bg"))
            else:
                self._round_rect(canvas, *at(*points[:2]), *at(*points[2:]), 6 * scale, fill=fill, outline=fill,
                                 tags=(tag, f"{tag}_bg"))
            x, y, text = MOUSE_TEXT[part]
            side = part in ("back", "forward")
            canvas.create_text(*at(x, y), text=text, anchor="e" if side else "center", justify="right",
                               fill=(TEXT if enabled else LINE), tags=(tag, f"{tag}_tx"),
                               font=(self.font, max(round(9 * scale), 7) if part != "middle" else
                                     max(round(8 * scale), 7), "bold" if not side else "normal"))
            if not enabled:
                continue
            paint(part)
            canvas.tag_bind(tag, "<Button-1>", lambda _e, n=names[part]: choose(n))
            canvas.tag_bind(tag, "<Enter>", lambda _e, p=part: (paint(p, True), canvas.configure(cursor="hand2")))
            canvas.tag_bind(tag, "<Leave>", lambda _e, p=part: (paint(p), canvas.configure(cursor="")))
        canvas.tag_raise("m_middle")
        return paint

    def pick_mouse(self, kind, current, on_pick, specials=(), title="Mouse button"):
        """A drawn mouse. Click a button on it, or press a button on your real mouse (right, middle or a side
        button) while this window is open."""
        if self._picking_key():
            self.key_window.destroy()
        top = tk.Toplevel(self.root)
        top.title(title)
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        body = card(top, title, "Click a button on the mouse, or press the right, middle or a side button on your "
                                "own mouse. Esc closes this without changing anything.")
        body.pack(fill="both", expand=True, padx=14, pady=14)
        scale = 1.05
        canvas = tk.Canvas(body, width=round(280 * scale), height=round(350 * scale), bg=PANEL, highlightthickness=0)
        canvas.grid(row=2, column=0, columnspan=2, pady=(4, 0))
        chosen = {"done": False}
        names = {part: part for part in MOUSE_SHAPES}

        def finish(name):
            if chosen["done"]:
                return
            chosen["done"] = True
            if top.winfo_exists():
                top.destroy()
            self.key_window = None
            on_pick(name)

        def choose(name, pressed=False):
            if chosen["done"] or chosen.get("flash"):
                return
            if not pressed:
                finish(name)
                return
            chosen["flash"] = name  # show which button was heard, then close
            for part, value in names.items():
                if value == name:
                    paint(part)
            top.after(220, lambda: finish(name))

        paint = self._draw_mouse(canvas, 98 * scale, 28 * scale, scale, names, current,
                                 lambda _n: chosen.get("flash"), choose)
        bar = ttk.Frame(body, style="Inner.TFrame")
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for value, text in specials:
            ttk.Button(bar, text=text, takefocus=False, style="Accent.TButton" if value == current else "TButton",
                       command=lambda v=value: choose(v)).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Cancel", takefocus=False, command=top.destroy).pack(side="right")
        ttk.Label(body, text=f"Now: {key_label(current, 'mouse')}", style="Muted.TLabel").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        # a real press: right and middle come as the window's own clicks, side buttons straight from the mouse
        top.bind("<Button-3>", lambda _e: choose("right", True) or "break")
        top.bind("<Button-2>", lambda _e: choose("middle", True) or "break")
        side = {MOUSE_VK["back"]: "back", MOUSE_VK["forward"]: "forward"}
        held = {vk for vk in side if key_is_down(vk)}

        def poll():
            if not top.winfo_exists() or chosen["done"]:
                return
            ours = foreground_is_ours()
            for vk, name in side.items():
                down = key_is_down(vk)
                if down and vk not in held:
                    held.add(vk)
                    if ours:
                        choose(name, pressed=True)
                        return
                elif not down:
                    held.discard(vk)
            top.after(15, poll)

        top.bind("<KeyPress-Escape>", lambda _e: top.destroy())
        top.bind("<Destroy>", lambda e: setattr(self, "key_window", None) if e.widget is top else None, add="+")
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_force()
        self.key_window, self.key_choose, self.key_canvas = top, choose, canvas  # tests reach in through these
        top.after(15, poll)

    @staticmethod
    def _round_rect(canvas, x1, y1, x2, y2, r, **options):
        points = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2,
                  x1, y2 - r, x1, y1 + r, x1, y1)
        return canvas.create_polygon(points, smooth=True, **options)

    def _build_image_page(self, page):
        side, editors = self._two_columns(page, "image")
        self.notebook = ttk.Notebook(page)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.farm = ImageList(self, self.state.targets, self.notebook, editors, "Click list", "Images to click",
                              "Checked top to bottom. Click the Priority column to ★ an image: "
                              "★ images are checked on every scan, the rest twice a second.")
        self.auto = ImageList(self, self.state.reconnect, self.notebook, editors, "Recovery",
                              "Recovery steps",
                              "If something interrupts the macro, like an error box or a sign-in screen, these get clicked in order. "
                              "Step 1 starts it, and the click list pauses until the last step.",
                              kind="reconnect")
        spam = card(side, "Repeat a key")
        spam.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        spam.columnconfigure(1, weight=1)
        ttk.Checkbutton(spam, text="Press a key over and over between clicks", variable=self.v_spam).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self._field(spam, 2, "Key", lambda p: KeyBox(p, self, self.v_key, "press", title="Key to repeat"))
        self._field(spam, 3, "Delay range (ms)", self._spam_range)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _build_steps_page(self, page, mode):
        side, editors = self._two_columns(page, mode)
        steps = ActionList(self, getattr(self.state, mode), page, editors, mode)
        playback = card(side, "Playback")
        playback.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        playback.columnconfigure(1, weight=1)
        variable = self.v_classic_loops if mode == "classic" else self.v_hybrid_loops
        self._field(playback, 1, "Repeat (times)", lambda p: ttk.Spinbox(
            p, from_=0, to=100000, increment=1, textvariable=variable))
        ttk.Label(playback, text="0 repeats forever, until you stop it.", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        return steps

    def _build_reaction_page(self, page):
        side, editors = self._two_columns(page, "reaction")
        holder = tk.Frame(page, bg=BG)
        holder.grid(row=0, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.reactions = ImageList(self, self.state.reactions, holder, editors, None, "Triggers",
                                   "When an image shows up, its key gets pressed. Snip what to watch for, then "
                                   "pick the key on the right.", kind="reaction")

    def _build_autoclick_page(self, page):
        side, editors = self._two_columns(page, "autoclick")
        self.ac_tabs = ttk.Notebook(page)
        self.ac_tabs.grid(row=0, column=0, sticky="nsew")

        speed = card(self.ac_tabs, "Clicking", "Pick a speed, or tap out a rhythm of your own. Natural variation makes "
                                               "every gap a little different, the way a person clicks.")
        self.ac_tabs.add(speed, text="Clicking")
        speed.columnconfigure(1, weight=1)
        self._field(speed, 2, "Mouse button", lambda p: KeyBox(p, self, self.v_ac_button, "mouse",
                                                               title="Button to click"))
        self._field(speed, 3, "Clicks per second", lambda p: ttk.Spinbox(
            p, from_=0.1, to=100, increment=1, textvariable=self.v_ac_cps))
        self._field(speed, 4, "Natural variation (%)", lambda p: ttk.Spinbox(
            p, from_=0, to=90, increment=5, textvariable=self.v_ac_variation))
        ttk.Checkbutton(speed, text="Double-click each time", variable=self.v_ac_double).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(speed, text="My rhythm", style="Section.TLabel").grid(row=6, column=0, columnspan=2, sticky="w",
                                                                         pady=(22, 0))
        ttk.Label(speed, text="Tap a beat and the clicks follow it, gaps and all, over and over.",
                  style="Muted.TLabel").grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 8))
        rhythm = ttk.Frame(speed, style="Inner.TFrame")
        rhythm.grid(row=8, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(rhythm, text="Click to my rhythm", variable=self.v_ac_rhythm_on).pack(side="left")
        ttk.Button(rhythm, text="Tap a rhythm", style="Accent.TButton", command=self.tap_rhythm).pack(side="right")
        self.ac_beat = tk.Canvas(speed, height=34, bg=FIELD, highlightthickness=0)
        self.ac_beat.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.ac_beat.bind("<Configure>", lambda _e: self._draw_beat(self.ac_beat, self.state.settings["ac_rhythm"]))
        self.v_ac_rhythm_text = tk.StringVar()
        ttk.Label(speed, textvariable=self.v_ac_rhythm_text, style="Muted.TLabel").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))

        route = card(self.ac_tabs, "Route", "Spots it clicks in turn, instead of wherever the mouse is.")
        self.ac_tabs.add(route, text="Route")
        ttk.Checkbutton(route, text="Use this route", variable=self.v_ac_where, onvalue=AC_ROUTE,
                        offvalue=AC_AT_MOUSE).grid(row=0, column=0, sticky="e")
        route.rowconfigure(2, weight=1)
        holder = ttk.Frame(route, style="Inner.TFrame")
        holder.grid(row=2, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.ac_spot_tree = ttk.Treeview(holder, columns=("num", "x", "y"), show="headings", style="Steps.Treeview",
                                         selectmode="browse", height=5)
        for column, text, width in (("num", "#", 50), ("x", "X", 120), ("y", "Y", 120)):
            self.ac_spot_tree.heading(column, text=text)
            self.ac_spot_tree.column(column, width=width, stretch=column != "num", anchor="center")
        self.ac_spot_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.ac_spot_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.ac_spot_tree.configure(yscrollcommand=scroll.set)
        bar = ttk.Frame(route, style="Inner.TFrame")
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(bar, text="+ Pick a spot", style="Accent.TButton", command=self.ac_add_spot).pack(side="left")
        ttk.Button(bar, text="Remove", style="Danger.TButton", command=self.ac_remove_spot).pack(side="right")
        ttk.Button(bar, text="↓", width=3, style="Small.TButton", command=lambda: self.ac_move_spot(1)).pack(
            side="right", padx=6)
        ttk.Button(bar, text="↑", width=3, style="Small.TButton", command=lambda: self.ac_move_spot(-1)).pack(
            side="right")
        extras = ttk.Frame(route, style="Inner.TFrame")
        extras.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        extras.columnconfigure(1, weight=1)
        self._field(extras, 0, "Clicks on each spot", lambda p: ttk.Spinbox(
            p, from_=1, to=10000, increment=1, textvariable=self.v_ac_per_spot))
        self._field(extras, 1, "Wobble (px)", lambda p: ttk.Spinbox(
            p, from_=0, to=200, increment=1, textvariable=self.v_ac_wobble))
        ttk.Label(extras, text="Wobble lands each click a few pixels off its spot, at random.",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self.watch = ImageList(self, self.state.watches, self.ac_tabs, editors, "Smart stop", "Smart stop",
                               "Snip something to watch for, like a Done message or a full inventory. When it shows "
                               "up, the autoclicker stops, pauses, or only clicks while it's there.", kind="watch")
        self.ac_tab_frames = {"speed": speed, "route": route, "watch": self.ac_tabs.tabs()[2]}

        self.ac_side = tk.Frame(side, bg=BG)
        self.ac_side.grid(row=2, column=0, sticky="new", pady=(16, 0))
        self.ac_side.columnconfigure(0, weight=1)
        control = card(self.ac_side, "Control")
        control.grid(row=0, column=0, sticky="ew")
        control.columnconfigure(1, weight=1)
        self._field(control, 2, "Key works as", lambda p: ttk.Combobox(
            p, textvariable=self.v_ac_trigger, values=[AC_TOGGLE, AC_HOLD], state="readonly"))
        self._field(control, 3, "Stop after (clicks)", lambda p: ttk.Spinbox(
            p, from_=0, to=10000000, increment=100, textvariable=self.v_ac_max_clicks))
        self._field(control, 4, "Stop after (min)", lambda p: ttk.Spinbox(
            p, from_=0, to=1440, increment=5, textvariable=self.v_ac_max_minutes))
        ttk.Label(control, text="0 means no limit. It never clicks MacroEZ or a safe zone.",
                  style="Muted.TLabel", wraplength=290, justify="left").grid(row=5, column=0, columnspan=2,
                                                                             sticky="w", pady=(4, 0))
        live = card(self.ac_side, "Live")
        live.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        stats = ttk.Frame(live, style="Inner.TFrame")
        stats.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.v_ac_live = {}
        for column, (key, caption) in enumerate((("cps", "clicks / s"), ("clicks", "clicks"), ("time", "running"))):
            stats.columnconfigure(column, weight=1, uniform="live")
            var = tk.StringVar(value="0" if key != "time" else "0:00")
            self.v_ac_live[key] = var
            tk.Label(stats, textvariable=var, bg=PANEL, fg=GLASS if key == "cps" else TEXT,
                     font=(self.display_font, 20, "bold")).grid(row=0, column=column, sticky="w")
            ttk.Label(stats, text=caption, style="Muted.TLabel").grid(row=1, column=column, sticky="w")
        self.ac_tabs.bind("<<NotebookTabChanged>>", self._ac_tab_changed)
        self._ac_spots_reload()
        self._show_rhythm()
        self._ac_tab_changed()

    def _ac_tab_changed(self, _event=None):
        """The Smart stop editor takes the right side on its tab; the other tabs show Control and Live."""
        if not hasattr(self, "watch"):
            return
        holder = self.watch.editor.master  # hidden too: an emptied frame would keep its old height
        if self.ac_tabs.select() == str(self.ac_tab_frames["watch"]):
            self.ac_side.grid_remove()
            holder.grid()
        else:
            holder.grid_remove()
            self.ac_side.grid()

    def _draw_beat(self, canvas, gaps):
        """Dots for the clicks of one loop of the beat; the hollow one is where it starts over."""
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 60), max(canvas.winfo_height(), 20)
        middle = height // 2
        canvas.create_line(12, middle, width - 12, middle, fill=LINE, width=2)
        if not gaps:
            canvas.create_text(width // 2, middle, text="No rhythm yet", fill=MUTED, font=(self.font, 9))
            return
        total, moment = sum(gaps), 0
        for gap in [0, *gaps]:
            moment += gap
            x = 12 + (width - 24) * moment / total
            last = moment == total
            canvas.create_oval(x - 6, middle - 6, x + 6, middle + 6, outline=GLASS, width=2,
                               fill=FIELD if last else GLASS)

    def _show_rhythm(self):
        gaps = self.state.settings["ac_rhythm"]
        if gaps:
            self.v_ac_rhythm_text.set(f"{len(gaps)} click{'s' if len(gaps) != 1 else ''} per loop, repeating every "
                                      f"{sum(gaps) / 1000:.2f} s.")
        else:
            self.v_ac_rhythm_text.set("Tap one to click in a pattern, like a double tap and a pause.")
        self._draw_beat(self.ac_beat, gaps)

    def tap_rhythm(self):
        """A small window to tap a beat into, with the mouse or the Space bar."""
        if self.engine.running:
            self.engine.stop()
        top = tk.Toplevel(self.root)
        top.title("Tap a rhythm")
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        body = card(top, "Tap a rhythm", "Click the pad or press Space once for every click. To make it loop "
                                         "smoothly, finish with one more tap where the pattern would start again.")
        body.pack(fill="both", expand=True, padx=14, pady=14)
        body.columnconfigure(0, weight=1)
        taps = []
        pad = tk.Label(body, text="TAP", bg=CHIP, fg=GLASS, font=(self.display_font, 24, "bold"), height=3,
                       cursor="hand2")
        pad.grid(row=2, column=0, sticky="ew", pady=(4, 10))
        beat = tk.Canvas(body, height=34, width=420, bg=FIELD, highlightthickness=0)
        beat.grid(row=3, column=0, sticky="ew")
        info = tk.StringVar(value="No taps yet.")
        ttk.Label(body, textvariable=info, style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 0))

        def gaps():
            return [min(max(round((b - a) * 1000), 10), 10000) for a, b in zip(taps, taps[1:])]

        def flash_off():
            if pad.winfo_exists():
                pad.configure(bg=CHIP, fg=GLASS)

        def tap(_event=None):
            taps.append(time.perf_counter())
            pad.configure(bg=GLASS, fg=ON_ACCENT)
            top.after(90, flash_off)
            now = gaps()
            self._draw_beat(beat, now)
            info.set(f"{len(taps)} tap{'s' if len(taps) != 1 else ''}" +
                     (f", loop of {sum(now) / 1000:.2f} s" if now else ""))
            return "break"

        def again():
            taps.clear()
            self._draw_beat(beat, [])
            info.set("No taps yet.")

        def save():
            beat_gaps = gaps()
            if not beat_gaps:
                info.set("Tap at least twice first.")
                return
            self.state.settings["ac_rhythm"] = beat_gaps
            self.v_ac_rhythm_on.set(True)
            self._show_rhythm()
            self.state.save()
            top.destroy()
            self.log(f"Rhythm saved: {len(beat_gaps)} clicks per loop. Click to my rhythm is on.")

        buttons = ttk.Frame(body, style="Inner.TFrame")
        buttons.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=save, takefocus=False).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=top.destroy, takefocus=False).pack(side="right", padx=6)
        ttk.Button(buttons, text="Start over", command=again, takefocus=False).pack(side="left")
        pad.bind("<Button-1>", tap)
        top.bind("<space>", tap)
        top.bind("<Escape>", lambda _e: top.destroy())
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        pad.focus_set()
        self.rhythm_window = top  # tests reach the pad through this
        self.rhythm_tap, self.rhythm_save = tap, save

    def _ac_spots_reload(self, select=None):
        tree = self.ac_spot_tree
        tree.delete(*tree.get_children())
        for number, (x, y) in enumerate(self.state.settings["ac_spots"], start=1):
            tree.insert("", "end", iid=str(number - 1), values=(number, x, y))
        if select is not None and tree.exists(str(select)):
            tree.selection_set(str(select))
            tree.see(str(select))

    def _ac_selected_spot(self):
        chosen = self.ac_spot_tree.selection()
        return int(chosen[0]) if chosen else None

    def ac_add_spot(self):
        def chosen(x, y):
            spots = self.state.settings["ac_spots"] + [[x, y]]
            self.state.settings["ac_spots"] = spots
            self._ac_spots_reload(len(spots) - 1)
            self.state.save()
            hint = "" if self.v_ac_where.get() == AC_ROUTE else " Turn on Use this route to click it."
            self.log(f"Spot {len(spots)} added at {x}, {y}.{hint}")
        self.pick_point(chosen)

    def ac_remove_spot(self):
        index = self._ac_selected_spot()
        if index is None:
            return
        spots = [s for n, s in enumerate(self.state.settings["ac_spots"]) if n != index]
        self.state.settings["ac_spots"] = spots
        self._ac_spots_reload(min(index, len(spots) - 1) if spots else None)
        self.state.save()

    def ac_move_spot(self, offset):
        index = self._ac_selected_spot()
        spots = list(self.state.settings["ac_spots"])
        if index is None or not 0 <= index + offset < len(spots):
            return
        spots[index], spots[index + offset] = spots[index + offset], spots[index]
        self.state.settings["ac_spots"] = spots
        self._ac_spots_reload(index + offset)
        self.state.save()

    def _build_settings(self, page):
        page.columnconfigure(0, weight=1, uniform="settings")
        page.columnconfigure(1, weight=1, uniform="settings")
        left = tk.Frame(page, bg=BG)
        left.grid(row=0, column=0, sticky="new", padx=(0, 9))
        left.columnconfigure(0, weight=1)
        right = tk.Frame(page, bg=BG)
        right.grid(row=0, column=1, sticky="new", padx=(9, 0))
        right.columnconfigure(0, weight=1)

        detection = card(left, "Detection", "Used by Image, Hybrid and Reaction modes, and the Autoclicker's Smart stop.")
        detection.grid(row=0, column=0, sticky="ew")
        detection.columnconfigure(1, weight=1)
        self._field(detection, 2, "Scan every (ms)",
                    lambda p: ttk.Spinbox(p, from_=30, to=5000, increment=10, textvariable=self.v_scan_ms))
        self._field(detection, 3, "Screen", lambda p: ttk.Combobox(
            p, textvariable=self.v_monitor, values=self.screens, state="readonly"))
        ttk.Label(detection, text="Which app each mode runs in is picked at the top of that mode's page.",
                  style="Muted.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        for row, (text, var) in enumerate((("Find images at different sizes", self.v_multi),
                                           ("Backup detection if the normal check fails", self.v_backup),
                                           ("Use backup detection on every scan", self.v_backup_every)), start=6):
            ttk.Checkbutton(detection, text=text, variable=var).grid(row=row, column=0, columnspan=2, sticky="w",
                                                                    pady=(8 if row == 6 else 3, 0))

        zones = card(left, "Safe zones", "Places it must never click, like a toolbar or a close button. Used by every mode.")
        zones.grid(row=1, column=0, sticky="ew", pady=(18, 0))
        row = ttk.Frame(zones, style="Inner.TFrame")
        row.grid(row=2, column=0, sticky="ew")
        ttk.Label(row, textvariable=self.v_zones).pack(side="left")
        ttk.Button(row, text="Clear", command=self.clear_zones).pack(side="right")
        ttk.Button(row, text="Draw zone", style="Accent.TButton", command=self.draw_zone).pack(side="right", padx=6)
        self._show_zone_count()

        keys = card(right, "Hotkeys", "These work in any app, even full screen. On a laptop, F-keys may need Fn. "
                                      "If one doesn't react, pick another key or a mouse side button. Each mode can "
                                      "also have its own start and stop keys, under Runs in.")
        keys.grid(row=0, column=0, sticky="ew")
        keys.columnconfigure(1, weight=1)
        self.hotkey_vars = {}
        for row, (action, label) in enumerate(HOTKEY_ACTIONS, start=2):
            var = tk.StringVar(value=self.state.hotkeys[action])
            self.hotkey_vars[action] = var
            self._field(keys, row, label, lambda p, v=var, t=label: KeyBox(
                p, self, v, "hotkey", (("None", "No key"),), title=f"Hotkey: {t}"))
            var.trace_add("write", lambda *_a, a=action: self._hotkey_changed(a))
        seen = ttk.Frame(keys, style="Inner.TFrame")
        seen.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(seen, text="Last key seen", style="Muted.TLabel").pack(side="left")
        self.v_key_seen = tk.StringVar(value="press one to test")
        ttk.Label(seen, textvariable=self.v_key_seen, style="Chip.TLabel").pack(side="right")

        simple = card(right, "Simple mode", "Shows only Image, Classic and the Autoclicker, and hides Hybrid, "
                                            "Reaction and Profiles. Your macros are kept either way.")
        simple.grid(row=1, column=0, sticky="ew", pady=(18, 0))
        self.v_simple = tk.BooleanVar(value=self.state.prefs["simple"])
        ttk.Checkbutton(simple, text="Simple mode", variable=self.v_simple,
                        command=lambda: self.set_simple(self.v_simple.get())).grid(row=2, column=0, sticky="w")

    # ---- Simple mode
    def set_simple(self, on, confirmed=False):
        """Turn Simple mode on (after a warning) or off."""
        if on and not confirmed:
            self.v_simple.set(False)
            self._simple_dialog(warning_only=True)
            return
        self.state.prefs["simple"] = bool(on)
        self.state.prefs["asked_simple"] = True
        self.v_simple.set(bool(on))
        if on:
            for engine in self.background.values():
                engine.stop()
            if self.state.settings["mode"] in SIMPLE_HIDDEN:
                self.engine.stop()
                self.select_page("image")
            elif self.page in SIMPLE_HIDDEN:
                self.select_page(self.state.settings["mode"])
        self.state.save()
        self._show_nav()
        self.log("Simple mode is on. Turn it off in Settings to get every feature back." if on else
                 "Simple mode is off. Every mode and feature is back.")

    def _simple_dialog(self, warning_only=False):
        """First run: offer Simple mode. Choosing it shows a warning before it's turned on."""
        if self._picking_key():
            self.key_window.destroy()
        top = tk.Toplevel(self.root)
        top.title(APP_NAME)
        top.configure(bg=BG)
        top.transient(self.root)
        top.resizable(False, False)
        body = ttk.Frame(top, style="Card.TFrame", padding=22)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        body.columnconfigure(0, weight=1)
        title = ttk.Label(body, style="Section.TLabel")
        title.grid(row=0, column=0, sticky="w")
        text = ttk.Label(body, style="Muted.TLabel", wraplength=420, justify="left")
        text.grid(row=1, column=0, sticky="w", pady=(8, 18))
        buttons = ttk.Frame(body, style="Inner.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")

        def done():
            self.state.prefs["asked_simple"] = True
            self.state.save()
            top.destroy()

        def ask():
            for child in buttons.winfo_children():
                child.destroy()
            title.configure(text=f"Welcome to {APP_NAME}")
            text.configure(text="Want to keep things simple? Simple mode shows only the easy modes: Image, Classic "
                                "and the Autoclicker. You can change this later in Settings.")
            ttk.Button(buttons, text="Use Simple mode", style="Accent.TButton", takefocus=False,
                       command=warn).pack(side="right")
            ttk.Button(buttons, text="Show everything", takefocus=False, command=done).pack(side="right", padx=(0, 8))

        def warn():
            for child in buttons.winfo_children():
                child.destroy()
            title.configure(text="Turn on Simple mode?")
            text.configure(text="While Simple mode is on, you won't be able to use the complex features: Hybrid, "
                                "Reaction, and running profiles side by side. To get them back, turn Simple mode off "
                                "in Settings.")
            ttk.Button(buttons, text="Turn on Simple mode", style="Accent.TButton", takefocus=False,
                       command=lambda: (done(), self.set_simple(True, confirmed=True))).pack(side="right")
            ttk.Button(buttons, text="Cancel" if warning_only else "Go back", takefocus=False,
                       command=top.destroy if warning_only else ask).pack(side="right", padx=(0, 8))

        warn() if warning_only else ask()
        top.protocol("WM_DELETE_WINDOW", done if not warning_only else top.destroy)
        top.bind("<Escape>", lambda _e: (done() if not warning_only else top.destroy()))
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_force()
        self.simple_window = top  # tests reach in through this
        self.simple_buttons = buttons

    def hotkey_name(self, action):
        name = self.start_key() if action == "start" else self.stop_key() if action == "stop" else \
            self.state.hotkeys.get(action, "None")
        return "its hotkey" if name == "None" else name

    def _hotkey_changed(self, action):
        name = self.hotkey_vars[action].get()
        if name not in HOTKEY_KEYS:
            return
        if name == self.state.hotkeys.get(action):
            return
        self.state.hotkeys[action] = name
        self._free_key(name, ("global", action))
        if action == "start":  # a mode whose own key is now the main key just uses the main key
            for keys in self.state.settings["mode_keys"].values():
                if keys.get("stop") and keys.get("stop") == (keys.get("start") or name):
                    keys.pop("stop")
        self.state.save()
        self._show_mode_keys()
        for steps in (self.classic, self.hybrid):
            steps.show_recording(steps is getattr(self, "recording_into", None) and bool(self.recorder))

    # ---- pages
    def select_page(self, name):
        if self.state.prefs["simple"] and name in SIMPLE_HIDDEN:
            name = "image"
        if name in MODES and name != self.state.settings["mode"]:
            if self.engine.running:
                self.engine.stop()
                self.log("Stopped, because you switched modes.")
            self.state.settings["mode"] = name
            self.state.save()
        self.page = name
        self.pages[name].tkraise()
        if name == "profiles":
            self._profiles_reload()
        if name == "presets" and not self.preset_list and not self.preset_loading:
            self.refresh_presets()
        for key, item in self.nav.items():
            item.set(key == name)
            item.badge.configure(text="●" if key == self.state.settings["mode"] else "")
        title, _, about = MODE_INFO[name]
        self.title_label.configure(text=title if name in ("settings", "autoclick", "profiles", "presets") else
                                   f"{title} macro")
        self.subtitle_label.configure(text=about)
        if name == "image":
            self._tab_changed()
        self._paint_start()

    @property
    def active(self):
        """The list that snips, pasted images and added files go into."""
        mode = self.page if self.page in MODES else self.state.settings["mode"]
        if mode == "image":
            return self.lists[self.notebook.index("current")] if hasattr(self, "notebook") else self.farm
        return {"classic": self.classic, "hybrid": self.hybrid, "reaction": self.reactions,
                "autoclick": self.watch}[mode]

    def _tab_changed(self, _event=None):
        if hasattr(self, "farm"):
            (self.farm, self.auto)[self.notebook.index("current")].editor.tkraise()

    def _field(self, parent, row, label, widget_factory):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        widget = widget_factory(parent)
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        return widget

    def _monitor_text(self, index):
        return self.screens[index - 1 if 1 <= index <= len(self.screens) else 0]

    def _spam_range(self, parent):
        box = ttk.Frame(parent, style="Inner.TFrame")
        ttk.Spinbox(box, from_=20, to=5000, increment=10, width=6, textvariable=self.v_spam_min).pack(
            side="left", fill="x", expand=True)
        ttk.Label(box, text="to").pack(side="left", padx=8)
        ttk.Spinbox(box, from_=20, to=5000, increment=10, width=6, textvariable=self.v_spam_max).pack(
            side="left", fill="x", expand=True)
        return box

    # ---- profiles
    def _show_profile_names(self):
        self.profile_box["values"] = self.state.profile_names()
        self.v_profile.set(self.state.active_profile)
        if hasattr(self, "profile_tree"):
            self._profiles_reload()

    # ---- Profiles page: several profiles running at once
    def _build_profiles_page(self, page):
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, minsize=340)
        page.rowconfigure(0, weight=1)
        listing = card(page, "Your profiles", "The open profile starts with the Start button and its keys as usual. "
                                              "Any other profile runs in the background, next to it, when you press "
                                              "its profile key. Every key does only one job, so each profile needs "
                                              "a different one.")
        listing.grid(row=0, column=0, sticky="nsew")
        listing.rowconfigure(2, weight=1)
        holder = ttk.Frame(listing, style="Inner.TFrame")
        holder.grid(row=2, column=0, columnspan=2, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=("name", "mode", "key", "status"), show="headings",
                            style="Steps.Treeview", selectmode="browse")
        for column, text, width in (("name", "Profile", 150), ("mode", "Mode", 100), ("key", "Profile key", 110),
                                    ("status", "Status", 120)):
            tree.heading(column, text=text, anchor="w")
            tree.column(column, width=width, stretch=column == "name", anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<<TreeviewSelect>>", lambda _e: self._profile_selected())
        tree.bind("<Double-1>", lambda _e: self.open_selected_profile())
        self.profile_tree = tree
        bar = ttk.Frame(listing, style="Inner.TFrame")
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(bar, text="Stop all", style="Danger.TButton", command=self.stop_all).pack(side="right")

        side = tk.Frame(page, bg=BG)
        side.grid(row=0, column=1, sticky="new", padx=(18, 0))
        side.columnconfigure(0, weight=1)
        chosen = card(side, "Selected profile")
        chosen.grid(row=0, column=0, sticky="ew")
        chosen.columnconfigure(1, weight=1)
        self.v_profile_pick = tk.StringVar(value="")
        ttk.Label(chosen, textvariable=self.v_profile_pick, style="Chip.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 8))
        self.v_profile_key = tk.StringVar(value="")
        self._field(chosen, 2, "Profile key", lambda p: KeyBox(
            p, self, self.v_profile_key, "hotkey", (("", "No key"),), title="Profile key"))
        self.v_profile_key.trace_add("write", lambda *_a: self._profile_key_picked())
        buttons = ttk.Frame(chosen, style="Inner.TFrame")
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.profile_run_button = ttk.Button(buttons, text="Start", style="Accent.TButton",
                                             command=self.run_selected_profile)
        self.profile_run_button.pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="Open", command=self.open_selected_profile).pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Label(chosen, text="Its key starts it with the mode it was saved in, and pressing it again stops it. "
                               "Hold to click works too.", style="Muted.TLabel", wraplength=290,
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tips = card(side, "Good to know")
        tips.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        ttk.Label(tips, text="Profiles running together share one mouse and keyboard, so pick ones that don't get "
                             "in each other's way, like a key spammer next to an image clicker. Switching profiles "
                             "keeps a running one going in the background if it has a profile key.",
                  style="Muted.TLabel", wraplength=290, justify="left").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.showing_profile_key = False
        self._profiles_reload()

    # ---- Presets page
    def _build_presets_page(self, page):
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, minsize=340)
        page.rowconfigure(0, weight=1)
        listing = card(page, "Shared presets", "Made by people who use MacroEZ, and checked before they're listed. "
                                               "Adding one never starts it: you look it over and press Start yourself.")
        listing.grid(row=0, column=0, sticky="nsew")
        listing.rowconfigure(2, weight=1)
        holder = ttk.Frame(listing, style="Inner.TFrame")
        holder.grid(row=2, column=0, columnspan=2, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=("name", "mode", "by"), show="headings", style="Steps.Treeview",
                            selectmode="browse")
        for column, text, width in (("name", "Preset", 260), ("mode", "Mode", 110), ("by", "Shared by", 140)):
            tree.heading(column, text=text, anchor="w")
            tree.column(column, width=width, stretch=column == "name", anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<<TreeviewSelect>>", lambda _e: self._preset_selected())
        tree.bind("<Double-1>", lambda _e: self.preview_selected_preset())
        self.preset_tree = tree
        bar = ttk.Frame(listing, style="Inner.TFrame")
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(bar, text="Refresh", command=self.refresh_presets).pack(side="left")
        ttk.Button(bar, text="Open a preset file", command=self.open_preset_file).pack(side="left", padx=6)
        self.v_preset_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.v_preset_status, style="Muted.TLabel").pack(side="right")

        side = tk.Frame(page, bg=BG)
        side.grid(row=0, column=1, sticky="new", padx=(18, 0))
        side.columnconfigure(0, weight=1)
        chosen = card(side, "Selected preset")
        chosen.grid(row=0, column=0, sticky="ew")
        self.v_preset_name, self.v_preset_info = tk.StringVar(value="Pick one on the left."), tk.StringVar(value="")
        ttk.Label(chosen, textvariable=self.v_preset_name, style="Chip.TLabel").grid(row=1, column=0, sticky="w",
                                                                                   pady=(8, 6))
        ttk.Label(chosen, textvariable=self.v_preset_info, style="Muted.TLabel", wraplength=290,
                  justify="left").grid(row=2, column=0, sticky="w")
        ttk.Button(chosen, text="Preview and add", style="Accent.TButton", command=self.preview_selected_preset).grid(
            row=3, column=0, sticky="ew", pady=(12, 0))
        share = card(side, "Share yours")  # a fixed-width hint: a card-wide one would fight the list for room
        share.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        ttk.Label(share, text="Turn the open profile into a preset for everyone. Once it's checked, it shows up "
                              "here and on macroez.com.", style="Muted.TLabel", wraplength=290,
                  justify="left").grid(row=1, column=0, sticky="w", pady=(2, 10))
        ttk.Button(share, text="Share this profile", command=self.share_profile).grid(row=2, column=0, sticky="ew")
        ttk.Button(share, text="See presets on the website", style="Small.TButton",
                   command=lambda: webbrowser.open(f"{PRESET_SITE}/presets/")).grid(row=3, column=0, sticky="ew",
                                                                                     pady=(8, 0))

    def refresh_presets(self):
        if self.preset_loading:
            return
        self.preset_loading = True
        self.v_preset_status.set("Loading…")

        def work():
            try:
                listing = json.loads(fetch(f"{PRESET_SITE}/presets/index.json", 2_000_000).decode("utf-8"))
                items = [p for p in listing.get("presets", []) if isinstance(p, dict)
                         and isinstance(p.get("id"), str) and PRESET_ID.match(p["id"])]
                result = (items, "")
            except (OSError, ValueError, AttributeError, PresetError):
                result = (None, "Couldn't reach macroez.com. Check your internet, then press Refresh.")
            self.events.put(("call", lambda: self._presets_loaded(*result)))

        threading.Thread(target=work, daemon=True).start()

    def _presets_loaded(self, items, problem):
        self.preset_loading = False
        if items is None:
            self.v_preset_status.set(problem)
            return
        self.preset_list = items
        tree = self.preset_tree
        tree.delete(*tree.get_children())
        for item in items:
            mode = item.get("mode") if item.get("mode") in MODES else "image"
            tree.insert("", "end", iid=item["id"], values=(str(item.get("name", item["id"]))[:60], MODE_INFO[mode][0],
                                                           str(item.get("author", ""))[:40]))
        self.v_preset_status.set(f"{len(items)} preset{'s' if len(items) != 1 else ''}" if items else
                                 "No presets yet. Be the first to share one!")
        if items:
            tree.selection_set(items[0]["id"])

    def _preset_selected(self):
        selected = self.preset_tree.selection()
        item = next((p for p in self.preset_list if selected and p["id"] == selected[0]), None)
        if item:
            self.v_preset_name.set(str(item.get("name", item["id"]))[:60])
            self.v_preset_info.set(str(item.get("description", ""))[:300] or "No description.")

    def preview_selected_preset(self):
        selected = self.preset_tree.selection()
        if selected:
            self.download_preset(selected[0])

    def download_preset(self, preset_id):
        if not PRESET_ID.match(preset_id or ""):
            return
        self.log(f"Downloading the preset from {PRESET_SITE}…")

        def work():
            try:
                preset = read_preset(fetch(f"{PRESET_SITE}/presets/{preset_id}/preset.zip"))
                done = lambda: self.preview_preset(preset)
            except PresetError as problem:
                done = lambda: self.log(str(problem))
            except (OSError, ValueError):
                done = lambda: self.log("Couldn't download that preset. Check your internet and try again.")
            self.events.put(("call", done))

        threading.Thread(target=work, daemon=True).start()

    def open_preset_file(self):
        chosen = filedialog.askopenfilename(parent=self.root, title="Open a MacroEZ preset",
                                            filetypes=[("MacroEZ presets", "*.zip"), ("All files", "*.*")])
        if not chosen:
            return
        try:
            self.preview_preset(read_preset(Path(chosen).read_bytes()))
        except (OSError, PresetError) as problem:
            self.log(str(problem) if isinstance(problem, PresetError) else "Couldn't open that file.")

    def preview_preset(self, preset):
        """Show everything a preset does before it's added. It's added as a profile, never started."""
        if self._picking_key():
            self.key_window.destroy()
        top = tk.Toplevel(self.root)
        top.title("Preview preset")
        top.configure(bg=BG)
        top.transient(self.root)
        body = card(top, preset["name"], preset["description"] or None)
        body.pack(fill="both", expand=True, padx=14, pady=14)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=preset_summary(preset), style="Chip.TLabel").grid(row=2, column=0, sticky="w")
        row = 3
        warnings = preset_warnings(preset)
        for warning in warnings + ["It isn't tied to an app yet. Pick one under Runs in before you start it."]:
            colour = AMBER if warning in warnings else MUTED
            tk.Label(body, text=("\u26a0  " if warning in warnings else "") + warning, bg=PANEL, fg=colour,
                     font=(self.font, 9), wraplength=480, justify="left").grid(row=row, column=0, sticky="w",
                                                                              pady=(8, 0))
            row += 1
        steps = tk.Text(body, height=12, width=64, bg=FIELD, fg=TEXT, bd=0, highlightthickness=0, padx=10, pady=8,
                        font=(self.font, 9), wrap="word")
        steps.insert("1.0", "\n".join(preset_lines(preset)) or "No steps.")
        steps.configure(state="disabled")
        steps.grid(row=row, column=0, sticky="nsew", pady=(12, 0))
        body.rowconfigure(row, weight=1)
        names = {n.lower() for n in self.state.profile_names()}
        name, number = preset["name"], 2
        while name.lower() in names:
            name, number = f"{preset['name']} {number}", number + 1
        line = ttk.Frame(body, style="Inner.TFrame")
        line.grid(row=row + 1, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(line, text="Save as profile").pack(side="left", padx=(0, 10))
        v_name = tk.StringVar(value=name)
        ttk.Entry(line, textvariable=v_name).pack(side="left", fill="x", expand=True)

        def add():
            if self.state.prefs["simple"] and preset["mode"] in SIMPLE_HIDDEN:
                return
            chosen = v_name.get().strip()[:40]
            if not chosen or chosen.lower() in {n.lower() for n in self.state.profile_names()}:
                v_name.set(name)
                return
            install_preset(self.state, preset, chosen)
            top.destroy()
            self.engine.stop()
            self.v_profile.set(chosen)
            self.switch_profile()
            self.log(f"Added the {preset['name']} preset as {chosen}. Look over its steps, pick where it runs "
                     f"under Runs in, then press {self.hotkey_name('start')} to start it.")

        buttons = ttk.Frame(body, style="Inner.TFrame")
        buttons.grid(row=row + 2, column=0, sticky="ew", pady=(14, 0))
        hidden = self.state.prefs["simple"] and preset["mode"] in SIMPLE_HIDDEN
        add_button = ttk.Button(buttons, text="Add as a profile", style="Accent.TButton", command=add)
        add_button.pack(side="right")
        if hidden:
            add_button.state(["disabled"])
            ttk.Label(buttons, text=f"{MODE_INFO[preset['mode']][0]} mode is hidden in Simple mode. Turn Simple "
                                    "mode off in Settings to add this.", style="Muted.TLabel", wraplength=300,
                      justify="left").pack(side="left")
        ttk.Button(buttons, text="Cancel", command=top.destroy).pack(side="right", padx=(0, 8))
        top.bind("<Escape>", lambda _e: top.destroy())
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - top.winfo_height()) // 3, 0)
        top.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        top.grab_set()
        top.focus_force()
        self.preset_window, self.preset_add, self.preset_name_var = top, add, v_name  # tests reach in through these

    def share_profile(self):
        """Save the open profile as a preset file, then open the page where it's handed in for checking."""
        name = self.state.active_profile
        description = simpledialog.askstring(
            APP_NAME, f"Sharing {name}.\n\nIn a sentence, what does it do?", parent=self.root)
        if description is None:
            return
        blob = export_preset(self.state, name, description)
        images = len(read_preset(blob)["images"])
        if images and not messagebox.askokcancel(
                APP_NAME, f"This preset includes {images} image{'s' if images != 1 else ''} you snipped. Everyone "
                          "will be able to see them, so check they don't show anything private, like your name, "
                          "email or messages.", parent=self.root):
            return
        slug = re.sub(r"[^A-Za-z0-9 _-]+", "", name).strip() or "preset"
        target = filedialog.asksaveasfilename(parent=self.root, title="Save the preset file",
                                              initialfile=f"{slug}.macroez.zip", defaultextension=".zip",
                                              filetypes=[("MacroEZ presets", "*.zip")])
        if not target:
            return
        Path(target).write_bytes(blob)
        query = urllib.parse.urlencode({"template": "preset.yml", "title": f"Preset: {name}", "name": name,
                                        "description": description, "mode": MODE_INFO[self.state.settings["mode"]][0]})
        webbrowser.open(f"https://github.com/{PRESET_REPO}/issues/new?{query}")
        self.log(f"Saved {Path(target).name}. On the page that just opened, drag that file into the Preset file "
                 "box and press Create. It shows up for everyone once it's been checked.")

    def _check_pending_preset(self):
        """A macroez:// link opened while MacroEZ was already running."""
        try:
            if not PENDING_PRESET.exists():
                return
            text = PENDING_PRESET.read_text(encoding="utf-8")[:300]
            PENDING_PRESET.unlink()
        except OSError:
            return
        preset_id = preset_link_id(text)
        if preset_id:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.download_preset(preset_id)

    def _profile_status(self, name):
        if name == self.state.active_profile:
            if self.engine.running:
                return "Running (open)"
            return "Open"
        engine = self.background.get(name)
        return "Running" if engine and engine.running else "Stopped"

    def _profiles_reload(self):
        tree = self.profile_tree
        keep = tree.selection()
        tree.delete(*tree.get_children())
        for name in self.state.profile_names():
            tree.insert("", "end", iid=name, values=(name, MODE_INFO[self.state.profile_mode(name)][0],
                                                     key_label(self.state.profile_key(name), "hotkey") or "\u2014",
                                                     self._profile_status(name)))
        pick = keep[0] if keep and tree.exists(keep[0]) else self.state.active_profile
        if tree.exists(pick):
            tree.selection_set(pick)
        self._profile_selected()

    def _profile_selected(self):
        selected = self.profile_tree.selection()
        if not selected:
            return
        name = selected[0]
        self.v_profile_pick.set(name)
        self.showing_profile_key = True
        try:
            self.v_profile_key.set(self.state.profile_key(name))
        finally:
            self.showing_profile_key = False
        self._show_profile_run_button()

    def _show_profile_run_button(self):
        selected = self.profile_tree.selection()
        if not selected:
            return
        name = selected[0]
        engine = self.engine if name == self.state.active_profile else self.background.get(name)
        running = bool(engine and engine.running)
        self.profile_run_button.configure(text="Stop" if running else "Start",
                                          style="Danger.TButton" if running else "Accent.TButton")

    def _profile_key_picked(self):
        if self.showing_profile_key or not self.profile_tree.selection():
            return
        self.set_profile_key(self.profile_tree.selection()[0], self.v_profile_key.get())

    def set_profile_key(self, name, key):
        if key not in HOTKEY_KEYS or key == "None":
            key = ""
        self.state.profile_settings(name)["profile_key"] = key
        if key:
            self._free_key(key, ("profile", name))
        self.state.save()
        self._profiles_reload()
        self.log(f"{key} now starts and stops {name}, from anywhere." if key else f"{name} has no profile key now.")

    def _profile_hotkey(self, name, down=True):
        """A profile key: the open profile toggles as usual, any other one runs in the background."""
        if name not in self.state.profile_names():
            return
        if name == self.state.active_profile:
            hold = self._holding_to_click()
            if not down:
                if hold and self.engine.running:
                    self.engine.stop()
                return
            if self.recorder:
                self.toggle_recording()
            elif hold:
                if not self.engine.running:
                    self.toggle(quiet=True)
            else:
                self.toggle()
            return
        engine = self.background.get(name)
        hold = self.state.profile_mode(name) == "autoclick" and \
            self.state.profile_settings(name).get("ac_trigger") == AC_HOLD
        if not down:
            if hold and engine and engine.running:
                engine.stop()
            return
        if hold and engine and engine.running:
            return
        self.toggle_background(name, quiet=hold)

    def toggle_background(self, name, quiet=False):
        engine = self.background.get(name)
        if engine and engine.running:
            engine.stop()
            self.log(f"Stopped {name}.")
            return
        key = self.state.profile_key(name)
        if not key:
            self.log(f"Give {name} its own profile key first, so you can stop it from anywhere while it runs in "
                     "the background.")
            return
        view = self.state.view(name)
        problem = macro_problem(view)
        if problem:
            self.log(f"{name}: {problem}")
            return
        engine = Engine(view, NamedEvents(name, self.events))
        engine.start()
        self.background[name] = engine
        if not quiet:
            self.log(f"Started {name} ({MODE_INFO[view.settings['mode']][0]} mode) in the background. "
                     f"{key} stops it.")

    def run_selected_profile(self):
        selected = self.profile_tree.selection()
        if not selected:
            return
        name = selected[0]
        if name == self.state.active_profile:
            self.toggle()
        else:
            self.toggle_background(name)
        self._profiles_reload()

    def open_selected_profile(self):
        selected = self.profile_tree.selection()
        if selected and selected[0] != self.state.active_profile:
            self.v_profile.set(selected[0])
            self.switch_profile()
            self.select_page("profiles")

    def stop_all(self):
        count = sum(e.running for e in (self.engine, *self.background.values()))
        self.engine.stop()
        for engine in self.background.values():
            engine.stop()
        self.background.clear()
        self.log(f"Stopped {count} running profile{'s' if count != 1 else ''}." if count else "Nothing was running.")
        self._profiles_reload()

    def _leave_profile(self, old, was_running):
        """The open profile is being swapped out: keep it going in the background if it has its own key."""
        if not was_running or old not in self.state.profiles:
            return
        if self.state.profile_key(old):
            self.toggle_background(old, quiet=True)
            self.log(f"{old} keeps running in the background. {self.state.profile_key(old)} stops it.")
        else:
            self.log(f"Stopped {old}. Give it a profile key on the Profiles page to keep it running while you "
                     "switch.")

    def _adopt_background(self):
        """The profile just opened was running in the background: carry on running it here."""
        engine = self.background.pop(self.state.active_profile, None)
        if engine and engine.running:
            engine.stop()
            self.toggle(quiet=True)
            self.log(f"{self.state.active_profile} is still running, now as the open profile.")

    def _fix_key_clashes(self):
        """A profile just opened: its mode keys must not take another profile's key."""
        taken = {self.state.profile_key(n): n for n in self.state.profile_names()}
        for mode, keys in self.state.settings["mode_keys"].items():
            for which, name in list(keys.items()):
                if name in taken:
                    keys.pop(which)
                    self.log(f"{name} is {taken[name]}'s profile key, so {MODE_INFO[mode][0]} mode's {which} key "
                             "went back to the default.")

    def _show_macro(self):
        """Refill the whole window from the profile that was just loaded."""
        s = self.state.settings
        self.showing_profile = True
        for key, var in self.setting_vars.items():
            var.set(s[key])
        self.v_monitor.set(self._monitor_text(s["monitor"]))
        self.showing_profile = False
        self._show_zone_count()
        self.engine.scores.clear()
        for item_list in (*self.lists, self.classic, self.hybrid):
            item_list.reload()
        self.farm.apply_order_view()
        self._ac_spots_reload()
        self._show_rhythm()
        self._fix_key_clashes()
        self._show_run_in()
        self._show_profile_names()
        self.select_page(s["mode"])

    def _ask_profile_name(self, prompt, initial="", keep=None):
        """Returns a clean unused name, or None if cancelled or unusable."""
        name = simpledialog.askstring(APP_NAME, prompt, initialvalue=initial, parent=self.root)
        name = (name or "").strip()[:40]
        if not name:
            return None
        if name.lower() in {n.lower() for n in self.state.profile_names() if n != keep}:
            self.log(f"There's already a profile called {name}. Pick a different name.")
            return None
        return name

    def switch_profile(self, _event=None):
        name = self.v_profile.get()
        if name == self.state.active_profile:
            return
        old, was_running = self.state.active_profile, self.engine.running
        self.engine.stop()
        self.state.switch_profile(name)
        self._show_macro()
        self.log(f"Switched to {name}. Press {self.hotkey_name('start')} to start it.")
        self._leave_profile(old, was_running)
        self._adopt_background()
        self._profiles_reload()

    def new_profile(self):
        name = self._ask_profile_name("Name for the new, empty profile:")
        if name:
            old, was_running = self.state.active_profile, self.engine.running
            self.engine.stop()
            self.state.new_profile(name, blank=True)
            self._show_macro()
            self._leave_profile(old, was_running)
            self.log(f"Created {name} with empty lists. Your settings and safe zones carried over.")

    def save_profile_as(self):
        name = self._ask_profile_name("Save this macro as a new profile called:")
        if name:
            self.state.new_profile(name, blank=False)
            self._show_profile_names()
            self.log(f"Saved a copy as {name}, and you're on it now.")

    def rename_profile(self):
        old = self.state.active_profile
        name = self._ask_profile_name("Rename this profile to:", initial=old, keep=old)
        if name and name != old:
            self.state.rename_profile(name)
            self._show_profile_names()
            self.log(f"Renamed {old} to {name}.")

    def delete_profile(self):
        name = self.state.active_profile
        if not self.state.profiles:
            self.log("This is your only profile, so it can't be deleted.")
            return
        if not messagebox.askyesno(APP_NAME, f"Delete the profile {name}? This can't be undone.", parent=self.root):
            return
        self.engine.stop()
        self.state.delete_profile()
        self._show_macro()
        self.log(f"Deleted {name}. Now on {self.state.active_profile}.")
        self._adopt_background()
        self._profiles_reload()

    # ---- adding images to whichever list is open
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Choose images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        for path in paths:
            self._add_path(path)

    def _add_path(self, path):
        try:
            with Image.open(path) as image:
                self.active.add(image, Path(path).stem)
        except OSError:
            messagebox.showerror(APP_NAME, f"That file isn't an image I can read:\n{path}")

    def paste(self):
        clip = ImageGrab.grabclipboard()
        if isinstance(clip, list):
            for path in clip:
                self._add_path(path)
        elif clip is not None:
            self.active.add(clip, self.active.next_name())
        else:
            self.log("No image on the clipboard. Copy one first (Win+Shift+S works).")

    def _settings_changed(self, *_):
        if self.showing_profile:  # half-filled boxes must not overwrite the profile just loaded
            return
        s = self.state.settings
        s["spam_enabled"] = bool(self.v_spam.get())
        s["spam_key"] = self.v_key.get()
        s["spam_min_ms"] = int(parse_number(self.v_spam_min.get(), s["spam_min_ms"], 20))
        s["spam_max_ms"] = int(parse_number(self.v_spam_max.get(), s["spam_max_ms"], 20))
        s["scan_interval_ms"] = int(parse_number(self.v_scan_ms.get(), s["scan_interval_ms"], 30))
        s["only_process"] = self.v_process.get().strip()
        s["monitor"] = int(self.v_monitor.get().split(":")[0])
        s["multiscale"] = bool(self.v_multi.get())
        s["reconnect_enabled"] = bool(self.v_reconnect.get())
        s["backup_every_scan"] = bool(self.v_backup_every.get())
        s["backup_enabled"] = bool(self.v_backup.get())
        s["farm_in_order"] = bool(self.v_order.get())
        s["order_timeout"] = parse_number(self.v_order_timeout.get(), s["order_timeout"], 1)
        s["classic_loops"] = int(parse_number(self.v_classic_loops.get(), s["classic_loops"]))
        s["hybrid_loops"] = int(parse_number(self.v_hybrid_loops.get(), s["hybrid_loops"]))
        s["ac_button"] = self.v_ac_button.get() if self.v_ac_button.get() in MOUSE_BUTTONS else s["ac_button"]
        s["ac_double"] = bool(self.v_ac_double.get())
        s["ac_cps"] = min(round(parse_number(self.v_ac_cps.get(), s["ac_cps"], 0.1), 2), 100.0)
        s["ac_variation"] = int(min(parse_number(self.v_ac_variation.get(), s["ac_variation"]), 90))
        s["ac_use_rhythm"] = bool(self.v_ac_rhythm_on.get())
        s["ac_where"] = self.v_ac_where.get() if self.v_ac_where.get() in (AC_AT_MOUSE, AC_ROUTE) else s["ac_where"]
        s["ac_per_spot"] = max(int(parse_number(self.v_ac_per_spot.get(), s["ac_per_spot"], 1)), 1)
        s["ac_wobble"] = int(min(parse_number(self.v_ac_wobble.get(), s["ac_wobble"]), 200))
        s["ac_trigger"] = self.v_ac_trigger.get() if self.v_ac_trigger.get() in (AC_TOGGLE, AC_HOLD) else s["ac_trigger"]
        s["ac_max_clicks"] = int(parse_number(self.v_ac_max_clicks.get(), s["ac_max_clicks"]))
        s["ac_max_minutes"] = round(parse_number(self.v_ac_max_minutes.get(), s["ac_max_minutes"]), 2)
        self.farm.apply_order_view()
        self.state.save()

    # ---- screen overlays: snip, safe zone, pick a spot
    def snip(self):
        if self.active is self.classic:
            self.log("Classic macros don't use images. Use Hybrid mode to mix in image steps.")
            return
        self._open_overlay("Drag around what to look for. Esc cancels.", self._snip_done, shapes=True)

    def draw_zone(self):
        self._open_overlay("Drag over the area it must never click, like a toolbar. Esc cancels.", self._zone_done)

    def pick_point(self, chosen):
        self._open_overlay("Click the spot it should click. Esc cancels.",
                           lambda _image, box, _monitor: chosen(box[0], box[1]), point=True)

    def cursor_on_screen(self):
        point = cursor_position()
        with mss.MSS() as sct:
            monitor = pick_monitor(sct, self.state.settings["monitor"])
        x = min(max(point[0] - monitor["left"], 0), monitor["width"] - 1)
        y = min(max(point[1] - monitor["top"], 0), monitor["height"] - 1)
        return x, y

    def _snip_done(self, image, box, _monitor, outline=None):
        piece = image.crop(box)
        if outline:
            piece, problem = cut_shape(image, outline)
            if piece is None:
                self.log(problem)
                if isinstance(self.active, ActionList):
                    self.active.pending = None
                return
        self.active.add(piece, self.active.next_name())

    def _zone_done(self, _image, box, monitor):
        left, top, right, bottom = box
        width, height = monitor["width"], monitor["height"]
        settings = self.state.settings
        settings["safe_zones"] = settings["safe_zones"] + [[left / width, top / height, right / width, bottom / height]]
        self._show_zone_count()
        self.state.save()
        self.log(f"Safe zone added. It will never click there. Press {self.hotkey_name('start')} to start again.")

    def clear_zones(self):
        self.state.settings["safe_zones"] = []
        self._show_zone_count()
        self.state.save()
        self.log("Safe zones cleared.")

    def _show_zone_count(self):
        count = len(self.state.settings["safe_zones"])
        self.v_zones.set(f"Safe zones: {count}")

    def _open_overlay(self, hint_text, done, point=False, shapes=False):
        if self.snipping:
            return
        if screen_capture_blocked():
            self.log("macOS is only letting MacroEZ see your wallpaper, not your apps. Turn on Screen Recording "
                     "for MacroEZ, then quit and reopen it.")
            screen_permission_help(self)
            return
        self.snipping = True
        self.engine.stop()
        self.root.withdraw()
        self.root.after(220, lambda: self._show_overlay(hint_text, done, point, shapes))

    def _show_overlay(self, hint_text, done, point=False, shapes=False):
        with mss.MSS() as sct:
            monitor = pick_monitor(sct, self.state.settings["monitor"])
            shot = sct.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        if image.size != (monitor["width"], monitor["height"]):  # Retina: match what the overlay shows
            image = image.resize((monitor["width"], monitor["height"]), Image.LANCZOS)
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry(f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}")
        canvas = tk.Canvas(top, highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        photo = ImageTk.PhotoImage(image)
        canvas.create_image(0, 0, image=photo, anchor="nw")
        canvas.photo = photo
        for left, upper, right, lower in zone_boxes(self.state.settings["safe_zones"], (image.height, image.width)):
            canvas.create_rectangle(left, upper, right, lower, outline=DANGER, width=2, fill=DANGER, stipple="gray25")
        hint = canvas.create_text(monitor["width"] // 2, 40, fill=TEXT, font=(self.font, 15, "bold"), text=hint_text)
        x0, y0, x1, y1 = canvas.bbox(hint)
        hint_box = canvas.create_rectangle(x0 - 18, y0 - 12, x1 + 18, y1 + 12, fill=BG, outline=GLASS)
        canvas.tag_lower(hint_box, hint)
        drag = {"x": 0, "y": 0, "box": None, "points": None,
                "shape": self.state.prefs.get("snip_shape", "box") if shapes else "box"}

        def show_hint(text):
            canvas.itemconfigure(hint, text=text)
            x0, y0, x1, y1 = canvas.bbox(hint)
            canvas.coords(hint_box, x0 - 18, y0 - 12, x1 + 18, y1 + 12)

        if shapes:
            def pick(shape):
                drag["shape"] = shape
                self.state.prefs["snip_shape"] = shape
                show_shape(shape)
                show_hint("Hold the mouse and draw around what to look for. Let go to finish. Esc cancels."
                          if shape == "draw" else
                          f"Drag around what to look for. Hold Shift to keep it even. Esc cancels.")

            bar, show_shape = shape_bar(top, drag["shape"], pick)
            canvas.create_window(monitor["width"] // 2, y1 + 22, window=bar, anchor="n")
            for number, shape in enumerate(SNIP_SHAPES, start=1):
                top.bind(str(number), lambda _e, s=shape: pick(s))
            if drag["shape"] != "box":
                pick(drag["shape"])

        def press(event):
            drag.update(x=event.x, y=event.y, points=None)
            if drag["box"]:
                canvas.delete(drag["box"])
            drag["box"] = None
            if point:
                return
            if drag["shape"] == "draw":
                drag["points"] = [(event.x, event.y)]
            else:
                drag["points"] = drag_outline(drag["shape"], (event.x, event.y), (event.x, event.y))
            drag["box"] = canvas.create_polygon(*flat(drag["points"]), outline=GLASS, fill="", width=2)

        def motion(event):
            if not drag["box"] or point:
                return
            if drag["shape"] == "draw":
                last = drag["points"][-1]
                if abs(event.x - last[0]) + abs(event.y - last[1]) < 3:
                    return
                drag["points"].append((event.x, event.y))
            else:
                drag["points"] = drag_outline(drag["shape"], (drag["x"], drag["y"]), (event.x, event.y),
                                              bool(event.state & 0x0001))
            canvas.coords(drag["box"], *flat(drag["points"]))

        def finish(box=None, outline=None):
            top.destroy()
            self.root.deiconify()
            self.snipping = False
            if box is not None:
                if outline:
                    done(image, box, monitor, outline=outline)
                else:
                    done(image, box, monitor)
            elif isinstance(self.active, ActionList):
                self.active.pending = None

        def release(event):
            if point:
                finish((event.x, event.y, event.x + 1, event.y + 1))
                return
            if drag["shape"] == "draw":
                points = drag["points"] or []
                if len(points) < 3:
                    self.log("Hold the mouse down and draw all the way around it, then let go.")
                    finish()
                    return
            else:
                points = drag_outline(drag["shape"], (drag["x"], drag["y"]), (event.x, event.y),
                                      bool(event.state & 0x0001))
            xs, ys = [x for x, _ in points], [y for _, y in points]
            left, upper, right, lower = math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys))
            if right - left < 6 or lower - upper < 6:
                self.log("That area was too small, try again.")
                finish()
                return
            finish((left, upper, right, lower), outline=None if drag["shape"] == "box" else points)

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", motion)
        canvas.bind("<ButtonRelease-1>", release)
        top.bind("<Escape>", lambda _e: finish())
        top.bind("<Button-3>", lambda _e: finish())
        top.focus_force()

    # ---- recording (Classic and Hybrid)
    def toggle_recording(self):
        if self.recorder:
            self._finish_recording()
            return
        mode = self.state.settings["mode"]
        if mode not in ("classic", "hybrid"):
            self.log("Recording works in Classic and Hybrid mode. Pick one of those first.")
            return
        if self.engine.running:
            self.engine.stop()
        recorder = Recorder(set(self._key_bindings()))
        try:
            recorder.start()
        except OSError as problem:
            self.log(str(problem))
            return
        self.recorder = recorder
        self.recording_into = self.classic if mode == "classic" else self.hybrid
        self.recording_into.show_recording(True)
        key = self.hotkey_name("record")
        self.log(f"Recording every key press, mouse click and scroll. Press {key} again to stop. "
                 "MacroEZ is minimized so you can get straight to your app.")
        self.root.iconify()

    def _finish_recording(self):
        events = self.recorder.stop()
        self.recorder = None
        target = self.recording_into
        target.show_recording(False)
        with mss.MSS() as sct:
            monitor = pick_monitor(sct, self.state.settings["monitor"])
        steps = recording_to_steps(events, monitor["left"], monitor["top"])
        self.root.deiconify()
        if not steps:
            self.log("Recording stopped. Nothing was pressed, clicked or scrolled, so no steps were added.")
            return
        target.add_recorded(steps)
        presses = sum(s.kind != "wait" for s in steps)
        seconds = (events[-1][0] - events[0][0]) if len(events) > 1 else 0
        self.log(f"Recorded {presses} key presses, clicks and scrolls over {seconds:.1f} s, added as "
                 f"{len(steps)} steps. Press {self.hotkey_name('start')} to play them back.")

    # ---- run control and event pump
    def toggle(self, quiet=False):
        if self.recorder:
            self._finish_recording()
            return
        if self.engine.running:
            self.engine.stop()
            self.log("Stopped.")
            return
        s = self.state.settings
        mode = s["mode"]
        if self.state.prefs["simple"] and mode in SIMPLE_HIDDEN:
            self.log(f"{MODE_INFO[mode][0]} mode is hidden in Simple mode. Turn Simple mode off in Settings to use it.")
            return
        problem = macro_problem(self.state)
        if problem:
            self.log(problem)
            return
        if mode in ("image", "hybrid", "reaction") and screen_capture_blocked():
            self.log("macOS is only letting MacroEZ see your wallpaper, so it can't find your pictures. Turn on "
                     "Screen Recording for MacroEZ, then quit and reopen it.")
            screen_permission_help(self)
            if mode != "hybrid":  # Hybrid can still press keys and click spots; its picture steps just won't match
                return
        self.engine.clicks = 0
        self.engine.start()
        if quiet:
            return
        where = run_in(s) or "any app"
        if mode == "autoclick" and s["ac_trigger"] == AC_HOLD:
            self.log(f"Started the Autoclicker. Next time, hold {self.hotkey_name('start')} to click and let go "
                     "to stop.")
            return
        self.log(f"Started {MODE_INFO[mode][0]} mode. It only acts while {where} is focused. "
                 f"{self.hotkey_name('stop')} stops it.")

    def _hotkey_loop(self):
        """Watch the keyboard directly, so hotkeys work even if a game or another app has grabbed them."""
        was_down = set()
        while not self.closing:
            try:
                wanted = self._key_bindings()
            except RuntimeError:  # a setting changed mid-read; try again next time round
                wanted = {}
            now = time.monotonic()
            for name, vk in HOTKEY_KEYS.items():
                if not vk:
                    continue
                down = key_is_down(vk)
                if down and vk not in was_down and now - _sent_at.get(vk, -1e9) > 0.3:
                    self.events.put(("keyseen", name))
                    if self._stop_now(vk, wanted.get(vk, ())):
                        was_down.add(vk)
                        continue
                    for action in wanted.get(vk, ()):
                        self.events.put(("hotkey", action))
                if down:
                    was_down.add(vk)
                elif vk in was_down:
                    was_down.discard(vk)
                    for action in wanted.get(vk, ()):
                        self.events.put(("hotkey_up", action))
            time.sleep(0.02)

    def _stop_now(self, vk, actions):
        """Runs on the key watcher: if this key stops something that's running, stop it this instant rather than
        on the window's next update. True if it did."""
        if not actions or vk in TYPING_VKS or self.key_window is not None or self.snipping or self.recorder:
            return False
        s = self.state.settings
        mode, active = s["mode"], self.state.active_profile
        stopped = False
        if self.engine.running and not (mode == "autoclick" and s["ac_trigger"] == AC_HOLD):
            if any(a in ("start", f"start:{mode}", f"stop:{mode}", f"profile:{active}") for a in actions):
                self.engine.stop()
                self.events.put(("log", "Stopped."))
                stopped = True
        for action in actions:
            if action.startswith("profile:"):
                name = action[len("profile:"):]
                engine = self.background.get(name)
                hold = (self.state.profile_mode(name) == "autoclick" and
                        self.state.profile_settings(name).get("ac_trigger") == AC_HOLD)
                if engine is not None and engine.running and not hold:
                    engine.stop()
                    self.events.put(("log", f"Stopped {name}."))
                    stopped = True
        return stopped

    def log(self, message):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 300:
            self.log_box.delete("1.0", "2.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start_text(self):
        running = self.engine.running
        key = self.stop_key() if running else self.start_key()
        label = "Stop" if running else "Start"
        return f"{label}   {key}" if key != "None" else label

    def _paint_start(self, hover=False):
        if not hasattr(self, "start_button"):
            return
        self.start_button.configure(text=self._start_text())
        self.start_button.configure(style="Stop.TButton" if self.engine.running else "Start.TButton")

    def _pump(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log(value)
                elif kind == "call":  # work finished on another thread, finished off here
                    value()
                elif kind == "keyseen":
                    self.v_key_seen.set(value)
                elif kind == "hotkey_up":
                    if self._picking_key():
                        continue
                    if value.startswith("profile:"):
                        self._profile_hotkey(value[len("profile:"):], down=False)
                        continue
                    if value in ("start", "start:autoclick") and self._holding_to_click() and self.engine.running:
                        self.engine.stop()
                elif kind == "hotkey":
                    if self.snipping or self._picking_key() or self._typing_here(value):
                        continue
                    if value.startswith("profile:"):
                        self._profile_hotkey(value[len("profile:"):])
                        continue
                    if ":" in value:
                        which, mode = value.split(":")
                        self._mode_hotkey(which, mode)
                        continue
                    shared = self.state.hotkeys["record"] == self.state.hotkeys["snip"]
                    mode = self.state.settings["mode"]
                    if value == "record":
                        if mode == "classic" or (mode == "hybrid" and not shared) or self.recorder:
                            self.toggle_recording()
                        elif not shared:
                            self.log("Recording works in Classic and Hybrid mode. Pick one of those first.")
                    elif self.recorder:
                        if value == "start":
                            self.toggle_recording()  # Start/stop while recording just ends the recording
                    elif value == "start":
                        if self._holding_to_click():
                            if not self.engine.running:
                                self.toggle(quiet=True)
                        else:
                            self.toggle()
                    elif value == "snip":
                        if not (shared and mode == "classic"):
                            self.snip()
                    elif value == "test":
                        self.engine.test_scan()
                    elif value == "zone":
                        self.draw_zone()
        except queue.Empty:
            pass
        running = self.engine.running
        status = self.engine.status if running else "STOPPED"
        colour = AMBER if status.startswith(("WAITING", "RECOVERING", "PAUSED", "CHECKING")) else \
            GOOD if running else MUTED
        if self.recorder:
            count = len(self.recorder.events)
            status, colour = f"RECORDING  {count} input{'s' if count != 1 else ''}", DANGER
        self.status_label.configure(text=status, fg=colour)
        self.status_dot.itemconfigure("dot", fill=colour)
        others = sum(engine.running for engine in self.background.values())
        self.running_mode_label.configure(text=f"{MODE_INFO[self.state.settings['mode']][0]} mode" + (
            f"\n+ {others} more profile{'s' if others != 1 else ''} running" if others else ""))
        if self.page == "profiles":
            for name in self.state.profile_names():
                if self.profile_tree.exists(name) and self.profile_tree.set(name, "status") != \
                        self._profile_status(name):
                    self.profile_tree.set(name, "status", self._profile_status(name))
            self._show_profile_run_button()
        if str(self.start_button.cget("style")) != ("Stop.TButton" if running else "Start.TButton") \
                or self.start_button.cget("text") != self._start_text():
            self._paint_start()
        count = self.engine.clicks
        self.clicks_label.configure(text=f"{count} action{'s' if count != 1 else ''}")
        for target_id, score in list(self.engine.scores.items()):
            for image_list in self.lists:
                if image_list.tree.exists(target_id):
                    image_list.tree.set(target_id, "score", f"{score:.0%}")
        if self.page == "autoclick":
            self._show_live(running)
        self.pump_count = getattr(self, "pump_count", 0) + 1
        if self.pump_count % 8 == 0:
            self._check_pending_preset()
        self.root.after(120, self._pump)

    def _holding_to_click(self):
        return self.state.settings["mode"] == "autoclick" and self.state.settings["ac_trigger"] == AC_HOLD

    def _show_live(self, running):
        engine, now = self.engine, time.monotonic()
        recent = sum(1 for moment in list(engine.ac_times) if now - moment <= 1.0) if running else 0
        elapsed = now - engine.ac_started if running and engine.ac_started else engine.ac_elapsed
        self.v_ac_live["cps"].set(str(recent))
        self.v_ac_live["clicks"].set(f"{engine.ac_done:,}")
        self.v_ac_live["time"].set(f"{int(elapsed // 60)}:{int(elapsed % 60):02d}")

    def _callback_error(self, *args):
        CRASH_LOG.write_text("".join(traceback.format_exception(*args)), encoding="utf-8")
        self.log(f"Something went wrong: {args[1]} (details in crash.log)")

    def close(self):
        self.closing = True
        if self.recorder:
            self.recorder.stop()
            self.recorder = None
        self.engine.stop()
        for engine in self.background.values():
            engine.stop()
        self.state.save()
        self.root.destroy()


if IS_MAC:
    import macroez_mac
    macroez_mac.install(globals())


def already_running():
    """True if another MacroEZ is open; two copies would fight over the mouse and the hotkeys."""
    if IS_MAC:
        return macroez_mac.already_running()
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    already_running.handle = kernel32.CreateMutexW(None, False, "MacroEZ-single-instance")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MacroEZ.App")  # taskbar shows our icon
    except (OSError, AttributeError):
        pass
    link = preset_link_id(sys.argv[1]) if len(sys.argv) > 1 else None
    if already_running():
        if link:
            try:
                PENDING_PRESET.write_text(f"macroez://preset/{link}", encoding="utf-8")
            except OSError:
                pass
        else:
            hidden = tk.Tk()
            hidden.withdraw()
            messagebox.showinfo(APP_NAME, "MacroEZ is already open. Check your taskbar.")
            hidden.destroy()
    else:
        register_link_handler()
        app = App()
        if os.environ.get("MACROEZ_SELFTEST"):  # build checks: open, report, close
            def report():
                (APP_DIR / "selftest.txt").write_text(f"ok {sys.platform} {len(app.pages)} pages", encoding="utf-8")
                app.close()
            app.root.after(2500, report)
        elif IS_MAC:
            macroez_mac.attach(app)
        if link:
            app.root.after(800, lambda: app.download_preset(link))
        app.root.mainloop()
