from __future__ import annotations
import json
import queue
from importlib import resources
from typing import NamedTuple
from collections.abc import Generator

import sounddevice as sd
from vosk import Model, KaldiRecognizer

import app.overlay
from app.beeps import play_sound
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.mode_switcher

from app.app_logging import logging, TRACE


logger = logging.getLogger(__name__)


# Папка, где лежит текущий .py файл
BASE_DIR = resources.files("resources")

# путь к распакованной русской модели Vosk

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600

IDLE_MODEL_PATH = BASE_DIR / "vosk-model-small-ru-0.22"

# Слова-триггеры
ACTIVATE_WORD_TO_CHAT_CHANNEL = {"бой": "bg", "сказать": "s", "крикнуть": "y", "гильдия": "g"}
ACTIVATE_WORDS = ACTIVATE_WORD_TO_CHAT_CHANNEL.keys()


class TextAndIsFinal(NamedTuple):
    text: str
    is_final: bool


class IdleProcessor(app.mode_switcher.ModeProcessor):

    prev_partial_text: str | None = None

    q = queue.Queue()  # очередь аудио-данных

    def __init__(self, switcher: app.mode_switcher.Switcher):
        super().__init__(switcher, "idle")

    # Наш обработчик данных от sounddevice
    def audio_callback(self, indata, frames, time_info, status):
        # Сообщаем статус аудио устройства, если нужно
        if status:
            logger.debug("status=%s", status)

        # Достаем байты из indata. Кладем эти байты в очередь, на которой у нас сидит vosk
        self.q.put(bytes(indata))

    def init_audio_stream(self):
        return sd.RawInputStream(
            samplerate=SAMPLE_RATE,  # Частота дискретизации - 16 000 сэмплов в секунду
            blocksize=BLOCK_SIZE,  # В одном блоке - 1600 сэмплов. Это - 0.1 секунды, т.к. частота дискретизации - 16 000 сэмплов в секунду
            dtype='int16',  # Каждый сэмпл - это 16 бит.
            channels=1,  # Один канал (моно)
            callback=self.audio_callback
        )

    def get_command_recognizer_texts(self) -> Generator[TextAndIsFinal, None, None]:

        model = Model(str(IDLE_MODEL_PATH))
        grammar = json.dumps(list(ACTIVATE_WORDS) + ["[unk]"], ensure_ascii=False)

        def get_command_recognizer():
            # return recording_recognizer
            return KaldiRecognizer(model, SAMPLE_RATE, grammar)

        # Теперь будем работать с микрофоном через модуль sounddevice (локально - sd).
        # Открываем сырой входящий поток звуковых данных.
        stream = self.init_audio_stream()
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
                data = self.q.get(timeout=1)
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

    def get_command_recognizer_token_groups(self) -> Generator[list[str], None, None]:
        for text_and_is_final in self.get_command_recognizer_texts():
            text = text_and_is_final.text

            logger.log(TRACE, "text=%s", text)

            text = text.strip().lower()
            if not text:
                return

            if self.prev_partial_text is not None and text == self.prev_partial_text:
                logger.debug("Same partial")
                return

            self.prev_partial_text = text

            # Разбиваем текст частичного результата на слова
            tokens = text.split()
            # Печатаем, какие слова там получились
            logger.info("tokens=%s, state=%r", tokens, app.state.state)

            yield tokens

    def command_recognizer_texts_processing_loop(self):
        for token_group in self.get_command_recognizer_token_groups():
            command = app.commands.command_selector.select_command(token_group)
            command.do_things()

    def on_mode_enter(self):
        self.prev_partial_text = None

    def on_mode_leave(self):
        ...
