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
python -m chat_terminal
```

Si ya existe `.env`, basta con activarlo e instalar las dependencias. También se
puede iniciar con `python main.py`.

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

Tras autenticar por SSH, aparece `Permiso aprobado. Introduzca apodo.` y debajo
`chat@<IP del servidor>'s apodo:`, sin limpiar la salida anterior.
El puente obtiene la dirección de destino de `SSH_CONNECTION`,
por lo que cambia automáticamente entre la red de casa y la de clase. Sin SSH,
se usa el nombre del servidor. Los intentos de apodo quedan visibles y, si fallan,
se muestra el error y se repite la petición. Hay 60 segundos desde que el chat
acepta la conexión para elegir un apodo válido; escribir o enviar intentos
inválidos no reinicia ese plazo. Al agotarse, se muestra un aviso y se cierra
la conexión. Una vez aceptado el apodo, este límite deja de aplicarse.
La bienvenida aparece después de
aceptar el apodo y muestra la versión real del sistema y del kernel, seguida de
un bloque decorativo con cifras ficticias de carga, procesos, disco, sesiones,
memoria y swap. Solo la fecha UTC y la dirección de la conexión se actualizan.
Después aparecen las advertencias de acceso y privacidad con formato de mensaje
de inicio de SSH. No se consultan estadísticas ni se añaden dependencias.

`--no-color` desactiva los colores del prompt. La interfaz necesita un terminal
compatible con ANSI/VT y UTF-8; los colores y la representación de emoji dependen
del emulador y su fuente. Al redimensionar, se reconstruye la pantalla con las
últimas entradas entregadas, conservando el borrador y la posición del cursor.
El búfer de presentación contiene hasta 300 entradas en RAM por sesión.

Para acceso remoto, ejecutar el puente en el servidor como `ForceCommand` de SSH
con un pseudo-terminal. Desde un terminal basta con `ssh chat@ip_server`; si el
cliente no solicita PTY, usar `ssh -t chat@ip_server`. El puente rechaza conexiones
sin terminal. La autenticación y el cifrado los proporciona SSH. El chat no ejecuta
órdenes del sistema.

## Despliegue SSH

Instalar juntos `chat_terminal/` y el puente, copiando `ssh-bridge.py` como
`/opt/chat/ssh_chat_bridge.py`. El servidor debe ejecutar el mismo paquete y
escuchar en `127.0.0.1:2323`. La cuenta `chat` necesita leer los archivos, pero no
debe poder modificarlos: usar propietario `root` y permisos de lectura/ejecución
sin escritura para `chat` (también en el directorio `/opt/chat`).

Comprobar las dependencias con el intérprete del ForceCommand:

```bash
cd /opt/chat
/usr/bin/python3 -c 'import wcwidth; from chat_terminal.protocol import TelnetFilter'
```

Si falta `wcwidth`, instalar la versión requerida en un entorno virtual de
despliegue y usar su ruta absoluta de Python tanto para el servidor como para el
ForceCommand. No basta con que funcione en el entorno virtual de desarrollo.

Configurar este bloque al final de `/etc/ssh/sshd_config`, manteniendo las demás
cuentas con su configuración habitual:

```text
Match User chat
    ForceCommand /usr/bin/python3 /opt/chat/ssh_chat_bridge.py
    PermitTTY yes
    PasswordAuthentication yes
    DisableForwarding yes
    PermitUserRC no
    AllowTcpForwarding no
    X11Forwarding no
    AllowAgentForwarding no
    ClientAliveInterval 60
```

Guardar una copia de la configuración y de la aplicación anteriores. Con una
sesión administrativa abierta, validar antes de recargar:

```bash
sudo sshd -t
sudo sshd -T -C user=chat,host=cliente,addr=192.0.2.10
```

Sustituir `host` y `addr` por los del cliente real y verificar `forcecommand`,
`permittty`, `passwordauthentication`, `disableforwarding` y `permituserrc` en la
salida. Recargar la unidad del servicio SSH de la distribución (en Ubuntu,
`sudo systemctl reload ssh`) y probar una conexión nueva antes de cerrar la
sesión administrativa. Probar también redimensionamiento, desconexión y ráfagas
con dos participantes. Si falla, restaurar aplicación y configuración anteriores,
validar y recargar de nuevo. No se necesitan cambios en PAM.

## Comandos y teclas

| Comando | Acción |
| --- | --- |
| `/msg <apodo> <texto>` | Mensaje privado, visible solo para sus participantes |
| `/lista`, `/who`, `ls`, `who` | Usuarios conectados |
| `man chat`, `/ayuda`, `/help`, `help` | Manual del chat |
| `/clear`, `clear` | Limpiar la pantalla |
| `history` | Mostrar las entradas propias de esta sesión |
| `/salir`, `/quit`, `/exit`, `exit`, `logout` | Salir |

| Tecla | Acción |
| --- | --- |
| Izquierda / derecha, Ctrl+B/F | Mover el cursor por caracteres completos |
| Arriba / abajo, Ctrl+P/N | Recorrer el historial y recuperar el borrador |
| Inicio / Fin, Ctrl+A/E | Ir al principio o final de la línea |
| Ctrl+izquierda/derecha | Moverse por palabras |
| Retroceso / Suprimir | Borrar antes del cursor / en el cursor |
| Ctrl+C | Cancelar la línea sin enviarla |
| Ctrl+D | Borrar en el cursor; salir si la línea está vacía |
| Ctrl+L | Limpiar la pantalla conservando entrada e historial |
| Tab | Completar comandos, destinatarios de `/msg` y menciones `@apodo` |

En `ls`, `/lista` y `who`, los demás apodos aparecen en azul intenso y el tuyo
en cian intenso (`01;36`), distinguible del verde del prompt. `--no-color` también desactiva
los colores de la lista y los del manual.

`man chat`, `/ayuda`, `/help` y `help` abren la misma ayuda a pantalla completa,
en la pantalla alternativa del terminal, con secciones de manual,
cabeceras en amarillo intenso, comandos en negrita y argumentos en cian
subrayado. Tab también completa `man` y el nombre `chat`.
Usa ↑/↓ para recorrer líneas, RePág/AvPág o espacio para avanzar por páginas,
e Inicio/Fin para ir a los extremos. `q`, Esc y Ctrl+C cierran solo el manual.
Al volver, el chat conserva los mensajes recibidos y su scroll habitual;
la ayuda no se añade a la conversación ni al scroll principal.
El visor requiere soporte de pantalla alternativa en el terminal.

Una coincidencia de Tab completa la palabra y añade un espacio. Con varias,
amplía el prefijo común; al pulsar Tab otra vez muestra opciones por columnas.
Las menciones completas de tu propio `@apodo` aparecen en cian suave
(`#7FAFAF`), sin negrita ni fondo, tanto en mensajes públicos como privados.
La comparación ignora mayúsculas, pero distingue tildes y la ñ; los apodos de otras personas
no se resaltan. La puntuación posterior queda sin color, incluidos puntos y
puntos suspensivos; se conservan los puntos que forman parte de tu apodo.
No generan sonidos ni notificaciones. `--no-color` desactiva
también este resaltado.

El pegado protegido del terminal convierte los saltos de línea y tabulaciones
en espacios y espera a que pulses Enter. Un cliente Telnet que no envíe las marcas
de pegado protegido tratará los saltos de línea como Enter.
Si un pegado protegido supera los 400 bytes UTF-8 disponibles en la línea,
se recorta y solo quien lo ha pegado recibe un aviso privado. El texto sigue
en el borrador hasta pulsar Enter.

## Sesiones y privacidad

Los apodos se registran y muestran en minúsculas, conservando las tildes y la ñ:
`JOSÉ` y `José` se convierten en `josé`, y `Peña` en `peña`.
`josé` y `jose` son apodos distintos, igual que `peña` y `pena`.
Los apodos que coincidan tras esa normalización no pueden usarse a la vez.
La misma regla se aplica a destinatarios de `/msg`, Tab y menciones;
el texto de los mensajes conserva sus mayúsculas y acentos originales.

La bienvenida mantiene el formato de conexión SSH y aclara que los mensajes
públicos son visibles para todos los conectados, mientras que `/msg` se entrega
solo al remitente y al destinatario. Esto no es cifrado de extremo a extremo:
el administrador puede acceder al proceso del servidor y su memoria, y los
participantes pueden copiar los mensajes. Los apodos no verifican identidades;
al desconectarse alguien, otra persona puede usar el mismo apodo.

Cada conexión puede enviar una ráfaga de cinco mensajes públicos o privados.
Se recupera un permiso por segundo, hasta cinco; ambos tipos comparten el límite.
Sin permisos, el mensaje se descarta y solo su remitente recibe
`[aviso privado] Vas demasiado rápido. Espera un segundo.` Como máximo se muestra
un aviso por segundo. Los intentos rechazados no retrasan la recuperación y no se
reenvían automáticamente. Comandos locales y privados inválidos no consumen
permisos. El historial propio puede incluir intentos rechazados. Al reconectar
se empieza con cinco permisos; el límite no identifica personas ni direcciones IP.

Los avisos `ana se ha unido.` y `ana ha salido.` se muestran en el chat, sin
fechas ni horas. No se imprimen los eventos de usuarios en la consola del servidor.

No se guardan mensajes, apodos, historial ni eventos en archivos o bases de datos.
Cada conexión mantiene únicamente estado temporal: hasta 300 entradas propias,
300 entradas de presentación ya entregadas, una línea de hasta 400 bytes UTF-8,
una cola limitada de mensajes por entregar y el estado del límite de envío.
Al terminar su hilo se vacían el historial, búfer de presentación, borrador, datos
de protocolo y cola, y se retira el usuario del registro. Una reconexión empieza vacía.
No hay búsqueda de texto ni historial compartido entre sesiones.

El terminal de cada participante puede conservar su propio scroll local. `clear`
y Ctrl+L no ordenan borrar ese scroll, pero vacían el búfer de presentación para
que sus mensajes no reaparezcan al redimensionar. El búfer solo sirve para
reconstruir la pantalla de esa sesión y no se comparte ni se recupera al reconectar.

Las conexiones lentas no bloquean a otros participantes: cada cliente tiene una
cola de salida; si se llena o la escritura agota su tiempo de espera, se cierra
esa conexión. El servidor admite hasta 128 conexiones simultáneas. SIGINT y
SIGTERM cierran las conexiones y esperan a que terminen sus hilos; el puente
restaura el modo del terminal, el cursor y el pegado protegido al salir.

## Organización y comprobaciones

`chat_terminal` separa el protocolo Telnet, la edición de línea, la presentación
ANSI y el servidor. `main.py` funciona como lanzador; el
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
