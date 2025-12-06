rem Ставим Python 3.10 через Windows инсталлятор в форме .exe
rem 	Страница релиза: https://www.python.org/downloads/release/python-31011/
rem 	Инсталлятор: https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe

rem Заходим в каталог проекта

rem При помощи поставленного Python310, ставим виртуальное окружение этого Python310
"D:\Program Files\Python310\python.exe" -m venv .venv

rem Активируем окружение.
rem Должно показать командную строку, типа такой:
rem 	(.venv) D:\projects\wow-speech-to-text>
.\.venv\Scripts\activate

rem В ТОЙ КОМАНДНОЙ СТРОКЕ ^^, выполняем следующие команды для настройки окружения:
pip install --upgrade pip
pip install setuptools vosk sounddevice pyautogui pyperclip pywin32 rus2num
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
rem pip install -U openai-whisper sounddevice numpy
pip install faster-whisper sounddevice numpy
pip install grpcio-tools PyAudio

python -m grpc_tools.protoc -I yandex-cloud-cloudapi -I yandex-cloud-cloudapi/third_party/googleapis ^
   --python_out=. ^
   --grpc_python_out=. ^
     google/api/http.proto ^
     google/api/annotations.proto ^
     yandex/cloud/access/access.proto ^
     yandex/cloud/api/operation.proto ^
     google/rpc/status.proto ^
     yandex/cloud/operation/operation.proto ^
     yandex/cloud/validation.proto ^
     yandex/cloud/ai/stt/v3/stt_service.proto ^
     yandex/cloud/ai/stt/v3/stt.proto ^
     yandex/cloud/resourcemanager/v1/cloud_service.proto ^
     yandex/cloud/resourcemanager/v1/cloud.proto ^
     yandex/cloud/resourcemanager/v1/folder_service.proto ^
     yandex/cloud/resourcemanager/v1/folder.proto
