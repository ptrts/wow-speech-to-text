import ctypes
import json
import queue
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import pyautogui
import sounddevice as sd
from vosk import Model, KaldiRecognizer

# ================== НАСТРОЙКИ ==================

# Папка, где лежит текущий .py файл
BASE_DIR = Path(__file__).resolve().parent

# путь к распакованной русской модели Vosk

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600

MODEL_PATH = BASE_DIR / "vosk-model-small-ru-0.22"

# MODEL_PATH = BASE_DIR / "vosk-model-ru-0.42"
# MODEL_PATH = BASE_DIR / "vosk-model-ru-0.10"

# Слова-триггеры
ACTIVATE_WORD_TO_CHAT_CHANNEL = {"бой": "bg", "би": "bg", "сказать": "s", "эс": "s", "крик": "y", "гильдия": "g", "гг": "g"}
ACTIVATE_WORDS = ACTIVATE_WORD_TO_CHAT_CHANNEL.keys()

SEND_WORDS = {"отправить", "готово", "окей", "ок"}  # отправляют в чат
CANCEL_WORDS = {"сброс", "отмена", "очистить"}  # сбрасывают буфер

# Задержки между нажатиями, чтобы игра точно всё проглотила
KEY_DELAY = 0.05  # секунды

# ================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ==================

state = "idle"  # "idle" | "recording"
prev_partial_text: str | None = None
start_command: str | None = None
final_tokens: list[str] = []
q = queue.Queue()  # очередь аудио-данных
idle_recognizer = None
recording_recognizer = None

# ================== Добавляем время в print ===

_old_print = print


# noinspection PyShadowingBuiltins
def print(*args, **kwargs):
    # время формата HH:MM:SS.mmm
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    _old_print(ts, *args, **kwargs)


# ================== Структуры и константы для SendInput ===

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# В некоторых версиях Python нет wintypes.ULONG_PTR — подменяем на WPARAM
ULONG_PTR = getattr(wintypes, "ULONG_PTR", wintypes.WPARAM)


# --- структуры из directkeys.py ---

class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = (
            ("ki", KEYBDINPUT),
            ("mi", MOUSEINPUT),
            ("hi", HARDWAREINPUT),
        )

    _anonymous_ = ("_input",)
    _fields_ = (
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    )


LPINPUT = ctypes.POINTER(INPUT)


def _check_count(result, func, args):
    # Если SendInput вернул 0 — поднимем нормальный WinError, чтобы видеть причину
    if result == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    return args


user32.SendInput.errcheck = _check_count
user32.SendInput.argtypes = (wintypes.UINT, LPINPUT, ctypes.c_int)


# --- печать юникод-строки в активное окно ---

def send_unicode_text(text: str, per_char_delay: float = 0.0):
    """
    Печатает текст как последовательность Unicode-клавиш.
    Не зависит от раскладки, главное – активное окно (WoW / блокнот).
    """
    text = text or ""
    if not text:
        return

    inputs = []

    for ch in text:
        code = ord(ch)

        down = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=0,
                wScan=code,
                dwFlags=KEYEVENTF_UNICODE,
                time=0,
                dwExtraInfo=0,
            ),
        )
        up = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=0,
                wScan=code,
                dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                time=0,
                dwExtraInfo=0,
            ),
        )

        if per_char_delay:
            arr = (INPUT * 2)(down, up)
            user32.SendInput(2, arr, ctypes.sizeof(INPUT))
            time.sleep(per_char_delay)
        else:
            inputs.append(down)
            inputs.append(up)

    if inputs and not per_char_delay:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


# ================== ОТПРАВКА В ЧАТ WoW ==================

def send_to_wow_chat(chat_channel: str, text: str):
    """
    Отправить сообщение в /bg:
      Enter, печать "/bg <текст>" как Unicode, Enter.
    """
    text = text.strip()
    if not text:
        print("[WOW] Пустой текст, не отправляем")
        return

    full_msg = f"/{chat_channel} {text}"
    print(f"[WOW] Отправляем: {full_msg!r}")

    # Небольшая пауза, чтобы не перебивать предыдущее действие
    time.sleep(0.1)

    # Открываем чат
    pyautogui.press("enter")
    time.sleep(KEY_DELAY)

    # Печатаем строку целиком, независимо от раскладки
    send_unicode_text(full_msg, per_char_delay=0.0)
    time.sleep(KEY_DELAY)

    # Отправляем
    pyautogui.press("enter")
    time.sleep(KEY_DELAY)


# ================== ОБРАБОТКА РАСПОЗНАННЫХ ФРАЗ ==================

def to_idle():
    global state, final_tokens, start_command, prev_partial_text
    final_tokens = []
    start_command = None
    state = "idle"
    prev_partial_text = None
    print("[STATE(partial)] => IDLE")


def reset_recognizer(recognizer):
    global q
    if recognizer is not None:
        recognizer.Reset()
        print("[VOSK] Reset() called")

    # вычищаем очередь, чтобы не доедать куски старой фразы
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


def handle_text(partial_text: str, is_final: bool):
    global state, prev_partial_text, final_tokens, start_command

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text and not is_final:
        print("Same partial")
        return

    prev_partial_text = partial_text

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()
    # Печатаем, какие слова там получились
    print(f"handle_text {tokens} | state={state!r}")

    stop_commands = SEND_WORDS | CANCEL_WORDS
    stop_command_position, stop_command = next(
        (
            (i, w)
            for i, w in enumerate(tokens) if w in stop_commands
        ),
        (None, None)
    )

    if stop_command is None:
        print("Нет стоп команды")
        if is_final:
            final_tokens.extend(tokens)
            print(f"Фраза финальная. Сохранили tokens {tokens} в final_tokens: {final_tokens}. final_start_command: {start_command}")
        return
    elif stop_command in SEND_WORDS:
        tokens = tokens[0: stop_command_position]
        final_tokens.extend(tokens)
        chat_channel = ACTIVATE_WORD_TO_CHAT_CHANNEL[start_command]
        text = " ".join(final_tokens)
        if text:
            print("[STATE(partial)] Отправляем по partial")
            send_to_wow_chat(chat_channel, text)
        else:
            print("[STATE(partial)] Пытались отправить (partial), но буфер пуст")
        to_idle()

    elif stop_command in CANCEL_WORDS:
        print("[STATE(partial)] Сброс по partial")
        to_idle()


def handle_text_idle(partial_text: str):
    global state, prev_partial_text, start_command

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text:
        print("Same partial")
        return

    prev_partial_text = partial_text

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()
    # Печатаем, какие слова там получились
    print(f"handle_text_idle {tokens} | state={state!r}")

    # Если в idle нам попалась пачка слов со словом "запись" или аналогами, то включаем режим записи
    start_command_position, start_command = next(((i, w) for i, w in enumerate(tokens) if w in ACTIVATE_WORDS), (None, None))
    if start_command is not None:
        state = "recording"
        prev_partial_text = None
        print("IDLE => RECORDING")


# ================== АУДИОПОТОК И РАСПОЗНАВАНИЕ ==================

# Наш обработчик данных от sounddevice
def audio_callback(indata, frames, time_info, status):

    # Сообщаем статус аудио устройства, если нужно
    if status:
        print(f"[AUDIO] {status}", flush=True)

    # Достаем байты из indata. Кладем эти байты в очередь, на которой у нас сидит vosk
    q.put(bytes(indata))


def recognition_loop():
    global idle_recognizer, recording_recognizer, state

    # Поднимаем нашу русскую восковую модель
    model = Model(str(MODEL_PATH))

    # На основе этой модели поднимаем распознаватели речи

    grammar = json.dumps(list(ACTIVATE_WORDS) + ["[unk]"], ensure_ascii=False)
    idle_recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)

    recording_recognizer = KaldiRecognizer(model, SAMPLE_RATE)

    # Теперь будем работать с микрофоном через модуль sounddevice (локально - sd).
    # Открываем сырой входящий поток звуковых данных.
    with sd.RawInputStream(
            samplerate=SAMPLE_RATE, # Частота дискретизации - 16 000 сэмплов в секунду
            blocksize=BLOCK_SIZE, # В одном блоке - 1600 сэмплов. Это - 0.1 секунды, т.к. частота дискретизации - 16 000 сэмплов в секунду
            dtype='int16', # Каждый сэмпл - это 16 бит.
            channels=1, # Один канал (моно)
            callback=audio_callback # Для обработки сэмплов использовать вот такой описанный нами выше обработчик
    ):
        # Входящий потом цифрового аудио инициализирован.
        # Сообщаем пользователю, что он уже может начинать говорить.
        print("[MAIN] Начали слушать микрофон. Скажите одну из команд старта, чтобы начать диктовку.")

        recognizer = None

        # И садимся в мертвый цикл
        while True:

            if state == "idle":
                new_recognizer_name = "idle_recognizer"
                new_recognizer = idle_recognizer
            else:
                new_recognizer_name = "recording_recognizer"
                new_recognizer = recording_recognizer

            if recognizer != new_recognizer:
                print(new_recognizer_name)
                recognizer = new_recognizer
                reset_recognizer(idle_recognizer)
                reset_recognizer(recording_recognizer)

            # Садимся ждать очередной кусок данных из входящего потока цифрового аудио
            data = q.get()

            is_final = recognizer.AcceptWaveform(data)
            if is_final:
                full_result = json.loads(recognizer.Result())
                text = full_result.get("text", "")
            else:
                partial_result = json.loads(recognizer.PartialResult())
                text = partial_result.get("partial", "")

            if text:
                if state == "idle":
                    handle_text_idle(text)
                else:
                    handle_text(text, is_final)


if __name__ == "__main__":
    try:
        recognition_loop()
    except KeyboardInterrupt:
        print("")
        print("[MAIN] Остановлено пользователем")
