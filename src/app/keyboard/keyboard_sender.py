from __future__ import annotations
import time
import ctypes
from ctypes import wintypes

from app.app_logging import logging


logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# В некоторых версиях Python нет wintypes.ULONG_PTR — подменяем на WPARAM
ULONG_PTR = getattr(wintypes, "ULONG_PTR", wintypes.WPARAM)


# --- структуры из directkeys.py ---

class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        )

    _anonymous_ = ("_input",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    )


LPINPUT = ctypes.POINTER(INPUT)


# Виртуальные коды клавиш
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_V = 0x56


def _check_count(result, func, args):
    # Если SendInput вернул 0 — поднимем нормальный WinError, чтобы видеть причину
    if result == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    return args


user32.SendInput.errcheck = _check_count
user32.SendInput.argtypes = (wintypes.UINT, LPINPUT, ctypes.c_int)


# Печать юникод-строки в активное окно
def send_unicode_text(text: str, per_char_delay: float = 0.0):
    """
    Печатает текст как последовательность Unicode-клавиш.
    Не зависит от раскладки, главное – активное окно (WoW / блокнот).
    """
    text = text or ""
    if not text:
        return

    inputs = []

    for ch in text:
        code = ord(ch)

        down = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=0,
                wScan=code,
                dwFlags=KEYEVENTF_UNICODE,
                time=0,
                dwExtraInfo=0,
            ),
        )
        up = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=0,
                wScan=code,
                dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                time=0,
                dwExtraInfo=0,
            ),
        )

        if per_char_delay:
            arr = (INPUT * 2)(down, up)
            user32.SendInput(2, arr, ctypes.sizeof(INPUT))
            time.sleep(per_char_delay)
        else:
            inputs.append(down)
            inputs.append(up)

    if inputs and not per_char_delay:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def send_vk(vk: int, keyup: bool = False):
    flags = KEYEVENTF_KEYUP if keyup else 0
    inp = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


# todo Не используется
def press_enter():
    send_vk(VK_RETURN, keyup=False)
    send_vk(VK_RETURN, keyup=True)


def press_ctrl_v():
    # Ctrl down
    send_vk(VK_CONTROL, keyup=False)
    # V down/up
    send_vk(VK_V, keyup=False)
    send_vk(VK_V, keyup=True)
    # Ctrl up
    send_vk(VK_CONTROL, keyup=True)
