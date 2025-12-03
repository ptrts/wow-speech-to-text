from rus2num import Rus2Num

_r2n = Rus2Num()  # один экземпляр на весь процесс, чтобы не инициализировать каждый раз


def replace_russian_numbers(words: list[str]) -> list[str]:
    """
    Принимает массив токенов (слов) и возвращает новый массив токенов,
    где числительные заменены на числа.
    Пример:
      ["раз", "два", "три", "четыре", "пять"]
        -> ["раз", "2", "3", "4", "5"]
    """
    text = " ".join(words)
    normalized = _r2n(text)
    # для WoW обычно нормально просто splt'нуть по пробелу
    return normalized.split()
