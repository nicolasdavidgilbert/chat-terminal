# chat-terminal

Chat efímero con el aspecto y la edición de línea de Bash en Ubuntu. Los mensajes
aparecen como `ana@servidor:~$ hola`, la salida fluye en la pantalla normal del
terminal y los mensajes entrantes respetan el borrador y la posición del cursor.

## Iniciar

Requiere Python 3.10 o posterior y `wcwidth`, para calcular el ancho de los
caracteres Unicode y editar grafemas completos (acentos, caracteres chinos y emoji).

```bash
python3 -m venv .env
source .env/bin/activate
python -m pip install -r requirements.txt
python tui-terminal-tab-colores.py
```

Si ya existe `.env`, basta con activarlo e instalar las dependencias. También se
puede iniciar con `python -m chat_terminal`.

En otro terminal, activa el mismo entorno y conecta el puente:

```bash
source .env/bin/activate
python ssh-bridge.py
```

El servidor escucha exclusivamente en `127.0.0.1:2323`. Para usar otro puerto o
personalizar el nombre del prompt:

```bash
python -m chat_terminal --port 2424 --prompt-host ubuntu
python ssh-bridge.py 127.0.0.1 2424
```

`--no-color` desactiva los colores del prompt. La interfaz necesita un terminal
compatible con ANSI/VT y UTF-8; los colores y la representación de emoji dependen
del emulador y su fuente. Al redimensionar, se vuelve a dibujar la entrada en la
pantalla visible; no se reconstruye un archivo de conversación en el servidor.

Para acceso remoto, ejecutar el puente en el servidor como `ForceCommand` de SSH
con un pseudo-terminal (`ssh -t`). La autenticación y el cifrado los proporciona
SSH; su configuración se hace por separado. El chat no ejecuta órdenes del sistema.

## Comandos y teclas

| Comando | Acción |
| --- | --- |
| `/msg <apodo> <texto>` | Mensaje privado, visible solo para sus participantes |
| `/lista`, `/who`, `ls`, `who` | Usuarios conectados |
| `/ayuda`, `/help`, `help` | Ayuda |
| `/clear`, `clear` | Limpiar la pantalla |
| `history` | Mostrar las entradas propias de esta sesión |
| `/salir`, `/quit`, `/exit`, `exit`, `logout` | Salir |

| Tecla | Acción |
| --- | --- |
| Izquierda / derecha, Ctrl+B/F | Mover el cursor por caracteres completos |
| Arriba / abajo, Ctrl+P/N | Recorrer el historial y recuperar el borrador |
| Inicio / Fin, Ctrl+A/E | Ir al principio o final de la línea |
| Alt+B/F, Ctrl+izquierda/derecha | Moverse por palabras |
| Retroceso / Suprimir | Borrar antes del cursor / en el cursor |
| Ctrl+U/K/W | Cortar hasta el principio / final / palabra anterior |
| Ctrl+Y | Recuperar el último texto cortado |
| Ctrl+C | Cancelar la línea sin enviarla |
| Ctrl+D | Borrar en el cursor; salir si la línea está vacía |
| Ctrl+L | Limpiar la pantalla conservando entrada e historial |
| Tab | Completar comandos, destinatarios de `/msg` y menciones `@apodo` |

Una coincidencia de Tab completa la palabra y añade un espacio. Con varias,
amplía el prefijo común; al pulsar Tab otra vez muestra opciones por columnas.
Las menciones `@apodo` son texto normal, sin notificaciones especiales.

El pegado protegido del terminal convierte los saltos de línea y tabulaciones
en espacios y espera a que pulses Enter. Un cliente Telnet que no envíe las marcas
de pegado protegido tratará los saltos de línea como Enter.

## Sesiones y privacidad

Los avisos `ana se ha unido.` y `ana ha salido.` se muestran en el chat, sin
fechas ni horas. No se imprimen los eventos de usuarios en la consola del servidor.

No se guardan mensajes, apodos, historial ni eventos en archivos o bases de datos.
Cada conexión mantiene únicamente estado temporal: hasta 300 entradas propias,
una línea de hasta 400 bytes UTF-8 y una cola limitada de mensajes por entregar.
Al terminar su hilo se vacían el historial, borrador, texto cortado, datos de
protocolo y cola, y se retira el usuario del registro. Una reconexión empieza vacía.
No hay búsqueda de texto ni historial compartido entre sesiones.

El terminal de cada participante puede conservar su propio scroll local. `clear`
y Ctrl+L no ordenan borrar ese scroll. Los avisos y mensajes ya entregados no se
conservan como historial de conversación en el servidor.

Las conexiones lentas no bloquean a otros participantes: cada cliente tiene una
cola de salida; si se llena o la escritura agota su tiempo de espera, se cierra
esa conexión. El servidor admite hasta 128 conexiones simultáneas. SIGINT y
SIGTERM cierran las conexiones y esperan a que terminen sus hilos; el puente
restaura el modo del terminal, el cursor y el pegado protegido al salir.

## Organización y comprobaciones

`chat_terminal` separa el protocolo Telnet, la edición de línea, la presentación
ANSI y el servidor. El archivo original sigue funcionando como lanzador; el
puente reutiliza el filtro Telnet del paquete. No se necesita un framework de TUI.

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Las pruebas cubren edición, Unicode, límites, autocompletado, paquetes partidos,
pegado, conversación con varios clientes, privacidad de mensajes, liberación de
sesiones y presentación en un emulador. En Linux también prueban el puente con
un pseudo-terminal real, cambios de tamaño y restauración tras SIGTERM.

Referencias utilizadas:

- [Prompt del paquete Bash de Ubuntu](https://git.launchpad.net/ubuntu/+source/bash/plain/debian/skel.bashrc?h=ubuntu/noble): verde intenso `01;32` para usuario y host, azul intenso `01;34` para el directorio.
- [Manual de Bash de Ubuntu: Readline](https://manpages.ubuntu.com/manpages/noble/man1/bash.1.html): edición, historial y atajos.
- [Módulos de Python](https://docs.python.org/3/tutorial/modules.html), [colas sincronizadas](https://docs.python.org/3/library/queue.html), [sockets](https://docs.python.org/3/library/socket.html) y [PEP 8](https://peps.python.org/pep-0008/): separación, concurrencia y estilo.
- [wcwidth](https://wcwidth.readthedocs.io/en/latest/intro.html): ancho visual y grafemas Unicode.

## Evolución conservada en Git

Los commits iniciales reconstruyen las etapas a partir de los archivos originales,
sin inventar fechas históricas. Las rutas siguientes corresponden a esos commits;
las versiones antiguas siguen disponibles en el historial.

| Etapa | Archivo histórico | Rama |
| --- | --- | --- |
| Chat básico por Telnet | `backup/main.py` | `main` y base de `chat-bonito` |
| TUI con panel lateral | `backup/tui.py` | [`chat-bonito`](https://github.com/nicolasdavidgilbert/chat-terminal/tree/chat-bonito) |
| Terminal Ubuntu y puente SSH | `backup/tui-terminal.py`, `backup/ssh-bridge.py` | `main` |
| Autocompletado con Tab | `backup/tui-terminal-tab.py` | `main` |
| Apodos verdes | `tui-terminal-tab-colores.py` | `main` |
| Editor tipo Bash y sesiones efímeras | Paquete `chat_terminal` | `main` |

El antiguo `backup/main.py` contenía la contraseña de ejemplo `secreto` y escuchaba
en todas las interfaces. El servidor actual escucha únicamente en localhost y
delega el acceso remoto en SSH. `.env` y los entornos locales quedan fuera de Git.
