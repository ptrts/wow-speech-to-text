import win32clipboard as cb
import win32con


def clipboard_copy(text: str):
    # Пишем в буфер обмена Юникод-строку
    cb.OpenClipboard()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        cb.CloseClipboard()
