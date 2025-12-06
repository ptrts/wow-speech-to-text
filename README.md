# Что делает приложение

Это приложение `speech-to-text` для отправки сообщений в разные чаты WoW не нажимая вообще ничего. 
Все голосом. 

При желании можно допилить, чтоб интегрировалась и с другими приложениями, 
вставляла туда распознанный текст в нужные места и нажимала в них кнопки.

Но, сначала...

# Про деньги, риски и безопасность

Чтоб пользоваться, нужно иметь аккаунт Yandex Cloud
и быть готовым платить им за распознание. 

[Yandex Cloud Speech Kit](https://yandex.cloud/ru/docs/speechkit/) мне показался точнее и быстрее остальных, 
кого я пробовал. 

Кроме Yandex Cloud Speech Kit пробовал вот что:
- https://alphacephei.com/vosk/models
- https://github.com/openai/whisper
- https://github.com/SYSTRAN/faster-whisper

Платить надо 16 копеек за 15 секунд речи для распознания.
Длительность при тарификации округляется вверх. Распознал секунду и ушел - заплатил за 15 секунд.

Правила тарификации:
https://yandex.cloud/ru/docs/speechkit/pricing

Прайс-лист:
https://yandex.cloud/ru/price-list

Приложение аутентифицируется от вашего имени в Yandex Cloud, и **делает там разное**. Имейте в виду.
Все **на ваш страх и риск**.
Пользователям рекомендуется провести ревью кода, на предмет,
не создает ли приложение **от вашего имени и за ваши деньги 100 виртуальных машин для майнинга**, например.

# Подробнее про функционал

Запуск:

```
wow_stt.cmd
```

Оно открывается в консоли и сначала висит тихо фоном в режиме ожидания. 

Далее, если произнести слово "сказать", начинается режим записи сообщения в чат с микрофона в текст. 
На основном мониторе, оверлеем по центру экрана, появляется вот такая зеленая надпись:

```
/s
```

Оверлей пока что видно только если игра запущена **в оконном режиме**. 

По мере диктовки появляется текст

```
/s зима крестьянин торжествуя
```

Если сказать "отправить", то надиктованный отображаемый текст пойдет в буфер обмена и  
будет симулировано нажатие пользователем Enter (открытие чата), 
Ctrl+v (вставка текста сообщения в чат), 
Enter (отправка сообщения).

Если сказать "отмена" - произойдет сброс надиктованного текста и возврат в режим ожидания.

Вместо команды "сказать", для чата /s, есть еще, "бой", которая пишет в чат /bg. "Крик" - /y, "Гильдия" - /g.  

"Удалить" - откат на слово назад.

Есть команды "запятая", "точка" и т.д.

Числительные заменяются умным образом на числа. 

Подробнее смотрите в коде [wow_stt.py](./wow_stt.py).

# Настройка и запуск

Так чтоб сразу запустил и заработало - такого у нас тут нет. 
Требуется кое-чего скачивать, распаковывать и запускать. 

Во-первых, делаем себе аккаунт на https://yandex.cloud/ (если нет). 
Настраиваем платежный метод. 

Приложение аутентифицируется от вашего имени в Yandex Cloud gRPC API, 
и пользуется Yandex Speech Kit gRPC API для распознания речи с микрофона. 

Заходим в каталог проекта. 

Клонируем спецификацию Yandex Cloud gRPC в подкаталог yandex-cloud-cloudapi:
```
git clone https://github.com/yandex-cloud/cloudapi yandex-cloud-cloudapi
```

Скачиваем и распаковываем такую Vosk модель в подкаталог проекта vosk-model-small-ru-0.22

https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip

Она используется для распознания фиксированного набора команд для начала сеанса распознания уже при помощи Yandex. 

Ставим Python 3.10 через тот Windows инсталлятор, который в форме .exe:
- Страница релиза: https://www.python.org/downloads/release/python-31011/
- Инсталлятор: https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe

При помощи поставленного Python310, ставим **виртуальное окружение venv** этого Python310 (путь к питону меняем на свой):

```
"D:\Program Files\Python310\python.exe" -m venv .venv
```

Активируем созданное виртуальное окружение venv.

```
.\.venv\Scripts\activate
```

Должно показать командную строку, типа такой:

```
(.venv) D:\projects\wow-speech-to-text>
```

В **ТОЙ КОМАНДНОЙ СТРОКЕ ^^**, выполняем следующие команды для настройки окружения:

```
pip install --upgrade pip
pip install setuptools vosk sounddevice pyautogui pyperclip pywin32 rus2num
pip install grpcio-tools PyAudio
```

Генерируем файлы .py для нужных нам интерфейсов Yandex Cloud gRPC:

```
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
```

Теперь можно запускать:

```
wow_stt.cmd
```
