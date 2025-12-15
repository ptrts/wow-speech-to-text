from __future__ import annotations

import threading
import app.overlay
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

    def on_mode_enter(self):
        ...

    def on_mode_leave(self):
        ...

    def on_after_leave_grace(self):
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

    # todo Кажется, нельзя запускать режимы независимым образом, и сигнатура запуска каждого режима зависит от специфики того или иного режима.
    #      Стало быть, разные режимы таки должны зависеть друг от друга.

    def to_idle(self):
        self.switch("idle")

    def to_recording(self, chat_channel: str):
        # noinspection PyTypeChecker
        recording_processor: app.recording_processor.RecordingTextsProcessor = self.mode_to_processor["recording"]
        recording_processor.chat_channel = chat_channel
        self.switch("recording")

    def after_timer(self, new_mode):
        logger.debug("%s => %s", self.mode, new_mode)

        old_mode = self.mode
        old_processor = self.mode_to_processor[old_mode]
        old_processor.on_after_leave_grace()

        self.mode = new_mode

        new_processor = self.mode_to_processor[new_mode]
        new_processor.on_mode_enter()
