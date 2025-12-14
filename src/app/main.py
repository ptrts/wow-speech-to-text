from __future__ import annotations

from app.overlay import start_overlay
import app.overlay
from app.yandex_cloud_oauth import get_oauth_and_iam_tokens
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.recording_processor
import app.idle_processor
import app.mode_switcher

from app.app_logging import logging


logger = logging.getLogger(__name__)


def main():
    security_tokens = get_oauth_and_iam_tokens()

    logger.info("Итог:")
    for k, v in security_tokens.items():
        logger.info(f"{k}: {v}")

    iam_token = security_tokens["iam_token"]
    app.recognize_thread.init(iam_token)

    start_overlay()

    recording_processor = app.recording_processor.RecordingTextsProcessor()
    idle_processor = app.idle_processor.IdleProcessor()

    switcher = app.mode_switcher.Switcher(idle_processor, recording_processor)
    recording_processor.switcher = switcher
    idle_processor.switcher = switcher






    try:
        app.idle_processor.command_recognizer_texts_processing_loop()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("[MAIN] Остановлено пользователем")
    finally:
        app.recognize_thread.shutdown()


if __name__ == "__main__":
    main()
