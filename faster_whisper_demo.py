import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd
import torch
from faster_whisper import WhisperModel
from app_logging import logging, TRACE


logger = logging.getLogger(__name__)

# ================ НАСТРОЙКИ ================

SAMPLE_RATE = 16000         # whisper ожидает 16 kHz
CHANNELS = 1                # моно

# Выбираем устройство и тип вычислений для faster-whisper
if torch.cuda.is_available():
    DEVICE = "cuda"
    COMPUTE_TYPE = "float16"   # обычно оптимально для GPU
else:
    DEVICE = "cpu"
    COMPUTE_TYPE = "int8"      # квантованное, быстрее на CPU


MODEL_SIZE = "small"           # tiny, base, small, medium, large-v2, distil-* и т.п.
LANGUAGE = "ru"

WINDOW_SECONDS = 5.0           # длина скользящего окна (секунд)
STEP_SECONDS = 1.0             # как часто запускать распознавание (секунд)

# ===========================================

# Очередь кусков двоичных аудио данных
audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()

buffer_lock = threading.Lock()

# Кольцевой буфер окном наиболее недавних аудио данных (5 секунд)
audio_buffer = np.zeros(0, dtype=np.float32)

stop_flag = False


def audio_callback(indata, frames, time_info, status):
    """Колбэк sounddevice: забираем аудио в очередь."""
    if status:
        print(f"[SD STATUS] {status}", file=sys.stderr)
    # indata: (frames, channels), float32
    mono = indata[:, 0].copy()
    audio_queue.put(mono)


def collector_thread():
    global audio_buffer, stop_flag

    max_len = int(WINDOW_SECONDS * SAMPLE_RATE)

    while not stop_flag:

        # Читаем из очереди очередной кусок двоичных данных
        try:
            chunk = audio_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        # Под блокировкой
        with buffer_lock:

            # Добавляем полученный новый кусок аудио данных к нашему буферу
            audio_buffer = np.concatenate([audio_buffer, chunk])

            # Если размер буфера превысил допустимый размер, то подрезаем этот буфер с начала.
            # Берем не более некого фиксированного количества сэмплов с конца буфера.
            if len(audio_buffer) > max_len:
                audio_buffer = audio_buffer[-max_len:]


def diff_words(prev_words, new_text):
    """
    prev_words: список слов из предыдущего распознавания
    new_text: полный текст текущего распознавания
    -> (new_words, words_now)
    """
    words_now = new_text.strip().split()
    i = 0
    while i < len(words_now) and i < len(prev_words) and words_now[i] == prev_words[i]:
        i += 1

    new_words = words_now[i:]
    return new_words, words_now


def main():
    global stop_flag

    print(f"Device: {DEVICE}, compute_type: {COMPUTE_TYPE}")
    print("Загружаю faster-whisper модель, это может занять время...")

    model = WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
    )

    print("Модель загружена. Говорите в микрофон.\nCtrl+C для выхода.\n")

    # Запускаем поток, который всегда копирует в буфер верхушку очереди в 5 секунд
    collector = threading.Thread(target=collector_thread, daemon=True)
    collector.start()

    # Какой-то список предыдущих слов
    prev_words = []

    # Открываем поток аудио данных с микрофона
    with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=audio_callback,
            blocksize=0,   # 0 = по умолчанию ОС
    ):
        last_time = 0.0

        try:
            while True:

                # Идем с шагом в одну секунду
                now = time.time()
                if now - last_time < STEP_SECONDS:
                    time.sleep(0.01)
                    continue
                last_time = now

                # Один поток, который наваливает данные в буфер из очереди аудио данных - мы уже запустили.
                # А здесь у нас - поток main.
                # Поток main берет блокировку этого буфера.
                with buffer_lock:
                    if len(audio_buffer) == 0:
                        continue
                    audio_copy = audio_buffer.copy()

                logger.info("В буфере что-то есть. Запускаем буфер в model.transcribe(...)")

                # audio_copy уже float32, 16 kHz, моно — формат, который faster-whisper понимает
                # Собираем текст из сегментов
                segments, info = model.transcribe(
                    audio_copy,
                    language=LANGUAGE,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    vad_filter=False,               # в демке можно выключить; потом поиграешься
                    condition_on_previous_text=False,
                )

                text_parts = [seg.text for seg in segments]
                text = " ".join(text_parts).strip()

                if not text:
                    continue

                logger.info("text=%s", text)

                new_words, prev_words = diff_words(prev_words, text)

                if new_words:
                    out = " ".join(new_words)
                    print(out, end=" ", flush=True)

        except KeyboardInterrupt:
            print("\nОстановка...")
        finally:
            stop_flag = True
            collector.join(timeout=1.0)


if __name__ == "__main__":
    main()
