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

## Despliegue SSH: paso a paso en Ubuntu

### 1. Preparar el servidor

Usar una máquina con Ubuntu Server 24.04 o posterior, ya sea un ordenador, una
máquina virtual o un VPS. Si estás instalando Ubuntu, crea una cuenta
administrativa con permisos `sudo` y selecciona la instalación de OpenSSH.
Los comandos siguientes se ejecutan en el servidor desde esa cuenta, mediante
su consola o una sesión SSH administrativa. Mantén esa sesión abierta hasta
haber probado el acceso al chat.

Instalar las herramientas necesarias:

```bash
sudo apt update
sudo apt install openssh-server python3 python3-venv git nano
sudo systemctl enable --now ssh
```

Si vas a administrarlo desde tu ordenador, obtén su dirección con `hostname -I`
en la consola del servidor y conecta con `ssh administrador@ip_server`.
Sustituye esos valores por tu usuario administrativo y la IP real. Si no conecta,
revisa el acceso de red del paso 6 desde la consola del servidor.

### 2. Descargar e instalar el proyecto

Elige una de las dos opciones siguientes. Para descargar el repositorio en
una instalación nueva:

```bash
sudo git clone https://github.com/nicolasdavidgilbert/chat-terminal.git /opt/chat
```

Si `/opt/chat` ya contiene una instalación, guarda una copia antes de actualizar;
no ejecutes `git clone` sobre ese directorio. Para instalar una copia local en
vez de descargarla, ejecuta desde la carpeta del proyecto en tu ordenador:

```bash
ssh administrador@ip_server 'mkdir -p ~/chat-upload'
scp -r chat_terminal ssh-bridge.py requirements.txt administrador@ip_server:chat-upload/
```

Sustituye `administrador` e `ip_server` por tu cuenta administrativa y el servidor.
Después, en el servidor, copia los archivos:

```bash
sudo mkdir -p /opt/chat
sudo cp -r ~/chat-upload/chat_terminal ~/chat-upload/ssh-bridge.py ~/chat-upload/requirements.txt /opt/chat/
```

Después de cualquiera de las dos opciones, ejecuta en el servidor:

```bash
sudo python3 -m venv /opt/chat/.env
sudo /opt/chat/.env/bin/python -m pip install -r /opt/chat/requirements.txt
sudo chown -R root:root /opt/chat
sudo chmod -R u=rwX,go=rX /opt/chat
```

En ambos casos deben existir `/opt/chat/chat_terminal/`,
`/opt/chat/ssh-bridge.py`, `/opt/chat/requirements.txt` y `/opt/chat/.env/`.
Los archivos pertenecen a `root`; la cuenta `chat` podrá leerlos y ejecutarlos,
pero no modificarlos.

### 3. Crear el usuario y establecer la contraseña

```bash
sudo useradd --create-home --user-group --shell /bin/bash chat
sudo passwd chat
```

Si la cuenta ya existe, omite `useradd` y comprueba con `id chat` que su grupo
principal es `chat`, tal como requiere el servicio del paso 4. Necesita una shell
válida (`getent passwd chat` muestra `/bin/bash` al final), porque SSH la
usa para ejecutar el ForceCommand. `passwd` pide escribir dos veces la contraseña
que elijas (no aparecen caracteres mientras escribes). Esa es la contraseña que
compartirás con los participantes. SSH gestiona el acceso; no hay una contraseña
predeterminada del chat.

Comprobar que el usuario puede cargar la aplicación y sus dependencias:

```bash
cd /opt/chat
sudo -u chat /opt/chat/.env/bin/python -B -c 'import wcwidth; from chat_terminal.protocol import TelnetFilter'
```

Si termina sin errores, puedes continuar.

### 4. Dejar el chat funcionando como servicio

Crear el archivo de servicio:

```bash
sudo nano /etc/systemd/system/chat-terminal.service
```

Pegar este contenido. En nano, guardar con Ctrl+O, confirmar con Enter y salir
con Ctrl+X:

```ini
[Unit]
Description=Servidor de chat de terminal
After=network.target

[Service]
Type=simple
User=chat
Group=chat
WorkingDirectory=/opt/chat
ExecStart=/opt/chat/.env/bin/python -B -u -m chat_terminal
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Activarlo para que arranque ahora y después de cada reinicio:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chat-terminal
sudo systemctl status chat-terminal --no-pager
sudo ss -ltnp 'sport = :2323'
```

El servicio debe aparecer como `active (running)` y escuchar en
`127.0.0.1:2323`. El servidor y el puente usan el mismo entorno virtual.
Si habías arrancado el chat manualmente, detenlo con Ctrl+C antes de iniciar
el servicio para liberar ese puerto. Ya puedes cerrar la consola del servidor
cuando termines la configuración: systemd mantiene el chat en funcionamiento.

### 5. Configurar el acceso SSH al chat

Guardar una copia de la configuración y abrirla:

```bash
sudo cp -an /etc/ssh/sshd_config /etc/ssh/sshd_config.before-chat
sudo nano /etc/ssh/sshd_config
```

Añadir este bloque al final del archivo. Si ya hay un bloque `Match User chat`,
sustitúyelo para evitar configuraciones duplicadas:

```text
Match User chat
    ForceCommand /opt/chat/.env/bin/python -B /opt/chat/ssh-bridge.py
    PermitTTY yes
    PasswordAuthentication yes
    AllowTcpForwarding no
    X11Forwarding no
    AllowAgentForwarding no
    ClientAliveInterval 60
    DisableForwarding yes
    PermitUserRC no
```

Guardar con Ctrl+O, Enter y Ctrl+X. La copia `sshd_config.before-chat` se crea
sin sobrescribir una copia anterior. Revisa también los archivos incluidos desde
`/etc/ssh/sshd_config.d/`: si otro bloque `Match` aplicable a `chat` ya establece
estas opciones, su primer valor puede prevalecer. Compruébalo con el comando de
configuración efectiva siguiente.

Cada conexión del usuario `chat` ejecutará automáticamente el puente hacia
el servidor local. Las demás cuentas conservan su configuración habitual.

Con la sesión administrativa todavía abierta, validar la configuración:

```bash
sudo /usr/sbin/sshd -t
sudo /usr/sbin/sshd -T -C user=chat,host=cliente,addr=192.0.2.10
```

En el segundo comando, sustituye `host` y `addr` por el nombre y la IP del
cliente real. Comprueba que `forcecommand` contiene la ruta anterior,
`permittty` y `passwordauthentication` son `yes`, y `disableforwarding` es
`yes` y `permituserrc` es `no`. Si tienes reglas `AllowUsers`, `AllowGroups`
o requisitos `AuthenticationMethods`, comprueba también que permiten a `chat`
entrar con contraseña. Si la validación falla, corrige el archivo antes de seguir.

Recargar SSH sin cerrar la sesión administrativa:

```bash
sudo systemctl reload ssh
```

### 6. Permitir las conexiones de red

Para conocer la IP del servidor en tu red local:

```bash
hostname -I
```

Si UFW está activo (puedes verlo con `sudo ufw status`), permitir SSH:

```bash
sudo ufw allow 22/tcp
```

Si SSH usa otro puerto, permite ese puerto y usa `ssh -p PUERTO` al conectar.
En un VPS, permite también ese puerto TCP en el firewall del proveedor. Para
acceder desde fuera de tu red doméstica, configura el reenvío del puerto SSH
hacia la IP local del servidor en el router y comparte tu dirección pública;
con CGNAT necesitarás una red VPN accesible por los participantes o un VPS.
En la misma red basta con la IP local. El puerto `2323` queda en localhost;
los participantes acceden por el puerto de SSH.

### 7. Probar y compartir el acceso

Desde otro terminal de tu ordenador:

```bash
ssh -t chat@ip_server
```

Sustituye `ip_server` por la IP o el nombre del servidor. En la primera conexión,
comprueba la huella SSH con la del servidor, que puedes consultar allí con:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Introduce la contraseña de `chat` y después elige tu apodo. Abre una segunda
conexión, elige otro apodo y comprueba que ambos pueden conversar. Prueba también
redimensionar el terminal y salir con `/salir`. Cuando funcione una conexión
nueva, puedes cerrar la sesión administrativa.

Comparte estos datos con las personas que vayan a entrar, sustituyendo los
marcadores por los valores reales:

```text
Servidor: <IP o nombre del servidor>
Puerto SSH: 22
Usuario SSH: chat
Contraseña SSH: <la que has establecido con sudo passwd chat>
Conexión desde un terminal: ssh -t chat@<IP o nombre del servidor>
Después de introducir la contraseña, elige tu propio apodo.
```

La contraseña se comparte con los participantes por el canal que uses para
invitarles; no hace falta guardarla en el repositorio. Para cambiarla, vuelve a
ejecutar `sudo passwd chat` y comunica la nueva contraseña.

Para comprobar el arranque automático, en una ventana de mantenimiento ejecuta
`sudo reboot`. Cuando el servidor vuelva a estar disponible, conecta otra vez por
SSH y comprueba `sudo systemctl is-enabled chat-terminal` (debe mostrar `enabled`)
y `sudo systemctl is-active chat-terminal` (debe mostrar `active`). Prueba también
una nueva conexión como `chat`.

### 8. Administrar el servicio y resolver problemas

```bash
sudo systemctl status chat-terminal --no-pager
sudo journalctl -u chat-terminal -n 50 --no-pager
sudo systemctl restart chat-terminal
sudo systemctl stop chat-terminal
sudo systemctl start chat-terminal
```

Ejecuta el comando que corresponda; reiniciar o detener el servicio desconecta
a quienes estén dentro. Los mensajes del chat no se guardan en el registro.
Después de actualizar la aplicación o sus dependencias, reinicia el servicio.

Si el puente indica que no puede conectar con el chat, comprueba el servicio y
el puerto `127.0.0.1:2323`. Si SSH rechaza la contraseña, comprueba el usuario,
restablécela con `sudo passwd chat` y revisa la configuración efectiva de SSH y
`sudo journalctl -u ssh -n 50 --no-pager`. Si la conexión agota el tiempo de
espera, revisa la dirección, el puerto y los firewalls.

Para deshacer la configuración SSH, desde la sesión administrativa restaura
la copia, valida y recarga:

```bash
sudo cp -a /etc/ssh/sshd_config.before-chat /etc/ssh/sshd_config
sudo /usr/sbin/sshd -t
sudo systemctl reload ssh
```

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
El pie muestra el intervalo de líneas visibles y el total; en pantallas estrechas
se abrevia y se da prioridad a recordar cómo salir.

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
Al superar el límite escribiendo, solo tú recibes un aviso, que no se repite
al mantener una tecla pulsada. Borrar texto, enviar o cancelar la línea permite
que vuelva a avisar si alcanzas otra vez el límite.

## Sesiones y privacidad

Al salir voluntariamente aparece `Has salido del chat.`. El timeout del apodo,
la cancelación de entrada y el cierre del servidor tienen avisos específicos.
El puente muestra `Se ha perdido la conexión con el chat.` si el servidor
desaparece o falla la conexión sin confirmar un cierre. La entrega del aviso
final es de mejor esfuerzo cuando la conexión ya está rota o saturada.
Los errores del transporte SSH externo los informa el propio cliente SSH.

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

- [Configuración oficial de OpenSSH](https://man.openbsd.org/sshd_config): `Match`, `ForceCommand`, autenticación y restricciones de la cuenta.

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
