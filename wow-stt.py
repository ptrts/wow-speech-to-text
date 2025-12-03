import ctypes
import json
import queue
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import threading

import pyautogui
import sounddevice as sd
from vosk import Model, KaldiRecognizer

from overlay import start_overlay, show_text, clear_text
from beeps import play_sound
from russian_numerals import replace_russian_numbers

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
ACTIVATE_WORD_TO_CHAT_CHANNEL = {"бой": "bg", "сказать": "s", "крикнуть": "y", "гильдия": "g"}
ACTIVATE_WORDS = ACTIVATE_WORD_TO_CHAT_CHANNEL.keys()

SEND_WORDS = {"отправить", "готово", "окей", "ок"}  # отправляют в чат
CANCEL_WORDS = {"сброс", "отмена"}  # сбрасывают буфер

# Задержки между нажатиями, чтобы игра точно всё проглотила
KEY_DELAY = 0.05  # секунды

# ================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ==================

state = "idle"  # "idle" | "timer" | "recording"
prev_partial_text: str | None = None
chat_channel: str | None = None
final_tokens: list[str] = []
final_text_preview: str | None = None
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

def send_to_wow_chat(channel: str, text: str):
    """
    Отправить сообщение в /bg:
      Enter, печать "/bg <текст>" как Unicode, Enter.
    """
    text = text.strip()
    if not text:
        print("send_to_wow_chat. Пустой текст, не отправляем")
        return

    full_msg = f"{channel} {text}"
    print(f"send_to_wow_chat. Отправляем: {full_msg!r}")

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
    print("to_idle")

    def schedule_state_callback():
        global state, final_tokens, chat_channel, prev_partial_text, final_text_preview
        final_tokens = []
        chat_channel = None
        prev_partial_text = None
        final_text_preview = None
        clear_text()

    set_state("idle", schedule_state_callback)


class TextModificationCommand:
    def __init__(self, substitute: str, *word_combinations: str):
        self.word_combinations = [WordCombination(it) for it in word_combinations]

        for word_combination in self.word_combinations:
            WordCombinationAndTextModificationCommand(word_combination, self)

        self.substitute = substitute
        self.code = "_".join(self.word_combinations[0].words).upper()
        text_modification_commands[self.code] = self


class WordCombination:
    def __init__(self, text: str):
        self.text = text
        self.words = text.split()


class WordCombinationAndTextModificationCommand:
    def __init__(self, word_combination: WordCombination, command: TextModificationCommand):
        self.word_combination = word_combination
        self.command = command
        word_combination_and_text_modification_commands.append(self)


text_modification_commands: dict[str, TextModificationCommand] = {}
word_combination_and_text_modification_commands: list[WordCombinationAndTextModificationCommand] = []

# Между словами в предложении
TextModificationCommand(",", "запятая")
TextModificationCommand(";", "точка с запятой")
TextModificationCommand(":", "двоеточие")
TextModificationCommand("...", "многоточие")

TextModificationCommand("(", "открывающая скобка", "открыть скобку", "скобка")
TextModificationCommand(")", "закрывающая скобка", "закрыть скобку")

TextModificationCommand("\"", "кавычки")
TextModificationCommand("-", "дефис")
TextModificationCommand("/", "слэш")
TextModificationCommand("\\", "обратный слэш", "бэк слэш")
TextModificationCommand("-", "тире")
TextModificationCommand(" ", "пробел")

# Конец предложения
TextModificationCommand(".", "точка")
TextModificationCommand("!", "восклицательный знак")
TextModificationCommand("?", "вопросительный знак")

# Команды
TextModificationCommand("", "большая буква")
TextModificationCommand("", "маленькая буква")
TextModificationCommand("", "удалить")

word_combination_and_text_modification_commands.sort(key=lambda it: len(it.word_combination.words), reverse=True)


def refresh_final_text_preview(new_tokens: list[str]):
    global final_tokens, final_text_preview

    tokens = final_tokens.copy()
    tokens.extend(new_tokens)

    if "очистить" in tokens:
        final_tokens = []
        tokens = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        print(f"refresh_final_text_preview. i={i}, token={token}")
        if token == "удалить":
            if i > 0:

                # Идем назад, ищем не пробел
                j = i - 1
                while j >= 0:
                    print(f"refresh_final_text_preview. j={j}, tokens[j]={tokens[j]}")
                    if tokens[j] != "пробел":
                        print(f"refresh_final_text_preview. From here!")
                        break
                    j -= 1
                print(f"refresh_final_text_preview. j={j}, tokens[j: i + 1]={tokens[j: i + 1]}")

                # Удаляем этот не пробел, текущий токен, и все пробелы между ними.
                tokens[j: i + 1] = []
                i = j
            else:
                tokens[i: i + 1] = []
                i = 0
        else:
            i += 1

    tokens = replace_russian_numbers(tokens)

    print(f"refresh_final_text_preview. final_tokens={final_tokens}, new_tokens={new_tokens}, tokens={tokens}")

    open_quote = False
    prev_token_category: str | None = None
    new_sentence = True

    i = 0
    while i < len(tokens):
        token = tokens[i]
        print(f"refresh_final_text_preview. i={i}, token={token}")

        is_word = True
        command = None
        for word_combination_and_command in word_combination_and_text_modification_commands:
            words = word_combination_and_command.word_combination.words
            command = word_combination_and_command.command

            print(f"refresh_final_text_preview. i={i}, command.code={command.code}, words={words}")

            if tokens[i: i + len(words)] == words:
                is_word = False
                token = command.code
                tokens[i: i + len(words)] = [token]
                print(f"refresh_final_text_preview. i={i}, word combination match, tokens={tokens}")
                break
            else:
                command = None

        if token == "КАВЫЧКИ":
            open_quote = not open_quote
            if open_quote:
                token = tokens[i] = "ОТКРЫВАЮЩИЕ_КАВЫЧКИ"
                print(f"refresh_final_text_preview. Данные кавычки - открывающие, token={token}")
            else:
                token = tokens[i] = "ЗАКРЫВАЮЩИЕ_КАВЫЧКИ"
                print(f"refresh_final_text_preview. Данные кавычки - закрывающие, token={token}")

        if is_word:
            token_category = "СЛОВО"
        elif token in ["ОТКРЫВАЮЩИЕ_КАВЫЧКИ", "ОТКРЫВАЮЩАЯ_СКОБКА"]:
            token_category = "ОТКРЫВАТЕЛЬ"
        elif token in ["ЗАКРЫВАЮЩИЕ_КАВЫЧКИ", "ЗАКРЫВАЮЩАЯ_СКОБКА"]:
            token_category = "ЗАКРЫВАТЕЛЬ"
        elif token in ["ТОЧКА", "ВОСКЛИЦАТЕЛЬНЫЙ_ЗНАК", "ВОПРОСИТЕЛЬНЫЙ_ЗНАК"]:
            token_category = "КОНЕЦ_ПРЕДЛОЖЕНИЯ"
        elif token in ["ЗАПЯТАЯ", "ТОЧКА_С_ЗАПЯТОЙ", "ДВОЕТОЧИЕ", "ТРОЕТОЧИЕ"]:
            token_category = "ТИПА_ЗАПЯТОЙ"
        elif token in ["ТИРЕ"]:
            token_category = "ТИРЕ"
        elif token in ["ПРОБЕЛ"]:
            token_category = "ПРОБЕЛ"
        else:
            token_category = None

        print(f"refresh_final_text_preview. token_category={token_category}")

        print(f"refresh_final_text_preview. new_sentence={new_sentence}")

        if token_category == "КОНЕЦ_ПРЕДЛОЖЕНИЯ":
            new_sentence = True
        elif token_category == "СЛОВО" and new_sentence:
            token = tokens[i] = token.capitalize()
            new_sentence = False

        print(f"refresh_final_text_preview. new_sentence={new_sentence}, token={token}")

        if command:
            if command.substitute:
                token = tokens[i] = command.substitute
            else:
                token = None
                tokens[i: i + 1] = []
                i -= 1
            print(f"refresh_final_text_preview. token={token}, tokens={tokens}")

        need_space = False
        if prev_token_category == "СЛОВО" and token_category == "СЛОВО":
            need_space = True
        elif prev_token_category == "СЛОВО" and token_category == "ОТКРЫВАТЕЛЬ":
            need_space = True
        elif prev_token_category == "ЗАКРЫВАТЕЛЬ" and token_category == "СЛОВО":
            need_space = True
        elif prev_token_category == "КОНЕЦ_ПРЕДЛОЖЕНИЯ" and token_category == "СЛОВО":
            need_space = True
        elif prev_token_category == "КОНЕЦ_ПРЕДЛОЖЕНИЯ" and token_category == "ОТКРЫВАТЕЛЬ":
            need_space = True
        elif prev_token_category == "ТИПА_ЗАПЯТОЙ" and token_category == "СЛОВО":
            need_space = True
        elif prev_token_category == "ТИПА_ЗАПЯТОЙ" and token_category == "ОТКРЫВАТЕЛЬ":
            need_space = True
        elif prev_token_category == "ТИРЕ" and token_category != "ПРОБЕЛ":
            need_space = True
        elif prev_token_category != "ПРОБЕЛ" and token_category == "ТИРЕ":
            need_space = True

        print(f"refresh_final_text_preview. prev_token_category={prev_token_category}, token_category={token_category}, need_space={need_space}")

        # Заменяем текущий токен на его представление

        if need_space:
            tokens[i: i+1] = [" ", token]
            print(f"refresh_final_text_preview. Добавили пробел. tokens={tokens}")
            i += 1

        prev_token_category = token_category

        i += 1

    final_text_preview = "".join(tokens)

    show_text(f"{chat_channel} {final_text_preview}")

    print(">>>")
    print(">>>")
    print(final_text_preview)
    print(">>>")
    print(">>>")


def handle_text(partial_text: str, is_final: bool):
    global state, prev_partial_text, final_tokens, chat_channel, final_text_preview

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text and not is_final:
        print("handle_text. Same partial")
        return

    print(f"handle_text. partial_text={partial_text}, is_final={is_final}")

    prev_partial_text = partial_text

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()
    # Печатаем, какие слова там получились
    print(f"handle_text. tokens={tokens}, state={state!r}")

    stop_commands = SEND_WORDS | CANCEL_WORDS
    stop_command_position, stop_command = next(
        (
            (i, w)
            for i, w in enumerate(tokens) if w in stop_commands
        ),
        (None, None)
    )

    if stop_command is None:
        print("handle_text. Нет стоп команды")
        if is_final:
            final_tokens.extend(tokens)
            print(f"handle_text. Фраза финальная. Сохранили tokens={tokens} в final_tokens={final_tokens}, chat_channel={chat_channel}")
            tokens.clear()
        refresh_final_text_preview(tokens)
    elif stop_command in SEND_WORDS:
        tokens = tokens[0: stop_command_position]
        refresh_final_text_preview(tokens)
        if final_text_preview:
            print("handle_text. Вызываем отправку в чат")
            play_sound("sending_started")
            send_to_wow_chat(chat_channel, final_text_preview)
            play_sound("sending_complete")
        else:
            play_sound("sending_error")
            print("handle_text. Пытались отправить, но буфер пуст")
        to_idle()

    elif stop_command in CANCEL_WORDS:
        print("handle_text. Сброс")
        play_sound("editing_cancelled")
        to_idle()


def on_schedule_state_timer(new_state, callback):
    global state
    old_state = state
    state = new_state
    if callback:
        callback()
    print(f"handle_text_idle. {old_state} => {state}")


def set_state(new_state, callback=None):
    global state
    state = "timer"
    threading.Timer(0.2, on_schedule_state_timer, args=(new_state, callback)).start()


def handle_text_idle(partial_text: str):
    global state, prev_partial_text, chat_channel

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text:
        print("handle_text_idle. Same partial")
        return

    prev_partial_text = partial_text

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()
    # Печатаем, какие слова там получились
    print(f"handle_text_idle. tokens={tokens}, state={state!r}")

    # Если в idle нам попалась пачка слов со словом "запись" или аналогами, то включаем режим записи
    start_command_position, start_command = next(((i, w) for i, w in enumerate(tokens) if w in ACTIVATE_WORDS), (None, None))
    if start_command is not None:
        chat_channel = f"/{ACTIVATE_WORD_TO_CHAT_CHANNEL[start_command]}"

        def schedule_state_callback():
            show_text(chat_channel)

        set_state("recording", schedule_state_callback)
        prev_partial_text = None


# ================== АУДИОПОТОК И РАСПОЗНАВАНИЕ ==================

# Наш обработчик данных от sounddevice
def audio_callback(indata, frames, time_info, status):
    # Сообщаем статус аудио устройства, если нужно
    if status:
        print(f"audio_callback. status={status}", flush=True)

    # Достаем байты из indata. Кладем эти байты в очередь, на которой у нас сидит vosk
    q.put(bytes(indata))


def recognition_loop():
    global idle_recognizer, recording_recognizer, state

    start_overlay()

    model = Model(str(MODEL_PATH))

    recording_recognizer = KaldiRecognizer(model, SAMPLE_RATE)

    grammar = json.dumps(list(ACTIVATE_WORDS) + ["[unk]"], ensure_ascii=False)
    idle_recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)
    # idle_recognizer = recording_recognizer

    # Теперь будем работать с микрофоном через модуль sounddevice (локально - sd).
    # Открываем сырой входящий поток звуковых данных.
    stream = init_audio_stream()
    stream.start()

    # Входящий потом цифрового аудио инициализирован.
    # Сообщаем пользователю, что он уже может начинать говорить.
    print("recognition_loop. Начали слушать микрофон. Скажите одну из команд старта, чтобы начать диктовку.")

    recognizer = None
    is_final = False
    old_recognizer = None

    # И садимся в мертвый цикл
    while True:

        local_state = state

        if local_state == "idle":
            new_recognizer_name = "idle"
            new_recognizer = idle_recognizer
        elif local_state == "recording":
            new_recognizer_name = "recording"
            new_recognizer = recording_recognizer
        else:
            new_recognizer_name = "[no recognizer]"
            new_recognizer = None

        if recognizer != new_recognizer:
            print(f"recognition_loop. new_recognizer_name={new_recognizer_name}")

            # Если активный распознаватель еще не закончил работать, то
            # он какое-то время доработает до завершения фразы,
            # возможно, параллельно с новым
            if recognizer and not is_final:
                old_recognizer = recognizer
                print(f"recognition_loop. old_recognizer is set")

            recognizer = new_recognizer

            if recognizer:
                play_sound(new_recognizer_name)

        # Садимся ждать очередной кусок данных из входящего потока цифрового аудио
        try:
            data = q.get(timeout=1)
        except queue.Empty:
            continue

        # print("recognition_loop. Got data")

        if old_recognizer:
            print(f"recognition_loop. old_recognizer is set")
            old_recognizer_is_final = old_recognizer.AcceptWaveform(data)
            print(f"recognition_loop. old_recognizer_is_final={old_recognizer_is_final}")
            if old_recognizer_is_final:
                old_recognizer.Reset()
                old_recognizer = None

        if not recognizer:
            continue

        # print("recognition_loop. recognizer is chosen")

        is_final = recognizer.AcceptWaveform(data)

        if is_final:
            full_result = json.loads(recognizer.Result())
            text = full_result.get("text", "")
        else:
            partial_result = json.loads(recognizer.PartialResult())
            text = partial_result.get("partial", "")

        # print(f"recognition_loop. text={text}")

        if text:
            if local_state == "idle":
                # if is_final:
                #     handle_text_idle(text)
                handle_text_idle(text)
            else:
                handle_text(text, is_final)


def init_audio_stream():
    return sd.RawInputStream(
        samplerate=SAMPLE_RATE,  # Частота дискретизации - 16 000 сэмплов в секунду
        blocksize=BLOCK_SIZE,  # В одном блоке - 1600 сэмплов. Это - 0.1 секунды, т.к. частота дискретизации - 16 000 сэмплов в секунду
        dtype='int16',  # Каждый сэмпл - это 16 бит.
        channels=1,  # Один канал (моно)
        callback=audio_callback  # Для обработки сэмплов использовать вот такой описанный нами выше обработчик
    )


if __name__ == "__main__":
    try:
        recognition_loop()
    except KeyboardInterrupt:
        print("")
        print("[MAIN] Остановлено пользователем")
