import unittest

import pyte
from wcwidth import wcswidth

from chat_terminal.editor import Editor
from chat_terminal.terminal import BLUE, Entry, GREEN, Terminal, layout
from terminal_emulator import AlternateScreen


class TerminalTests(unittest.TestCase):
    def test_manual_position_tracks_navigation_resize_and_narrow_footer(self):
        screen = AlternateScreen(100, 10)
        terminal = Terminal(pyte.Stream(screen).feed, "ubuntu")
        terminal.width, terminal.height = 100, 10
        editor = Editor()
        terminal.open_manual(tuple(Entry(f"linea {i}") for i in range(30)))
        self.assertIn("líneas 1–9 de 30", screen.display[-1])
        terminal.manual_key("end")
        self.assertIn("líneas 22–30 de 30", screen.display[-1])
        screen.resize(lines=5, columns=30)
        terminal.resize(30, 5, editor)
        self.assertIn("22–25/30 · q salir", screen.display[-1])
        screen.resize(lines=2, columns=3)
        terminal.resize(3, 2, editor)
        self.assertEqual(screen.display[-1], "q s")
        self.assertLess(screen.cursor.y, 2)

    def test_manual_screen_restores_chat_and_preserves_scrollback(self):
        screen = AlternateScreen(40, 12)
        terminal = Terminal(pyte.Stream(screen).feed, "ubuntu")
        terminal.width, terminal.height = 40, 12
        terminal.nick = "ana"
        editor = Editor()
        editor.insert("borrador")
        terminal.output([Entry("mensaje anterior")], editor)
        manual = tuple(Entry(f"ayuda linea {i}") for i in range(50))
        terminal.open_manual(manual)
        self.assertIn("ayuda linea 0", "\n".join(screen.display))
        terminal.manual_key("end")
        self.assertIn("ayuda linea 49", "\n".join(screen.display))
        terminal.manual_key("page_up")
        self.assertLess(terminal.manual_offset, 39)
        terminal.manual_key("home")
        self.assertEqual(terminal.manual_offset, 0)
        for i in range(30):
            terminal.output([Entry(f"entrante {i}")], editor)
        self.assertIs(screen.screen, screen.alternate)
        self.assertIn("ayuda linea 0", "\n".join(screen.display))
        terminal.close_manual()
        self.assertIs(screen.screen, screen.main)
        self.assertIn("entrante 29", "\n".join(screen.display))
        self.assertIn("borrador", "\n".join(screen.display))
        history = "".join(cell.data for row in screen.history.top for cell in row.values())
        self.assertIn("mensaje anterior", history)
        self.assertNotIn("ayuda linea", history + "\n".join(screen.display))
        self.assertFalse(any(e in manual for e in terminal.entries))
        self.assertEqual(editor.line, "borrador")
        terminal.open_manual(manual)
        self.assertEqual(terminal.manual_offset, 0)

    def test_manual_resize_tiny_terminal_and_no_color(self):
        sent = []
        terminal = Terminal(sent.append, "ubuntu", color=False)
        terminal.nick = "ana"
        editor = Editor()
        terminal.open_manual(tuple(Entry("contenido " * 10, kind="heading") for _ in range(5)))
        terminal.manual_key("end")
        for width, height in ((8, 3), (1, 2), (80, 24)):
            terminal.resize(width, height, editor)
            self.assertIsNotNone(terminal.manual)
            self.assertNotIn("\x1b[01;33m", sent[-1])
            self.assertIn(f"\x1b[{height};1H", sent[-1])
        terminal.manual_key("cancel")
        self.assertIsNone(terminal.manual)

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
        self.editor.key("cancel")
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

    def test_resize_rebuilds_messages_and_preserves_unicode_cursor(self):
        self.terminal.output([Entry("mensaje antiguo"), Entry("hola 中", "bea", kind="message")], self.editor)
        self.editor.insert("á中e\u0301" + "x" * 40)
        self.editor.key("home")
        self.editor.key("right")
        expected = (self.editor.line, self.editor.cursor)
        for width, height in ((20, 10), (60, 15), (8, 3), (40, 12)):
            self.screen.resize(lines=height, columns=width)
            self.terminal.resize(width, height, self.editor)
            self.assertEqual((self.editor.line, self.editor.cursor), expected)
            self.assertLess(self.screen.cursor.y, height)
            self.assertLess(self.screen.cursor.x, width)
        self.assertIn("mensaje antiguo", "\n".join(self.screen.display))
        self.assertIn("bea@ubuntu:~$ hola 中", "\n".join(self.screen.display))
        self.assertEqual((self.screen.cursor.x, self.screen.cursor.y), (15, 2))
        self.terminal.output([Entry("entrante tras resize")], self.editor)
        self.assertIn("entrante tras resize", "\n".join(self.screen.display))
        self.assertEqual((self.screen.cursor.x, self.screen.cursor.y), (15, 3))
        self.assertEqual((self.editor.line, self.editor.cursor), expected)

    def test_resize_does_not_scroll_and_clear_does_not_resurrect_messages(self):
        for index in range(30):
            self.terminal.output([Entry(f"mensaje {index}")], self.editor)
        old_history = list(self.screen.history.top)
        self.screen.resize(lines=10, columns=30)
        self.terminal.resize(30, 10, self.editor)
        self.assertEqual(list(self.screen.history.top), old_history)
        snapshot = self.screen.display[:]
        self.terminal.resize(30, 10, self.editor)
        self.assertEqual(self.screen.display, snapshot)
        self.editor.insert("borrador")
        self.terminal.clear(self.editor)
        self.screen.resize(lines=12, columns=40)
        self.terminal.resize(40, 12, self.editor)
        self.assertNotIn("mensaje", "\n".join(self.screen.display))
        self.assertIn("borrador", "\n".join(self.screen.display))

    def test_presentation_buffer_is_bounded_and_disposed(self):
        self.terminal.output((Entry(str(index)) for index in range(305)), self.editor)
        self.assertEqual(len(self.terminal.entries), 300)
        self.assertEqual(self.terminal.entries[0].text, "5")
        self.terminal.dispose()
        self.assertFalse(self.terminal.entries)

    def test_nickname_prompt_keeps_previous_ssh_output(self):
        self.terminal.nick = ""
        self.stream.feed("$ ssh chat@ubuntu\r\nchat@ubuntu's password: \r\n")
        self.terminal.redraw(self.editor)
        self.screen.resize(lines=16, columns=60)
        self.terminal.resize(60, 16, self.editor)
        self.assertEqual(self.screen.display[0].strip(), "$ ssh chat@ubuntu")
        self.assertEqual(self.screen.display[1].strip(), "chat@ubuntu's password:")
        self.assertEqual(self.screen.display[2].strip(), "chat@ubuntu's apodo:")

    def test_user_listing_colors_are_relative_to_viewer(self):
        entry = Entry("", kind="users", names=("ana", "bea"))
        self.terminal.output([entry], self.editor)
        self.assertEqual(self.screen.display[0].strip(), "ana  bea")
        self.assertEqual(self.screen.buffer[0][0].fg, "cyan")
        self.assertEqual(self.screen.buffer[0][5].fg, "blue")
        self.assertTrue(self.screen.buffer[0][0].bold)
        self.terminal.nick = "bea"
        self.terminal.output([entry], self.editor)
        self.assertEqual(self.screen.buffer[1][0].fg, "blue")
        self.assertEqual(self.screen.buffer[1][5].fg, "cyan")

    def test_user_listing_without_color(self):
        self.terminal.color = False
        self.terminal.output([Entry("", kind="users", names=("ana", "bea"))], self.editor)
        self.assertEqual(self.screen.display[0].strip(), "ana  bea")
        self.assertEqual(self.screen.buffer[0][0].fg, "default")
        self.assertEqual(self.screen.buffer[0][5].fg, "default")

    def test_own_mentions_are_soft_cyan_in_public_and_private_messages(self):
        for kind in ("message", "private"):
            with self.subTest(kind=kind):
                self.screen.reset()
                self.terminal.drawn = False
                self.terminal.output([
                    Entry("hola @ANA, @bea", "bea", "ana", kind)
                ], self.editor)
                row = self.screen.display[0]
                start = row.index("@ANA")
                for index in range(start, start + 4):
                    cell = self.screen.buffer[0][index]
                    self.assertEqual(cell.fg, "7fafaf")
                    self.assertFalse(cell.bold)
                self.assertEqual(self.screen.buffer[0][start + 4].fg, "default")
                self.assertEqual(self.screen.buffer[0][row.index("@bea")].fg, "default")

    def test_mentions_require_whole_nickname_and_respect_no_color(self):
        text = "@anabel @ana-extra @ana.foo correo@ana @ana @ANA"
        spans = self.terminal.message_spans(text)
        self.assertEqual([part for style, part in spans if style], ["@ana", "@ANA"])
        self.assertEqual("".join(part for _, part in spans), text)
        rows, _ = layout(spans, 80, color=False)
        self.assertEqual(rows, [text])

    def test_mentions_with_punctuation_keep_punctuation_uncolored(self):
        for suffix in (".", "...", ",", ";", ":", "!", "?", "…", ")", "]", "»"):
            with self.subTest(suffix=suffix):
                text = "hola (@ANA" + suffix
                spans = self.terminal.message_spans(text)
                self.assertEqual([part for style, part in spans if style], ["@ANA"])
                self.assertEqual(spans[-1], ("", suffix))
                self.assertEqual("".join(part for _, part in spans), text)

    def test_mentions_preserve_dots_inside_and_at_end_of_nickname(self):
        for nickname in ("ana.foo", "ana.", "ana.."):
            with self.subTest(nickname=nickname):
                self.terminal.nick = nickname
                for suffix in ("", ".", "..."):
                    spans = self.terminal.message_spans("@" + nickname + suffix)
                    self.assertEqual([part for style, part in spans if style], ["@" + nickname])
                    self.assertEqual(spans[-1], ("", suffix))

    def test_mentions_normalize_accents_without_changing_message_text(self):
        self.terminal.nick = "josé"
        text = "@JOSÉ, @jose\u0301. @josefina @jose"
        spans = self.terminal.message_spans(text)
        self.assertEqual([part for style, part in spans if style], ["@JOSÉ", "@jose\u0301"])
        self.assertEqual("".join(part for _, part in spans), text)

    def test_mentions_distinguish_enye_from_n(self):
        self.terminal.nick = "peña"
        spans = self.terminal.message_spans("@PEÑA @pen\u0303a @pena")
        self.assertEqual([part for style, part in spans if style], ["@PEÑA", "@pen\u0303a"])

    def test_manual_styles_and_plain_text_mode(self):
        self.terminal.output([
            Entry("COMANDOS", kind="heading"),
            Entry("  /msg <apodo> <texto>   Enviar un privado", kind="manual"),
        ], self.editor)
        # pyte calls ANSI 33 "brown"; bold produces intense yellow.
        self.assertEqual(self.screen.buffer[0][0].fg, "brown")
        self.assertTrue(self.screen.buffer[0][0].bold)
        self.assertTrue(self.screen.buffer[1][2].bold)
        self.assertEqual(self.screen.buffer[1][7].fg, "cyan")
        self.assertTrue(self.screen.buffer[1][7].underscore)
        self.terminal.color = False
        rows, _ = layout(self.terminal.spans(
            Entry("  /msg <apodo> <texto>   Enviar un privado", kind="manual")
        ), 80, color=False)
        self.assertEqual(rows, ["  /msg <apodo> <texto>   Enviar un privado"])
