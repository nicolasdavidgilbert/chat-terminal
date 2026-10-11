"""Edición tipo Readline: estado privado y efímero de una conexión."""

from dataclasses import dataclass
from os.path import commonprefix
import re

from wcwidth import iter_graphemes

from .protocol import MAX_INPUT
from .names import normalize_nick

HISTORY_SIZE = 300
COMMANDS = ("/msg", "/lista", "/clear", "/ayuda", "/salir", "/help",
            "/who", "/quit", "/exit", "ls", "who", "clear", "help", "exit",
            "logout", "history", "man")


@dataclass(frozen=True)
class EditResult:
    action: str = "redraw"
    text: str = ""
    choices: tuple[str, ...] = ()


def safe_text(text: str) -> str:
    """Elimina controles y caracteres de formato que podrían alterar el terminal."""
    return "".join(ch for ch in text if ch.isprintable() or ch == "\u200d")


class Editor:
    def __init__(self):
        self.line = ""
        self.cursor = 0
        self.history: list[str] = []
        self.history_index = 0
        self.draft = ("", 0)
        self.last_completion = None

    def dispose(self):
        self.line = ""
        self.cursor = 0
        self.history.clear()
        self.history_index = 0
        self.draft = ("", 0)
        self.last_completion = None

    def boundaries(self) -> list[int]:
        positions = [0]
        for cluster in iter_graphemes(self.line):
            positions.append(positions[-1] + len(cluster))
        return positions

    def insert(self, text: str) -> bool:
        text = safe_text(text)
        remaining = MAX_INPUT - len(self.line.encode("utf-8"))
        text = text.encode("utf-8")[:remaining].decode("utf-8", "ignore")
        if not text:
            return False
        self.line = self.line[:self.cursor] + text + self.line[self.cursor:]
        self.cursor += len(text)
        self.cursor = next(p for p in self.boundaries() if p >= self.cursor)
        self.last_completion = None
        return True

    def remember(self, text: str):
        if text.strip() and (not self.history or self.history[-1] != text):
            self.history.append(text)
            del self.history[:-HISTORY_SIZE]
        self.history_index = len(self.history)
        self.draft = ("", 0)

    def _history(self, direction: int):
        if self.history_index == len(self.history):
            self.draft = (self.line, self.cursor)
        self.history_index = max(
            0, min(len(self.history), self.history_index + direction)
        )
        if self.history_index == len(self.history):
            self.line, self.cursor = self.draft
        else:
            self.line = self.history[self.history_index]
            self.cursor = len(self.line)

    def _word_left(self) -> int:
        position = self.cursor
        while position and not self.line[position - 1].isalnum():
            position -= 1
        while position and self.line[position - 1].isalnum():
            position -= 1
        return position

    def _word_right(self) -> int:
        position = self.cursor
        while position < len(self.line) and not self.line[position].isalnum():
            position += 1
        while position < len(self.line) and self.line[position].isalnum():
            position += 1
        return position

    def key(self, key: str) -> EditResult:
        self.last_completion = None
        positions = self.boundaries()
        before = max((p for p in positions if p < self.cursor), default=0)
        after = next((p for p in positions if p > self.cursor), len(self.line))
        if key == "enter":
            text = self.line
            self.remember(text)
            self.line, self.cursor = "", 0
            return EditResult("submit", text)
        if key == "cancel":
            text = self.line
            self.line, self.cursor = "", 0
            self.history_index = len(self.history)
            self.draft = ("", 0)
            return EditResult("cancel", text)
        if key == "eof" and not self.line:
            return EditResult("exit")
        if key == "clear":
            return EditResult("clear")
        if key == "left":
            self.cursor = before
        elif key == "right":
            self.cursor = after
        elif key == "home":
            self.cursor = 0
        elif key == "end":
            self.cursor = len(self.line)
        elif key == "word_left":
            self.cursor = self._word_left()
        elif key == "word_right":
            self.cursor = self._word_right()
        elif key == "up":
            self._history(-1)
        elif key == "down":
            self._history(1)
        elif key == "backspace":
            self.line = self.line[:before] + self.line[self.cursor:]
            self.cursor = before
        elif key in ("delete", "eof"):
            self.line = self.line[:self.cursor] + self.line[after:]
        self.cursor = next(p for p in self.boundaries() if p >= self.cursor)
        return EditResult()

    def complete(self, nicknames: list[str]) -> EditResult:
        """Completa solo el token del cursor, conservando el resto de la línea."""
        prefix = self.line[:self.cursor]
        match = re.search(r"\S*$", prefix)
        start = match.start()
        token = prefix[start:]
        if token.startswith("@"):
            candidates = tuple("@" + n for n in nicknames)
        elif start == 0:
            candidates = COMMANDS
        elif re.fullmatch(r"man\s+", prefix[:start], re.IGNORECASE):
            candidates = ("chat",)
        elif re.fullmatch(r"/msg\s+", prefix[:start], re.IGNORECASE):
            candidates = tuple(n for n in nicknames)
        else:
            self.last_completion = None
            return EditResult("bell")
        matches = tuple(sorted((n for n in candidates
                                if normalize_nick(n).startswith(normalize_nick(token))),
                               key=str.casefold))
        if not matches:
            self.last_completion = None
            return EditResult("bell")
        replacement = matches[0] if len(matches) == 1 else commonprefix(matches)
        suffix = re.sub(r"^\S*", "", self.line[self.cursor:])
        if len(matches) == 1 and (not suffix or not suffix[0].isspace()):
            replacement += " "
        signature = (self.line, self.cursor, matches)
        if len(matches) > 1 and self.last_completion == signature:
            return EditResult("choices", choices=matches)
        if len(normalize_nick(replacement)) >= len(normalize_nick(token)):
            result = self.line[:start] + replacement + suffix
            if len(result.encode("utf-8")) > MAX_INPUT:
                return EditResult("bell")
            self.line = result
            self.cursor = start + len(replacement)
        self.last_completion = (self.line, self.cursor, matches)
        action = "redraw" if len(matches) == 1 or replacement != token else "bell"
        return EditResult(action)
