"""Pruebas del puente con un pseudo-terminal Linux real."""

import fcntl
import os
from pathlib import Path
import pty
import select
import socket
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import unittest

import pyte

from chat_terminal.server import ChatServer
from chat_terminal.protocol import CLOSE_COMPLETE
from terminal_emulator import AlternateScreen


class BridgeTests(unittest.TestCase):
    def test_bridge_reports_unexpected_eof_and_socket_error_but_not_known_closes(self):
        for message, reset in ((b"", False), (b"", True),
                               (b"Has salido del chat.\r\n" + CLOSE_COMPLETE, False),
                               (b"Chat lleno.\r\n" + CLOSE_COMPLETE, False)):
            with self.subTest(message=message, reset=reset):
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    listener.settimeout(3)
                    master, slave = pty.openpty()
                    original = termios.tcgetattr(slave)
                    process = subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve().parents[1] / "ssh-bridge.py"),
                         "127.0.0.1", str(listener.getsockname()[1])],
                        stdin=slave, stdout=slave, stderr=slave,
                    )
                    try:
                        conn, _ = listener.accept()
                        with conn:
                            conn.settimeout(2)
                            conn.recv(4096)  # Drain the bridge's initial negotiation.
                            if message:
                                for byte in message:
                                    conn.sendall(bytes((byte,)))
                                conn.shutdown(socket.SHUT_WR)
                                # Drain remaining input so this closes cleanly, not by RST.
                                while conn.recv(4096):
                                    pass
                            elif reset:
                                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                                struct.pack("ii", 1, 0))
                            else:
                                conn.shutdown(socket.SHUT_WR)
                                while conn.recv(4096):
                                    pass
                        process.wait(timeout=3)
                        received = bytearray()
                        while select.select([master], [], [], 0)[0]:
                            received.extend(os.read(master, 65536))
                        warning = "Se ha perdido la conexión con el chat.".encode()
                        self.assertEqual(received.count(warning), 0 if message else 1)
                        self.assertEqual(termios.tcgetattr(slave), original)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=3)
                        os.close(master)
                        os.close(slave)
    def test_requires_terminal_before_connecting(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "ssh-bridge.py")],
            input=b"", capture_output=True, timeout=3,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"ssh -t chat@ip_server", result.stderr)
        self.assertNotIn(b"No se pudo conectar", result.stderr)

    def test_raw_terminal_resize_and_restoration_on_signal(self):
        self.check_terminal_restoration(disconnect=False)

    def test_raw_terminal_restoration_on_server_disconnect(self):
        self.check_terminal_restoration(disconnect=True)

    def test_manual_restoration_on_signal(self):
        self.check_terminal_restoration(disconnect=False, manual=True)

    def test_manual_restoration_on_server_disconnect(self):
        self.check_terminal_restoration(disconnect=True, manual=True)

    def check_terminal_restoration(self, disconnect, manual=False):
        server = ChatServer(0, "ubuntu", color=False)
        thread = threading.Thread(target=server.serve)
        thread.start()
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 12, 40, 0, 0))
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "ssh-bridge.py"),
             "127.0.0.1", str(server.port)],
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            env={**os.environ, "SSH_CONNECTION": "192.168.1.100 54321 192.168.1.166 22"},
        )
        screen = AlternateScreen(40, 12)
        stream = pyte.ByteStream(screen)
        received = bytearray()

        def until(text):
            deadline = time.monotonic() + 3
            while text not in "\n".join(screen.display):
                if time.monotonic() >= deadline:
                    raise AssertionError(f"No llegó {text!r}: {screen.display}")
                ready, _, _ = select.select([master], [], [], 0.05)
                if ready:
                    data = os.read(master, 65536)
                    received.extend(data)
                    stream.feed(data)

        try:
            until("chat@192.168.1.166's apodo:")
            self.assertNotIn(b"chat@ubuntu's apodo:", received)
            self.assertEqual(received.count(b"Permiso aprobado. Introduzca apodo."), 1)
            self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
            os.write(master, b"ana\r")
            until("Conectado como ana")
            os.write(master, "hola á中".encode())
            until("hola á中")
            self.assertEqual(screen.cursor.x, len("ana@ubuntu:~$ ") + 8)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 25, 0, 0))
            screen.resize(lines=8, columns=25)
            process.send_signal(signal.SIGWINCH)
            # NAWS travels from the bridge to the server and redraws the draft.
            os.write(master, b"\x1b[D!\r")
            until("hola á!中")
            if manual:
                os.write(master, b"man chat\r")
                until("CHAT(1)")
                self.assertIs(screen.screen, screen.alternate)
            if disconnect:
                server.stop()
            else:
                process.send_signal(signal.SIGTERM)
            process.wait(timeout=3)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(termios.tcgetattr(slave), original)
            while select.select([master], [], [], 0)[0]:
                data = os.read(master, 65536)
                received.extend(data)
                stream.feed(data)
            self.assertIn(b"\x1b[?2004h", received)
            self.assertIn(b"\x1b[?2004l", received)
            self.assertNotIn("Se ha perdido la conexión".encode(), received)
            if disconnect:
                self.assertEqual(received.count("El servidor ha cerrado el chat.".encode()), 1)
            if manual:
                self.assertIn(b"\x1b[?1049h", received)
                self.assertIs(screen.screen, screen.main)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            os.close(master)
            os.close(slave)
            server.stop()
            thread.join(3)
            self.assertFalse(thread.is_alive())
