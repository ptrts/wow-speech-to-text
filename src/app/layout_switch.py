import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

KLF_ACTIVATE = 0x00000001
WM_INPUTLANGCHANGEREQUEST = 0x0050

user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
user32.LoadKeyboardLayoutW.restype = wintypes.HKL

user32.GetForegroundWindow.restype = wintypes.HWND
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def switch_to_russian():
    hkl = user32.LoadKeyboardLayoutW("00000419", KLF_ACTIVATE)
    if not hkl:
        raise ctypes.WinError(ctypes.get_last_error())

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("Не удалось получить foreground window")

    # wParam обычно 0, lParam — HKL
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
