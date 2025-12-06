import ctypes
import json
import queue
import time
from ctypes import wintypes
from pathlib import Path
import threading
import win32clipboard as cb
import win32con

import pyautogui
import sounddevice as sd
from vosk import Model, KaldiRecognizer

from overlay import start_overlay, show_text, clear_text
from beeps import play_sound
from russian_numerals import replace_russian_numbers
from layout_switch import switch_to_russian
from yandex_cloud_oauth import get_oauth_and_iam_tokens
from yandex_speech_kit import yandex_speech_kit_init, yandex_speech_kit_shutdown, recognize_from_microphone
from app_logging import logging, TRACE


logger = logging.getLogger(__name__)

# ================== НАСТРОЙКИ ==================

# Папка, где лежит текущий .py файл
BASE_DIR = Path(__file__).resolve().parent

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

# Задержки между нажатиями, чтобы игра точно всё проглотила
KEY_DELAY = 0.05  # секунды

# ================== ГЛОБАЛЬНОЕ СОСТОЯНИЕ ==================

state = "idle"  # "idle" | "timer" | "recording"
prev_partial_text: str | None = None
chat_channel: str | None = None
final_tokens: list[str] = []
final_text_preview: str | None = None
q = queue.Queue()  # очередь аудио-данных

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

def clipboard_copy(text: str):
    # Пишем в буфер обмена Юникод-строку
    cb.OpenClipboard()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        cb.CloseClipboard()


# Виртуальные коды клавиш
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_V = 0x56


def send_vk(vk: int, keyup: bool = False):
    flags = KEYEVENTF_KEYUP if keyup else 0
    inp = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def press_enter():
    send_vk(VK_RETURN, keyup=False)
    send_vk(VK_RETURN, keyup=True)


def press_ctrl_v():
    # Ctrl down
    send_vk(VK_CONTROL, keyup=False)
    # V down/up
    send_vk(VK_V, keyup=False)
    send_vk(VK_V, keyup=True)
    # Ctrl up
    send_vk(VK_CONTROL, keyup=True)


def send_to_wow_chat(channel: str, text: str, let_edit: bool = False):
    """
    Отправить сообщение в /bg:
      Enter, печать "/bg <текст>" как Unicode, Enter.
    """
    text = text.strip()
    if not text:
        logger.info("Пустой текст, не отправляем")
        return

    full_msg = f"{channel} {text}"
    logger.info("Отправляем: %r", full_msg)

    clipboard_copy(full_msg)

    # Небольшая пауза, чтобы не перебивать предыдущее действие
    time.sleep(KEY_DELAY)

    # Открываем чат
    pyautogui.press("enter")
    time.sleep(KEY_DELAY)

    # Вставляем текст через буфер
    press_ctrl_v()
    time.sleep(KEY_DELAY)

    # Отправляем
    if not let_edit:
        pyautogui.press("enter")
        time.sleep(KEY_DELAY)

    switch_to_russian()


# ================== ОБРАБОТКА РАСПОЗНАННЫХ ФРАЗ ==================

def to_idle():
    logger.debug("start")

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
        logger.debug("i=%s, token=%s", i, token)
        if token == "удалить":
            if i > 0:

                # Идем назад, ищем не пробел
                j = i - 1
                while j >= 0:
                    logger.debug("j=%s, tokens[j]=%s", j, tokens[j])
                    if tokens[j] != "пробел":
                        logger.debug("From here!")
                        break
                    j -= 1
                logger.debug("j=%s, tokens[j: i + 1]=%s", j, tokens[j: i + 1])

                # Удаляем этот не пробел, текущий токен, и все пробелы между ними.
                tokens[j: i + 1] = []
                i = j
            else:
                tokens[i: i + 1] = []
                i = 0
        else:
            i += 1

    tokens = replace_russian_numbers(tokens)

    logger.debug("final_tokens=%s, new_tokens=%s, tokens=%s", final_tokens, new_tokens, tokens)

    open_quote = False
    prev_token_category: str | None = None
    new_sentence = True

    i = 0
    while i < len(tokens):
        token = tokens[i]
        logger.debug("i=%s, token=%s", i, token)

        is_word = True
        command = None
        for word_combination_and_command in word_combination_and_text_modification_commands:
            words = word_combination_and_command.word_combination.words
            command = word_combination_and_command.command

            logger.debug("i=%s, command.code=%s, words=%s", i, command.code, words)

            if tokens[i: i + len(words)] == words:
                is_word = False
                token = command.code
                tokens[i: i + len(words)] = [token]
                logger.debug("i=%s, word combination match, tokens=%s", i, tokens)
                break
            else:
                command = None

        if token == "КАВЫЧКИ":
            open_quote = not open_quote
            if open_quote:
                token = tokens[i] = "ОТКРЫВАЮЩИЕ_КАВЫЧКИ"
                logger.debug("Данные кавычки - открывающие, token=%s", token)
            else:
                token = tokens[i] = "ЗАКРЫВАЮЩИЕ_КАВЫЧКИ"
                logger.debug("Данные кавычки - закрывающие, token=%s", token)

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

        logger.debug("token_category=%s", token_category)

        logger.debug("new_sentence=%s", new_sentence)

        if token_category == "КОНЕЦ_ПРЕДЛОЖЕНИЯ":
            new_sentence = True
        elif token_category == "СЛОВО" and new_sentence:
            token = tokens[i] = token.capitalize()
            new_sentence = False

        logger.debug("new_sentence=%s, token=%s", new_sentence, token)

        if command:
            if command.substitute:
                token = tokens[i] = command.substitute
            else:
                token = None
                tokens[i: i + 1] = []
                i -= 1
            logger.debug("token=%s, tokens=%s", token, tokens)

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

        logger.debug("prev_token_category=%s, token_category=%s, need_space=%s", prev_token_category, token_category, need_space)

        # Заменяем текущий токен на его представление

        if need_space:
            tokens[i: i+1] = [" ", token]
            logger.debug("Добавили пробел. tokens=%s", tokens)
            i += 1

        prev_token_category = token_category

        i += 1

    final_text_preview = "".join(tokens)

    show_text(f"{chat_channel} {final_text_preview}")

    logger.debug(">>>")
    logger.debug(">>>")
    logger.debug(final_text_preview)
    logger.debug(">>>")
    logger.debug(">>>")


def handle_text(partial_text: str, is_final: bool):
    global state, prev_partial_text, final_tokens, chat_channel, final_text_preview

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text and not is_final:
        logger.debug("Same partial")
        return

    logger.info("partial_text=%s, is_final=%s", partial_text, is_final)

    prev_partial_text = partial_text

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()
    # Печатаем, какие слова там получились
    logger.debug("tokens=%s, state=%r", tokens, state)

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
        if is_final:
            final_tokens.extend(tokens)
            logger.info("Фраза финальная. Сохранили tokens=%s в final_tokens=%s, chat_channel=%s", tokens, final_tokens, chat_channel)
            tokens.clear()
        refresh_final_text_preview(tokens)
    elif stop_command in SEND_WORDS:
        tokens = tokens[0: stop_command_position]
        refresh_final_text_preview(tokens)
        if final_text_preview:
            logger.debug("Вызываем отправку в чат")
            play_sound("sending_started")
            send_to_wow_chat(chat_channel, final_text_preview, let_edit=(stop_command == "дописать"))
            play_sound("sending_complete")
        else:
            play_sound("sending_error")
            logger.debug("Пытались отправить, но буфер пуст")
        to_idle()

    elif stop_command in CANCEL_WORDS:
        logger.debug("Сброс")
        play_sound("editing_cancelled")
        to_idle()


def on_schedule_state_timer(new_state, callback):
    global state
    old_state = state
    state = new_state
    if callback:
        callback()
    logger.debug("%s => %s", old_state, state)


def set_state(new_state, callback=None):
    global state
    state = "timer"
    threading.Timer(0.2, on_schedule_state_timer, args=(new_state, callback)).start()


def recognized_fragment_callback(alternatives: list[str], is_final: bool):
    # handle_text(alternatives[0], is_final)
    pass


def recognize_thread():
    recognize_from_microphone(recognized_fragment_callback)


def on_recording():
    show_text(chat_channel)
    threading.Thread(target=recognize_thread, daemon=True).start()


def handle_text_idle(partial_text: str):
    global state, prev_partial_text, chat_channel

    partial_text = partial_text.strip().lower()
    if not partial_text:
        return

    if prev_partial_text is not None and partial_text == prev_partial_text:
        logger.debug("Same partial")
        return

    prev_partial_text = partial_text

    # Разбиваем текст частичного результата на слова
    tokens = partial_text.split()
    # Печатаем, какие слова там получились
    logger.info("tokens=%s, state=%r", tokens, state)

    # Если в idle нам попалась пачка слов со словом "запись" или аналогами, то включаем режим записи
    start_command_position, start_command = next(((i, w) for i, w in enumerate(tokens) if w in ACTIVATE_WORDS), (None, None))
    if start_command is not None:
        chat_channel = f"/{ACTIVATE_WORD_TO_CHAT_CHANNEL[start_command]}"
        set_state("recording", on_recording)
        prev_partial_text = None


# ================== АУДИОПОТОК И РАСПОЗНАВАНИЕ ==================

# Наш обработчик данных от sounddevice
def audio_callback(indata, frames, time_info, status):
    # Сообщаем статус аудио устройства, если нужно
    if status:
        logger.debug("status=%s", status)

    # Достаем байты из indata. Кладем эти байты в очередь, на которой у нас сидит vosk
    q.put(bytes(indata))


def idle_recognition_loop():
    global state

    start_overlay()

    idle_model = Model(str(IDLE_MODEL_PATH))
    grammar = json.dumps(list(ACTIVATE_WORDS) + ["[unk]"], ensure_ascii=False)

    def get_idle_recognizer():
        # return recording_recognizer
        return KaldiRecognizer(idle_model, SAMPLE_RATE, grammar)

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
        if state != local_state:
            local_state = state

            logger.debug("local_state=%s", local_state)

            if local_state == "idle":
                recognizer = get_idle_recognizer()
            else:
                recognizer = None

            if state == "idle":
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

        if state == "idle":
            if is_final:
                full_result = json.loads(recognizer.Result())
                text = full_result.get("text", "")
            else:
                partial_result = json.loads(recognizer.PartialResult())
                text = partial_result.get("partial", "")

            logger.log(TRACE, "text=%s", text)

            if text:
                handle_text_idle(text)
        else:
            logger.debug("idle recognizer finishes its work. is_final=%s", is_final)
            if is_final:
                recognizer.Reset()
                recognizer = None


def init_audio_stream():
    return sd.RawInputStream(
        samplerate=SAMPLE_RATE,  # Частота дискретизации - 16 000 сэмплов в секунду
        blocksize=BLOCK_SIZE,  # В одном блоке - 1600 сэмплов. Это - 0.1 секунды, т.к. частота дискретизации - 16 000 сэмплов в секунду
        dtype='int16',  # Каждый сэмпл - это 16 бит.
        channels=1,  # Один канал (моно)
        callback=audio_callback  # Для обработки сэмплов использовать вот такой описанный нами выше обработчик
    )


if __name__ == "__main__":
    security_tokens = get_oauth_and_iam_tokens()

    print("Итог:")
    for k, v in security_tokens.items():
        print(f"{k}: {v}")

    iam_token = security_tokens["iam_token"]
    yandex_speech_kit_init(iam_token)

    try:
        idle_recognition_loop()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("[MAIN] Остановлено пользователем")
    finally:
        yandex_speech_kit_shutdown()
