import select
import socket
import threading
import time
import unittest

import pyte
from terminal_emulator import AlternateScreen

from chat_terminal.protocol import TelnetFilter
from chat_terminal.protocol import Event
from chat_terminal.server import ChatServer, Room, Session


class Client:
    def __init__(self, port):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.screen = AlternateScreen(80, 24, history=1000)
        self.stream = pyte.ByteStream(self.screen)
        self.filter = TelnetFilter()
        self.data = bytearray()
        self.until(" apodo:")

    def display_text(self):
        history = ["".join(row[index].data if index in row else " "
                           for index in range(self.screen.columns))
                   for row in self.screen.history.top]
        return "\n".join(history + self.screen.display)

    def send(self, text):
        self.socket.sendall(text.encode())

    def read(self, timeout=0.15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.socket], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            data = self.socket.recv(65536)
            if not data:
                break
            clean = self.filter.feed(data)
            self.data.extend(clean)
            self.stream.feed(clean)

    def until(self, text):
        deadline = time.monotonic() + 3
        while text not in "\n".join(self.screen.display):
            if time.monotonic() >= deadline:
                raise AssertionError(f"No llegó {text!r}: {self.screen.display}")
            self.read(0.05)

    def close(self):
        self.socket.close()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ChatServer(0, "ubuntu")
        self.thread = threading.Thread(target=self.server.serve)
        self.thread.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.stop()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.server.room.names(), [])

    def client(self, nick):
        client = Client(self.server.port)
        self.clients.append(client)
        client.send(nick + "\r")
        client.until("Conectado como " + nick.lower())
        return client

    def test_public_private_completion_and_presence(self):
        ana = self.client("ana")
        bea = self.client("bea")
        carlos = self.client("carlos")
        ana.until("bea se ha unido.")
        ana.send("hola  mundo\r")
        bea.until("ana@ubuntu:~$ hola  mundo")
        ana.send("/msg be\tsecreto de prueba\r")
        bea.until("[privado] ana -> bea: secreto de prueba")
        carlos.read()
        self.assertNotIn(b"secreto de prueba", carlos.data)
        ana.send("hola @ca\t\r")
        bea.until("hola @carlos")
        carlos.close()
        ana.until("carlos ha salido.")

    def test_input_events_in_same_packet_and_unknown_msg(self):
        ana = self.client("ana")
        bea = self.client("bea")
        ana.send("uno\r\ndos\r\n/msgs bea NO_PRIVADO\r")
        bea.until("ana@ubuntu:~$ dos")
        ana.until("/msgs: comando desconocido")
        bea.read()
        self.assertNotIn(b"NO_PRIVADO", bea.data)

    def test_history_and_dispose_after_disconnect(self):
        ana = self.client("ana")
        ana.send("entrada vieja\r")
        ana.until("ana@ubuntu:~$ entrada vieja")
        with self.server.room.lock:
            old = self.server.room.users["ana"]
        ana.send("exit\r")
        deadline = time.monotonic() + 2
        while not old.stopping.wait(0.01):
            self.assertLess(time.monotonic(), deadline)
        deadline = time.monotonic() + 2
        while old.editor.history:
            self.assertLess(time.monotonic(), deadline)
            threading.Event().wait(0.01)
        self.assertEqual(old.editor.line, "")
        self.assertTrue(old.pending.empty())
        self.assertFalse(old.terminal.entries)
        fresh = self.client("ana")
        fresh.send("\x1b[A")
        fresh.read()
        self.assertNotIn(b"entrada vieja", fresh.data)
        fresh.send("history\r")
        fresh.until("1  history")

    def test_message_during_edit_and_paste(self):
        ana = self.client("ana")
        bea = self.client("bea")
        ana.send("borrador\x1b[D\x1b[D")
        ana.until("borrador")
        bea.send("mensaje entrante\r")
        ana.until("mensaje entrante")
        ana.send("X\r")
        bea.until("borradXor")
        ana.send("\x1b[200~uno\nexit\x1b[201~")
        ana.until("uno exit")
        bea.read()
        self.assertNotIn(b"uno exit", bea.data)
        ana.send("\r")
        bea.until("uno exit")

    def test_full_history_does_not_overflow_output_queue(self):
        ana = self.client("ana")
        with self.server.room.lock:
            session = self.server.room.users["ana"]
        ana.send("".join(f"entrada-{index}\r" for index in range(300)) + "history\r")
        ana.until("300  history")
        self.assertFalse(session.stopping.is_set())

    def test_paste_truncation_notice_is_private_and_does_not_submit(self):
        ana = self.client("ana")
        bea = self.client("bea")
        for draft, paste in (("", "x" * 450), ("d" * 390, "x" * 20),
                             ("d" * 400, "x")):
            with self.subTest(draft_length=len(draft)):
                ana.send("\x03" + draft + "\x1b[200~" + paste + "\x1b[201~")
                deadline = time.monotonic() + 2
                expected_count = ana.data.count(b"El texto pegado") + 1
                while ana.data.count(b"El texto pegado") < expected_count:
                    self.assertLess(time.monotonic(), deadline)
                    ana.read(0.05)
                bea.read(0.05)
                self.assertNotIn(b"El texto pegado", bea.data)
                self.assertNotIn(b"Has alcanzado", ana.data)
                self.assertNotIn(b"x" * 20, bea.data)
                with self.server.room.lock:
                    session = self.server.room.users["ana"]
                    self.assertEqual(len(session.editor.line.encode()), 400)
                    self.assertEqual(session.editor.history, [])

    def test_typing_limit_notice_is_private_once_until_deleting_or_cancelling(self):
        ana = self.client("ana")
        bea = self.client("bea")
        warning = "Has alcanzado el límite"
        ana.send("á" * 200 + "xxxxx")
        ana.until(warning)
        ana.read(0.1)
        self.assertEqual(ana.data.count(warning.encode()), 1)
        bea.read(0.1)
        self.assertNotIn(warning.encode(), bea.data)
        with self.server.room.lock:
            self.assertEqual(self.server.room.users["ana"].editor.line, "á" * 200)
        ana.send("\x7fáx")
        ana.read(0.2)
        self.assertEqual(ana.data.count(warning.encode()), 2)
        ana.send("\x03" + "x" * 401)
        ana.read(0.2)
        self.assertEqual(ana.data.count(warning.encode()), 3)
        ana.send("\r" + "x" * 401)
        ana.read(0.2)
        self.assertEqual(ana.data.count(warning.encode()), 4)

    def test_voluntary_exit_has_one_message_and_complete_marker(self):
        for key in ("exit\r", "\x04"):
            client = self.client("ana")
            client.send(key)
            client.until("Has salido del chat.")
            client.read(0.1)
            self.assertEqual(client.data.count(b"Has salido del chat."), 1)
            self.assertTrue(client.filter.close_complete)

    def test_shutdown_informs_client_and_marks_completed_close(self):
        ana = self.client("ana")
        self.server.stop()
        ana.until("El servidor ha cerrado el chat.")
        ana.read(0.1)
        self.assertTrue(ana.filter.close_complete)

    def test_cancelled_login_has_its_own_close_message(self):
        client = Client(self.server.port)
        self.clients.append(client)
        client.send("\x03")
        client.until("Conexión cancelada.")
        client.read(0.1)
        self.assertTrue(client.filter.close_complete)

    def test_saturated_session_sends_best_effort_reason(self):
        ana = self.client("ana")
        with self.server.room.lock:
            session = self.server.room.users["ana"]
        session.stop("slow_client")
        ana.until("no se pudo entregar la salida a tiempo.")
        ana.read(0.1)
        self.assertTrue(ana.filter.close_complete)

    def test_slow_client_queue_is_bounded_and_cleared(self):
        local, remote = socket.socketpair()
        session = Session(local, Room(), "ubuntu")
        try:
            from chat_terminal.terminal import Entry
            for index in range(301):
                session.enqueue(Entry(str(index)))
            self.assertTrue(session.stopping.is_set())
            self.assertLessEqual(session.pending.qsize(), 300)
            session.run()
            self.assertTrue(session.pending.empty())
        finally:
            local.close()
            remote.close()

    def test_resize_before_nick_does_not_clear_screen(self):
        local, remote = socket.socketpair()
        session = Session(local, Room(), "ubuntu")
        try:
            sent = []
            session.terminal.send = sent.append
            session.terminal.redraw(session.editor)
            session.event(Event("resize", (40, 12)))
            session.event(Event("resize", (60, 16)))
            self.assertFalse(session.terminal.entries)
            self.assertNotIn("\x1b[2J", "".join(sent))
            self.assertNotIn("\x1b[H", "".join(sent))
        finally:
            local.close()
            remote.close()

    def test_nick_attempts_remain_visible_and_welcome_follows_acceptance(self):
        client = Client(self.server.port)
        self.clients.append(client)
        self.assertNotIn(b"Welcome to ", client.data)
        self.assertNotIn(b"Si te pilla el profe", client.data)
        client.send("mal apodo\r")
        client.until("Apodo inválido")
        visible = "\n".join(client.screen.display)
        self.assertIn("chat@ubuntu's apodo: mal apodo", visible)
        self.assertIn("chat@ubuntu's apodo:", client.screen.display[client.screen.cursor.y])
        self.assertEqual(client.data.count(b"Permiso aprobado. Introduzca apodo."), 1)
        self.assertNotIn(b"Welcome to ", client.data)
        client.send("ana\r")
        client.until("Conectado como ana")
        self.assertIn("chat@ubuntu's apodo: ana", client.display_text())
        self.assertEqual(client.data.count(b"Welcome to "), 1)
        self.assertEqual(client.data.count(b"Si te pilla el profe"), 1)
        client.send("history\r")
        client.until("1  history")
        with self.server.room.lock:
            self.assertEqual(self.server.room.users["ana"].editor.history, ["history"])

    def test_duplicate_names_and_unicode_normalization(self):
        ana = self.client("ana")
        other = Client(self.server.port)
        self.clients.append(other)
        other.send("ANA\r")
        other.until("Ese apodo ya está en uso.")
        other.send("jose\u0301\r")
        other.until("Conectado como josé")
        ana.until("josé se ha unido.")
        duplicate = Client(self.server.port)
        self.clients.append(duplicate)
        duplicate.send("JOSÉ\r")
        duplicate.until("Ese apodo ya está en uso.")
        ana.send("/msg JOSE\u0301 hola normalizado\r")
        other.until("[privado] ana -> josé: hola normalizado")
        unaccented = self.client("Jose")
        self.assertIn("jose", self.server.room.names())
        self.assertIn("josé", self.server.room.names())
        ana.send("/msg JOSE sin tilde\r")
        unaccented.until("[privado] ana -> jose: sin tilde")

    def test_enye_and_other_accents_are_preserved(self):
        self.client("PEÑA")
        self.client("PENA")
        self.client("ÁNGELA")
        self.assertEqual(self.server.room.names(), ["pena", "peña", "ángela"])

    def test_login_timeout_closes_connection_and_releases_slot(self):
        client = Client(self.server.port)
        self.clients.append(client)
        client.send("mal apodo\r")
        client.until("Apodo inválido")
        with self.server.lock:
            session, thread = next(iter(self.server.sessions.items()))
        session.clock = lambda: session.login_deadline
        client.until("Tiempo de espera agotado")
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(client.socket.recv(4096), b"")
        self.assertEqual(self.server.room.names(), [])
        self.assertEqual(self.server.sessions, {})
        self.assertTrue(client.filter.close_complete)

    def test_accepted_nick_disables_login_timeout(self):
        ana = self.client("ana")
        with self.server.room.lock:
            session = self.server.room.users["ana"]
        session.clock = lambda: session.login_deadline + 1
        ana.send("sigo conectado\r")
        ana.until("sigo conectado")
        self.assertFalse(session.stopping.is_set())
        self.assertIn("Los apodos no verifican", ana.display_text())
        self.assertIn("Tu terminal puede conservarlo", ana.display_text())

    def test_help_is_grouped_and_has_no_removed_shortcuts(self):
        ana = self.client("ana")
        ana.send("/help\r")
        ana.until("CHAT(1)")
        visible = ana.display_text()
        ana.send("\x1b[6~")
        ana.until("Tu historial se borra al salir.")
        visible += ana.display_text()
        for heading in ("CHAT(1)", "NOMBRE", "USO", "COMANDOS", "TECLAS"):
            self.assertIn(heading, visible)
        self.assertIn("/msg y @apodo", visible)
        for shortcut in ("Ctrl+U", "Ctrl+K", "Ctrl+W", "Ctrl+Y", "Alt+B", "Alt+F"):
            self.assertNotIn(shortcut, visible)

    def test_man_chat_alias(self):
        ana = self.client("ana")
        ana.send("man chat\r")
        ana.until("CHAT(1)")
        self.assertIn("CHAT(1)", ana.display_text())

    def test_manual_aliases_exit_without_polluting_chat(self):
        ana = self.client("ana")
        for command, exit_key in (("man chat", "q"), ("/help", "\x1b"),
                                  ("/ayuda", "\x03"), ("help", "q")):
            with self.subTest(command=command):
                ana.send(command + "\r")
                ana.until("CHAT(1)")
                ana.send(exit_key)
                ana.until("ana@ubuntu:~$")
                self.assertNotIn("CHAT(1)", ana.display_text())
                self.assertNotIn("RePág/AvPág", ana.display_text())
                with self.server.room.lock:
                    session = self.server.room.users["ana"]
                    self.assertIsNone(session.terminal.manual)
                    self.assertFalse(any("CHAT(1)" in e.text for e in session.terminal.entries))
                    self.assertEqual(session.editor.line, "")

    def test_messages_during_manual_reach_normal_scrollback(self):
        ana = self.client("ana")
        bea = self.client("bea")
        ana.send("/help\r")
        ana.until("CHAT(1)")
        ana.send("\x1b[200~q\x03hola\x1b[201~")
        bea.send("publico durante manual\r/msg ana privado durante manual\r")
        ana.read(0.2)
        self.assertIn("CHAT(1)", ana.display_text())
        ana.send("q")
        ana.until("privado durante manual")
        self.assertIn("publico durante manual", ana.display_text())
        self.assertNotIn("CHAT(1)", ana.display_text())
        with self.server.room.lock:
            self.assertEqual(self.server.room.users["ana"].editor.line, "")

    def test_burst_is_limited_and_warning_is_private(self):
        ana = self.client("ana")
        bea = self.client("bea")
        ana.send("".join(f"ráfaga-{index}\r" for index in range(8)))
        bea.until("ráfaga-4")
        ana.until("Vas demasiado rápido")
        bea.read()
        self.assertNotIn("ráfaga-5".encode(), bea.data)
        self.assertNotIn(b"Vas demasiado", bea.data)
        self.assertEqual(ana.data.count(b"Vas demasiado"), 1)


class RateLimitTests(unittest.TestCase):
    def test_partially_rejected_unicode_input_and_filtered_controls(self):
        self.ana.editor.insert("x" * 399)
        self.ana.terminal.send = lambda data: None
        self.ana.event(Event("text", "á"))
        self.assertEqual(self.ana.editor.line, "x" * 399)
        self.assertIn("Has alcanzado", self.entries(self.ana)[0].text)
        self.ana.input_limit_notified = False
        self.ana.event(Event("text", "\x00"))
        self.assertEqual(self.entries(self.ana), [])

    def setUp(self):
        self.room = Room()
        self.sockets = []
        self.now = 0.0
        self.ana = self.session("ana")
        self.bea = self.session("bea")

    def session(self, nick):
        local, remote = socket.socketpair()
        self.sockets.extend((local, remote))
        session = Session(local, self.room, "ubuntu")
        session.clock = lambda: self.now
        session.token_time = self.now
        self.room.register(session, nick)
        return session

    def tearDown(self):
        for sock in self.sockets:
            sock.close()

    def entries(self, session):
        result = []
        while not session.pending.empty():
            result.append(session.pending.get_nowait())
        return result

    def test_refill_boundaries_and_rejected_attempts_do_not_delay(self):
        for _ in range(5):
            self.assertTrue(self.ana.allow_message())
        self.assertFalse(self.ana.allow_message())
        self.now = 0.99
        self.assertFalse(self.ana.allow_message())
        self.now = 1.0
        self.assertTrue(self.ana.allow_message())
        self.assertFalse(self.ana.allow_message())
        self.assertEqual(len(self.entries(self.ana)), 2)
        self.now = 100
        for _ in range(5):
            self.assertTrue(self.ana.allow_message())
        self.assertFalse(self.ana.allow_message())

    def test_public_private_share_limit_and_sessions_are_independent(self):
        for index in range(4):
            self.ana.line(f"publico-{index}")
        self.ana.line("/msg bea privado")
        self.ana.line("/msg bea rechazado")
        self.ana.line("publico rechazado")
        delivered = self.entries(self.bea)
        self.assertEqual(len(delivered), 5)
        self.assertEqual(delivered[-1].kind, "private")
        notices = self.entries(self.ana)
        self.assertEqual(sum("Vas demasiado" in entry.text for entry in notices), 1)
        self.bea.line("independiente")
        self.assertEqual(self.entries(self.ana)[0].text, "independiente")

    def test_invalid_private_messages_and_commands_do_not_consume_tokens(self):
        for text in ("/msg", "/msg nadie hola", "/msg ana hola", "/help", "ls", "", "/desconocido"):
            self.ana.line(text)
        self.assertEqual(self.ana.message_tokens, 5)
        for _ in range(5):
            self.ana.allow_message()
        self.assertFalse(self.ana.line("exit"))
