import app.mode_container
import app.recording_processor
import app.overlay


class Command(object):
    mode: str
    new_mode: str

    def __init__(self, mode: str, words: tuple[str, ...], new_mode: str):
        self.mode = mode
        self.words = words
        self.new_mode = new_mode

    def do_things(self):
        ...


class StartRecordingCommand(Command):

    chat_channel: str

    def __init__(self, chat, *words: str):
        super().__init__("idle", words, "recording")
        self.chat_channel = chat

    def do_things(self):
        app.recording_processor.recording_processor.switch_to(self.chat_channel)


commands = [
    StartRecordingCommand("bg", "бой"),
    StartRecordingCommand("s", "сказать"),
    StartRecordingCommand("y", "крикнуть"),
    StartRecordingCommand("g", "гильдия"),
]


class CommandSelector(object):
    mode_to_word_to_command: dict[str, dict[str, Command]] = {}

    def __init__(self, commands_arg: list[Command]):
        for command in commands_arg:
            self._register_command(command)

    def select_command(self, tokens: list[str]):
        word_to_command = self.mode_to_word_to_command.get(app.mode_container.mode_container.mode)
        if word_to_command:
            for token in tokens:
                command = word_to_command.get(token)
                if command:
                    return command

    def _register_command(self, command):
        word_to_command = self.mode_to_word_to_command.get(command.mode)
        if not word_to_command:
            word_to_command = {}
            self.mode_to_word_to_command[command.mode] = word_to_command
        for word in command.words:
            word_to_command[word] = command


command_selector = CommandSelector(commands)
