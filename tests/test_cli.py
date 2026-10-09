import select
import socket
import subprocess
import sys
import unittest

from test_server import Client


class CliTests(unittest.TestCase):
    def test_shutdown_and_no_user_logs(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, "-m", "chat_terminal", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        client = None
        try:
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            self.assertIn(b"Chat escuchando", process.stdout.readline())
            client = Client(port)
            client.send("usuario_pr\r")
            client.until("Conectado como usuario_pr")
            client.send("contenido privado de prueba\r")
            client.until("contenido privado de prueba")
            process.terminate()
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")
        finally:
            if client:
                client.close()
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=3)
