#!/usr/bin/env python3
"""Puente SSH -> chat. Se ejecuta en el SERVIDOR, como ForceCommand de sshd.

sshd da a cada usuario un pseudo-terminal cifrado. Este script:
  1. conecta con el servidor de chat (solo escucha en 127.0.0.1),
  2. pone el terminal en modo raw (cada tecla se envía al instante),
  3. avisa al chat del tamaño de la ventana y lo actualiza cada vez que
     el usuario la redimensiona (SIGWINCH), para que el TUI sea responsive.

Uso:  ssh_chat_bridge.py [host] [puerto]      (por defecto 127.0.0.1 2323)
"""

import os
import select
import signal
import socket
import struct
import sys
import termios
import tty

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 2323

IAC, SB, SE = 255, 250, 240
OPT_NAWS = 31
NEGOTIATION = (251, 252, 253, 254)       # WILL, WONT, DO, DONT


class TelnetFilter:
    """Quita la negociación telnet del flujo del servidor (válido entre lecturas partidas)."""

    def __init__(self):
        self.state = 0      # 0 datos, 1 tras IAC, 2 byte de opción, 3 subnegociación, 4 IAC dentro de SB

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if self.state == 0:
                if b == IAC:
                    self.state = 1
                else:
                    out.append(b)
            elif self.state == 1:
                if b == IAC:                 # IAC IAC = byte 255 literal
                    out.append(IAC)
                    self.state = 0
                elif b in NEGOTIATION:
                    self.state = 2
                elif b == SB:
                    self.state = 3
                else:
                    self.state = 0
            elif self.state == 2:
                self.state = 0
            elif self.state == 3:
                if b == IAC:
                    self.state = 4
            elif self.state == 4:
                self.state = 0 if b == SE else 3
        return bytes(out)


def window_size():
    try:
        cols, rows = os.get_terminal_size(sys.stdin.fileno())
    except OSError:
        return 80, 24
    return (cols or 80), (rows or 24)


def send_size(sock):
    """Envía el tamaño actual (telnet NAWS: IAC SB 31 ancho alto IAC SE)."""
    cols, rows = window_size()
    payload = struct.pack("!HH", cols, rows).replace(b"\xff", b"\xff\xff")
    try:
        sock.sendall(bytes([IAC, SB, OPT_NAWS]) + payload + bytes([IAC, SE]))
    except OSError:
        pass


def write_all(fd, data: bytes):
    while data:
        n = os.write(fd, data)
        data = data[n:]


def main():
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except OSError as e:
        sys.stdout.write(f"No se pudo conectar con el chat ({e}).\r\n")
        return
    sock.settimeout(None)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    stdin_fd, stdout_fd = sys.stdin.fileno(), sys.stdout.fileno()
    resized = []
    signal.signal(signal.SIGWINCH, lambda *_: resized.append(1))
    filtro = TelnetFilter()
    try:
        old = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
    except termios.error:
        old = None          # sin terminal (por ejemplo, ssh -T): se sigue sin modo raw

    try:
        send_size(sock)
        while True:
            ready, _, _ = select.select([sock, stdin_fd], [], [], 0.1)
            if resized:                      # el usuario cambió el tamaño de su ventana
                resized.clear()
                send_size(sock)
            if sock in ready:
                data = sock.recv(4096)
                if not data:
                    break
                write_all(stdout_fd, filtro.feed(data))
            if stdin_fd in ready:
                keys = os.read(stdin_fd, 1024)
                if not keys:
                    break
                sock.sendall(keys.replace(b"\xff", b"\xff\xff"))
    except OSError:
        pass
    finally:
        if old is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
        try:
            write_all(stdout_fd, b"\x1b[0m\x1b[?25h\r\n")
        except OSError:
            pass
        sock.close()


if __name__ == "__main__":
    main()