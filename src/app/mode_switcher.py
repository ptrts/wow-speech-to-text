from __future__ import annotations

from app.overlay import start_overlay, show_text
import app.overlay
import app.tokens_to_text_builder as tokens_to_text_builder
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.recording_texts_processor

from app.app_logging import logging


logger = logging.getLogger(__name__)


def on_idle():
    global idle_prev_partial_text, recording_prev_partial_text
    app.state.chat_channel = None
    idle_prev_partial_text = None
    recording_prev_partial_text = None
    tokens_to_text_builder.reset()
    app.overlay.clear_all()


def to_idle():
    logger.info("start")
    app.recognize_thread.stop()
    app.state.set_state("idle", on_idle)



