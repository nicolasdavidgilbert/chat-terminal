import unittest

from chat_terminal.editor import Editor, HISTORY_SIZE
from chat_terminal.protocol import MAX_INPUT


class EditorTests(unittest.TestCase):
    def test_insert_delete_and_cursor(self):
        editor = Editor()
        editor.insert("hola mundo")
        editor.key("word_left")
        editor.insert("gran ")
        self.assertEqual(editor.line, "hola gran mundo")
        editor.key("eof")
        self.assertEqual(editor.line, "hola gran undo")
        editor.key("home")
        editor.key("right")
        editor.key("backspace")
        self.assertEqual(editor.line, "ola gran undo")
        self.assertEqual(editor.cursor, 0)

    def test_history_restores_draft_and_cursor(self):
        editor = Editor()
        for text in ("primero", "segundo"):
            editor.insert(text)
            self.assertEqual(editor.key("enter").text, text)
        editor.insert("borrador")
        editor.key("left")
        editor.key("up")
        self.assertEqual(editor.line, "segundo")
        editor.key("up")
        self.assertEqual(editor.line, "primero")
        editor.key("down")
        editor.key("down")
        self.assertEqual((editor.line, editor.cursor), ("borrador", 7))

    def test_cancel_does_not_save_input(self):
        editor = Editor()
        editor.insert("hola mundo")
        self.assertEqual(editor.key("cancel").text, "hola mundo")
        self.assertEqual(editor.history, [])
        self.assertEqual(editor.line, "")
        self.assertEqual(editor.key("eof").action, "exit")

    def test_unicode_clusters_and_byte_limit(self):
        editor = Editor()
        editor.insert("e\u0301👨‍👩‍👧")
        editor.key("backspace")
        self.assertEqual(editor.line, "e\u0301")
        editor.key("left")
        self.assertEqual(editor.cursor, 0)
        editor.key("delete")
        self.assertEqual(editor.line, "")
        editor.insert("á" * MAX_INPUT)
        self.assertEqual(len(editor.line.encode()), MAX_INPUT)
        self.assertFalse(editor.insert("x"))
        self.assertNotIn("�", editor.line)

    def test_completion_commands_nicks_and_mentions(self):
        editor = Editor()
        editor.insert("/m")
        editor.complete(["ana", "antonio", "bea"])
        self.assertEqual(editor.line, "/msg ")
        editor.insert("an")
        first = editor.complete(["ana", "antonio", "bea"])
        self.assertEqual(first.action, "bell")
        second = editor.complete(["ana", "antonio", "bea"])
        self.assertEqual(second.choices, ("ana", "antonio"))
        editor.key("backspace")
        editor.insert("na")
        editor.complete(["ana", "antonio", "bea"])
        self.assertEqual(editor.line, "/msg ana ")
        editor.key("cancel")
        editor.insert("@be")
        editor.complete(["bea"])
        self.assertEqual(editor.line, "@bea ")
        editor.insert("hola @an mundo")
        editor.cursor = editor.line.index(" mundo")
        editor.complete(["ana"])
        self.assertEqual(editor.line, "@bea hola @ana mundo")

    def test_history_is_bounded_and_disposable(self):
        editor = Editor()
        for index in range(HISTORY_SIZE + 10):
            editor.remember(str(index))
        self.assertEqual(len(editor.history), HISTORY_SIZE)
        editor.insert("borrador")
        editor.complete([])
        editor.dispose()
        self.assertEqual(editor.history, [])
        self.assertEqual((editor.line, editor.draft), ("", ("", 0)))

    def test_control_characters_are_filtered(self):
        editor = Editor()
        editor.insert("hola\x1b\x00\u202emundo")
        self.assertEqual(editor.line, "holamundo")

    def test_completion_replaces_whole_word_at_cursor(self):
        editor = Editor()
        editor.insert("/msg ana hola")
        editor.cursor = 7
        editor.complete(["ana"])
        self.assertEqual(editor.line, "/msg ana hola")
        self.assertEqual(editor.cursor, 8)
