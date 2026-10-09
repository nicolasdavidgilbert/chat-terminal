#!/usr/bin/env python3
"""Chat privado con aspecto de terminal bash (Ubuntu) y un hilo por cliente.

Cada mensaje se muestra como una línea de comando:   ana@servidor:~$ hola
No tiene contraseña propia: el acceso lo controla SSH (ssh_chat_bridge.py),
por eso escucha SOLO en 127.0.0.1.

No es una shell de verdad: cualquier cosa que se escriba (también "ls") se envía
como mensaje al resto. Comandos del chat: /msg, /lista, /clear, /ayuda, /salir.
Teclas como en bash: Ctrl+C cancela la línea, Ctrl+D (línea vacía) o "exit" salen,
Ctrl+L limpia la pantalla.
"""

import socket
import textwrap
import threading

# Si lo cambias a "0.0.0.0", cualquiera que alcance el puerto entraría sin
# autenticarse y sin cifrado, saltándose SSH.
HOST = "127.0.0.1"
PORT = 2323
MAX_NICK = 12
MAX_INPUT = 400        # bytes máximos en la línea de entrada
HISTORY = 300          # líneas que guarda cada cliente
# Nombre de máquina que sale en el prompt (usuario@NOMBRE:~$). Por defecto, el real.
PROMPT_HOST = (socket.gethostname().split(".")[0] or "chat")[:15]

# ---- Telnet ----
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
OPT_ECHO, OPT_SGA, OPT_NAWS, OPT_LINEMODE = 1, 3, 31, 34

# ---- ANSI (colores del prompt de Ubuntu) ----
ESC = "\x1b"
RESET = f"{ESC}[0m"
DIM = f"{ESC}[2m"
BLUE = f"{ESC}[01;34m"
MAGENTA = f"{ESC}[95m"
# PALETTE = [32, 36, 91, 92, 93, 94, 96]     # el primero es el verde de Ubuntu
PALETTE = [32]     # el primero es el verde de Ubuntu

users = {}                 # apodo_en_minúsculas -> Client
lock = threading.Lock()    # protege `users`, `color_counter` y los `log` de los clientes
color_counter = 0


class Client:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.nick = None
        self.style = ""
        self.state = "nick"            # nick -> chat
        self.input = bytearray()
        self.log = []                  # líneas visibles para este cliente (ya en chat)
        self.login_log = []            # líneas de la pantalla de apodo
        self.prompt_row = 1            # fila donde está el prompt (se recalcula al dibujar)
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
        ("line", texto), ("key",), ("resize",), ("cancel",), ("eof",), ("clear",)."""
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
                elif b == 3:                       # Ctrl+C
                    add(("cancel",))
                elif b == 4:                       # Ctrl+D
                    add(("eof",))
                elif b == 12:                      # Ctrl+L
                    add(("clear",))
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
#  Líneas del chat: (prefijo_plano, prefijo_coloreado, texto, estilo)
# =====================================================================
def prompt_plain(nick: str) -> str:
    return f"{nick}@{PROMPT_HOST}:~$ "


def prompt_colored(u) -> str:
    """Prompt como el de Ubuntu: usuario@host en color, ':' normal, '~' en azul y '$'."""
    return f"{u.style}{u.nick}@{PROMPT_HOST}{RESET}:{BLUE}~{RESET}$ "


def sys_entry(text, style=""):
    return ("", "", text, style)


def msg_entry(sender, text):
    """Un mensaje se ve igual que un comando escrito en el prompt."""
    return (prompt_plain(sender.nick), prompt_colored(sender), text, "")


def priv_entry(src, dst, text):
    pp = f"[privado] {src.nick} -> {dst.nick}: "
    pc = (f"{MAGENTA}[privado]{RESET} {src.style}{src.nick}{RESET} -> "
          f"{dst.style}{dst.nick}{RESET}: ")
    return (pp, pc, text, "")


def push(targets, entry):
    """Añade una línea al registro de cada cliente y redibuja su pantalla."""
    with lock:
        for t in targets:
            t.log.append(entry)
            del t.log[:-HISTORY]
    for t in targets:
        render(t)


def everyone():
    with lock:
        return list(users.values())


def echo(c, text):
    """Deja en pantalla lo que el usuario acaba de escribir (como hace bash)."""
    with lock:
        c.log.append(msg_entry(c, text))


def notify(c, *lines):
    with lock:
        for line in lines:
            c.log.append(sys_entry(line))
    render(c)


def format_entry(entry, width):
    """Parte una línea en trozos de ancho `width`, como un terminal: sin sangría."""
    pp, pc, text, style = entry
    lead = " " * len(pp)
    parts = textwrap.wrap(text, max(20, width), initial_indent=lead) or [lead]
    out = [f"{pc}{style}{parts[0][len(pp):]}{RESET}"]
    out.extend(f"{style}{p}{RESET}" for p in parts[1:])
    return out


# =====================================================================
#  Dibujo de pantalla
# =====================================================================
def draw(c, rows, cur_row, cur_col):
    """Dibuja todas las filas (posicionamiento absoluto) y coloca el cursor."""
    H = c.height
    rows = rows[:H] + [""] * (H - len(rows))        # las filas sobrantes se borran
    buf = [f"{ESC}[?25l"]
    for i, row in enumerate(rows):
        buf.append(f"{ESC}[{i + 1};1H{row}{RESET}{ESC}[K")
    buf.append(f"{ESC}[{cur_row};{cur_col}H{ESC}[?25h")
    c.send("".join(buf))


def input_row(c):
    plain = prompt_plain(c.nick)
    avail = max(1, c.width - 1 - len(plain))
    shown = c.text()[-avail:]
    return f"{prompt_colored(c)}{shown}", len(plain) + len(shown) + 1


def render_input(c):
    """Redibuja solo la línea del prompt (para cada tecla pulsada)."""
    with c.send_lock:
        row, col = input_row(c)
        r = c.prompt_row
        c.send(f"{ESC}[{r};1H{row}{RESET}{ESC}[K{ESC}[{r};{col}H")


def render_login(c):
    H = c.height
    lines = c.login_log[-(H - 1):]
    label = "Apodo: "
    shown = c.text()[-max(1, c.width - len(label) - 1):]
    rows = lines + [label + shown]
    c.prompt_row = len(rows)
    draw(c, rows, c.prompt_row, len(label) + len(shown) + 1)


def render_chat(c):
    """Como un terminal: el texto empieza arriba y el prompt va justo debajo de la
    última línea; al llenarse la pantalla, el contenido sube y el prompt queda abajo."""
    W, H = c.width, c.height
    with lock:
        log = list(c.log[-200:])
    lines = []
    for e in log:
        lines.extend(format_entry(e, W - 1))
    lines = lines[-(H - 1):]
    row, col = input_row(c)
    c.prompt_row = len(lines) + 1
    draw(c, lines + [row], c.prompt_row, col)


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
            error = f"Apodo inválido (sin espacios, máx. {MAX_NICK} caracteres)."
        elif not register(c, nick):
            error = "Ese apodo ya está en uso."
        else:
            c.state = "chat"
            c.login_log.clear()
            with lock:
                c.log.append(sys_entry(f"Conectado como {nick}. Escribe /ayuda para ver los comandos."))
            push(everyone(), sys_entry(f"{nick} se ha unido.", DIM))
            print(f"[+] {nick} conectado desde {c.addr}")
            return True
        c.login_log += [f"Apodo: {nick}", error]
        del c.login_log[:-50]
        render(c)
        return True

    # --- Chat ---
    text = text.strip()
    if not text:                       # Enter en blanco = otra línea de prompt, como bash
        push([c], msg_entry(c, ""))
        return True
    low = text.lower()
    if low in ("/salir", "/quit", "/exit", "exit", "logout"):
        return False
    if low == "/clear":
        with lock:
            c.log.clear()
        render(c)
    elif low in ("/ayuda", "/help"):
        echo(c, text)
        notify(c, "Comandos:",
               "  /msg <nick> <texto>   mensaje privado",
               "  /lista                usuarios conectados",
               "  /clear                limpia la pantalla (también Ctrl+L)",
               "  /salir                sale (también exit, logout o Ctrl+D)")
    elif low in ("/lista", "/who"):
        echo(c, text)
        with lock:
            nombres = sorted((u.nick for u in users.values()), key=str.lower)
        notify(c, f"Conectados ({len(nombres)}): " + ", ".join(nombres))
    elif low.startswith("/msg"):
        partes = text.split(None, 2)
        if len(partes) < 3:
            echo(c, text)
            notify(c, "Uso: /msg <nick> <texto>")
        else:
            with lock:
                dst = users.get(partes[1].lower())
            if dst is None:
                echo(c, text)
                notify(c, f"No existe el usuario '{partes[1]}'.")
            elif dst is c:
                echo(c, text)
                notify(c, "No puedes enviarte un mensaje a ti mismo.")
            else:
                push([c, dst], priv_entry(c, dst, partes[2]))
    elif text.startswith("/"):
        echo(c, text)
        notify(c, "Comando desconocido. Prueba /ayuda.")
    else:
        push(everyone(), msg_entry(c, text))
    return True


def process(c, ev) -> bool:
    kind = ev[0]
    if kind == "cancel":               # Ctrl+C
        if c.state != "chat":
            return False
        partial = c.take_line()
        with lock:
            c.log.append(msg_entry(c, partial + "^C"))
        render(c)
    elif kind == "eof":                # Ctrl+D
        if c.state != "chat" or not c.input:
            return False
    elif kind == "clear":              # Ctrl+L
        if c.state == "chat":
            with lock:
                c.log.clear()
            render(c)
    elif kind == "resize":
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
            push(everyone(), sys_entry(f"{c.nick} ha salido.", DIM))
            print(f"[-] {c.nick} desconectado")
        # Despedida como en bash: se deja la pantalla tal cual y se escribe "logout" debajo
        row = c.prompt_row if c.state == "chat" else min(len(c.login_log), c.height - 1) + 1
        c.send(bytes([IAC, WONT, OPT_ECHO]))
        c.send(f"{RESET}{ESC}[{row};1H\r\nlogout\r\n{ESC}[?25h")
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