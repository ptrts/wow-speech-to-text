import queue
import threading
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

# ===== НАСТРОЙКИ =====

SAMPLE_RATE = 16000
CHANNELS = 1

BLOCK_SECONDS = 0.2            # длительность блока ~200 мс
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_SECONDS)

RMS_SPEECH_THRESHOLD = 0.001  # порог "есть речь"
END_SILENCE_SECONDS = 0.8      # сколько тишины считать концом фразы

PARTIAL_INTERVAL = 0.7         # как часто делать partial-распознавание (сек)

MODEL_SIZE = "small"           # small / medium / large-v3
LANGUAGE = "ru"

# ======================

audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
running = True


def audio_callback(indata, frames, time_info, status):
    if status:
        print("Audio status:", status)
    # indata: (frames, channels), int16
    audio_q.put(indata.copy())


def worker_loop():
    global running

    print("Загружаю модель faster-whisper...")
    model = WhisperModel(
        MODEL_SIZE,
        device="cuda",        # если есть CUDA: device="cuda", compute_type="float16"
        compute_type="float32",
    )
    print("Модель загружена.")

    state = "IDLE"          # или "IN_SPEECH"
    utterance_blocks = []   # список np.ndarray(float32)
    silence_time = 0.0
    last_partial_time = 0.0

    while running:

        # Если есть - взяли очередной блок. Если нет - уходим подождать 100мс
        try:
            block_int16 = audio_q.get(timeout=0.1)
        except queue.Empty:
            continue

        # Из блока достаем массив сэмплов чисто по одному каналу, и все цифры преобразуем из формата [-32768, 32768] в формат [-1, 1]
        block = block_int16[:, 0].astype(np.float32) / 32768.0

        # Вычисляем среднюю громкость именно в этом блоке.
        rms = float(np.sqrt(np.mean(block ** 2)))

        # Вычисляем длительность блока в секундах.
        block_duration = len(block) / SAMPLE_RATE

        # --- FSM для обнаружения речи ---
        if state == "IDLE":
            # Смотрим, громкость блока выше порога?
            if rms > RMS_SPEECH_THRESHOLD:

                # старт новой фразы
                state = "IN_SPEECH"

                # Создаем специальный массив каких-то блоков. Наш громкий блок будет в нем первым элементом.
                utterance_blocks = [block]
                # Обнуляем время молчания, молчание у нас только что нарушилось.
                silence_time = 0.0
                # Сохраняем время начала говорения в специальную переменную.
                last_partial_time = time.time()

                print(f"\n[STATE] IDLE -> IN_SPEECH (rms={rms:.6f})")
            else:
                # всё ещё тишина
                continue
        else:  # IN_SPEECH
            # В режиме говорения набрасываем в массив все блоки подряд, вне зависимости от их громкости.
            utterance_blocks.append(block)
            # В режиме говорения, тихие блоки увеличивают время молчания, а громкие - обнуляют время молчания.
            if rms > RMS_SPEECH_THRESHOLD:
                silence_time = 0.0
            else:
                silence_time += block_duration

        # --- PARTIAL распознавание (по текущей фразе) ---

        now = time.time()

        # Если мы в режиме говорения, то смотрим, сколько прошло времени с начала говорения или с прошлого временного распознавания.
        if state == "IN_SPEECH" and (now - last_partial_time) >= PARTIAL_INTERVAL:
            # Уже прошло достаточно времени. Будем запускать распознавание с начала времени говорения.

            # Сохраняем время последнего временного распознавания.
            last_partial_time = now

            # Сцепляем все накопившиеся с начала говорения блоки в один большой блок.
            audio_utt = np.concatenate(utterance_blocks)

            # Засылаем все накопившееся с начала говорения в распознавание.
            segments, info = model.transcribe(
                audio_utt,
                language=LANGUAGE,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
                no_speech_threshold=1.0,   # не отбрасывать как "тишину"
                log_prob_threshold=-1.0,   # не выкидывать из-за "сомнительности"
                without_timestamps=True,   # нам не нужны тайм коды, чуть быстрее
            )

            # Нам отдают сегменты распознанного текста.

            # Объединяем тексты всех сегментов в один большой текст.
            text = "".join(s.text for s in segments).strip()

            # Если что-то получилось - выводим.
            if text:
                print("\nPARTIAL:", repr(text))

        # Смотрим, не затянулось ли молчание?
        if state == "IN_SPEECH" and silence_time >= END_SILENCE_SECONDS:

            # Да, молчание затянулось.

            # Переходим в ждущий режим.
            print(f"[STATE] IN_SPEECH -> IDLE (silence={silence_time:.3f}s)")
            state = "IDLE"

            # Накопились ли за время молчания какие-нибудь блоки?
            if utterance_blocks:

                # Да, блоки есть. Засылаем в финальное распознавание.

                audio_utt = np.concatenate(utterance_blocks)

                segments, info = model.transcribe(
                    audio_utt,
                    language=LANGUAGE,
                    beam_size=5,
                    best_of=5,
                    temperature=0.0,
                    vad_filter=False,
                    condition_on_previous_text=False,
                    no_speech_threshold=1.0,
                    log_prob_threshold=-1.0,
                    without_timestamps=True,
                )

                final_text = "".join(s.text for s in segments).strip()
                if final_text:
                    print("FINAL :", repr(final_text))

            utterance_blocks = []
            silence_time = 0.0


def main():
    global running

    sd.default.samplerate = SAMPLE_RATE
    sd.default.channels = CHANNELS

    print("Демка faster-whisper по фразам.")
    print("Говори фразами, между ними делай паузу ~секунду. Ctrl+C — выйти.\n")

    worker = threading.Thread(target=worker_loop, daemon=True)
    worker.start()

    with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=audio_callback,
            blocksize=BLOCK_SIZE,
    ):
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nОстанавливаюсь...")
        finally:
            running = False
            worker.join()


if __name__ == "__main__":
    main()
