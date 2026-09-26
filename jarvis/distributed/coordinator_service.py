from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict


HOST = "0.0.0.0"
PORT = 8765
HEARTBEAT_TIMEOUT = 30.0


class ClusterState:
    def __init__(self):
        self.lock = threading.RLock()
        self.workers: Dict[str, Dict[str, Any]] = {}

    def register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        worker_id = payload["worker_id"]

        with self.lock:
            existing = self.workers.get(worker_id, {})

            self.workers[worker_id] = {
                **existing,
                **payload,
                "status": "online",
                "registered_at": existing.get(
                    "registered_at",
                    time.time(),
                ),
                "last_heartbeat": time.time(),
            }

            return dict(self.workers[worker_id])

    def heartbeat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        worker_id = payload["worker_id"]

        with self.lock:
            if worker_id not in self.workers:
                return {
                    "ok": False,
                    "error": "worker_not_registered",
                }

            worker = self.workers[worker_id]

            worker["last_heartbeat"] = time.time()
            worker["status"] = payload.get(
                "status",
                worker.get("status", "online"),
            )

            if "load" in payload:
                worker["load"] = payload["load"]

            return {
                "ok": True,
                "worker_id": worker_id,
            }

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()

        with self.lock:
            workers = {}

            for worker_id, worker in self.workers.items():
                item = dict(worker)

                age = now - item["last_heartbeat"]

                if age > HEARTBEAT_TIMEOUT:
                    item["status"] = "offline"

                item["heartbeat_age"] = round(age, 2)
                workers[worker_id] = item

            return {
                "leader": "m4-local",
                "workers": workers,
            }


STATE = ClusterState()


class Handler(BaseHTTPRequestHandler):

    def _send(
        self,
        status: int,
        payload: Dict[str, Any],
    ) -> None:

        body = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(
            self.headers.get("Content-Length", "0")
        )

        if length <= 0:
            return {}

        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "service": "velko-coordinator",
                    "leader": "m4-local",
                },
            )
            return

        if self.path == "/workers":
            self._send(
                200,
                STATE.snapshot(),
            )
            return

        self._send(
            404,
            {"error": "not_found"},
        )

    def do_POST(self) -> None:
        try:
            payload = self._read_json()

            if self.path == "/register":
                required = {
                    "worker_id",
                    "hostname",
                    "os",
                    "capabilities",
                }

                missing = required - set(payload)

                if missing:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "missing_fields",
                            "fields": sorted(missing),
                        },
                    )
                    return

                worker = STATE.register(payload)

                self._send(
                    200,
                    {
                        "ok": True,
                        "worker": worker,
                    },
                )
                return

            if self.path == "/heartbeat":
                result = STATE.heartbeat(payload)

                self._send(
                    200 if result.get("ok") else 404,
                    result,
                )
                return

            self._send(
                404,
                {"error": "not_found"},
            )

        except Exception as exc:
            self._send(
                500,
                {
                    "ok": False,
                    "error": str(exc),
                },
            )

    def log_message(self, format, *args):
        print(
            "[HTTP]",
            self.address_string(),
            format % args,
            flush=True,
        )


def main() -> None:
    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler,
    )

    print("=" * 64)
    print("VELKO DISTRIBUTED COORDINATOR")
    print("=" * 64)
    print(f"Local    : http://127.0.0.1:{PORT}")
    print(f"Tailscale: http://100.120.13.54:{PORT}")
    print("CTRL+C pour arrêter")
    print("=" * 64)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du coordinateur...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
