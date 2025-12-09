from __future__ import annotations
import copy
from typing import cast

from app.app_logging import logging


_logger = logging.getLogger(__name__)


__all__ = ["text", "build_text", "reset"]


text: str = ""

_all_tokens: list[str] = []
_final_token_index: int = -1
_prev_partial_tokens: list[str] = []
_text_actions: list[_TextAction] = []
_token_index_to_text_action_index: dict[int, int] = {}


class _SentenceState:
    def __init__(self, open_quote=False, new_sentence=True):
        self.open_quote = open_quote
        self.new_sentence = new_sentence


class _TextAction:
    def __init__(self, raw_token_index: int):
        self.raw_token_index = raw_token_index


class _AdditionTextAction(_TextAction):
    def __init__(
            self,
            raw_token_index: int,
            base_action_index: int,
            addition: str,
            syntax_rules: _SyntaxRules,
            sentence_state: _SentenceState,
            text_version: str,
    ):
        super().__init__(raw_token_index)
        self.base_action_index = base_action_index
        self.addition = addition
        self.syntax_rules = syntax_rules
        self.sentence_state = sentence_state
        self.text_version = text_version


class _RemovalTextAction(_TextAction):
    def __init__(
            self,
            raw_token_index: int,
            base_action_index: int,
    ):
        super().__init__(raw_token_index)
        self.base_action_index = base_action_index


class _SyntaxRules:
    def __init__(self, lean_left=False, lean_right=False, sentence_end=False, is_word=False):
        self.lean_left = lean_left
        self.lean_right = lean_right
        self.sentence_end = sentence_end
        self.is_word = is_word


_SYNTAX_LEAN_NONE = _SyntaxRules()
_SYNTAX_LEAN_LEFT = _SyntaxRules(lean_left=True)
_SYNTAX_LEAN_RIGHT = _SyntaxRules(lean_right=True)
_SYNTAX_LEAN_BOTH = _SyntaxRules(lean_left=True, lean_right=True)
_SYNTAX_SENTENCE_END = _SyntaxRules(lean_left=True, sentence_end=True)
_SYNTAX_WORD = _SyntaxRules(is_word=True)


class _SmartToken:
    def __init__(self, text_arg: str, syntax_rules: _SyntaxRules | None):
        self.text = text_arg
        self.syntax_rules = syntax_rules


_word_combination_to_smart_token: dict[tuple[str, ...], _SmartToken] = {}

_MAX_COMMAND_WORDS = 0


def _add_smart_token(syntax_rules: _SyntaxRules | None, smart_token: str, *word_combinations: str):
    global _MAX_COMMAND_WORDS
    smart_token = _SmartToken(smart_token, syntax_rules)
    for word_combination in word_combinations:
        words = word_combination.split()
        _MAX_COMMAND_WORDS = max(_MAX_COMMAND_WORDS, len(words))
        _word_combination_to_smart_token[tuple(words)] = smart_token


_add_smart_token(_SYNTAX_LEAN_LEFT, ",", "запятая")
_add_smart_token(_SYNTAX_LEAN_LEFT, ";", "точка с запятой")
_add_smart_token(_SYNTAX_LEAN_LEFT, ":", "двоеточие")
_add_smart_token(_SYNTAX_LEAN_LEFT, "...", "многоточие")

_add_smart_token(_SYNTAX_LEAN_RIGHT, "(", "открывающая скобка", "открыть скобку", "скобка")
_add_smart_token(_SYNTAX_LEAN_LEFT, ")", "закрывающая скобка", "закрыть скобку")

_add_smart_token(None, "\"", "кавычки")

_add_smart_token(_SYNTAX_LEAN_BOTH, "-", "дефис")
_add_smart_token(_SYNTAX_LEAN_BOTH, "/", "слэш")
_add_smart_token(_SYNTAX_LEAN_BOTH, "\\", "обратный слэш")
_add_smart_token(_SYNTAX_LEAN_BOTH, " ", "пробел")

_add_smart_token(_SYNTAX_LEAN_NONE, "-", "тире")

_add_smart_token(_SYNTAX_SENTENCE_END, ".", "точка")
_add_smart_token(_SYNTAX_SENTENCE_END, "!", "восклицательный знак")
_add_smart_token(_SYNTAX_SENTENCE_END, "?", "вопросительный знак")


def _get_first_diff_index(a: list[str], b: list[str]):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    # если цикл не нашёл разницы
    return min(len(a), len(b)) if len(a) != len(b) else None


def _get_last_visible_text_addition(max_index: int | None = None) -> tuple[int, _AdditionTextAction | None]:
    j = len(_text_actions) - 1
    if max_index is not None:
        j = min(j, max_index)
    while j >= 0:
        action = _text_actions[j]
        if isinstance(action, _AdditionTextAction):
            return j, cast(_AdditionTextAction, action)
        elif isinstance(action, _RemovalTextAction):
            j = cast(_RemovalTextAction, action).base_action_index
        else:
            raise TypeError(f"Unexpected subclass {type(action).__name__}")
    return j, None


def build_text(new_raw_tokens: list[str], is_final: bool) -> str:
    global text, _all_tokens, _final_token_index, _prev_partial_tokens

    _logger.debug(
        "_all_tokens=%s, _final_token_index=%s, _prev_partial_tokens=%s, new_raw_tokens=%s, is_final=%s",
        _all_tokens, _final_token_index, _prev_partial_tokens, new_raw_tokens, is_final
    )

    del _all_tokens[_final_token_index + 1:]
    _all_tokens.extend(new_raw_tokens)

    _logger.debug("_all_tokens=%s", _all_tokens)

    first_diff_index = _get_first_diff_index(_prev_partial_tokens, new_raw_tokens)

    _logger.debug("first_diff_index=%s", first_diff_index)

    if first_diff_index is None:
        _logger.debug("Same partial tokens")
        return text

    i = _final_token_index + 1 + first_diff_index

    _logger.debug("i=%s", i)

    # Отбрасывание действий тех токенов, которые не пришли в этот раз.
    first_discarded_action_index = _token_index_to_text_action_index.get(i)
    _logger.debug("first_discarded_action_index=%s", first_discarded_action_index)
    if first_discarded_action_index is not None:
        del _text_actions[first_discarded_action_index:]

    while i < len(_all_tokens):

        # Достаем текущий сырой токен
        token = _all_tokens[i]
        _logger.debug("i=%s, token=%s", i, token)

        if token == "удалить":
            # Какое сейчас последнее видимое добавление?
            # Будем откатывать до базовой версии этого видимого добавления
            last_visible_addition_index, last_visible_addition = _get_last_visible_text_addition()
            if last_visible_addition_index >= 0:
                base_action_index = last_visible_addition.base_action_index
            else:
                base_action_index = -1

            new_text_action = _RemovalTextAction(
                raw_token_index=i,
                base_action_index=base_action_index,
            )
        elif token == "очистить":
            new_text_action = _RemovalTextAction(
                raw_token_index=i,
                base_action_index=-1,
            )
        else:
            # Добавление к тексту

            # Идем назад, ищем видимые токены, чтоб по ним потом смотреть, нет ли таких многословных команд
            visible_additions = [None]
            command_candidate_words = [token]
            text_action_index = len(_text_actions) - 1
            while len(visible_additions) < _MAX_COMMAND_WORDS:
                last_visible_addition_index, last_visible_addition = _get_last_visible_text_addition(text_action_index)
                if last_visible_addition:

                    visible_additions.insert(0, last_visible_addition)

                    candidate_word_token_index = last_visible_addition.raw_token_index
                    candidate_word_token = _all_tokens[candidate_word_token_index]
                    command_candidate_words.insert(0, candidate_word_token)

                    text_action_index = last_visible_addition_index - 1
                else:
                    break

            # Используем найденные токены для поиска команд разной длины.
            # Начинаем от самых длинных команд.
            substitute = None
            while len(command_candidate_words) > 0:
                substitute = _word_combination_to_smart_token.get(tuple(command_candidate_words))
                if substitute:
                    break
                del visible_additions[0]
                del command_candidate_words[0]

            if substitute:
                first_token_addition = visible_additions[0]
                if first_token_addition is None:
                    # Значит, первый токен - это текущий токен, по которому действие еще не создано.
                    # Это значит, что мы должны базироваться на последнем действии, какое есть.
                    base_action_index = len(_text_actions) - 1
                else:
                    base_action_index = first_token_addition.base_action_index
                token = substitute.text
                syntax_rules = substitute.syntax_rules
            else:
                base_action_index = len(_text_actions) - 1
                syntax_rules = _SYNTAX_WORD

            _, last_visible_addition = _get_last_visible_text_addition(base_action_index)
            last_addition_sentence_state = last_visible_addition.sentence_state if last_visible_addition else _SentenceState(open_quote=False, new_sentence=True)

            # Наследуем объект состояния как мы его оставим после себя
            # от
            # объекта состояния как его оставило после себя предыдущее добавление к тексту
            this_addition_sentence_state = copy.copy(last_addition_sentence_state)

            # Открывающие и закрывающие кавычки
            if token == "\"":
                this_addition_sentence_state.open_quote = not this_addition_sentence_state.open_quote
                if this_addition_sentence_state.open_quote:
                    syntax_rules = _SYNTAX_LEAN_RIGHT
                    _logger.debug("Данные кавычки - открывающие")
                else:
                    syntax_rules = _SYNTAX_LEAN_LEFT
                    _logger.debug("Данные кавычки - закрывающие")
            _logger.debug("new_sentence=%s", this_addition_sentence_state.new_sentence)

            # Большие буквы в начале предложения
            if syntax_rules.sentence_end:
                this_addition_sentence_state.new_sentence = True
            elif syntax_rules.is_word and this_addition_sentence_state.new_sentence:
                token = token.capitalize()
                this_addition_sentence_state.new_sentence = False
            _logger.debug("new_sentence=%s, token=%s", this_addition_sentence_state.new_sentence, token)

            # Расстановка пробелов
            not_need_space = last_visible_addition is None or last_visible_addition.syntax_rules.lean_right or syntax_rules.lean_left
            need_space = not not_need_space
            space_or_empty = " " if need_space else ""

            # Новая версия текста
            prev_text = last_visible_addition.text_version if last_visible_addition else ""
            new_text = prev_text + space_or_empty + token
            new_text_action = _AdditionTextAction(
                raw_token_index=i,
                base_action_index=base_action_index,
                addition=token,
                syntax_rules=syntax_rules,
                sentence_state=this_addition_sentence_state,
                text_version=new_text,
            )

        _text_actions.append(new_text_action)

        # Записываем связь от сырых токенов к версии
        this_version_index = len(_text_actions) - 1
        _token_index_to_text_action_index[new_text_action.raw_token_index] = this_version_index

        _logger.debug("new_text_action=%s", new_text_action)

        i += 1

    _, last_visible_addition = _get_last_visible_text_addition()
    text = last_visible_addition.text_version if last_visible_addition else ""

    # todo А это нужно? Или Яндекс и так это делает за нас?
    # new_raw_tokens = replace_russian_numbers(new_raw_tokens)

    _logger.debug(">>>")
    _logger.debug(">>>")
    _logger.debug(text)
    _logger.debug(">>>")
    _logger.debug(">>>")

    if is_final:
        _final_token_index = len(_all_tokens) - 1
        _prev_partial_tokens.clear()
    else:
        _prev_partial_tokens = new_raw_tokens

    return text


def reset():
    global text, _final_token_index

    text = ""
    _all_tokens.clear()
    _final_token_index = -1
    _prev_partial_tokens.clear()
    _text_actions.clear()
    _token_index_to_text_action_index.clear()
