import threading
import time

import win32api
import win32con
import win32gui


CENTER_TEXT = ("", "")
TOP_TEXT = ""
BOTTOM_TEXT = ""

HWND = None
H_FONT = None

WM_UPDATE_TEXT = win32con.WM_USER + 1


def show_text(
        green_text: str,
        red_text: str,
        duration: float | None = None,
):
    set_text(green_text, red_text)
    if duration is not None:
        threading.Timer(duration, clear_text)


def show_top(
        top_text: str,
        duration: float | None = None,
):
    set_top_text(top_text)
    if duration is not None:
        threading.Timer(duration, clear_top_text)


def show_bottom(
        bottom_text: str,
        duration: float | None = None,
):
    set_bottom_text(bottom_text)
    if duration is not None:
        threading.Timer(duration, clear_bottom_text)


def set_all(
        green_text: str,
        red_text: str,
        top_text: str,
        bottom_text: str,
):
    global CENTER_TEXT, TOP_TEXT, BOTTOM_TEXT
    CENTER_TEXT = (green_text, red_text)
    TOP_TEXT = top_text
    BOTTOM_TEXT = bottom_text
    refresh()


def set_text(
        green_text: str,
        red_text: str,
):
    global CENTER_TEXT
    CENTER_TEXT = (green_text, red_text)
    refresh()


def set_top_text(top_text: str):
    global TOP_TEXT
    TOP_TEXT = top_text
    refresh()


def set_bottom_text(bottom_text: str):
    global BOTTOM_TEXT
    BOTTOM_TEXT = bottom_text
    refresh()


def refresh():
    if HWND:
        win32gui.PostMessage(HWND, WM_UPDATE_TEXT, 0, 0)


def clear_all():
    set_all("", "", "", "")


def clear_text():
    set_text("", "")


def clear_top_text():
    set_top_text("")


def clear_bottom_text():
    set_bottom_text("")


def wnd_proc(hwnd, msg, wparam, lparam):
    global CENTER_TEXT, H_FONT, TOP_TEXT, BOTTOM_TEXT

    if msg == win32con.WM_PAINT:
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            rect = win32gui.GetClientRect(hwnd)

            # фон заполняем как и раньше
            brush = win32gui.GetStockObject(win32con.BLACK_BRUSH)
            win32gui.FillRect(hdc, rect, brush)

            # --- поддержка и старого и нового формата CENTER_TEXT ---
            if isinstance(CENTER_TEXT, tuple):
                green_text, red_text = CENTER_TEXT
            else:
                green_text, red_text = CENTER_TEXT, ""
            full_text = (green_text or "") + (red_text or "")
            top_text = TOP_TEXT or ""
            bottom_text = BOTTOM_TEXT or ""

            if full_text or top_text or bottom_text:
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

                line_spacing = 6

                def draw_centered(text: str, color: int, y_coord: int):
                    text_w, text_h = win32gui.GetTextExtentPoint32(hdc, text)
                    text_x = left + (client_w - text_w) // 2
                    text_rect = (text_x, y_coord, text_x + text_w, y_coord + text_h)
                    win32gui.SetTextColor(hdc, color)
                    win32gui.DrawText(
                        hdc,
                        text,
                        -1,
                        text_rect,
                        win32con.DT_LEFT
                        | win32con.DT_TOP
                        | win32con.DT_SINGLELINE,
                    )
                    return text_h

                # ширина/высота основной строки (используем пробел, чтобы узнать высоту)
                base_text = full_text if full_text else " "
                full_w, full_h = win32gui.GetTextExtentPoint32(hdc, base_text)

                # точка старта, чтобы центрировать текст
                x = left + (client_w - full_w) // 2
                y = top + (client_h - full_h) // 2

                # прямоугольник под всю строку
                full_rect = (x, y, x + full_w, y + full_h)

                if full_text:
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

                if top_text:
                    draw_centered(top_text, win32api.RGB(200, 0, 255), y - full_h - line_spacing)

                if bottom_text:
                    bottom_y = y + full_h + line_spacing
                    draw_centered(bottom_text, win32api.RGB(200, 0, 255), bottom_y)

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
