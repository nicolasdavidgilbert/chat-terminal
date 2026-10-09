# chat-terminal

Chat en Python con apariencia de terminal Ubuntu, mensajes públicos y privados,
autocompletado con Tab y acceso mediante un puente SSH.

## Versión actual

```bash
python3 tui-terminal-tab-colores.py
```

Escucha en `127.0.0.1:2323`. El puente `ssh-bridge.py` permite conectar
un terminal local o una sesión SSH al chat:

```bash
python3 ssh-bridge.py
```

El servidor actual delega la autenticación en SSH; configurar el puente como
`ForceCommand` requiere configurar el servidor SSH por separado.

Comandos: `/msg <nick> <texto>`, `/lista` (o `ls`), `/clear`, `/ayuda`, `/salir`.
Tab completa comandos; Ctrl+C cancela la entrada, Ctrl+L limpia y Ctrl+D sale
cuando la entrada está vacía. No ejecuta comandos del sistema.

## Evolución conservada

Los commits reconstruyen las etapas a partir de los archivos originales,
sin modificar su código ni inventar fechas históricas. Las versiones anteriores
se conservan en el historial de Git; la carpeta de trabajo contiene únicamente
la versión actual y el puente SSH. Las rutas de la tabla son las históricas.

| Etapa | Archivo | Rama |
| --- | --- | --- |
| Chat básico por Telnet | `backup/main.py` | `main` y base de `chat-bonito` |
| TUI con panel lateral de usuarios | `backup/tui.py` | [`chat-bonito`](https://github.com/nicolasdavidgilbert/chat-terminal/tree/chat-bonito) |
| Aspecto de terminal Ubuntu y puente SSH | `backup/tui-terminal.py`, `backup/ssh-bridge.py` | `main` |
| Autocompletado con Tab | `backup/tui-terminal-tab.py` | `main` |
| Apodos verdes | `tui-terminal-tab-colores.py` | `main` |

`backup/main.py` es una versión histórica: escucha en todas las interfaces y
contiene la contraseña de ejemplo `secreto`. La versión actual escucha solo
en localhost. Los entornos locales y archivos `.env` quedan excluidos de Git.
