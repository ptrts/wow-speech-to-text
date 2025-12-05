from __future__ import annotations

import pyaudio
import wave
import grpc
import yandex.cloud.ai.stt.v3.stt_pb2 as stt_pb2
import yandex.cloud.ai.stt.v3.stt_service_pb2_grpc as stt_service_pb2_grpc


# todo Вынести из проекта или получать по Yandex Cloud API
FOLDER_ID = "b1gq2ols8cpvtgum2j63"

# Настройки потокового распознавания.
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLES_PER_SECOND = 8000
SAMPLES_PER_BLOCK = 4096
RECORD_SECONDS = 30
WAVE_OUTPUT_FILENAME = "audio.wav"

audio = pyaudio.PyAudio()


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
            if event_type == 'final':
                alternatives = [a.text for a in streaming_response.final.alternatives]
            if event_type == 'final_refinement':
                alternatives = [a.text for a in streaming_response.final_refinement.normalized_text.alternatives]

            # Выводим список альтернатив в лог.
            print(f'type={event_type}, alternatives={alternatives}')

    except grpc._channel._Rendezvous as err:
        print(f'Error code {err._state.code}, message: {err._state.details}')
        raise err
