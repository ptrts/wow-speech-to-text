from __future__ import annotations

import threading
from app.overlay import start_overlay
import app.overlay
import app.tokens_to_text_builder as tokens_to_text_builder
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.recording_processor
import app.idle_processor

from app.app_logging import logging


logger = logging.getLogger(__name__)


class ModeProcessor(object):

    def __init__(self, switcher: Switcher, mode: str):
        self.switcher = switcher
        self.switcher.register(mode, self)

    def on_mode_leave(self):
        ...

    def on_mode_leave_2(self):
        ...

    def on_mode_enter(self):
        ...


class Switcher(object):

    mode_to_processor: dict[str, ModeProcessor] = {}
    mode: str

    def register(self, mode: str, processor: ModeProcessor):
        self.mode_to_processor[mode] = processor

    def switch(self, mode: str):
        current_processor = self.mode_to_processor[self.mode]
        current_processor.on_mode_leave()

        logger.debug(mode)
        self.mode = "timer"
        threading.Timer(0.2, self.after_timer, args=(mode,)).start()

    def after_timer(self, new_mode):
        logger.debug("%s => %s", self.mode, new_mode)

        old_mode = self.mode
        old_processor = self.mode_to_processor[old_mode]
        old_processor.on_mode_leave_2()

        self.mode = new_mode

        new_processor = self.mode_to_processor[new_mode]
        new_processor.on_mode_enter()
