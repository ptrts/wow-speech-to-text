import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd
import torch
import whisper

# ================= НАСТРОЙКИ =================

SAMPLE_RATE = 16000          # Whisper ожидает 16 kHz
CHANNELS = 1                 # моно
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

MODEL_SIZE = "small"         # tiny, base, small, medium, large
LANGUAGE = "ru"              # явное указание языка

WINDOW_SECONDS = 5.0         # сколько последних секунд анализируем
STEP_SECONDS = 1.0           # как часто запускать распознавание

# ============================================

audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
buffer_lock = threading.Lock()
audio_buffer = np.zeros(0, dtype=np.float32)

stop_flag = False


def audio_callback(indata, frames, time_info, status):
    """Колбэк sounddevice: вызывается в отдельном потоке."""
    if status:
        print(f"[SD STATUS] {status}", file=sys.stderr)
    # indata shape: (frames, channels)
    mono = indata[:, 0].copy()  # забираем первый канал
    audio_queue.put(mono)


def collector_thread():
    """Читает из очереди и накапливает данные в общем буфере."""
    global audio_buffer, stop_flag
    max_len = int(WINDOW_SECONDS * SAMPLE_RATE)

    while not stop_flag:
        try:
            chunk = audio_queue.get(timeout=0.1)
        except queue.Empty:
            continue

        with buffer_lock:
            audio_buffer = np.concatenate([audio_buffer, chunk])
            # Обрезаем, чтобы держать только последние WINDOW_SECONDS
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

    print(f"Device: {DEVICE}")
    print("Загружаю модель Whisper, это может занять время...")
    model = whisper.load_model(MODEL_SIZE, device=DEVICE)
    print("Модель загружена. Говорите в микрофон.\nCtrl+C для выхода.\n")

    # Запускаем поток сборщика
    collector = threading.Thread(target=collector_thread, daemon=True)
    collector.start()

    prev_words = []

    # Открываем аудио поток
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
                now = time.time()
                if now - last_time < STEP_SECONDS:
                    time.sleep(0.01)
                    continue
                last_time = now

                # Берём копию буфера под замком
                with buffer_lock:
                    if len(audio_buffer) == 0:
                        continue
                    audio_copy = audio_buffer.copy()

                # Нормализация не обязательна, но иногда помогает
                # audio_copy = audio_copy / np.max(np.abs(audio_copy))

                # Прогоняем через Whisper
                # fp16 имеет смысл только на GPU
                result = model.transcribe(
                    audio_copy,
                    language=LANGUAGE,
                    fp16=(DEVICE == "cuda"),
                    verbose=None,
                )
                text = result["text"].strip()

                if not text:
                    continue

                new_words, prev_words = diff_words(prev_words, text)

                if new_words:
                    # Печатаем только новые слова
                    out = " ".join(new_words)
                    print(out, end=" ", flush=True)

        except KeyboardInterrupt:
            print("\nОстановка...")
        finally:
            stop_flag = True
            collector.join(timeout=1.0)


if __name__ == "__main__":
    main()
