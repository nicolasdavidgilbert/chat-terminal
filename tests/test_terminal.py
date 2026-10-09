import unittest

import pyte
from wcwidth import wcswidth

from chat_terminal.editor import Editor
from chat_terminal.terminal import BLUE, Entry, GREEN, Terminal, layout


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.screen = pyte.HistoryScreen(40, 12, history=1000)
        self.stream = pyte.Stream(self.screen)
        self.terminal = Terminal(self.stream.feed, "ubuntu")
        self.terminal.width, self.terminal.height = 40, 12
        self.terminal.nick = "ana"
        self.editor = Editor()

    def test_output_preserves_draft_and_cursor(self):
        self.editor.insert("hola mundo")
        self.editor.key("word_left")
        self.terminal.redraw(self.editor)
        self.terminal.output([Entry("hola", "bea", kind="message")], self.editor)
        self.assertIn("bea@ubuntu:~$ hola", self.screen.display[0])
        self.assertIn("ana@ubuntu:~$ hola mundo", self.screen.display[1])
        self.assertEqual((self.screen.cursor.x, self.screen.cursor.y), (19, 1))
        self.assertEqual((self.editor.line, self.editor.cursor), ("hola mundo", 5))
        self.assertEqual(self.screen.buffer[1][0].fg, "green")
        self.assertEqual(self.screen.buffer[1][0].bold, True)
        self.assertEqual(self.screen.buffer[1][11].fg, "blue")

    def test_long_input_wraps_and_delete_clears_stale_rows(self):
        self.editor.insert("x" * 85)
        self.terminal.redraw(self.editor)
        self.assertEqual(self.screen.cursor.y, 2)
        self.editor.key("home")
        self.terminal.redraw(self.editor)
        self.assertEqual((self.screen.cursor.x, self.screen.cursor.y), (14, 0))
        self.editor.key("kill_end")
        self.terminal.redraw(self.editor)
        self.assertTrue(all(not row.strip() for row in self.screen.display[1:]))

    def test_unicode_spacing_and_colors(self):
        rows, position = layout([("", "a中e\u0301")], 4, cursor=4, color=False)
        self.assertEqual(rows, ["a中e\u0301", ""])
        self.assertEqual(position, (1, 0))
        rows, _ = layout([(GREEN, "a"), (BLUE, "~")], 20, color=False)
        self.assertEqual(rows, ["a~"])
        self.assertEqual(wcswidth("a中e\u0301"), 4)

    def test_clear_keeps_editing_history_and_scrollback(self):
        for index in range(30):
            self.terminal.output([Entry(f"mensaje {index}")], self.editor)
        self.editor.remember("privado de prueba")
        self.editor.insert("borrador")
        self.terminal.clear(self.editor)
        self.assertIn("borrador", self.screen.display[0])
        self.assertEqual(self.editor.history, ["privado de prueba"])
        self.assertGreater(len(self.screen.history.top), 0)

    def test_tiny_terminal_has_cursor_in_viewport(self):
        self.screen.resize(lines=3, columns=8)
        self.terminal.resize(8, 3, self.editor)
        self.editor.insert("x" * 120)
        self.editor.key("home")
        self.terminal.redraw(self.editor)
        self.assertLess(self.screen.cursor.y, 3)
        self.editor.key("end")
        self.terminal.redraw(self.editor)
        self.assertLess(self.screen.cursor.y, 3)
