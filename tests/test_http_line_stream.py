"""Tests ciblés de http_line_stream (connectors.py).

Vérifie la lecture ligne à ligne d'une réponse HTTP (NDJSON / SSE), les
erreurs HTTP, le timeout et l'absence de fuite de connexion (le générateur
ferme proprement la réponse quand on l'abandonne en cours de stream).
"""
from __future__ import annotations

import http.server
import threading
import time
import unittest
import urllib.error

from jarvis.connectors import http_line_stream


class _TrackerHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # connexion fermée après chaque réponse

    def log_message(self, *args):
        pass

    def _write_chunk(self, chunk: str, delay: float = 0.0) -> bool:
        try:
            if delay:
                time.sleep(delay)
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.server.client_disconnected = True
            return False

    def _begin(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

    def handle(self) -> None:  # garde-fou : erreur de boucle ne tue pas le thread
        try:
            super().handle()
        except OSError:
            pass

    def do_GET(self) -> None:
        if self.path == "/stream":
            self._begin()
            for chunk in ('{"n":1}\r\n', '{"n":2}\r\n', '{"n":3}\r\n', "\r\n"):
                self._write_chunk(chunk, delay=0.05)
        elif self.path == "/slow":
            self._begin()
            self._write_chunk('{"n":1}\r\n')
            time.sleep(1.5)
            self._write_chunk('{"n":2}\r\n')
        elif self.path == "/caught":
            self._begin()
            self._write_chunk('{"n":1}\r\n')
            time.sleep(0.25)
            # Après la fermeture du client (FIN), une lecture renvoie EOF :
            # c'est le signe fiable que la connexion a été refermée côté client.
            try:
                self.connection.settimeout(2.0)
                if self.rfile.read(1) == b"":
                    self.server.client_closed = True
            except OSError:
                self.server.client_closed = True
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/stream":
            self._begin()
            for chunk in ('{"echo":"ok"}\r\n', "data: [DONE]\r\n"):
                self._write_chunk(chunk, delay=0.05)
        elif self.path == "/error":
            self.send_error(500)
        else:
            self.send_error(404)


class HttpLineStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TrackerHandler)
        self.server.client_disconnected = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()

    def test_streams_multiple_lines_in_order(self) -> None:
        lines = list(http_line_stream(f"{self.base}/stream", method="GET", timeout=5))
        self.assertEqual(lines, ['{"n":1}', '{"n":2}', '{"n":3}'])

    def test_post_with_json_body_and_done_marker(self) -> None:
        lines = list(http_line_stream(
            f"{self.base}/stream", method="POST",
            headers={"X-A": "b"}, body={"q": "hello"}, timeout=5))
        self.assertEqual(lines, ['{"echo":"ok"}', "data: [DONE]"])

    def test_returns_a_generator(self) -> None:
        gen = http_line_stream(f"{self.base}/stream", method="GET")
        self.assertTrue(hasattr(gen, "__next__"))
        self.assertTrue(hasattr(gen, "close"))
        gen.close()

    def test_http_error_propagates(self) -> None:
        with self.assertRaises(urllib.error.HTTPError):
            list(http_line_stream(f"{self.base}/error", method="POST",
                                  body="x", timeout=5))

    def test_timeout_raises(self) -> None:
        with self.assertRaises((urllib.error.URLError, TimeoutError, OSError)):
            list(http_line_stream(f"{self.base}/slow", timeout=0.3))

    def test_early_close_releases_the_connection(self) -> None:
        gen = http_line_stream(f"{self.base}/caught", method="GET", timeout=5)
        self.assertEqual(next(gen), '{"n":1}')
        gen.close()  # abandon volontaire du stream
        time.sleep(0.8)  # laisse le serveur détecter la fermeture (EOF)
        self.assertTrue(self.server.client_closed,
                        "la connexion est restée ouverte côté client après close()")

    def test_scalar_body_payload(self) -> None:
        lines = list(http_line_stream(f"{self.base}/stream", method="POST",
                                      body="bonjour", timeout=5))
        self.assertIn('{"echo":"ok"}', lines)


if __name__ == "__main__":
    unittest.main()