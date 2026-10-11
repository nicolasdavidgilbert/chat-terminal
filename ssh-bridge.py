#!/usr/bin/env python3
"""Puente de terminal/SSH al chat. Uso: ssh-bridge.py [host] [puerto].

Para SSH, instalar como ForceCommand del servidor con un pseudo-terminal.
Restaura el modo del terminal, cursor y pegado protegido incluso al salir.
"""

import argparse
import ipaddress
import os
import select
import signal
import socket
import struct
import sys
import termios
import tty

from chat_terminal.protocol import IAC, NAWS, SB, SE, SSH_ADDRESS, TelnetFilter
from chat_terminal.terminal import RESTORE


def window_size():
    try:
        cols, rows = os.get_terminal_size(sys.stdin.fileno())
    except OSError:
        return 80, 24
    return (cols or 80), (rows or 24)


def send_size(sock, size=None):
    cols, rows = window_size() if size is None else size
    payload = struct.pack("!HH", min(cols, 65535), min(rows, 65535))
    payload = payload.replace(b"\xff", b"\xff\xff")
    sock.sendall(bytes((IAC, SB, NAWS)) + payload + bytes((IAC, SE)))


def send_ssh_address(sock):
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) != 4:
        return
    try:
        address = str(ipaddress.ip_address(connection[2]))
    except ValueError:
        return
    sock.sendall(bytes((IAC, SB, SSH_ADDRESS)) + address.encode("ascii")
                 + bytes((IAC, SE)))


def write_all(fd, data: bytes):
    while data:
        count = os.write(fd, data)
        if not count:
            raise OSError("No se pudo escribir en el terminal")
        data = data[count:]


def main():
    parser = argparse.ArgumentParser(description="Conecta el terminal al chat.")
    parser.add_argument("host", nargs="?", default="127.0.0.1")
    parser.add_argument("port", nargs="?", type=int, default=2323)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("el puerto debe estar entre 1 y 65535")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.exit(1, "Se necesita un terminal. Usa: ssh -t chat@ip_server\n")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=5)
    except OSError as error:
        parser.exit(1, f"No se pudo conectar con el chat ({error}).\n")
    sock.settimeout(1.0)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    stdin_fd, stdout_fd = sys.stdin.fileno(), sys.stdout.fileno()
    old = None
    handlers = {}
    flags = {"resized": False, "stop": False}
    filtro = TelnetFilter()
    connection_lost = False

    def resize(*_):
        flags["resized"] = True

    def stop(*_):
        flags["stop"] = True

    try:
        for sig, handler in ((signal.SIGWINCH, resize), (signal.SIGTERM, stop),
                             (signal.SIGHUP, stop), (signal.SIGINT, stop)):
            handlers[sig] = signal.signal(sig, handler)
        old = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
        send_ssh_address(sock)
        last_size = window_size()
        send_size(sock, last_size)
        while not flags["stop"]:
            readable, _, _ = select.select([sock, stdin_fd], [], [], 0.1)
            if flags["resized"]:
                flags["resized"] = False
                size = window_size()
                if size != last_size:
                    send_size(sock, size)
                    last_size = size
            if sock in readable:
                data = sock.recv(4096)
                if not data:
                    connection_lost = not filtro.close_complete
                    break
                write_all(stdout_fd, filtro.feed(data))
            if stdin_fd in readable:
                keys = os.read(stdin_fd, 4096)
                if not keys:
                    break
                sock.sendall(keys.replace(b"\xff", b"\xff\xff"))
    except OSError:
        connection_lost = not filtro.close_complete and not flags["stop"]
    except (termios.error, KeyboardInterrupt):
        pass
    finally:
        try:
            message = "Se ha perdido la conexión con el chat.\r\n" if connection_lost else ""
            write_all(stdout_fd, (RESTORE + "\r\n" + message).encode())
        except OSError:
            pass
        try:
            if old is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
        finally:
            sock.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    main()
