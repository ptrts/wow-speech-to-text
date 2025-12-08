import threading
import time

import win32con
import win32gui
import win32api

# ===== Глобальное состояние оверлея =====

CURRENT_TEXT = ""
HWND = None
H_FONT = None  # сюда положим большой шрифт

WM_UPDATE_TEXT = win32con.WM_USER + 1


# ===== Публичные функции для использования из других модулей =====

def show_text(message: str, duration: float | None = None):
    """
    Показать текст на оверлее.
    duration (сек) — если задано, через это время текст исчезнет.
    Можно вызывать из любого потока.
    """
    global CURRENT_TEXT

    CURRENT_TEXT = message

    if HWND:
        # попросим окно перерисоваться
        win32gui.PostMessage(HWND, WM_UPDATE_TEXT, 0, 0)

    if duration is not None:
        def clear_later():
            time.sleep(duration)
            clear_text()

        threading.Thread(target=clear_later, daemon=True).start()


def clear_text():
    """Стереть текст (сделать оверлей пустым)."""
    global CURRENT_TEXT
    CURRENT_TEXT = ""
    if HWND:
        win32gui.PostMessage(HWND, WM_UPDATE_TEXT, 0, 0)


# ===== Оконная процедура =====

def wnd_proc(hwnd, msg, wparam, lparam):
    global CURRENT_TEXT, H_FONT

    if msg == win32con.WM_PAINT:
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            rect = win32gui.GetClientRect(hwnd)

            # Фон заливаем "волшебным" цветом (чёрный), который станет прозрачным
            brush = win32gui.GetStockObject(win32con.BLACK_BRUSH)
            win32gui.FillRect(hdc, rect, brush)

            if CURRENT_TEXT:
                # Создаём большой шрифт один раз, через LOGFONT + CreateFontIndirect
                if H_FONT is None:
                    logfont = win32gui.LOGFONT()
                    logfont.lfHeight = -32  # размер шрифта (по модулю больше → крупнее)
                    logfont.lfWeight = win32con.FW_BOLD
                    logfont.lfCharSet = win32con.DEFAULT_CHARSET
                    logfont.lfQuality = win32con.DEFAULT_QUALITY
                    logfont.lfPitchAndFamily = win32con.DEFAULT_PITCH | win32con.FF_DONTCARE
                    # Имя шрифта (можно 'Arial', 'Tahoma', 'Consolas', и т.п.)
                    logfont.lfFaceName = "Segoe UI"

                    H_FONT = win32gui.CreateFontIndirect(logfont)

                old_font = win32gui.SelectObject(hdc, H_FONT)

                win32gui.SetBkMode(hdc, win32con.TRANSPARENT)
                # Цвет текста — ЯРКИЙ, НЕ чёрный (иначе сольётся с colorkey)
                win32gui.SetTextColor(hdc, win32api.RGB(0, 255, 0))

                win32gui.DrawText(
                    hdc,
                    CURRENT_TEXT,
                    -1,
                    rect,
                    win32con.DT_CENTER
                    | win32con.DT_VCENTER
                    | win32con.DT_SINGLELINE,
                    )

                win32gui.SelectObject(hdc, old_font)

        finally:
            win32gui.EndPaint(hwnd, ps)

        return 0

    if msg == WM_UPDATE_TEXT:
        win32gui.InvalidateRect(hwnd, None, True)
        return 0

    if msg == win32con.WM_MOUSEACTIVATE:
        # Не забирать фокус даже при клике мышью
        return win32con.MA_NOACTIVATE

    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0

    # ВАЖНО: для всех прочих сообщений — DefWindowProc
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


# ===== Поток с окном-оверлеем =====

def _overlay_thread():
    global HWND

    h_instance = win32api.GetModuleHandle(None)
    class_name = "WowOverlayWithBigFontIndirect"

    wnd_class = win32gui.WNDCLASS()
    wnd_class.hInstance = h_instance
    wnd_class.lpszClassName = class_name
    wnd_class.lpfnWndProc = wnd_proc
    wnd_class.hCursor = win32gui.LoadCursor(None, win32con.IDC_ARROW)
    wnd_class.hbrBackground = win32con.COLOR_WINDOW

    atom = win32gui.RegisterClass(wnd_class)

    sw = win32api.GetSystemMetrics(0)
    sh = win32api.GetSystemMetrics(1)

    ex_style = (
            win32con.WS_EX_TOPMOST
            | win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT
            | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE
    )

    style = win32con.WS_POPUP

    hwnd = win32gui.CreateWindowEx(
        ex_style,
        atom,
        None,
        style,
        0,
        0,
        sw,
        sh,
        0,
        0,
        h_instance,
        None,
    )
    HWND = hwnd

    # Всё чёрное в окне будет полностью прозрачным (colorkey)
    win32gui.SetLayeredWindowAttributes(
        hwnd,
        win32api.RGB(0, 0, 0),
        255,
        win32con.LWA_COLORKEY,
    )

    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    # Для проверки: показываем тестовый текст
    def demo():
        time.sleep(1)
        show_text("Overlay OK (LOGFONT)", duration=1)

    threading.Thread(target=demo, daemon=True).start()

    win32gui.PumpMessages()


def start_overlay():
    """Запуск оверлея в отдельном потоке. Вызывать один раз при старте программы."""
    t = threading.Thread(target=_overlay_thread, daemon=True)
    t.start()
