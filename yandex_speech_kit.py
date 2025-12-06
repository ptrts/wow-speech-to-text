from __future__ import annotations

from typing import Protocol
import pyaudio
import grpc
import yandex.cloud.ai.stt.v3.stt_pb2 as stt_pb2
import yandex.cloud.ai.stt.v3.stt_service_pb2_grpc as stt_service_pb2_grpc
from app_logging import logging, TRACE


logger = logging.getLogger(__name__)

# todo Вынести из проекта или получать по Yandex Cloud API
FOLDER_ID = "b1gq2ols8cpvtgum2j63"

# Настройки потокового распознавания.
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLES_PER_SECOND = 8000
SAMPLES_PER_BLOCK = 4096
RECORD_SECONDS = 30
WAVE_OUTPUT_FILENAME = "audio.wav"

audio: pyaudio.PyAudio | None = None
cred: grpc.ChannelCredentials | None = None
channel: grpc.Channel | None = None
recognizer: stt_service_pb2_grpc.RecognizerStub | None = None
secret: str | None = None


def yandex_speech_kit_init(secret_arg: str):
    global cred, channel, recognizer, audio, secret

    # Создаем дефолтный объект кредов для соединения.
    cred = grpc.ssl_channel_credentials()

    # С этими кредами создаем соединение с TLS эндпойнтом нужного нам gRPC API
    channel = grpc.secure_channel('stt.api.cloud.yandex.net:443', cred)

    # В рамках установленного соединения
    # получаем
    # прокси-имплементацию для общения по интерфейсу RecognizerStub из файла stt_service_pb2_grpc.
    # Это у нас интерфейс какого-то распознавателя.
    recognizer = stt_service_pb2_grpc.RecognizerStub(channel)

    audio = pyaudio.PyAudio()
    secret = secret_arg


def yandex_speech_kit_shutdown():
    audio.terminate()


def recognize_requests_generator():
    global audio

    stream = None
    try:
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

        logger.info("recording")

        while True:
            # Читаем из микрофона очередной блок.
            data = stream.read(SAMPLES_PER_BLOCK)

            # Отправляем считанный из микрофона блок на распознание.
            yield stt_pb2.StreamingRequest(chunk=stt_pb2.AudioChunk(data=data))
    finally:
        # Закрываем поток аудио данных
        if stream:
            stream.stop_stream()
            stream.close()


class RecognizedFragmentCallback(Protocol):
    def __call__(self, alternatives: list[str], is_final: bool) -> None:
        ...


def recognize_from_microphone(callback: RecognizedFragmentCallback):
    global recognizer, secret

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
            ('x-folder-id', FOLDER_ID),
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
                callback(alternatives, False)
            if event_type == 'final':
                alternatives = [a.text for a in streaming_response.final.alternatives]
                callback(alternatives, True)

    except grpc._channel._Rendezvous as err:
        logger.error("Error code %s, message: %s", err._state.code, err._state.details)
        raise err
