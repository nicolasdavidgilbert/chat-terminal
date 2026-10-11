"""Telnet y teclas incrementales; independiente del chat y de su presentación."""

import codecs
import ipaddress
from dataclasses import dataclass

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
ECHO, SGA, NAWS, LINEMODE = 1, 3, 31, 34
# Private bridge metadata: the server address of this particular SSH session.
SSH_ADDRESS = 200
NEGOTIATION = bytes((IAC, WILL, ECHO, IAC, WILL, SGA,
                     IAC, DO, NAWS, IAC, DONT, LINEMODE))
MAX_INPUT = 400


@dataclass(frozen=True)
class Event:
    kind: str
    value: str | bytes | tuple[int, int] = ""
    truncated: bool = False


KEYS = {
    1: "home", 2: "left", 3: "cancel", 4: "eof", 5: "end",
    6: "right", 8: "backspace", 9: "tab", 12: "clear",
    14: "down", 16: "up", 127: "backspace",
}
SEQUENCES = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left",
    "[H": "home", "[F": "end", "OH": "home", "OF": "end",
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",
    "[1~": "home", "[7~": "home", "[4~": "end", "[8~": "end",
    "[3~": "delete", "[1;5C": "word_right", "[1;5D": "word_left",
}


class Protocol:
    """Conserva secuencias partidas entre recv; los eventos respetan su orden."""

    def __init__(self):
        self.state = "data"
        self.telnet_state = "data"
        self.option_command = 0
        self.sub = bytearray()
        self.sequence = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.last_cr = False
        self.pasting = False
        self.paste = bytearray()
        self.paste_truncated = False

    def reset(self):
        """Libera también datos de un pegado interrumpido al cerrar la sesión."""
        self.__init__()

    def feed(self, data: bytes) -> list[Event]:
        events = []
        for byte in data:
            state = self.telnet_state
            if state == "data":
                if byte == IAC:
                    self.telnet_state = "iac"
                else:
                    self._key(byte, events)
            elif state == "iac":
                if byte in (DO, DONT, WILL, WONT):
                    self.option_command = byte
                    self.telnet_state = "option"
                elif byte == SB:
                    self.sub.clear()
                    self.telnet_state = "sub"
                else:
                    self.telnet_state = "data"
                    if byte == IAC:
                        self._key(byte, events)
            elif state == "option":
                if self.option_command == DO and byte not in (ECHO, SGA):
                    events.append(Event("reply", bytes((IAC, WONT, byte))))
                elif self.option_command == WILL and byte != NAWS:
                    events.append(Event("reply", bytes((IAC, DONT, byte))))
                self.telnet_state = "data"
            elif state == "sub":
                if byte == IAC:
                    self.telnet_state = "sub_iac"
                elif len(self.sub) < 64:
                    self.sub.append(byte)
            elif state == "sub_iac":
                if byte == SE:
                    if len(self.sub) == 5 and self.sub[0] == NAWS:
                        width = int.from_bytes(self.sub[1:3], "big")
                        height = int.from_bytes(self.sub[3:5], "big")
                        if width and height:
                            events.append(Event("resize", (width, height)))
                    elif self.sub and self.sub[0] == SSH_ADDRESS:
                        try:
                            address = str(ipaddress.ip_address(bytes(self.sub[1:]).decode("ascii")))
                        except (ValueError, UnicodeDecodeError):
                            pass
                        else:
                            events.append(Event("ssh_address", address))
                    self.sub.clear()
                    self.telnet_state = "data"
                else:
                    if byte == IAC and len(self.sub) < 64:
                        self.sub.append(byte)
                    self.telnet_state = "sub"
        return events

    def _key(self, byte: int, events: list[Event]):
        if self.state == "osc":
            if byte == 7:
                self.state = "data"
            elif byte == 27:
                self.state = "osc_escape"
            return
        if self.state == "osc_escape":
            self.state = "data" if byte == ord("\\") else "osc"
            return
        if self.state == "escape":
            self.sequence += chr(byte)
            if len(self.sequence) > 64:
                self.state = "data"
                self.sequence = ""
                return
            # CSI and SS3 terminate at their final byte, not at '[' or 'O'.
            if self.sequence == "]":
                self.state = "osc"
                self.sequence = ""
                return
            if self.sequence in ("[", "O"):
                return
            if self.sequence.startswith(("[", "O")) and not 64 <= byte <= 126:
                return
            sequence = self.sequence
            self.sequence = ""
            self.state = "data"
            if sequence == "[200~":
                self.pasting = True
                self.paste.clear()
                self.paste_truncated = False
            elif sequence == "[201~" and self.pasting:
                text = self.paste.decode("utf-8", "ignore" if self.paste_truncated else "replace")
                text = "".join(
                    " " if ch in "\r\n\t" else ch
                    for ch in text
                    if ch.isprintable() or ch in "\r\n\t\u200d"
                )
                events.append(Event("paste", text, self.paste_truncated))
                self.pasting = False
                self.paste.clear()
                self.paste_truncated = False
            elif self.pasting:
                # Control sequences inside pasted text never become key events.
                pass
            elif sequence in SEQUENCES:
                events.append(Event("key", SEQUENCES[sequence]))
            return
        if byte == 27:
            self.last_cr = False
            self.state = "escape"
            self.sequence = ""
            self.decoder.reset()
            return
        if self.pasting:
            if len(self.paste) < MAX_INPUT:
                self.paste.append(byte)
            else:
                self.paste_truncated = True
            return
        if byte in (13, 10):
            if byte == 13 or not self.last_cr:
                events.append(Event("key", "enter"))
            self.last_cr = byte == 13
            self.decoder.reset()
        elif byte == 0:
            pass  # NVT CR NUL keeps last_cr, so CR NUL LF is a single Enter.
        elif byte in KEYS:
            self.last_cr = False
            self.decoder.reset()
            events.append(Event("key", KEYS[byte]))
        elif byte >= 32:
            self.last_cr = False
            text = self.decoder.decode(bytes((byte,)))
            if text:
                events.append(Event("text", text))


class TelnetFilter:
    """Quita la negociación de la salida del servidor, incluso si está partida."""

    def __init__(self):
        self.state = "data"

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            if self.state == "data":
                if byte == IAC:
                    self.state = "iac"
                else:
                    out.append(byte)
            elif self.state == "iac":
                if byte == IAC:
                    out.append(byte)
                    self.state = "data"
                elif byte in (DO, DONT, WILL, WONT):
                    self.state = "option"
                elif byte == SB:
                    self.state = "sub"
                else:
                    self.state = "data"
            elif self.state == "option":
                self.state = "data"
            elif self.state == "sub":
                if byte == IAC:
                    self.state = "sub_iac"
            elif self.state == "sub_iac":
                self.state = "data" if byte == SE else "sub"
        return bytes(out)
