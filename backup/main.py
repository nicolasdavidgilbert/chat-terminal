#!/usr/bin/env python3
"""Servidor de chat privado accesible por telnet, con un hilo por cliente."""

import socket
import threading

HOST = "0.0.0.0"       # Escucha en todas las interfaces (usa 127.0.0.1 para solo local)
PORT = 2323            # Puerto > 1024 para no necesitar permisos de administrador
PASSWORD = "secreto"   # Contraseña de acceso al chat privado

clients = {}           # {socket: apodo}
lock = threading.Lock()  # Protege el diccionario compartido entre hilos

# Bytes del protocolo telnet (negociación de opciones)
IAC, SB, SE = 255, 250, 240


def limpiar_telnet(data: bytes) -> bytes:
    """Elimina las secuencias de negociación telnet (IAC ...) del flujo de bytes."""
    salida = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == IAC:
            if i + 1 < len(data):
                cmd = data[i + 1]
                if cmd == IAC:               # IAC IAC = byte 255 literal
                    salida.append(IAC)
                    i += 2
                elif cmd == SB:              # Subnegociación: hasta IAC SE
                    fin = data.find(bytes([IAC, SE]), i)
                    i = fin + 2 if fin != -1 else len(data)
                else:                        # IAC WILL/WONT/DO/DONT opción
                    i += 3
            else:
                i += 1
        else:
            salida.append(b)
            i += 1
    return bytes(salida)


def enviar(conn, texto: str):
    try:
        conn.sendall(texto.replace("\n", "\r\n").encode("utf-8", errors="replace"))
    except OSError:
        pass


def leer_linea(conn, buffer: bytearray):
    """Devuelve una línea completa (str) o None si el cliente se desconecta.
    Funciona tanto con clientes que envían línea a línea como carácter a carácter."""
    while True:
        for i, b in enumerate(buffer):
            if b in (10, 13):  # \n o \r
                linea = bytes(buffer[:i])
                del buffer[: i + 1]
                # Si venía \r\n, consumir también el \n sobrante
                if b == 13 and buffer[:1] in (b"\n", b"\x00"):
                    del buffer[:1]
                return linea.decode("utf-8", errors="replace").strip()
        try:
            data = conn.recv(1024)
        except OSError:
            return None
        if not data:
            return None
        buffer.extend(limpiar_telnet(data))


def broadcast(mensaje: str, excluir=None):
    with lock:
        destinatarios = [(c, n) for c, n in clients.items() if c is not excluir]
    for c, n in destinatarios:
        # "\r" vuelve al inicio de la línea para no pegar el mensaje al prompt;
        # después se vuelve a dibujar el prompt del destinatario.
        enviar(c, "\r" + mensaje + f"{n}> ")


def mensaje_privado(origen, destino_nick: str, texto: str):
    with lock:
        nick_origen = clients.get(origen)
        destino = next((c for c, n in clients.items() if n.lower() == destino_nick.lower()), None)
    if destino is None:
        enviar(origen, f"* No existe el usuario '{destino_nick}'.\n")
        return
    with lock:
        nick_destino = clients.get(destino, destino_nick)
    enviar(destino, f"\r[privado de {nick_origen}] {texto}\n{nick_destino}> ")
    enviar(origen, f"[privado para {nick_destino}] {texto}\n")


def nick_en_uso(nick: str) -> bool:
    with lock:
        return any(n.lower() == nick.lower() for n in clients.values())


def manejar_cliente(conn, addr):
    buffer = bytearray()
    nick = None
    try:
        # 1) Autenticación
        enviar(conn, "=== Chat privado ===\nContraseña: ")
        clave = leer_linea(conn, buffer)
        if clave != PASSWORD:
            enviar(conn, "Contraseña incorrecta. Adiós.\n")
            return

        # 2) Elegir apodo
        while True:
            enviar(conn, "Elige un apodo: ")
            nick = leer_linea(conn, buffer)
            if nick is None:
                return
            if not nick or " " in nick or len(nick) > 20:
                enviar(conn, "Apodo inválido (sin espacios, máx. 20 caracteres).\n")
            elif nick_en_uso(nick):
                enviar(conn, "Ese apodo ya está en uso.\n")
            else:
                break

        with lock:
            clients[conn] = nick
        enviar(conn, f"Bienvenido, {nick}. Comandos: /lista, /msg <nick> <texto>, /salir\n")
        broadcast(f"* {nick} se ha unido al chat.\n", excluir=conn)
        print(f"[+] {nick} conectado desde {addr}")

        # 3) Bucle principal del chat
        while True:
            enviar(conn, f"{nick}> ")   # Prompt antes de cada línea que escribe el usuario
            linea = leer_linea(conn, buffer)
            if linea is None or linea.lower() == "/salir":
                break
            if not linea:
                continue
            if linea.lower() == "/lista":
                with lock:
                    nombres = ", ".join(clients.values())
                enviar(conn, f"* Conectados: {nombres}\n")
            elif linea.lower().startswith("/msg "):
                partes = linea.split(" ", 2)
                if len(partes) < 3:
                    enviar(conn, "* Uso: /msg <nick> <texto>\n")
                else:
                    mensaje_privado(conn, partes[1], partes[2])
            elif linea.startswith("/"):
                enviar(conn, "* Comando desconocido.\n")
            else:
                broadcast(f"<{nick}> {linea}\n", excluir=conn)
    finally:
        # 4) Limpieza al desconectar
        with lock:
            clients.pop(conn, None)
        if nick:
            broadcast(f"* {nick} ha salido del chat.\n")
            print(f"[-] {nick} desconectado")
        try:
            conn.close()
        except OSError:
            pass


def main():
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((HOST, PORT))
    servidor.listen()
    print(f"Servidor escuchando en {HOST}:{PORT}")
    try:
        while True:
            conn, addr = servidor.accept()
            hilo = threading.Thread(target=manejar_cliente, args=(conn, addr), daemon=True)
            hilo.start()
    except KeyboardInterrupt:
        print("\nCerrando servidor...")
    finally:
        servidor.close()


if __name__ == "__main__":
    main()