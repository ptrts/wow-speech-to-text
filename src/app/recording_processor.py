from __future__ import annotations

import app.overlay
import app.beeps
import app.tokens_to_text_builder as tokens_to_text_builder
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread
import app.mode_container
import app.idle_processor

from app.app_logging import logging


logger = logging.getLogger(__name__)

SEND_WORDS = {"отправить", "готово", "окей", "ок", "дописать"}  # отправляют в чат
CANCEL_WORDS = {"сброс", "отмена"}  # сбрасывают буфер


class RecordingTextsProcessor(app.mode_container.ModeProcessor):

    chat_channel: str | None = None
    prev_partial_text: str | None = None

    idle_processor: app.idle_processor.IdleProcessor

    def __init__(self, mode_container: app.mode_container.ModeContainer):
        super().__init__(mode_container, "recording")

    def set_idle_processor(self, idle_processor: app.idle_processor.IdleProcessor):
        self.idle_processor = idle_processor

    def handle_recognized_fragment(self, recognized_fragment: str, is_final: bool):

        recognized_fragment = recognized_fragment.strip().lower()
        if not recognized_fragment:
            return

        if self.prev_partial_text is not None and recognized_fragment == self.prev_partial_text and not is_final:
            logger.debug("Same partial")
            return

        logger.info("recognized_fragment=%s, is_final=%s", recognized_fragment, is_final)

        # Разбиваем текст частичного результата на слова
        tokens = recognized_fragment.split()

        self.prev_partial_text = recognized_fragment

        # Печатаем, какие слова там получились
        logger.debug("tokens=%s", tokens)

        stop_commands = SEND_WORDS | CANCEL_WORDS
        stop_command_position, stop_command = next(
            (
                (i, w)
                for i, w in enumerate(tokens) if w in stop_commands
            ),
            (None, None)
        )

        if stop_command is None:
            logger.debug("Нет стоп команды")
            tokens_to_text_builder.build_text(tokens, is_final)
            self.recording_refresh_overlay()
        elif stop_command in SEND_WORDS:
            tokens = tokens[0: stop_command_position]
            tokens_to_text_builder.build_text(tokens, True)
            self.recording_refresh_overlay()
            app.recognize_thread.stop()
            if tokens_to_text_builder.text:
                logger.debug("Вызываем отправку в чат")
                app.beeps.play_sound("sending_started")
                app.wow_chat_sender.send_to_wow_chat(self.chat_channel, tokens_to_text_builder.text, let_edit=(stop_command == "дописать"))
                app.beeps.play_sound("sending_complete")
            else:
                app.beeps.play_sound("sending_error")
                logger.debug("Пытались отправить, но буфер пуст")
            self.to_idle()

        elif stop_command in CANCEL_WORDS:
            logger.debug("Сброс")
            app.beeps.play_sound("editing_cancelled")
            self.to_idle()

    def on_recognized_fragment(self, alternatives: list[str], is_final: bool):
        if self.mode_container.mode == "recording":
            self.handle_recognized_fragment(alternatives[0], is_final)

    def recording_refresh_overlay(self):
        text_1 = f"/{self.chat_channel} {tokens_to_text_builder.final_text}"
        text_2 = tokens_to_text_builder.non_final_text
        app.overlay.show_text(text_1, text_2)

    def to_idle(self):
        self.mode_container.to_mode(self, app.idle_processor.idle_processor.mode)

    def on_mode_enter(self, chat_channel: str):
        self.chat_channel = chat_channel
        self.prev_partial_text = None
        self.recording_refresh_overlay()
        app.recognize_thread.start(self.on_recognized_fragment)

    def on_mode_leave(self):
        app.recognize_thread.stop()

    def on_after_mode_leave_grace(self):
        self.chat_channel = None
        tokens_to_text_builder.reset()
        app.overlay.clear_all()


recording_processor = RecordingTextsProcessor(app.mode_container.mode_container)
