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

from app.app_logging import logging, TRACE


logger = logging.getLogger(__name__)

# ================== НАСТРОЙКИ ==================

# Папка, где лежит текущий .py файл
BASE_DIR = resources.files("resources")

# путь к распакованной русской модели Vosk

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600

# RECORDING_MODEL_PATH = BASE_DIR / "vosk-model-small-ru-0.22"
RECORDING_MODEL_PATH = BASE_DIR / "vosk-model-ru-0.42"
# RECORDING_MODEL_PATH = BASE_DIR / "vosk-model-ru-0.10"

IDLE_MODEL_PATH = BASE_DIR / "vosk-model-small-ru-0.22"
# IDLE_MODEL_PATH = BASE_DIR / "vosk-model-ru-0.42"
# IDLE_MODEL_PATH = BASE_DIR / "vosk-model-ru-0.10"

# Слова-триггеры
ACTIVATE_WORD_TO_CHAT_CHANNEL = {"бой": "bg", "сказать": "s", "крикнуть": "y", "гильдия": "g"}
ACTIVATE_WORDS = ACTIVATE_WORD_TO_CHAT_CHANNEL.keys()

SEND_WORDS = {"отправить", "готово", "окей", "ок", "дописать"}  # отправляют в чат
CANCEL_WORDS = {"сброс", "отмена"}  # сбрасывают буфер

# ================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ==================

prev_partial_text: str | None = None

q = queue.Queue()  # очередь аудио-данных


def recording_refresh_overlay():
    text_1 = f"{app.state.chat_channel} {tokens_to_text_builder.final_text}"
    text_2 = tokens_to_text_builder.non_final_text
    app.overlay.show_text(text_1, text_2)


def handle_text(partial_text: str, is_final: bool):
    global prev_partial_text

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text and not is_final:
        logger.debug("Same partial")
        return

    logger.info("partial_text=%s, is_final=%s", partial_text, is_final)

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()

    prev_partial_text = partial_text

    # Печатаем, какие слова там получились
    logger.debug("tokens=%s", tokens)

    stop_commands = SEND_WORDS | CANCEL_WORDS
    stop_command_position, stop_command = next(
        (
            (i, w)
            for i, w in enumerate(tokens) if w in stop_commands
        ),
        (None, None)
    )

    if stop_command is None:
        logger.debug("Нет стоп команды")
        tokens_to_text_builder.build_text(tokens, is_final)
        recording_refresh_overlay()
    elif stop_command in SEND_WORDS:
        tokens = tokens[0: stop_command_position]
        tokens_to_text_builder.build_text(tokens, True)
        recording_refresh_overlay()
        app.recognize_thread.stop()
        if tokens_to_text_builder.text:
            logger.debug("Вызываем отправку в чат")
            play_sound("sending_started")
            app.wow_chat_sender.send_to_wow_chat(app.state.chat_channel, tokens_to_text_builder.text, let_edit=(stop_command == "дописать"))
            play_sound("sending_complete")
        else:
            play_sound("sending_error")
            logger.debug("Пытались отправить, но буфер пуст")
        to_idle()

    elif stop_command in CANCEL_WORDS:
        logger.debug("Сброс")
        play_sound("editing_cancelled")
        to_idle()


def on_recognized_fragment(alternatives: list[str], is_final: bool):
    if app.state.state == "recording":
        handle_text(alternatives[0], is_final)


def on_recording():
    show_text(app.state.chat_channel, "")
    app.recognize_thread.start(on_recognized_fragment)


def on_idle():
    global prev_partial_text
    app.state.chat_channel = None
    prev_partial_text = None
    tokens_to_text_builder.reset()
    app.overlay.clear_all()


def to_idle():
    logger.info("start")
    app.recognize_thread.stop()
    app.state.set_state("idle", on_idle)


# ================== АУДИОПОТОК И РАСПОЗНАВАНИЕ ==================

# Наш обработчик данных от sounddevice
def audio_callback(indata, frames, time_info, status):
    # Сообщаем статус аудио устройства, если нужно
    if status:
        logger.debug("status=%s", status)

    # Достаем байты из indata. Кладем эти байты в очередь, на которой у нас сидит vosk
    q.put(bytes(indata))


def init_audio_stream():
    return sd.RawInputStream(
        samplerate=SAMPLE_RATE,  # Частота дискретизации - 16 000 сэмплов в секунду
        blocksize=BLOCK_SIZE,  # В одном блоке - 1600 сэмплов. Это - 0.1 секунды, т.к. частота дискретизации - 16 000 сэмплов в секунду
        dtype='int16',  # Каждый сэмпл - это 16 бит.
        channels=1,  # Один канал (моно)
        callback=audio_callback  # Для обработки сэмплов использовать вот такой описанный нами выше обработчик
    )


class TextAndIsFinal(NamedTuple):
    text: str
    is_final: bool


def get_command_recognizer_texts() -> Generator[TextAndIsFinal, None, None]:

    model = Model(str(IDLE_MODEL_PATH))
    grammar = json.dumps(list(ACTIVATE_WORDS) + ["[unk]"], ensure_ascii=False)

    def get_command_recognizer():
        # return recording_recognizer
        return KaldiRecognizer(model, SAMPLE_RATE, grammar)

    # Теперь будем работать с микрофоном через модуль sounddevice (локально - sd).
    # Открываем сырой входящий поток звуковых данных.
    stream = init_audio_stream()
    stream.start()

    # Входящий потом цифрового аудио инициализирован.
    # Сообщаем пользователю, что он уже может начинать говорить.
    logger.info("Начали слушать микрофон. Скажите одну из команд старта, чтобы начать диктовку.")

    recognizer = None

    local_state: str | None = None

    # И садимся в мертвый цикл
    while True:
        if app.state.state != local_state:
            local_state = app.state.state

            logger.debug("local_state=%s", local_state)

            if local_state == "idle" or local_state == "pause":
                recognizer = get_command_recognizer()
            else:
                recognizer = None

            if app.state.state in ("idle", "pause"):
                play_sound(local_state)

        # Садимся ждать очередной кусок данных из входящего потока цифрового аудио
        try:
            data = q.get(timeout=1)
        except queue.Empty:
            continue

        logger.log(TRACE, "Got data")

        if not recognizer:
            continue

        logger.log(TRACE, "recognizer is chosen")

        is_final = recognizer.AcceptWaveform(data)

        if local_state in ("idle", "pause"):
            if is_final:
                full_result = json.loads(recognizer.Result())
                text = full_result.get("text", "")
            else:
                partial_result = json.loads(recognizer.PartialResult())
                text = partial_result.get("partial", "")
            if text:
                yield TextAndIsFinal(text, is_final)
        else:
            logger.debug("idle recognizer finishes its work. is_final=%s", is_final)
            if is_final:
                recognizer.Reset()
                recognizer = None


def get_command_recognizer_token_groups() -> Generator[list[str], None, None]:
    global prev_partial_text

    for text_and_is_final in get_command_recognizer_texts():
        text = text_and_is_final.text

        logger.log(TRACE, "text=%s", text)

        text = text.strip().lower()
        if not text:
            return

        if prev_partial_text is not None and text == prev_partial_text:
            logger.debug("Same partial")
            return

        prev_partial_text = text

        # Разбиваем текст частичного результата на слова
        tokens = text.split()
        # Печатаем, какие слова там получились
        logger.info("tokens=%s, state=%r", tokens, app.state.state)

        yield tokens


def command_recognizer_texts_processing_loop():
    for token_group in get_command_recognizer_token_groups():
        command = app.commands.command_selector.select_command(token_group)
        command.do_things()


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
