import app.state
import app.overlay


class Command(object):
    state: str
    new_state: str

    def __init__(self, state: str, words: tuple[str, ...], new_state: str):
        self.state = state
        self.words = words
        self.new_state = new_state

    def do_things(self):
        ...


class StartRecordingCommand(Command):

    chat_channel: str

    def __init__(self, chat, *words: str):
        super().__init__("idle", words, "recording")
        self.chat_channel = chat

    def do_things(self):
        app.state.chat_channel = f"/{self.chat_channel}"
        app.state.set_state("recording", on_recording)
        prev_partial_text = None
        ...


commands = [
    StartRecordingCommand("bg", "бой"),
    StartRecordingCommand("s", "сказать"),
    StartRecordingCommand("y", "крикнуть"),
    StartRecordingCommand("g", "гильдия"),
]


class CommandSelector(object):
    state_to_word_to_command: dict[str, dict[str, Command]] = {}

    def __init__(self, commands_arg: list[Command]):
        for command in commands_arg:
            self._register_command(command)

    def select_command(self, tokens: list[str]):
        word_to_command = self.state_to_word_to_command.get(app.state.state)
        if word_to_command:
            for token in tokens:
                command = word_to_command.get(token)
                if command:
                    return command

    def _register_command(self, command):
        word_to_command = self.state_to_word_to_command.get(command.state)
        if not word_to_command:
            word_to_command = {}
            self.state_to_word_to_command[command.state] = word_to_command
        for word in command.words:
            word_to_command[word] = command


command_selector = CommandSelector(commands)
