import threading
import app.yandex_speech_kit

from app.app_logging import logging


logger = logging.getLogger(__name__)

recognize_thread: threading.Thread | None = None
recognize_thread_stop_event: threading.Event | None = None


def init(iam_token: str):
    app.yandex_speech_kit.yandex_speech_kit_init(iam_token)


def shutdown():
    app.yandex_speech_kit.yandex_speech_kit_shutdown()


def start(callback: app.yandex_speech_kit.RecognizedFragmentCallback):
    global recognize_thread_stop_event, recognize_thread

    recognize_thread_stop_event = threading.Event()
    recognize_thread = threading.Thread(
        target=app.yandex_speech_kit.recognize_from_microphone,
        args=(recognize_thread_stop_event, callback),
        daemon=True
    )
    recognize_thread.start()


def stop():
    global recognize_thread, recognize_thread_stop_event
    if recognize_thread:
        logger.info("recognize_thread is set. Stopping the thread")
        recognize_thread_stop_event.set()
        recognize_thread = None
    else:
        logger.info("recognize_thread is not set")
