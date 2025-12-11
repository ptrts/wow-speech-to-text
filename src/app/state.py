import threading
from app.app_logging import logging, TRACE


logger = logging.getLogger(__name__)


state = "idle"  # "idle" | "pause" | "timer" | "recording"
chat_channel: str | None = None

bottom_text: str = ""

overlay_line_center_green: str | None = None
overlay_line_center_red: str | None = None
overlay_line_bottom: str | None = None


def on_schedule_state_timer(new_state, callback):
    global state
    old_state = state
    state = new_state
    if callback:
        callback()
    logger.debug("%s => %s", old_state, state)


def set_state(new_state, callback=None):
    global state
    logger.debug(new_state)
    state = "timer"
    threading.Timer(0.2, on_schedule_state_timer, args=(new_state, callback)).start()
