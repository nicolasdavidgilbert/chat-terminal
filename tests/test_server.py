import select
import socket
import threading
import time
import unittest

import pyte

from chat_terminal.protocol import TelnetFilter
from chat_terminal.protocol import Event
from chat_terminal.server import ChatServer, Room, Session


class Client:
    def __init__(self, port):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.screen = pyte.HistoryScreen(80, 24, history=1000)
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
        client.until("Conectado como " + nick)
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
        self.assertIn("chat@ubuntu's apodo: ana", "\n".join(client.screen.display))
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

    def test_help_is_grouped_and_has_no_removed_shortcuts(self):
        ana = self.client("ana")
        ana.send("/help\r")
        ana.until("Tu historial se borra al salir.")
        visible = ana.display_text()
        for heading in ("CHAT(1)", "NOMBRE", "USO", "COMANDOS", "TECLAS"):
            self.assertIn(heading, visible)
        self.assertIn("/msg y @apodo", visible)
        for shortcut in ("Ctrl+U", "Ctrl+K", "Ctrl+W", "Ctrl+Y", "Alt+B", "Alt+F"):
            self.assertNotIn(shortcut, visible)

    def test_man_chat_alias(self):
        ana = self.client("ana")
        ana.send("man chat\r")
        ana.until("Tu historial se borra al salir.")
        self.assertIn("CHAT(1)", ana.display_text())

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
