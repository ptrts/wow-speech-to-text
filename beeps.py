import threading
import queue
import winsound

# Очередь звуковых событий
sound_queue = queue.Queue()

# Набор звуков под разные события
SOUND_MAP = {
    "editing_cancelled": ("beep", (110, 300)),
    # "idle":              ("beep", (220, 300)),
    "recording":         ("beep", (330, 300)),
    "sending_started":   ("beep", (440, 300)),
    "sending_complete":  ("beep", (550, 300)),
    "sending_error":     ("beep", (110, 300)),
}


def sound_worker():
    while True:
        event = sound_queue.get()
        if event is None:
            # возможность аккуратно завершить поток, если понадобится
            break

        play_sound_sync(event)
        sound_queue.task_done()


def play_sound_sync(event):
    kind, params = SOUND_MAP.get(event, (None, None))
    if kind == "beep":
        freq, dur = params
        try:
            print(f">>>>>>>>>>>>>>>>>>>>>>>>>>>>> event={event}, kind={kind}, freq={freq}, freq={dur}")
            winsound.Beep(freq, dur)
            print(f"<<<<<<<<<<<<<<<<<<<<<<<<<<<<< event={event}, kind={kind}, freq={freq}, freq={dur}")
        except RuntimeError:
            # например, если нет звукового устройства
            pass


# Запускаем поток-работник
sound_thread = threading.Thread(target=sound_worker, daemon=True)
sound_thread.start()


def play_sound_async(event_name: str):
    """Поставить событие в очередь на озвучивание."""
    # Можно сделать защиту от переполнения очереди:
    if sound_queue.qsize() < 20:
        sound_queue.put(event_name)


def play_sound_stub(event_name: str):
    pass


play_sound = play_sound_sync
