#!/usr/bin/env python3
"""Chat privado por telnet con interfaz TUI (secuencias ANSI) y un hilo por cliente.

Conéctate con un cliente telnet real:  telnet localhost 2323
(El servidor negocia modo carácter y dibuja la pantalla completa.)
"""

import re
import socket
import textwrap
import threading
import time

# Este servidor NO tiene contraseña propia: el acceso lo controla SSH.
# Por eso escucha SOLO en local. Si lo cambias a "0.0.0.0", cualquiera que alcance
# el puerto entraría sin autenticarse y sin cifrado, saltándose SSH.
HOST = "127.0.0.1"
PORT = 2323
MAX_NICK = 12
MAX_INPUT = 400        # bytes máximos en la línea de entrada
SIDEBAR_W = 22         # ancho de la columna de usuarios
HISTORY = 300          # mensajes que guarda cada cliente

# ---- Telnet ----
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
OPT_ECHO, OPT_SGA, OPT_NAWS, OPT_LINEMODE = 1, 3, 31, 34

# ---- ANSI ----
ESC = "\x1b"
RESET = f"{ESC}[0m"
BOLD = f"{ESC}[1m"
DIM = f"{ESC}[2m"
RED = f"{ESC}[91m"
YELLOW = f"{ESC}[33m"
MAGENTA = f"{ESC}[95m"
HEADER = f"{ESC}[1;97;44m"
PALETTE = [32, 36, 91, 92, 93, 94, 96]     # colores para los apodos
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

users = {}                 # apodo_en_minúsculas -> Client
lock = threading.Lock()    # protege `users`, `color_counter` y los `log` de los clientes
color_counter = 0


def vlen(s: str) -> int:
    """Longitud visible de un texto (sin secuencias ANSI)."""
    return len(ANSI_RE.sub("", s))


class Client:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.nick = None
        self.style = ""
        self.state = "nick"            # nick -> chat
        self.error = ""
        self.input = bytearray()
        self.log = []                  # entradas visibles para este cliente
        self.width, self.height = 80, 24
        self.send_lock = threading.RLock()
        # estado del analizador de bytes entrantes
        self._ps = "data"
        self._sb = bytearray()
        self._last_cr = False

    # ---------- salida ----------
    def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        try:
            with self.send_lock:
                self.conn.sendall(data)
        except OSError:
            pass

    # ---------- entrada ----------
    def text(self) -> str:
        """Línea de entrada actual, sin caracteres de control (evita inyectar ANSI)."""
        s = self.input.decode("utf-8", errors="replace")
        return "".join(ch for ch in s if ch.isprintable())

    def take_line(self) -> str:
        s = self.text()
        self.input.clear()
        return s

    def backspace(self):
        while self.input and (self.input[-1] & 0xC0) == 0x80:   # bytes de continuación UTF-8
            self.input.pop()
        if self.input:
            self.input.pop()

    def feed(self, data: bytes):
        """Procesa bytes del cliente (telnet + teclas) y devuelve una lista de eventos:
        ("line", texto), ("key",), ("resize",), ("quit",)."""
        ev = []

        def add(e):
            if e[0] == "key" and ev and ev[-1] == e:
                return
            ev.append(e)

        for b in data:
            st = self._ps
            if st == "data":
                if b == IAC:
                    self._ps = "iac"
                elif b == 27:
                    self._ps = "esc"
                elif b == 13:
                    add(("line", self.take_line()))
                elif b == 10:
                    if not self._last_cr:
                        add(("line", self.take_line()))
                elif b == 0:
                    pass
                elif b in (8, 127):
                    if self.input:
                        self.backspace()
                        add(("key",))
                elif b in (3, 4):                  # Ctrl+C / Ctrl+D
                    add(("quit",))
                elif b == 21:                      # Ctrl+U
                    self.input.clear()
                    add(("key",))
                elif b >= 32:
                    if len(self.input) < MAX_INPUT:
                        self.input.append(b)
                        add(("key",))
                self._last_cr = (b == 13)
            elif st == "esc":                      # teclas especiales (flechas, etc.): se ignoran
                self._ps = "csi" if b == ord("[") else ("ss3" if b == ord("O") else "data")
            elif st == "csi":
                if 0x40 <= b <= 0x7E:
                    self._ps = "data"
            elif st == "ss3":
                self._ps = "data"
            elif st == "iac":
                if b in (DO, DONT, WILL, WONT):
                    self._ps = "iac_opt"
                elif b == SB:
                    self._ps = "sb"
                    self._sb.clear()
                else:
                    self._ps = "data"
            elif st == "iac_opt":
                self._ps = "data"
            elif st == "sb":
                if b == IAC:
                    self._ps = "sb_iac"
                else:
                    self._sb.append(b)
            elif st == "sb_iac":
                if b == SE:
                    sb = self._sb
                    if len(sb) == 5 and sb[0] == OPT_NAWS:       # tamaño de ventana
                        w, h = (sb[1] << 8) | sb[2], (sb[3] << 8) | sb[4]
                        if w and h:
                            self.width = min(max(w, 40), 200)
                            self.height = min(max(h, 10), 80)
                            add(("resize",))
                    self._ps = "data"
                elif b == IAC:
                    self._sb.append(IAC)
                    self._ps = "sb"
                else:
                    self._ps = "sb"
        return ev


# =====================================================================
#  Entradas del chat: (prefijo_plano, prefijo_coloreado, texto, estilo)
# =====================================================================
def now() -> str:
    return time.strftime("%H:%M")


def sys_entry(text):
    ts = now()
    return (f"{ts} ", f"{DIM}{ts}{RESET} ", "* " + text, YELLOW)


def msg_entry(sender, text):
    ts = now()
    return (f"{ts} {sender.nick}: ",
            f"{DIM}{ts}{RESET} {sender.style}{sender.nick}{RESET}: ", text, "")


def priv_entry(src, dst, text):
    ts = now()
    return (f"{ts} {src.nick}→{dst.nick}: ",
            f"{DIM}{ts}{RESET} {MAGENTA}{src.nick}→{dst.nick}{RESET}: ", text, MAGENTA)


def push(targets, entry):
    """Añade una entrada al registro de cada cliente y redibuja su pantalla."""
    with lock:
        for t in targets:
            t.log.append(entry)
            del t.log[:-HISTORY]
    for t in targets:
        render(t)


def everyone():
    with lock:
        return list(users.values())


def notify(c, *lines):
    with lock:
        for line in lines:
            c.log.append(sys_entry(line))
    render(c)


def format_entry(entry, width):
    pp, pc, text, style = entry
    avail = max(10, width - len(pp))
    parts = textwrap.wrap(text, avail) or [""]
    out = []
    for i, part in enumerate(parts):
        lead = pc if i == 0 else " " * len(pp)
        out.append(f"{lead}{style}{part}{RESET}")
    return out


# =====================================================================
#  Dibujo de pantalla
# =====================================================================
def draw(c, rows, cur_row, cur_col):
    """Dibuja todas las filas (posicionamiento absoluto) y coloca el cursor."""
    buf = [f"{ESC}[?25l"]
    for i, row in enumerate(rows[: c.height]):
        buf.append(f"{ESC}[{i + 1};1H{row}{RESET}{ESC}[K")
    buf.append(f"{ESC}[{cur_row};{cur_col}H{ESC}[?25h")
    c.send("".join(buf))


def input_row(c):
    prompt_plain = f"{c.nick}> "
    avail = max(1, c.width - 1 - len(prompt_plain))
    shown = c.text()[-avail:]
    row = f"{c.style}{c.nick}>{RESET} {shown}"
    return row, len(prompt_plain) + len(shown) + 1


def render_input(c):
    """Redibuja solo la línea de entrada (para cada tecla pulsada)."""
    with c.send_lock:
        row, col = input_row(c)
        H = c.height
        c.send(f"{ESC}[{H};1H{row}{RESET}{ESC}[K{ESC}[{H};{col}H")


def render_login(c):
    W, H = c.width, c.height
    bw = min(46, W - 2)
    inner = bw - 2
    margin = (W - bw) // 2
    pad = " " * margin

    def line(txt, style=""):
        return pad + "║" + style + txt.center(inner) + RESET + "║"

    box = [
        pad + "╔" + "═" * inner + "╗",
        line("CHAT PRIVADO", BOLD + f"{ESC}[96m"),
        line("telnet · Python · hilos", DIM),
        pad + "╚" + "═" * inner + "╝",
    ]
    rows = [""] * H
    top = max(0, (H - 10) // 2)
    for i, l in enumerate(box):
        rows[top + i] = l

    label, shown = "Apodo: ", c.text()
    hint = f"Hasta {MAX_NICK} caracteres, sin espacios."
    shown = shown[-max(1, W - margin - len(label) - 1):]

    r = top + 6
    rows[r] = pad + BOLD + label + RESET + shown
    rows[r + 1] = pad + DIM + hint + RESET
    if c.error:
        rows[r + 3] = pad + RED + c.error + RESET
    draw(c, rows, r + 1, margin + len(label) + len(shown) + 1)


def render_chat(c):
    W, H = c.width, c.height
    with lock:
        log = list(c.log[-100:])
        names = [(u.nick, u.style, u is c)
                 for u in sorted(users.values(), key=lambda u: u.nick.lower())]

    side_w = SIDEBAR_W if W >= 70 else 0
    chat_w = W - side_w - (1 if side_w else 0)
    body_h = max(1, H - 3)

    # Mensajes (los más nuevos abajo, pegados a la línea de entrada)
    lines = []
    for e in log:
        lines.extend(format_entry(e, chat_w))
    lines = lines[-body_h:]
    lines = [""] * (body_h - len(lines)) + lines

    # Columna de usuarios
    side = []
    if side_w:
        side.append(f"{BOLD}Conectados ({len(names)}){RESET}")
        side.append(f"{DIM}{'─' * (side_w - 2)}{RESET}")
        for nick, style, me in names:
            tag = f"{DIM} (tú){RESET}" if me else ""
            side.append(f"{style}● {nick}{RESET}{tag}")

    # Cabecera
    left = " CHAT PRIVADO"
    right = f"{c.nick} · {len(names)} online "
    head = (left + " " * max(1, W - len(left) - len(right)) + right)[:W]
    rows = [f"{HEADER}{head}{RESET}"]

    # Cuerpo
    for i in range(body_h):
        l = lines[i]
        row = l + " " * max(0, chat_w - vlen(l))
        if side_w:
            s = side[i] if i < len(side) else ""
            row += f"{DIM}│{RESET} {s}"
        rows.append(row)

    # Separador con ayuda y línea de entrada
    hint = " /msg nick texto · /clear · /ayuda · /salir "
    sep = ("──" + hint + "─" * (W - 2 - len(hint))) if len(hint) + 4 <= W else "─" * W
    rows.append(f"{DIM}{sep}{RESET}")
    row, col = input_row(c)
    rows.append(row)
    draw(c, rows, H, col)


def render(c):
    with c.send_lock:      # evita que dos hilos mezclen fotogramas del mismo cliente
        if c.state == "chat":
            render_chat(c)
        else:
            render_login(c)


# =====================================================================
#  Lógica del chat
# =====================================================================
def register(c, nick) -> bool:
    global color_counter
    with lock:
        if nick.lower() in users:
            return False
        c.nick = nick
        c.style = f"{ESC}[1;{PALETTE[color_counter % len(PALETTE)]}m"
        color_counter += 1
        users[nick.lower()] = c
        return True


def on_line(c, text) -> bool:
    """Procesa una línea terminada con Enter. Devuelve False para cerrar la conexión."""
    # --- Apodo ---
    if c.state == "nick":
        nick = text.strip()
        if not nick or any(ch.isspace() for ch in nick) or len(nick) > MAX_NICK:
            c.error = f"Apodo inválido (sin espacios, máx. {MAX_NICK})."
        elif not register(c, nick):
            c.error = "Ese apodo ya está en uso."
        else:
            c.state, c.error = "chat", ""
            with lock:
                c.log.append(sys_entry(f"Bienvenido, {nick}. Escribe /ayuda para ver los comandos."))
            push(everyone(), sys_entry(f"{nick} se ha unido al chat."))
            print(f"[+] {nick} conectado desde {c.addr}")
            return True
        render(c)
        return True

    # --- Chat ---
    text = text.strip()
    if not text:
        render_input(c)
        return True
    low = text.lower()
    if low in ("/salir", "/quit", "/exit"):
        return False
    if low == "/clear":
        with lock:
            c.log.clear()
        render(c)
    elif low in ("/ayuda", "/help"):
        notify(c, "Comandos:",
               "/msg <nick> <texto>  mensaje privado",
               "/clear  limpia la pantalla",
               "/salir  abandona el chat")
    elif low.startswith("/msg"):
        partes = text.split(None, 2)
        if len(partes) < 3:
            notify(c, "Uso: /msg <nick> <texto>")
        else:
            with lock:
                dst = users.get(partes[1].lower())
            if dst is None:
                notify(c, f"No existe el usuario '{partes[1]}'.")
            elif dst is c:
                notify(c, "No puedes enviarte un mensaje a ti mismo.")
            else:
                push([c, dst], priv_entry(c, dst, partes[2]))
    elif text.startswith("/"):
        notify(c, "Comando desconocido. Prueba /ayuda.")
    else:
        push(everyone(), msg_entry(c, text))
    return True


def process(c, ev) -> bool:
    kind = ev[0]
    if kind == "quit":
        return False
    if kind == "resize":
        render(c)
    elif kind == "key":
        render_input(c) if c.state == "chat" else render(c)
    elif kind == "line":
        return on_line(c, ev[1])
    return True


def handle(conn, addr):
    c = Client(conn, addr)
    try:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Modo carácter: el servidor hace el eco, sin modo línea, y pide el tamaño de ventana
        c.send(bytes([IAC, WILL, OPT_ECHO, IAC, WILL, OPT_SGA,
                      IAC, DO, OPT_NAWS, IAC, DONT, OPT_LINEMODE]))
        c.send(f"{ESC}[2J")
        render(c)
        while True:
            try:
                data = conn.recv(1024)
            except OSError:
                break
            if not data:
                break
            for ev in c.feed(data):
                if not process(c, ev):
                    return
    finally:
        if c.nick:
            with lock:
                if users.get(c.nick.lower()) is c:
                    del users[c.nick.lower()]
            push(everyone(), sys_entry(f"{c.nick} ha salido del chat."))
            print(f"[-] {c.nick} desconectado")
        c.send(bytes([IAC, WONT, OPT_ECHO]))
        c.send(f"{RESET}{ESC}[2J{ESC}[H{ESC}[?25hHasta pronto.\r\n")
        try:
            conn.close()
        except OSError:
            pass


def main():
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((HOST, PORT))
    servidor.listen()
    print(f"Servidor TUI escuchando en {HOST}:{PORT}")
    try:
        while True:
            conn, addr = servidor.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nCerrando servidor...")
    finally:
        servidor.close()


if __name__ == "__main__":
    main()