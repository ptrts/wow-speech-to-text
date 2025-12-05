from __future__ import annotations

from typing import TypedDict, Optional
import http.server
import socketserver
import threading
import urllib.parse
import time
import webbrowser
import secrets
import base64
import hashlib
from typing import Any, Dict
import requests
import pyaudio
import wave
import grpc
import yandex.cloud.ai.stt.v3.stt_pb2 as stt_pb2
import yandex.cloud.ai.stt.v3.stt_service_pb2_grpc as stt_service_pb2_grpc


PORT = 52123
CALLBACK_PATH = "/oauth/callback"
REDIRECT_URI = f"http://localhost:{PORT}{CALLBACK_PATH}"

CLIENT_ID = "50988b5588004fc1bdc757217b681a9b"
SCOPE = "cloud:auth"

AUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.com/token"
IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"

# Настройки потокового распознавания.
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLES_PER_SECOND = 8000
SAMPLES_PER_BLOCK = 4096
RECORD_SECONDS = 30
WAVE_OUTPUT_FILENAME = "audio.wav"

audio = pyaudio.PyAudio()


class OAuth:
    code: str
    code_verifier: str
    code_challenge: str

    def __init__(self):
        self.state = secrets.token_urlsafe(16)
        self.code_verifier, self.code_challenge = OAuth._generate_pkce_pair()

    def launch(self):
        auth_url = OAuth._build_auth_url(CLIENT_ID, REDIRECT_URI, SCOPE, self.state, self.code_challenge)

        print("Открываю браузер по адресу:")
        print(auth_url)
        webbrowser.open_new_tab(auth_url)

    @staticmethod
    def _generate_pkce_pair() -> tuple[str, str]:
        """
        Генерим (code_verifier, code_challenge) для PKCE (S256).
        """
        # verifier: произвольная строка 43–128 символов
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_verifier_bytes = code_verifier.encode("ascii")

        digest = hashlib.sha256(code_verifier_bytes).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge

    @staticmethod
    def _build_auth_url(
            client_id: str,
            redirect_uri: str,
            scope: str,
            state: str,
            code_challenge: str
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)


class OAuthResult(TypedDict):
    code: Optional[str]
    state: Optional[str]
    error: Optional[str]
    raw_query: dict[str, list[str]]


class OAuthTCPServer(socketserver.TCPServer):

    state: str
    auth_result: Optional[OAuthResult] = None

    def __init__(self, server_address, RequestHandlerClass, state: str, bind_and_activate=True):
        self.state = state
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        pass


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    # Подсказываем типизатору, что server именно OAuthTCPServer
    server: OAuthTCPServer

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path != CALLBACK_PATH:
            self.send_error(404, "Not Found")
            return

        # Выводим в лог поступивший запрос
        print("===== RAW HTTP REQUEST =====")
        print(self.requestline)
        print()
        raw_headers = self.headers.as_bytes().decode("iso-8859-1", errors="replace")
        print(raw_headers.rstrip("\r\n"))
        print()

        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]

        if state != self.server.state:
            self.send_error(400, "Bad State")
            return

        # Здесь типизатор уже знает, что у сервера есть auth_result
        self.server.auth_result = {
            "code": code,
            "state": state,
            "error": error,
            "raw_query": params,
        }

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        html = """
        <html>
          <body>
            <h1>Авторизация завершена</h1>
            <p>Это окно можно закрыть и вернуться в приложение.</p>
          </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

        # threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format, *args):
        # чтобы не спамило логами
        pass


def launch_server(state: str):
    httpd = OAuthTCPServer(("127.0.0.1", PORT), OAuthCallbackHandler, state)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def wait_for_oauth_callback(httpd: OAuthTCPServer, timeout: float):
    print(f"Жду редиректа на http://127.0.0.1:{PORT}{CALLBACK_PATH} ...")

    started = time.time()
    while True:
        if time.time() - started > timeout:
            httpd.shutdown()
            raise TimeoutError("Не дождались OAuth-редиректа от Яндекса")

        if httpd.auth_result is None:
            time.sleep(0.1)
            continue

        return httpd.auth_result


def get_oauth_and_iam_tokens(timeout: float = 300.0) -> Dict[str, Any]:

    oauth = OAuth()

    with launch_server(oauth.state) as httpd:
        oauth.launch()
        cb = wait_for_oauth_callback(httpd, timeout)

    print("Callback:", cb)
    # cb ожидается вида:
    # {
    #   "code": str | None,
    #   "state": str | None,
    #   "error": str | None,
    #   "raw_query": dict[str, list[str]]
    # }

    if cb.get("error"):
        raise RuntimeError(f"OAuth error: {cb['error']}")

    if cb.get("state") != oauth.state:
        raise RuntimeError(f"state mismatch: ожидали {oauth.state}, получили {cb.get('state')}")

    code = cb.get("code")
    if not code:
        raise RuntimeError("В callback нет параметра 'code'")

    print("Обмениваю code на OAuth-токен...")
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": oauth.code_verifier,
        # device_id / device_name можно добавить при желании
    }
    resp = requests.post(
        TOKEN_URL,
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    tj = resp.json()
    # По доке:
    # {
    #   "token_type": "bearer",
    #   "access_token": "...",
    #   "expires_in": 1234,
    #   "refresh_token": "...",
    #   "scope": "..."
    # }
    oauth_access_token = tj["access_token"]
    oauth_refresh_token = tj.get("refresh_token")
    oauth_expires_in = tj.get("expires_in")

    print("OAuth-токен получен.")

    print("Обмениваю OAuth-токен на IAM-токен...")
    iam_resp = requests.post(
        IAM_URL,
        json={"yandexPassportOauthToken": oauth_access_token},
        timeout=10,
    )
    iam_resp.raise_for_status()
    ij = iam_resp.json()
    # По доке:
    # { "iamToken": "...", "expiresAt": "..." }
    iam_token = ij["iamToken"]
    iam_expires_at = ij.get("expiresAt")

    print("IAM-токен получен.")

    return {
        "oauth_access_token": oauth_access_token,
        "oauth_refresh_token": oauth_refresh_token,
        "oauth_expires_in": oauth_expires_in,
        "iam_token": iam_token,
        "iam_expires_at": iam_expires_at,
    }


def recognize_requests_generator():
    # Из объектов модели конфигурации распознания нашего gRPC API создаем конфигурацию.
    recognize_options = stt_pb2.StreamingOptions(
        recognition_model=stt_pb2.RecognitionModelOptions(
            audio_format=stt_pb2.AudioFormatOptions(
                raw_audio=stt_pb2.RawAudio(
                    audio_encoding=stt_pb2.RawAudio.LINEAR16_PCM,
                    sample_rate_hertz=8000,
                    audio_channel_count=1
                )
            ),
            text_normalization=stt_pb2.TextNormalizationOptions(
                text_normalization=stt_pb2.TextNormalizationOptions.TEXT_NORMALIZATION_ENABLED,
                profanity_filter=True,
                literature_text=False
            ),
            language_restriction=stt_pb2.LanguageRestrictionOptions(
                restriction_type=stt_pb2.LanguageRestrictionOptions.WHITELIST,
                language_code=['ru-RU']
            ),
            audio_processing_type=stt_pb2.RecognitionModelOptions.REAL_TIME
        )
    )

    # Отправляем на сервер собранные нами настройки распознавания.
    yield stt_pb2.StreamingRequest(session_options=recognize_options)

    # При помощи PyAudio открываем поток аудио данных с микрофона.
    stream = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLES_PER_SECOND,
        input=True,
        frames_per_buffer=SAMPLES_PER_BLOCK
    )

    print("recording")

    # Список блоков, отправленных на распознание, чтобы их сохранить в .wav файл
    wav_file_blocks = []

    # Выясняем, сколько блоков мы планируем записать.
    blocks_per_second = SAMPLES_PER_SECOND / SAMPLES_PER_BLOCK
    blocks_to_record = blocks_per_second * RECORD_SECONDS

    # Делаем цикл по количеству блоков.
    for i in range(0, int(blocks_to_record)):

        # Читаем из микрофона очередной блок.
        data = stream.read(SAMPLES_PER_BLOCK)

        # Отправляем считанный из микрофона блок на распознание.
        yield stt_pb2.StreamingRequest(chunk=stt_pb2.AudioChunk(data=data))

        # Сохраняем отправленный на распознание блок к себе в список.
        wav_file_blocks.append(data)

    print("finished")

    # Закрываем поток аудио данных
    stream.stop_stream()
    stream.close()

    # Закрываем PyAudio
    audio.terminate()

    # Данные, которые мы сняли с микрофона и отправили на распознание, мы почему-то записываем в .wav файл
    wave_file = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
    wave_file.setnchannels(CHANNELS)
    wave_file.setsampwidth(audio.get_sample_size(FORMAT))
    wave_file.setframerate(SAMPLES_PER_SECOND)
    wave_file.writeframes(b''.join(wav_file_blocks))
    wave_file.close()


def recognize_from_microphone(secret):

    # Установите соединение с сервером.

    # Создаем дефолтный объект кредов для соединения.
    cred = grpc.ssl_channel_credentials()

    # С этими кредами создаем соединение с TLS эндпойнтом нужного нам gRPC API
    channel = grpc.secure_channel('stt.api.cloud.yandex.net:443', cred)

    # В рамках установленного соединения
    # получаем
    # прокси-имплементацию для общения по интерфейсу RecognizerStub из файла stt_service_pb2_grpc.
    # Это у нас интерфейс какого-то распознавателя.
    recognizer = stt_service_pb2_grpc.RecognizerStub(channel)

    # Отправьте данные для распознавания.
    # Это функция типа stream_stream. Она принимает генератор и сама возвращает некий итерируемый ответ.
    # Вот тут мы получаем итератор it по результатам распознания.
    it = recognizer.RecognizeStreaming(

        # Снять с микрофона нужное количество блоков на заданное количество секунд.
        # Отправить эти блоки на распознание.
        # Сохранить отправленные бинарные данные в .wav файл.
        # Это функция-генератор, т.к. там внутри используется yield.
        # Что-то вроде флоу в котлине.
        recognize_requests_generator(),

        # Для установки соединения использовать такие заголовки.
        metadata=(

            # Параметры для аутентификации с API-ключом от имени сервисного аккаунта
            # ('authorization', f'Api-Key {secret}'),

            # Параметры для аутентификации с IAM-токеном
            ('authorization', f'Bearer {secret}'),
        )
    )

    # Обработайте ответы сервера и выведите результат в консоль.
    try:
        # Идем по итератору результатов распознания.
        for streaming_response in it:

            # Будем собирать вот такой список каких-то там альтернатив.
            alternatives = None

            # Получаем имя того поля группы Event внутри StreamingResponse,
            # которое (поле) присутствует в StreamingResponse.
            # В каждом из этих полей содержится объект какого-то своего класса и какой-то своей структуры.
            event_type = streaming_response.WhichOneof('Event')
            # Делаем разное, в зависимости от того, какое из этих полей там было задано.
            # Достаем список альтернативных слов из соответствующего объекта.
            if event_type == 'partial' and len(streaming_response.partial.alternatives) > 0:
                alternatives = [a.text for a in streaming_response.partial.alternatives]
            if event_type == 'final':
                alternatives = [a.text for a in streaming_response.final.alternatives]
            if event_type == 'final_refinement':
                alternatives = [a.text for a in streaming_response.final_refinement.normalized_text.alternatives]

            # Выводим список альтернатив в лог.
            print(f'type={event_type}, alternatives={alternatives}')

    except grpc._channel._Rendezvous as err:
        print(f'Error code {err._state.code}, message: {err._state.details}')
        raise err


if __name__ == "__main__":
    tokens = get_oauth_and_iam_tokens()
    print("Итог:")
    for k, v in tokens.items():
        print(f"{k}: {v}")
    recognize_from_microphone(tokens["iam_token"])
