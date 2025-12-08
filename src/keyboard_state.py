import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# сигнатура GetAsyncKeyState
user32.GetAsyncKeyState.argtypes = [wintypes.INT]
user32.GetAsyncKeyState.restype  = wintypes.SHORT

MOUSE_VKS = {0x01, 0x02, 0x04, 0x05, 0x06}  # LBUTTON, RBUTTON, MBUTTON, XBUTTON1, XBUTTON2


def keyboard_is_clean() -> bool:
    """
    True -> на клавиатуре ничего не зажато (кнопки мыши игнорируем).
    """
    for vk in range(256):
        if vk in MOUSE_VKS:
            continue
        state = user32.GetAsyncKeyState(vk)
        if state & 0x8000:  # старший бит = клавиша сейчас зажата
            return False
    return True


def wait_for_keyboard_clean(stable_ms: int = 150, timeout_s: float = 3.0) -> bool:
    deadline = time.time() + timeout_s
    clean_since = None

    while time.time() < deadline:
        if keyboard_is_clean():
            if clean_since is None:
                clean_since = time.time()
            elif (time.time() - clean_since) * 1000 >= stable_ms:
                return True
        else:
            clean_since = None

        time.sleep(0.01)  # 10 мс

    return False
