from __future__ import annotations
import time

import pyautogui

from app.keyboard.layout_switch import switch_to_russian
from app.keyboard.keyboard_state import keyboard_is_clean, wait_for_keyboard_clean
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier

from app.app_logging import logging, TRACE


logger = logging.getLogger(__name__)


# Задержки между нажатиями, чтобы игра точно всё проглотила
KEY_DELAY = 0.05  # секунды


def send_to_wow_chat(channel: str, text: str, let_edit: bool = False):
    """
    Отправить сообщение в /bg:
      Enter, печать "/bg <текст>" как Unicode, Enter.
    """

    text = text.strip()
    if not text:
        logger.info("Пустой текст, не отправляем")
        return

    switch_to_russian()

    full_msg = f"{channel} {text}"
    logger.info("Отправляем: %r", full_msg)

    app.keyboard.clipboard_copier.clipboard_copy(full_msg)

    # Небольшая пауза, чтобы не перебивать предыдущее действие
    time.sleep(KEY_DELAY)

    if not keyboard_is_clean():
        app.state.overlay_line_bottom = "Отпускай!"
    refresh_overlay()

    still_clean = wait_for_keyboard_clean()

    app.state.overlay_line_bottom = None
    refresh_overlay()

    if not still_clean:
        return

    # Открываем чат
    pyautogui.press("enter")
    time.sleep(KEY_DELAY)

    # Вставляем текст через буфер
    app.keyboard.keyboard_sender.press_ctrl_v()
    time.sleep(KEY_DELAY)

    # Отправляем
    if not let_edit:
        pyautogui.press("enter")
        time.sleep(KEY_DELAY)
