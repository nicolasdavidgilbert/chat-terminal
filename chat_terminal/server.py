"""Servidor de chat efímero. Cada cliente posee su editor y su salida."""

import argparse
import platform
from queue import Empty, Full, Queue
import re
import select
import signal
import socket
import threading
import time
import unicodedata

from .editor import Editor, safe_text
from .protocol import Event, NEGOTIATION, Protocol
from .terminal import Entry, Terminal

def system_welcome() -> str:
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        name = platform.system()
    else:
        name = release.get("PRETTY_NAME", platform.system())
    return safe_text(f"Welcome to {name} ({platform.system()} {platform.release()} {platform.machine()})")


WELCOME = (
    Entry(system_welcome()),
    Entry(""),
    Entry(" * Documentation:  https://help.ubuntu.com"),
    Entry(" * Management:     https://landscape.canonical.com"),
    Entry(" * Support:        https://ubuntu.com/pro"),
    Entry(""),
    Entry("Aviso de acceso:"),
    Entry("  Si te pilla el profe, es tu problema."),
    Entry("  El host no se hace responsable."),
    Entry(""),
    Entry("Aviso de privacidad:"),
    Entry("  No compartas nombres ni datos personales."),
    Entry("  Los mensajes públicos son visibles para todos los conectados."),
    Entry("  /msg entrega el mensaje solo a su remitente y destinatario."),
    Entry("  El chat no guarda conversaciones en disco; usa memoria temporal."),
    Entry("  Al salir se elimina el historial de esta sesión en el chat."),
    Entry("  Tu terminal puede conservarlo y otros pueden copiar los mensajes."),
    Entry("  El administrador del servidor puede acceder al proceso y su memoria."),
    Entry(""),
    Entry("Aviso de identidad:"),
    Entry("  Los apodos no verifican la identidad de una persona."),
    Entry("  Al desconectarse alguien, otra persona puede usar su apodo."),
    Entry(""),
)


def system_information(address: str) -> tuple[Entry, ...]:
    """Bloque decorativo: las cifras son ficticias; fecha y dirección son reales."""
    stamp = time.strftime("%a %d %b %Y %H:%M:%S UTC", time.gmtime())
    return (
        Entry(f" System information as of {stamp}"),
        Entry(""),
        Entry("  System load:  0.03              Processes:               106"),
        Entry("  Usage of /:   67.7% of 11.21GB  Users logged in:         1"),
        Entry(f"  Memory usage: 13%               IP address:              {address}"),
        Entry("  Swap usage:   0%"),
        Entry(""),
    )

MAX_NICK = 12
LOGIN_TIMEOUT = 60.0
HELP = (
    Entry("CHAT(1)                 Manual del chat                 CHAT(1)", kind="notice"),
    Entry(""),
    Entry("NOMBRE", kind="heading"),
    Entry("  chat — conversación con edición de línea tipo Bash"),
    Entry(""),
    Entry("USO", kind="heading"),
    Entry("  man chat, /help       Mostrar este manual", kind="manual"),
    Entry(""),
    Entry("COMANDOS", kind="heading"),
    Entry("  /msg <apodo> <texto>   Enviar un mensaje privado", kind="manual"),
    Entry("  ls, /lista            Ver usuarios: tú en cian, los demás en azul", kind="manual"),
    Entry("  clear, /clear         Limpiar la pantalla", kind="manual"),
    Entry("  history               Ver tu historial de esta sesión", kind="manual"),
    Entry("  /ayuda, help          Mostrar este manual", kind="manual"),
    Entry("  exit, /salir          Salir del chat", kind="manual"),
    Entry(""),
    Entry("TECLAS", kind="heading"),
    Entry("  ←/→, Inicio/Fin       Editar la línea", kind="manual"),
    Entry("  ↑/↓                   Historial y recuperación del borrador", kind="manual"),
    Entry("  Tab                   Completar comandos, /msg y @apodo", kind="manual"),
    Entry("  Ctrl+C / Ctrl+L       Cancelar la entrada / limpiar pantalla", kind="manual"),
    Entry("  Ctrl+D                Borrar; salir si la entrada está vacía", kind="manual"),
    Entry(""),
    Entry("Tu historial se borra al salir. Las menciones no envían avisos."),
    Entry(""),
    Entry("Máximo 5 mensajes seguidos; recuperas uno por segundo."),
)


class Room:
    """Registro en memoria. Nunca escribe datos de usuarios ni conversaciones."""

    def __init__(self):
        self.lock = threading.Lock()
        self.users: dict[str, Session] = {}

    def register(self, session: "Session", nick: str) -> bool:
        with self.lock:
            key = nick.casefold()
            if key in self.users:
                return False
            session.nick = nick
            self.users[key] = session
            return True

    def remove(self, session: "Session"):
        with self.lock:
            key = session.nick.casefold()
            if self.users.get(key) is session:
                del self.users[key]

    def names(self) -> list[str]:
        with self.lock:
            return sorted((s.nick for s in self.users.values()), key=str.casefold)

    def broadcast(self, entry: Entry):
        with self.lock:
            targets = list(self.users.values())
        for session in targets:
            session.enqueue(entry)

    def private(self, source: "Session", nick: str, text: str) -> str | None:
        with self.lock:
            target = self.users.get(nick.casefold())
        if target is None:
            return f"No existe el usuario '{nick}'."
        if target is source:
            return "No puedes enviarte un mensaje a ti mismo."
        entry = Entry(text, source.nick, target.nick, "private")
        source.enqueue(entry)
        target.enqueue(entry)
        return None


class Session:
    def __init__(self, conn: socket.socket, room: Room, hostname: str,
                 color: bool = True):
        self.conn = conn
        self.room = room
        self.nick = ""
        self.editor = Editor()
        self.protocol = Protocol()
        self.terminal = Terminal(self.send, hostname, color)
        self.pending: Queue[Entry] = Queue(maxsize=300)
        self.stopping = threading.Event()
        self.state_lock = threading.Lock()
        self.clock = time.monotonic
        self.login_deadline = self.clock() + LOGIN_TIMEOUT
        self.message_tokens = 5.0
        self.token_time = self.clock()
        self.last_rate_notice = float("-inf")

    def allow_message(self) -> bool:
        now = self.clock()
        self.message_tokens = min(5.0, self.message_tokens + max(0, now - self.token_time))
        self.token_time = now
        if self.message_tokens >= 1:
            self.message_tokens -= 1
            return True
        if now - self.last_rate_notice >= 1:
            self.enqueue(Entry(
                "[aviso privado] Vas demasiado rápido. Espera un segundo."
            ))
            self.last_rate_notice = now
        return False

    def send(self, data: str):
        self.conn.sendall(data.encode("utf-8"))

    def enqueue(self, entry: Entry):
        with self.state_lock:
            if self.stopping.is_set():
                return
            try:
                self.pending.put_nowait(entry)
                return
            except Full:
                pass
        # Never block another client's thread while delivering a message.
        self.stop()

    def stop(self):
        self.stopping.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def flush(self):
        entries = []
        while len(entries) < 300:
            try:
                entries.append(self.pending.get_nowait())
            except Empty:
                break
        if entries:
            self.terminal.output(entries, self.editor)

    def line(self, text: str) -> bool:
        if not self.nick:
            # Leave each submitted attempt above the next prompt, like SSH.
            self.terminal.output([
                Entry(f"chat@{self.terminal.login_host}'s apodo: {safe_text(text)}")
            ], self.editor)
            nick = unicodedata.normalize("NFC", text.strip())
            if not re.fullmatch(rf"[\w.-]{{1,{MAX_NICK}}}", nick, re.UNICODE):
                self.enqueue(Entry(
                    f"Apodo inválido: letras, números, _.-; máximo {MAX_NICK}."
                ))
            elif not self.room.register(self, nick):
                self.enqueue(Entry("Ese apodo ya está en uso."))
            else:
                # Nick input must not become part of the chat's editing history.
                self.editor.dispose()
                self.terminal.nick = nick
                welcome = WELCOME[:6] + system_information(self.terminal.login_host) + WELCOME[6:]
                for entry in welcome:
                    self.enqueue(entry)
                self.enqueue(Entry(
                    f"Conectado como {nick}. Escribe /ayuda para ver los comandos."
                ))
                self.room.broadcast(Entry(f"{nick} se ha unido.", kind="join"))
            return True
        # Preserve spaces in messages; commands alone are parsed with whitespace.
        parts = text.split(None, 2)
        command = parts[0].casefold() if parts else ""
        if command in ("/salir", "/quit", "/exit", "exit", "logout") and len(parts) == 1:
            self.terminal.output(
                [Entry(text, self.nick, kind="message")], self.editor
            )
            return False
        if command in ("/clear", "clear") and len(parts) == 1:
            self.terminal.clear(self.editor)
        elif ((command in ("/ayuda", "/help", "help") and len(parts) == 1)
              or (command == "man" and len(parts) == 2
                  and parts[1].casefold() == "chat")):
            self.enqueue(Entry(text, self.nick, kind="message"))
            for entry in HELP:
                self.enqueue(entry)
        elif command in ("/lista", "/who", "ls", "who") and len(parts) == 1:
            self.enqueue(Entry(text, self.nick, kind="message"))
            self.enqueue(Entry("", kind="users", names=tuple(self.room.names())))
        elif command == "history" and len(parts) == 1:
            self.flush()
            entries = [Entry(text, self.nick, kind="message")]
            entries.extend(Entry(f"{index:5}  {item}")
                           for index, item in enumerate(self.editor.history, 1))
            self.terminal.output(entries, self.editor)
        elif command == "/msg":
            error = "Uso: /msg <apodo> <texto>" if len(parts) != 3 else None
            if error is None:
                with self.room.lock:
                    target = self.room.users.get(parts[1].casefold())
                if target is None:
                    error = f"No existe el usuario '{parts[1]}'."
                elif target is self:
                    error = "No puedes enviarte un mensaje a ti mismo."
                elif not self.allow_message():
                    return True
                else:
                    error = self.room.private(self, parts[1], parts[2])
            if error:
                self.enqueue(Entry(text, self.nick, kind="message"))
                self.enqueue(Entry(error))
        elif command.startswith("/"):
            self.enqueue(Entry(text, self.nick, kind="message"))
            self.enqueue(Entry(f"{parts[0]}: comando desconocido. Prueba /ayuda."))
        elif not text.strip():
            self.enqueue(Entry("", self.nick, kind="message"))
        else:
            if self.allow_message():
                self.room.broadcast(Entry(text, self.nick, kind="message"))
        return True

    def event(self, event: Event) -> bool:
        if self.login_expired():
            return False
        if event.kind == "ssh_address":
            if not self.nick:
                self.terminal.login_host = event.value
                if self.terminal.drawn:
                    self.terminal.redraw(self.editor)
        elif event.kind in ("text", "paste"):
            if not self.editor.insert(event.value):
                self.send("\a")
        elif event.kind == "reply":
            self.conn.sendall(event.value)
        elif event.kind == "resize":
            self.terminal.resize(*event.value, self.editor)
        elif event.kind == "key":
            if event.value == "tab":
                if not self.nick:
                    self.send("\a")
                    return True
                result = self.editor.complete(self.room.names())
            else:
                result = self.editor.key(event.value)
            if result.action == "submit":
                return self.line(result.text)
            if result.action == "exit":
                return False
            if result.action == "cancel":
                if not self.nick:
                    return False
                self.enqueue(Entry(result.text + "^C", self.nick, kind="message"))
            elif result.action == "clear":
                self.terminal.clear(self.editor)
            elif result.action == "choices":
                self.terminal.choices(result.choices, self.editor)
            elif result.action == "bell":
                self.send("\a")
        return True

    def login_expired(self) -> bool:
        if self.nick or self.clock() < self.login_deadline:
            return False
        self.terminal.output([
            Entry("Tiempo de espera agotado: 60 segundos para elegir apodo."),
            Entry("Conexión cerrada."),
        ], self.editor)
        return True

    def run(self):
        try:
            self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # Timeout bounds a blocked send. select avoids polling recv timeouts.
            self.conn.settimeout(1.0)
            self.conn.sendall(NEGOTIATION)
            self.terminal.begin_login()
            # Read the bridge's initial address/size before printing a prompt.
            # Direct Telnet clients receive the normal hostname after 100 ms.
            readable, _, _ = select.select([self.conn], [], [], 0.1)
            if readable:
                data = self.conn.recv(4096)
                if not data:
                    return
                for event in self.protocol.feed(data):
                    if not self.event(event):
                        return
                    if event.kind == "key" and event.value in ("enter", "cancel"):
                        self.flush()
                self.flush()
            self.terminal.redraw(self.editor)
            while not self.stopping.is_set():
                if self.login_expired():
                    break
                self.flush()
                readable, _, _ = select.select([self.conn], [], [], 0.05)
                if not readable:
                    continue
                data = self.conn.recv(4096)
                if not data:
                    break
                for event in self.protocol.feed(data):
                    if not self.event(event):
                        return
                    # Commit output before processing subsequent input, so lines
                    # pasted in one TCP packet never share later editor state.
                    if event.kind == "key" and event.value in ("enter", "cancel"):
                        self.flush()
                self.flush()
                self.terminal.redraw(self.editor)
        except OSError:
            # Connection failures contain no application log or user data.
            pass
        finally:
            with self.state_lock:
                self.stopping.set()
            if self.nick:
                self.room.remove(self)
                self.room.broadcast(Entry(f"{self.nick} ha salido.", kind="notice"))
            try:
                self.terminal.finish(self.editor)
            except OSError:
                pass
            self.conn.close()
            self.editor.dispose()
            self.protocol.reset()
            while True:
                try:
                    self.pending.get_nowait()
                except Empty:
                    break
            self.nick = ""
            self.terminal.dispose()
            self.message_tokens = 0.0
            self.token_time = 0.0
            self.last_rate_notice = float("-inf")


class ChatServer:
    def __init__(self, port: int = 2323, hostname: str | None = None,
                 color: bool = True):
        self.room = Room()
        self.hostname = safe_text(hostname or socket.gethostname().split(".")[0])
        self.color = color
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.socket.bind(("127.0.0.1", port))
            self.socket.listen(64)
            self.socket.settimeout(0.2)
        except OSError:
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]
        self.stopping = threading.Event()
        self.sessions: dict[Session, threading.Thread] = {}
        self.lock = threading.Lock()

    def stop(self):
        self.stopping.set()

    def _run(self, session: Session):
        try:
            session.run()
        finally:
            with self.lock:
                self.sessions.pop(session, None)

    def serve(self):
        try:
            while not self.stopping.is_set():
                try:
                    conn, _ = self.socket.accept()
                except socket.timeout:
                    continue
                with self.lock:
                    full = len(self.sessions) >= 128
                if full:
                    try:
                        conn.settimeout(0.2)
                        conn.sendall(b"Chat lleno. Intentalo de nuevo mas tarde.\r\n")
                    except OSError:
                        pass
                    finally:
                        conn.close()
                    continue
                session = Session(conn, self.room, self.hostname, self.color)
                thread = threading.Thread(
                    target=self._run, args=(session,), daemon=False
                )
                with self.lock:
                    self.sessions[session] = thread
                thread.start()
        finally:
            self.socket.close()
            with self.lock:
                sessions = list(self.sessions.items())
            for session, _ in sessions:
                session.stop()
            for _, thread in sessions:
                thread.join()


def main():
    parser = argparse.ArgumentParser(
        description="Chat efímero con aspecto de Bash/Ubuntu."
    )
    parser.add_argument("--port", type=int, default=2323, help="puerto local (2323)")
    parser.add_argument("--prompt-host", help="nombre de máquina mostrado en el prompt")
    parser.add_argument("--no-color", action="store_true", help="prompt sin colores")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("el puerto debe estar entre 1 y 65535")
    try:
        server = ChatServer(args.port, args.prompt_host, not args.no_color)
    except OSError as error:
        parser.exit(1, f"No se pudo iniciar el chat: {error}\n")
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: server.stop())
    # Only startup information, never connection events or chat content.
    print(f"Chat escuchando en 127.0.0.1:{server.port}", flush=True)
    server.serve()
