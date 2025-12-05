import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
DURATION = 5  # секунд записи


def main():
    print("Готово к записи. Скажи вслух что-нибудь по-русски 5 секунд...")
    sd.default.samplerate = SAMPLE_RATE
    sd.default.channels = 1

    # Пишем звук как int16, как это делает большинство примеров под Whisper
    audio = sd.rec(
        int(DURATION * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
    )
    sd.wait()

    # Вытаскиваем моно-канал
    audio_int16 = audio[:, 0]
    # Преобразуем в float32 [-1.0, 1.0], как ожидает Whisper
    audio_float = audio_int16.astype(np.float32) / 32768.0

    # Быстрый чек уровня сигнала
    peak = float(np.max(np.abs(audio_float)))
    rms = float(np.sqrt(np.mean(audio_float ** 2)))
    print(f"Peak level: {peak:.4f}, RMS: {rms:.4f}")

    # Если тут всё ≈0.0000, значит до модели реально долетает почти тишина
    # (или устройство/канал не тот)

    print("Загружаю модель faster-whisper (small, CPU int8)...")
    model = WhisperModel(
        "small",           # можешь сменить на "medium" или "large-v3"
        device="cpu",      # или "cuda"
        compute_type="int8",
    )

    segments, info = model.transcribe(
        audio_float,
        language="ru",
        beam_size=5,
        best_of=5,
        condition_on_previous_text=False,
        vad_filter=True,
    )

    print(f"Detected language: {info.language} (p={info.language_probability:.3f})")
    print("Распознанный текст:")
    full_text = ""
    for s in segments:
        print(f"[{s.start:.2f}–{s.end:.2f}] {s.text}")
        full_text += s.text + " "
    print("=== Сводка ===")
    print(full_text.strip())


if __name__ == "__main__":
    main()
