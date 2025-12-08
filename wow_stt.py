from __future__ import annotations
import ctypes
import json
import queue
import time
from ctypes import wintypes
from pathlib import Path
import threading
import win32clipboard as cb
import win32con
import copy
from typing import cast

import pyautogui
import sounddevice as sd
from vosk import Model, KaldiRecognizer

from overlay import start_overlay, show_text, clear_text
from beeps import play_sound
from russian_numerals import replace_russian_numbers
from layout_switch import switch_to_russian
from yandex_cloud_oauth import get_oauth_and_iam_tokens
from yandex_speech_kit import yandex_speech_kit_init, yandex_speech_kit_shutdown, recognize_from_microphone
from keyboard_state import keyboard_is_clean, wait_for_keyboard_clean
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
prev_partial_tokens: list[str] | None = None
chat_channel: str | None = None
final_tokens: list[str] = []
text_preview: str | None = None
overlay_line_2: str | None = None
recognize_thread: threading.Thread | None = None
recognize_thread_stop_event: threading.Event | None = None
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

    global overlay_line_2

    text = text.strip()
    if not text:
        logger.info("Пустой текст, не отправляем")
        return

    switch_to_russian()

    full_msg = f"{channel} {text}"
    logger.info("Отправляем: %r", full_msg)

    clipboard_copy(full_msg)

    # Небольшая пауза, чтобы не перебивать предыдущее действие
    time.sleep(KEY_DELAY)

    if not keyboard_is_clean():
        overlay_line_2 = "... Отпускай!"
    refresh_overlay()

    still_clean = wait_for_keyboard_clean()

    overlay_line_2 = None
    refresh_overlay()

    if not still_clean:
        return

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


# ================== ОБРАБОТКА РАСПОЗНАННЫХ ФРАЗ ==================

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


def refresh_overlay():
    if state == "recording":
        text = f"{chat_channel} {text_preview}"
        if overlay_line_2:
            text += overlay_line_2

        show_text(text)
    else:
        clear_text()


class SentenceState:
    def __init__(self, open_quote=False, new_sentence=True):
        self.open_quote = open_quote
        self.new_sentence = new_sentence


class TextAction:
    def __init__(self, raw_tokens_indexes: tuple[int, ...]):
        self.raw_tokens_indexes = raw_tokens_indexes


class AdditionTextAction(TextAction):
    def __init__(
            self,
            raw_tokens_indexes: tuple[int, ...],
            addition: str,
            syntax_rules: SyntaxRules,
            sentence_state: SentenceState,
            text: str,
    ):
        super().__init__(raw_tokens_indexes)
        self.addition = addition
        self.syntax_rules = syntax_rules
        self.sentence_state = sentence_state
        self.text = text


class RemovalTextAction(TextAction):
    def __init__(
            self,
            raw_tokens_indexes: tuple[int, ...],
            last_visible_addition_index: int,
    ):
        super().__init__(raw_tokens_indexes)
        self.last_visible_addition_index = last_visible_addition_index


text_actions: list[TextAction] = []
token_index_to_text_action_index: dict[int, int]


class SyntaxRules:
    def __init__(self, lean_left=False, lean_right=False, sentence_end=False, is_word=False):
        self.lean_left = lean_left
        self.lean_right = lean_right
        self.sentence_end = sentence_end
        self.is_word = is_word


SYNTAX_LEAN_NONE = SyntaxRules()
SYNTAX_LEAN_LEFT = SyntaxRules(lean_left=True)
SYNTAX_LEAN_RIGHT = SyntaxRules(lean_right=True)
SYNTAX_LEAN_BOTH = SyntaxRules(lean_left=True, lean_right=True)
SYNTAX_SENTENCE_END = SyntaxRules(lean_left=True, sentence_end=True)
SYNTAX_WORD = SyntaxRules(is_word=True)


class SmartToken:
    def __init__(self, text: str, syntax_rules: SyntaxRules | None):
        self.text = text
        self.syntax_rules = syntax_rules


word_combination_to_smart_token: dict[tuple[str, ...], SmartToken] = {}


def add_smart_token(syntax_rules: SyntaxRules | None, smart_token: str, *word_combinations: str):
    smart_token = SmartToken(smart_token, syntax_rules)
    for word_combination in word_combinations:
        words = word_combination.split()
        word_combination_to_smart_token[tuple(words)] = smart_token


add_smart_token(SYNTAX_LEAN_LEFT, ",", "запятая")
add_smart_token(SYNTAX_LEAN_LEFT, ";", "точка с запятой")
add_smart_token(SYNTAX_LEAN_LEFT, ":", "двоеточие")
add_smart_token(SYNTAX_LEAN_LEFT, "...", "многоточие")

add_smart_token(SYNTAX_LEAN_RIGHT, "(", "открывающая скобка", "открыть скобку", "скобка")
add_smart_token(SYNTAX_LEAN_LEFT, ")", "закрывающая скобка", "закрыть скобку")

add_smart_token(None, "\"", "кавычки")

add_smart_token(SYNTAX_LEAN_BOTH, "-", "дефис")
add_smart_token(SYNTAX_LEAN_BOTH, "/", "слэш")
add_smart_token(SYNTAX_LEAN_BOTH, "\\", "обратный слэш")
add_smart_token(SYNTAX_LEAN_BOTH, " ", "пробел")

add_smart_token(SYNTAX_LEAN_NONE, "-", "тире")

add_smart_token(SYNTAX_SENTENCE_END, ".", "точка")
add_smart_token(SYNTAX_SENTENCE_END, "!", "восклицательный знак")
add_smart_token(SYNTAX_SENTENCE_END, "?", "вопросительный знак")

final_version_index = -1


def get_first_diff_index(a: list[str], b: list[str]):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    # если цикл не нашёл разницы
    return min(len(a), len(b)) if len(a) != len(b) else None


def get_last_visible_text_addition(max_index: int | None = None) -> tuple[int, AdditionTextAction | None]:
    j = len(text_actions) - 1
    if max_index is not None:
        j = min(j, max_index)
    while j >= 0:
        text_version = text_actions[j]
        if text_version is AdditionTextAction:
            return j, cast(AdditionTextAction, text_version)
        elif text_version is RemovalTextAction:
            j = cast(RemovalTextAction, text_version).last_visible_addition_index
        else:
            raise TypeError(f"Unexpected subclass {type(text_version).__name__}")
    return j, None


def refresh_text_preview(current_raw_tokens: list[str]):
    global text_preview, final_version_index

    i = get_first_diff_index(prev_partial_tokens, current_raw_tokens)

    if i is None:
        logger.info("Same partial tokens")
        return

    # Удаляем действия, по которым токены приходили в прошлый раз, а сейчас не пришли.
    # Не важно, это действия добавления к тексту или удаления.
    # Возможно корректируем индекс сырого токена, от которого мы пойдем формировать новые действия над текстом.
    if i < len(current_raw_tokens):
        first_discarded_action_index = token_index_to_text_action_index[i]
        first_discarded_action = text_actions[first_discarded_action_index]
        # Допустим версию создали по сочетанию слов "закрывающая скобка".
        # Если в прошлый раз пришло "закрывающая", "скобка", а в этот раз "закрывающая", "собака", то
        # ту версию, добавившую в текст ")" нужно откатить.
        # После отката версии, применение новых сырых токенов нужно начинать с "закрывающая", а не с "собака",
        # хоть "закрывающая" приходило и в прошлый раз и это не новый токен, как у нас здесь "собака".
        i = first_discarded_action.raw_tokens_indexes[1]

        # Откатываем историю
        del text_actions[first_discarded_action_index:]

    # todo А это куда? Может это делать уже где-то после всего? Или Яндекс и так это делает за нас?
    # current_raw_tokens = replace_russian_numbers(current_raw_tokens)

    _, last_visible_addition = get_last_visible_text_addition()
    last_addition_sentence_state = last_visible_addition.sentence_state if last_visible_addition else SentenceState(open_quote=False, new_sentence=True)

    # Вот он этот наш самый главный цикл
    while i < len(current_raw_tokens):

        # Достаем текущий сырой токен
        token = current_raw_tokens[i]
        logger.debug("i=%s, token=%s", i, token)

        if token == "удалить":
            # Какое сейчас последнее видимое добавление?
            last_visible_addition_index, last_visible_addition = get_last_visible_text_addition()
            if last_visible_addition_index >= 0:
                # А когда мы его откатим, тогда какое будет последнее видимое добавление?
                last_visible_addition_index, last_visible_addition = get_last_visible_text_addition(last_visible_addition_index - 1)
            else:
                # Последнего видимого добавления нет. Это значит, что видимый текст пустой
                pass

            new_text_action = RemovalTextAction(
                raw_tokens_indexes=(i,),
                last_visible_addition_index=last_visible_addition_index,
            )
        elif token == "очистить":
            new_text_action = RemovalTextAction(
                raw_tokens_indexes=(i,),
                last_visible_addition_index=-1,
            )
        else:
            # Добавление к тексту

            # Может быть это команда?
            max_command_words = 3
            command_candidate_words = tuple(current_raw_tokens[i: i + max_command_words])
            substitute = None
            while len(command_candidate_words) > 0:
                substitute = word_combination_to_smart_token[command_candidate_words]
                if substitute:
                    break
                command_candidate_words = command_candidate_words[:-1]

            if substitute:
                token = substitute.text
                syntax_rules = substitute.syntax_rules
                raw_tokens_number = len(command_candidate_words)
            else:
                syntax_rules = SYNTAX_WORD
                raw_tokens_number = 1
            raw_tokens_indexes = tuple(range(i, i + raw_tokens_number))

            # Наследуем объект состояния как мы его оставим после себя
            # от
            # объекта состояния как его оставило после себя предыдущее добавление к тексту
            this_addition_sentence_state = copy.copy(last_addition_sentence_state)

            # Открывающие и закрывающие кавычки
            if token == "\"":
                this_addition_sentence_state.open_quote = not this_addition_sentence_state.open_quote
                if this_addition_sentence_state.open_quote:
                    syntax_rules = SYNTAX_LEAN_RIGHT
                    logger.debug("Данные кавычки - открывающие")
                else:
                    syntax_rules = SYNTAX_LEAN_LEFT
                    logger.debug("Данные кавычки - закрывающие")
            logger.debug("new_sentence=%s", this_addition_sentence_state.new_sentence)

            # Большие буквы в начале предложения
            if syntax_rules.sentence_end:
                this_addition_sentence_state.new_sentence = True
            elif syntax_rules.is_word and this_addition_sentence_state.new_sentence:
                token = token.capitalize()
                this_addition_sentence_state.new_sentence = False
            logger.debug("new_sentence=%s, token=%s", this_addition_sentence_state.new_sentence, token)

            # Расстановка пробелов
            not_need_space = last_visible_addition is None or last_visible_addition.syntax_rules.lean_right or syntax_rules.lean_left
            need_space = not not_need_space
            space_or_empty = " " if need_space else ""

            # Новая версия текста
            new_text = last_visible_addition.text + space_or_empty + token
            new_text_action = AdditionTextAction(
                raw_tokens_indexes=raw_tokens_indexes,
                addition=token,
                syntax_rules=syntax_rules,
                sentence_state=this_addition_sentence_state,
                text=new_text,
            )

        text_actions.append(new_text_action)

        _, last_visible_addition = get_last_visible_text_addition()
        last_addition_sentence_state = last_visible_addition.sentence_state if last_visible_addition else SentenceState(open_quote=False, new_sentence=True)

        # Записываем связь от сырых токенов к версии
        this_version_index = len(text_actions) - 1
        for token_index in new_text_action.raw_tokens_indexes:
            token_index_to_text_action_index[token_index] = this_version_index

        logger.debug("new_text_action=%s", new_text_action)

        i += len(new_text_action.raw_tokens_indexes)

    text_preview = last_visible_addition.text if last_visible_addition else ""

    refresh_overlay()

    logger.debug(">>>")
    logger.debug(">>>")
    logger.debug(text_preview)
    logger.debug(">>>")
    logger.debug(">>>")


def handle_text(partial_text: str, is_final: bool):
    global state, prev_partial_text, prev_partial_tokens, final_tokens, chat_channel, text_preview

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
    prev_partial_tokens = tokens

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
        refresh_text_preview(tokens)
    elif stop_command in SEND_WORDS:
        tokens = tokens[0: stop_command_position]
        refresh_text_preview(tokens)
        if text_preview:
            logger.debug("Вызываем отправку в чат")
            play_sound("sending_started")
            send_to_wow_chat(chat_channel, text_preview, let_edit=(stop_command == "дописать"))
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
    logger.debug(new_state)
    state = "timer"
    threading.Timer(0.2, on_schedule_state_timer, args=(new_state, callback)).start()


def on_recognized_fragment(alternatives: list[str], is_final: bool):
    if state == "recording":
        handle_text(alternatives[0], is_final)


def on_recording():
    global recognize_thread, recognize_thread_stop_event
    show_text(chat_channel)

    recognize_thread_stop_event = threading.Event()
    recognize_thread = threading.Thread(
        target=recognize_from_microphone,
        args=(recognize_thread_stop_event, on_recognized_fragment),
        daemon=True
    )
    recognize_thread.start()


def on_idle():
    global state, final_tokens, chat_channel, prev_partial_text, text_preview, overlay_line_2
    final_tokens = []
    chat_channel = None
    prev_partial_text = None
    text_preview = None
    overlay_line_2 = None
    refresh_overlay()


def to_idle():
    global recognize_thread, recognize_thread_stop_event
    logger.info("start")
    if recognize_thread:
        logger.info("recognize_thread is set. Stopping the thread")
        recognize_thread_stop_event.set()
        recognize_thread = None
    else:
        logger.info("recognize_thread is not set")
    set_state("idle", on_idle)


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

    logger.info("Итог:")
    for k, v in security_tokens.items():
        logger.info(f"{k}: {v}")

    iam_token = security_tokens["iam_token"]
    yandex_speech_kit_init(iam_token)

    try:
        idle_recognition_loop()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("[MAIN] Остановлено пользователем")
    finally:
        yandex_speech_kit_shutdown()
