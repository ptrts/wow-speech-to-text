from __future__ import annotations

import app.overlay
import app.beeps
import app.tokens_to_text_builder as tokens_to_text_builder
import app.state
import app.commands
import app.keyboard.keyboard_sender
import app.keyboard.clipboard_copier
import app.wow_chat_sender
import app.recognize_thread

from app.app_logging import logging


logger = logging.getLogger(__name__)

SEND_WORDS = {"отправить", "готово", "окей", "ок", "дописать"}  # отправляют в чат
CANCEL_WORDS = {"сброс", "отмена"}  # сбрасывают буфер


class RecordingTextsProcessor(object):

    prev_partial_text: str | None = None

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
            RecordingTextsProcessor.recording_refresh_overlay()
        elif stop_command in SEND_WORDS:
            tokens = tokens[0: stop_command_position]
            tokens_to_text_builder.build_text(tokens, True)
            RecordingTextsProcessor.recording_refresh_overlay()
            app.recognize_thread.stop()
            if tokens_to_text_builder.text:
                logger.debug("Вызываем отправку в чат")
                app.beeps.play_sound("sending_started")
                app.wow_chat_sender.send_to_wow_chat(app.state.chat_channel, tokens_to_text_builder.text, let_edit=(stop_command == "дописать"))
                app.beeps.play_sound("sending_complete")
            else:
                app.beeps.play_sound("sending_error")
                logger.debug("Пытались отправить, но буфер пуст")
            to_idle()

        elif stop_command in CANCEL_WORDS:
            logger.debug("Сброс")
            app.beeps.play_sound("editing_cancelled")
            to_idle()


    def on_recording():
        show_text(app.state.chat_channel, "")
        app.recognize_thread.start(on_recognized_fragment)


    def on_recognized_fragment(alternatives: list[str], is_final: bool):
        if app.state.state == "recording":
            app.recording_texts_processor.handle_recognized_fragment(alternatives[0], is_final)


    @staticmethod
    def recording_refresh_overlay():
        text_1 = f"{app.state.chat_channel} {tokens_to_text_builder.final_text}"
        text_2 = tokens_to_text_builder.non_final_text
        app.overlay.show_text(text_1, text_2)
