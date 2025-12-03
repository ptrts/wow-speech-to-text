rem Ставим Python 3.10 через Windows инсталлятор в форме .exe
rem 	Страница релиза: https://www.python.org/downloads/release/python-31011/
rem 	Инсталлятор: https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe

rem Заходим в каталог проекта

rem При помощи поставленного Python310, ставим виртуальное окружение этого Python310
"D:\Program Files\Python310\python.exe" -m venv .venv310

rem Активируем окружение.
rem Должно показать командную строку, типа такой:
rem 	(.venv310) D:\projects\wow-speech-to-text>
.\.venv310\Scripts\activate

rem В ТОЙ КОМАНДНОЙ СТРОКЕ ^^, выполняем следующие команды для настройки окружения:
python -m pip install --upgrade pip
python -m pip install setuptools vosk sounddevice pyautogui pyperclip pywin32 rus2num
