from __future__ import annotations
import copy
from typing import cast

from app.app_logging import logging


_logger = logging.getLogger(__name__)


__all__ = ["tokens_to_text_builder_text", "build_text", "tokens_to_text_builder_reset"]


tokens_to_text_builder_text: str = ""

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
    def __init__(self, raw_tokens_indexes: tuple[int, ...]):
        self.raw_tokens_indexes = raw_tokens_indexes


class _AdditionTextAction(_TextAction):
    def __init__(
            self,
            raw_tokens_indexes: tuple[int, ...],
            addition: str,
            syntax_rules: _SyntaxRules,
            sentence_state: _SentenceState,
            text_arg: str,
    ):
        super().__init__(raw_tokens_indexes)
        self.addition = addition
        self.syntax_rules = syntax_rules
        self.sentence_state = sentence_state
        self.text = text_arg


class _RemovalTextAction(_TextAction):
    def __init__(
            self,
            raw_tokens_indexes: tuple[int, ...],
            last_visible_addition_index: int,
    ):
        super().__init__(raw_tokens_indexes)
        self.last_visible_addition_index = last_visible_addition_index


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
        if action is _AdditionTextAction:
            return j, cast(_AdditionTextAction, action)
        elif action is _RemovalTextAction:
            j = cast(_RemovalTextAction, action).last_visible_addition_index
        else:
            raise TypeError(f"Unexpected subclass {type(action).__name__}")
    return j, None


def build_text(new_raw_tokens: list[str], is_final: bool) -> str:
    global tokens_to_text_builder_text, _all_tokens, _final_token_index, _prev_partial_tokens

    if _final_token_index >= 0:
        del _all_tokens[_final_token_index + 1:]
    _all_tokens.extend(new_raw_tokens)

    first_diff_index = _get_first_diff_index(_prev_partial_tokens, new_raw_tokens)
    if first_diff_index is None:
        _logger.info("Same partial tokens")
        return tokens_to_text_builder_text

    i = _final_token_index + first_diff_index

    # Вдруг, последние предыдущие токены - это часть команды, и только сейчас пришли завершающие токены этой команды?
    # А по ним уже действия оформились. Надо бы эти действия значит откатить, и собрать по новой из освободившихся токенов.
    max_one_side_tokens = _MAX_COMMAND_WORDS - 1
    if i >= max_one_side_tokens:
        i -= max_one_side_tokens

    # Откатываем действия.
    first_discarded_action_index = _token_index_to_text_action_index[i]
    if first_discarded_action_index is not None:
        first_discarded_action = _text_actions[first_discarded_action_index]
        # Токены будем применять с первого токена первого удаленного действия
        i = first_discarded_action.raw_tokens_indexes[1]
        del _text_actions[first_discarded_action_index:]

    _, last_visible_addition = _get_last_visible_text_addition()
    last_addition_sentence_state = last_visible_addition.sentence_state if last_visible_addition else _SentenceState(open_quote=False, new_sentence=True)

    # Вот он этот наш самый главный цикл
    while i < len(_all_tokens):

        # Достаем текущий сырой токен
        token = _all_tokens[i]
        _logger.debug("i=%s, token=%s", i, token)

        if token == "удалить":
            # Какое сейчас последнее видимое добавление?
            last_visible_addition_index, last_visible_addition = _get_last_visible_text_addition()
            if last_visible_addition_index >= 0:
                # А когда мы его откатим, тогда какое будет последнее видимое добавление?
                last_visible_addition_index, last_visible_addition = _get_last_visible_text_addition(last_visible_addition_index - 1)
            else:
                # Последнего видимого добавления нет. Это значит, что видимый текст пустой
                pass

            new_text_action = _RemovalTextAction(
                raw_tokens_indexes=(i,),
                last_visible_addition_index=last_visible_addition_index,
            )
        elif token == "очистить":
            new_text_action = _RemovalTextAction(
                raw_tokens_indexes=(i,),
                last_visible_addition_index=-1,
            )
        else:
            # Добавление к тексту

            # Может быть это команда?
            command_candidate_words = tuple(_all_tokens[i: i + _MAX_COMMAND_WORDS])
            substitute = None
            while len(command_candidate_words) > 0:
                substitute = _word_combination_to_smart_token[command_candidate_words]
                if substitute:
                    break
                command_candidate_words = command_candidate_words[:-1]

            if substitute:
                token = substitute.text
                syntax_rules = substitute.syntax_rules
                raw_tokens_number = len(command_candidate_words)
            else:
                syntax_rules = _SYNTAX_WORD
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
            new_text = last_visible_addition.text + space_or_empty + token
            new_text_action = _AdditionTextAction(
                raw_tokens_indexes=raw_tokens_indexes,
                addition=token,
                syntax_rules=syntax_rules,
                sentence_state=this_addition_sentence_state,
                text_arg=new_text,
            )

        _text_actions.append(new_text_action)

        _, last_visible_addition = _get_last_visible_text_addition()
        last_addition_sentence_state = last_visible_addition.sentence_state if last_visible_addition else _SentenceState(open_quote=False, new_sentence=True)

        # Записываем связь от сырых токенов к версии
        this_version_index = len(_text_actions) - 1
        for token_index in new_text_action.raw_tokens_indexes:
            _token_index_to_text_action_index[token_index] = this_version_index

        _logger.debug("new_text_action=%s", new_text_action)

        i += len(new_text_action.raw_tokens_indexes)

    tokens_to_text_builder_text = last_visible_addition.text if last_visible_addition else ""

    # todo А это нужно? Или Яндекс и так это делает за нас?
    # new_raw_tokens = replace_russian_numbers(new_raw_tokens)

    _logger.debug(">>>")
    _logger.debug(">>>")
    _logger.debug(tokens_to_text_builder_text)
    _logger.debug(">>>")
    _logger.debug(">>>")

    if is_final:
        _final_token_index = len(_all_tokens) - 1
        _prev_partial_tokens.clear()
    else:
        _prev_partial_tokens = new_raw_tokens

    return tokens_to_text_builder_text


def tokens_to_text_builder_reset():
    global tokens_to_text_builder_text, _final_token_index

    tokens_to_text_builder_text = ""
    _all_tokens.clear()
    _final_token_index = -1
    _prev_partial_tokens.clear()
    _text_actions.clear()
    _token_index_to_text_action_index.clear()
