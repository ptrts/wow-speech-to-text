import threading
import time

import win32con
import win32gui
import win32api

# ===== Глобальное состояние оверлея =====

CURRENT_TEXT = ("", "")
HWND = None
H_FONT = None  # сюда положим большой шрифт

WM_UPDATE_TEXT = win32con.WM_USER + 1


# ===== Публичные функции для использования из других модулей =====

def show_text(text_1: str, text_2: str, duration: float | None = None):
    """
    Показать текст на оверлее.
    duration (сек) — если задано, через это время текст исчезнет.
    Можно вызывать из любого потока.
    """
    global CURRENT_TEXT

    CURRENT_TEXT = (text_1, text_2)

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
    CURRENT_TEXT = ("", "")
    if HWND:
        win32gui.PostMessage(HWND, WM_UPDATE_TEXT, 0, 0)


# ===== Оконная процедура =====

def wnd_proc(hwnd, msg, wparam, lparam):
    global CURRENT_TEXT, H_FONT

    if msg == win32con.WM_PAINT:
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            rect = win32gui.GetClientRect(hwnd)

            # фон заполняем как и раньше
            brush = win32gui.GetStockObject(win32con.BLACK_BRUSH)
            win32gui.FillRect(hdc, rect, brush)

            # --- поддержка и старого и нового формата CURRENT_TEXT ---
            if isinstance(CURRENT_TEXT, tuple):
                green_text, red_text = CURRENT_TEXT
            else:
                green_text, red_text = CURRENT_TEXT, ""
            full_text = (green_text or "") + (red_text or "")

            if full_text:
                if H_FONT is None:
                    logfont = win32gui.LOGFONT()
                    logfont.lfHeight = -32
                    logfont.lfWeight = win32con.FW_BOLD
                    logfont.lfCharSet = win32con.DEFAULT_CHARSET
                    logfont.lfQuality = win32con.DEFAULT_QUALITY
                    logfont.lfPitchAndFamily = win32con.DEFAULT_PITCH | win32con.FF_DONTCARE
                    logfont.lfFaceName = "Segoe UI"
                    H_FONT = win32gui.CreateFontIndirect(logfont)

                old_font = win32gui.SelectObject(hdc, H_FONT)

                win32gui.SetBkMode(hdc, win32con.TRANSPARENT)

                left, top, right, bottom = rect
                client_w = right - left
                client_h = bottom - top

                # ширина/высота всей строки
                full_w, full_h = win32gui.GetTextExtentPoint32(hdc, full_text)

                # точка старта, чтобы центрировать текст
                x = left + (client_w - full_w) // 2
                y = top + (client_h - full_h) // 2

                # прямоугольник под всю строку
                full_rect = (x, y, x + full_w, y + full_h)

                # 1) весь текст красным
                win32gui.SetTextColor(hdc, win32api.RGB(255, 0, 0))
                win32gui.DrawText(
                    hdc,
                    full_text,
                    -1,
                    full_rect,
                    win32con.DT_LEFT
                    | win32con.DT_TOP
                    | win32con.DT_SINGLELINE,
                    )

                # 2) поверх — зелёный префикс
                if green_text:
                    green_w, _ = win32gui.GetTextExtentPoint32(hdc, green_text)
                    green_rect = (x, y, x + green_w, y + full_h)

                    win32gui.SetTextColor(hdc, win32api.RGB(0, 255, 0))
                    win32gui.DrawText(
                        hdc,
                        green_text,
                        -1,
                        green_rect,
                        win32con.DT_LEFT
                        | win32con.DT_TOP
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
        show_text("Overlay OK (LOGFONT)", "", duration=1)

    threading.Thread(target=demo, daemon=True).start()

    win32gui.PumpMessages()


def start_overlay():
    """Запуск оверлея в отдельном потоке. Вызывать один раз при старте программы."""
    t = threading.Thread(target=_overlay_thread, daemon=True)
    t.start()
