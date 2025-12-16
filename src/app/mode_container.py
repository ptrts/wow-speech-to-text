from __future__ import annotations

import threading
from collections.abc import Callable

from app.app_logging import logging


logger = logging.getLogger(__name__)


class ModeProcessor(object):

    def __init__(self, mode_container_arg: ModeContainer, mode: str):
        self.mode = mode
        self.mode_container = mode_container_arg

    def on_mode_leave(self):
        ...

    def on_after_mode_leave_grace(self):
        ...


class ModeContainer(object):

    mode = "idle"  # "idle" | "pause" | "timer" | "recording"

    def to_mode(self, from_mode_processor: ModeProcessor, to_mode: str, enter_mode_callback: Callable = None):
        from_mode_processor.on_mode_leave()
        self.mode = "timer"
        threading.Timer(0.2, self.after_timer, (from_mode_processor, to_mode, enter_mode_callback)).start()

    def after_timer(self, from_mode_processor: ModeProcessor, to_mode: str, enter_mode_callback: Callable = None):
        logger.debug("%s => %s", self.mode, to_mode)
        from_mode_processor.on_after_mode_leave_grace()
        self.mode = to_mode
        if enter_mode_callback:
            enter_mode_callback()


mode_container = ModeContainer()
