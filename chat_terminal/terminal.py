"""Presentación ANSI incremental en la pantalla normal: conserva el scroll local."""

from collections.abc import Callable, Iterable
from collections import deque
from dataclasses import dataclass
import re

from wcwidth import iter_graphemes, wcswidth

from .editor import Editor, safe_text
from .names import normalize_nick

ESC = "\x1b"
RESET = f"{ESC}[0m"
BOLD = f"{ESC}[1m"
GREEN = f"{ESC}[01;32m"
BLUE = f"{ESC}[01;34m"
CYAN = f"{ESC}[01;36m"
YELLOW = f"{ESC}[01;33m"
UNDERLINE = f"{ESC}[4m"
DIM = f"{ESC}[2m"
GRAY = f"{ESC}[90m"
MENTION = f"{ESC}[38;2;127;175;175m"
MAGENTA = f"{ESC}[35m"
START = f"\r{ESC}[?2004h{ESC}[?25h"
ALT_ENTER = f"{ESC}[?1049h"
ALT_EXIT = f"{ESC}[?1049l"
RESTORE = f"{ESC}[?2026l{ALT_EXIT}{RESET}{ESC}[?2004l{ESC}[?25h"
Span = tuple[str, str]


@dataclass(frozen=True)
class Entry:
    text: str
    sender: str = ""
    recipient: str = ""
    kind: str = "system"
    names: tuple[str, ...] = ()


def prompt(nick: str, hostname: str) -> list[Span]:
    return [(GREEN, f"{nick}@{hostname}"), ("", ":"), (BLUE, "~"), ("", "$ ")]


def layout(spans: Iterable[Span], width: int, cursor: int | None = None,
           color: bool = True) -> tuple[list[str], tuple[int, int]]:
    """Envuelve por celdas y grafemas, conservando espacios y colores."""
    width = max(1, width)
    rows = [""]
    column = 0
    offset = 0
    position = (0, 0)
    for style, text in spans:
        for cluster in iter_graphemes(text):
            length = len(cluster)
            cells = max(0, wcswidth(cluster))
            if cells > width:
                cluster, cells = "?", 1
            if column + cells > width or column == width:
                rows.append("")
                column = 0
            if cursor is not None and offset == cursor:
                position = (len(rows) - 1, column)
            rows[-1] += (style + cluster + RESET) if color and style else cluster
            column += cells
            offset += length
    if cursor is not None and offset == cursor:
        if column == width:
            rows.append("")
            column = 0
        position = (len(rows) - 1, column)
    return rows, position


class Terminal:
    """Presentación y búfer efímero, usados solo por el hilo del cliente."""

    def __init__(self, send: Callable[[str], None], hostname: str, color: bool = True):
        self.send = send
        self.hostname = safe_text(hostname)[:32] or "chat"
        self.login_host = self.hostname
        self.color = color
        self.width, self.height = 80, 24
        self.nick = ""
        self.cursor_row = 0
        self.drawn = False
        self.entries: deque[Entry] = deque(maxlen=300)
        self.manual: tuple[Entry, ...] | None = None
        self.manual_offset = 0

    def _manual_screen(self) -> str:
        rows = []
        for entry in self.manual or ():
            rows.extend(layout(self.spans(entry), self.width, color=self.color)[0])
        page = self.height - 1
        self.manual_offset = max(0, min(self.manual_offset, max(0, len(rows) - page)))
        visible = rows[self.manual_offset:self.manual_offset + page]
        buf = [f"{ESC}[?25l{ESC}[2J{ESC}[H"]
        for index, row in enumerate(visible, 1):
            buf.append(f"{ESC}[{index};1H" + row + RESET)
        footer = "↑/↓ mover · RePág/AvPág página · q/Esc/Ctrl+C salir"
        # Keep the footer on a single row, including narrow terminals.
        footer_row = layout([("", footer)], self.width, color=self.color)[0][0]
        buf.append(f"{ESC}[{self.height};1H" + footer_row + RESET)
        return "".join(buf)

    def open_manual(self, entries: tuple[Entry, ...]):
        self.manual = entries
        self.manual_offset = 0
        self.send(ALT_ENTER + self._manual_screen())

    def close_manual(self):
        if self.manual is not None:
            self.manual = None
            self.manual_offset = 0
            self.send(ALT_EXIT + f"{ESC}[?25h")

    def manual_key(self, key: str):
        page = self.height - 1
        if key in ("q", "escape", "cancel"):
            self.close_manual()
            return
        movement = {"up": -1, "down": 1, "page_up": -page,
                    "page_down": page, " ": page, "enter": 1}
        if key == "home":
            self.manual_offset = 0
        elif key == "end":
            self.manual_offset = 10**9
        elif key in movement:
            self.manual_offset += movement[key]
        else:
            return
        self.send(self._manual_screen())

    def _update_main(self, operation):
        """Update normal scrollback, then repaint the separate manual atomically."""
        manual = self.manual
        original_send = self.send
        parts = []
        self.manual = None
        self.send = parts.append
        try:
            operation()
        finally:
            self.send = original_send
            self.manual = manual
        original_send(f"{ESC}[?2026h" + ALT_EXIT + "".join(parts)
                      + ALT_ENTER + self._manual_screen() + f"{ESC}[?2026l")

    def prefix(self) -> list[Span]:
        return (prompt(self.nick, self.hostname) if self.nick
                else [("", f"chat@{self.login_host}'s apodo: ")])

    def begin_login(self):
        entry = Entry("Permiso aprobado. Introduzca apodo.")
        self.entries.append(entry)
        self.send(START + entry.text + "\r\n")

    def _erase(self) -> str:
        if not self.drawn:
            return ""
        up = f"{ESC}[{self.cursor_row}A" if self.cursor_row else ""
        self.drawn = False
        self.cursor_row = 0
        return up + "\r" + f"{ESC}[J"

    def _input(self, editor: Editor) -> str:
        prefix = self.prefix()
        offset = sum(len(text) for _, text in prefix) + editor.cursor
        rows, (row, column) = layout(prefix + [("", editor.line)], self.width,
                                     offset, self.color)
        # A very long input on a tiny terminal gets a viewport around the cursor.
        available = max(1, self.height - 1)
        start = max(0, min(row, len(rows) - available))
        rows = rows[start:start + available]
        row -= start
        up = len(rows) - 1 - row
        self.cursor_row = row
        self.drawn = True
        return ("\r\n".join(rows) + RESET
                + (f"{ESC}[{up}A" if up else "")
                + "\r" + (f"{ESC}[{column}C" if column else ""))

    def redraw(self, editor: Editor):
        if self.manual is not None:
            return
        self.send(f"{ESC}[?25l" + self._erase() + self._input(editor) + f"{ESC}[?25h")

    def message_spans(self, text: str) -> list[Span]:
        spans = []
        end = 0
        for match in re.finditer(r"(?<![\w.@\-\u0300-\u036f])@([\w.\-\u0300-\u036f]+)", text):
            if not self.nick:
                continue
            nickname = match[1]
            # Prefer the full nickname; trailing dots may be sentence punctuation.
            while nickname.endswith(".") and normalize_nick(nickname) != normalize_nick(self.nick):
                nickname = nickname[:-1]
            if normalize_nick(nickname) != normalize_nick(self.nick):
                continue
            end_of_mention = match.start() + 1 + len(nickname)
            spans.extend((("", text[end:match.start()]),
                          (MENTION, text[match.start():end_of_mention])))
            end = end_of_mention
        spans.append(("", text[end:]))
        return spans

    def spans(self, entry: Entry) -> list[Span]:
        if entry.kind == "users":
            spans = []
            for index, nick in enumerate(entry.names):
                if index:
                    spans.append(("", "  "))
                style = CYAN if normalize_nick(nick) == normalize_nick(self.nick) else BLUE
                spans.append((style, nick))
            return spans
        if entry.kind == "heading":
            return [(YELLOW, entry.text)]
        if entry.kind == "manual":
            match = re.fullmatch(r"(\s+)(.*?)(\s{2,})(.*)", entry.text)
            if not match:
                return [(BOLD, entry.text)]
            indent, command, gap, description = match.groups()
            spans = [("", indent)]
            for part in re.split(r"(<[^>]+>)", command):
                style = CYAN + UNDERLINE if part.startswith("<") else BOLD
                spans.append((style, part))
            spans.extend((("", gap), ("", description)))
            return spans
        if entry.kind == "message":
            return prompt(entry.sender, self.hostname) + self.message_spans(entry.text)
        if entry.kind == "private":
            return [(MAGENTA, "[privado] "), (GREEN, entry.sender),
                    ("", " -> "), (GREEN, entry.recipient), ("", ": ")] + self.message_spans(entry.text)
        if entry.kind == "join":
            return [(GRAY, entry.text)]
        return [(DIM if entry.kind == "notice" else "", entry.text)]

    def output(self, entries: Iterable[Entry], editor: Editor):
        if self.manual is not None:
            self._update_main(lambda: self.output(entries, editor))
            return
        buf = [f"{ESC}[?25l", self._erase()]
        for entry in entries:
            self.entries.append(entry)
            rows, _ = layout(self.spans(entry), self.width, color=self.color)
            buf.append("\r\n".join(rows) + RESET + "\r\n")
        buf.extend((self._input(editor), f"{ESC}[?25h"))
        self.send("".join(buf))

    def clear(self, editor: Editor):
        self.entries.clear()
        self.drawn = False
        self.cursor_row = 0
        # Do not erase the terminal's scrollback with CSI 3 J.
        self.send(f"{ESC}[2J{ESC}[H" + self._input(editor))

    def resize(self, width: int, height: int, editor: Editor):
        if self.manual is not None:
            self._update_main(lambda: self.resize(width, height, editor))
            return
        width, height = max(1, min(width, 1000)), max(2, min(height, 500))
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        if not self.nick:
            # The SSH command and password prompt belong to the client. Keep
            # them visible while choosing a nickname, including initial NAWS.
            self.redraw(editor)
            return
        prefix = self.prefix()
        offset = sum(len(text) for _, text in prefix) + editor.cursor
        rows, (row, column) = layout(prefix + [("", editor.line)], width,
                                     offset, self.color)
        available = height - 1
        start = max(0, min(row, len(rows) - available))
        input_rows = rows[start:start + available]
        history_rows = []
        for entry in self.entries:
            history_rows.extend(layout(self.spans(entry), width, color=self.color)[0])
        room = height - len(input_rows)
        history_rows = history_rows[-room:]
        visible = history_rows + input_rows
        # Absolute positions avoid scrolling at the bottom or trusting the
        # emulator's old cursor position after it reflows the previous screen.
        buf = [f"{ESC}[?25l{ESC}[2J"]
        for index, text in enumerate(visible, 1):
            buf.append(f"{ESC}[{index};1H" + text + RESET)
        self.cursor_row = row - start
        self.drawn = True
        buf.append(f"{ESC}[{len(history_rows) + self.cursor_row + 1};{column + 1}H"
                   + f"{ESC}[?25h")
        self.send("".join(buf))

    def dispose(self):
        self.manual = None
        self.manual_offset = 0
        self.entries.clear()
        self.nick = ""
        self.login_host = self.hostname
        self.drawn = False
        self.cursor_row = 0

    def choices(self, choices: tuple[str, ...], editor: Editor):
        cell_width = max(wcswidth(choice) for choice in choices) + 2
        columns = max(1, self.width // cell_width)
        count = (len(choices) + columns - 1) // columns
        entries = []
        for row in range(count):
            cells = []
            for column in range(columns):
                index = column * count + row
                if index < len(choices):
                    value = choices[index]
                    cells.append(value + " " * (cell_width - wcswidth(value)))
            entries.append(Entry("".join(cells).rstrip()))
        # Like Bash, list below the current input, then repeat the prompt.
        self.output([Entry(editor.line, self.nick, kind="message"), *entries], editor)

    def finish(self, editor: Editor, text: str = "logout"):
        self.close_manual()
        self.send(self._erase() + RESTORE + text + "\r\n")
