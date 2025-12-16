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
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.mode_container
import app.recording_processor

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
# todo Вытащить как-то из списка команд
ACTIVATE_WORDS = ACTIVATE_WORD_TO_CHAT_CHANNEL.keys()


class TextAndIsFinal(NamedTuple):
    text: str
    is_final: bool


class IdleProcessor(app.mode_container.ModeProcessor):

    prev_partial_text: str | None = None

    q = queue.Queue()  # очередь аудио-данных

    recording_processor: app.recording_processor.RecordingTextsProcessor

    def __init__(self, mode_container: app.mode_container.ModeContainer):
        super().__init__(mode_container, "idle")

    def set_recording_processor(self, recording_processor: app.recording_processor.RecordingTextsProcessor):
        self.recording_processor = recording_processor

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

        local_mode: str | None = None

        # И садимся в мертвый цикл
        while True:
            if self.mode_container.mode != local_mode:
                local_mode = self.mode_container.mode

                logger.debug("local_mode=%s", local_mode)

                if local_mode == "idle" or local_mode == "pause":
                    recognizer = get_command_recognizer()
                else:
                    recognizer = None

                if self.mode_container.mode in ("idle", "pause"):
                    play_sound(local_mode)

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

            if local_mode in ("idle", "pause"):
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
            logger.info("tokens=%s, mode=%r", tokens, self.mode_container.mode)

            yield tokens

    def command_recognizer_texts_processing_loop(self):
        for token_group in self.get_command_recognizer_token_groups():
            command = app.commands.command_selector.select_command(token_group)
            command.do_things()

    def to_recording(self, chat_channel: str):
        def enter_mode():
            self.recording_processor.on_mode_enter(chat_channel)
        self.mode_container.to_mode(self, self.recording_processor.mode, enter_mode)

    def on_mode_enter(self):
        self.prev_partial_text = None


idle_processor = IdleProcessor(app.mode_container.mode_container)
