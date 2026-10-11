"""Two-screen emulator: pyte itself does not implement DEC mode 1049."""

import pyte


class AlternateScreen:
    def __init__(self, columns, lines, history=1000):
        self.main = pyte.HistoryScreen(columns, lines, history=history)
        self.alternate = pyte.HistoryScreen(columns, lines, history=history)
        self.screen = self.main

    def __getattr__(self, name):
        value = getattr(self.screen, name)
        if callable(value):
            return lambda *args, **kwargs: getattr(self.screen, name)(*args, **kwargs)
        return value

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private") and 1049 in modes:
            self.alternate.reset()
            self.screen = self.alternate
        self.screen.set_mode(*(mode for mode in modes if mode != 1049), **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private") and 1049 in modes:
            self.screen = self.main
        self.screen.reset_mode(*(mode for mode in modes if mode != 1049), **kwargs)

    def resize(self, lines=None, columns=None):
        self.main.resize(lines=lines, columns=columns)
        self.alternate.resize(lines=lines, columns=columns)
