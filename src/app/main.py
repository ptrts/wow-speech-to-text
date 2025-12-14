from __future__ import annotations
import json
import queue
from importlib import resources
from typing import NamedTuple
from collections.abc import Generator

import sounddevice as sd
from vosk import Model, KaldiRecognizer

from app.overlay import start_overlay, show_text
import app.overlay
from app.beeps import play_sound
from app.yandex_cloud_oauth import get_oauth_and_iam_tokens
import app.tokens_to_text_builder as tokens_to_text_builder
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.recording_texts_processor

from app.app_logging import logging, TRACE


logger = logging.getLogger(__name__)


def main():
    security_tokens = get_oauth_and_iam_tokens()

    logger.info("Итог:")
    for k, v in security_tokens.items():
        logger.info(f"{k}: {v}")

    iam_token = security_tokens["iam_token"]
    app.recognize_thread.init(iam_token)

    start_overlay()

    try:
        command_recognizer_texts_processing_loop()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("[MAIN] Остановлено пользователем")
    finally:
        app.recognize_thread.shutdown()


if __name__ == "__main__":
    main()
