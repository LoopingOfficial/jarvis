from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List


DEFAULT_COORDINATOR = "http://100.120.13.54:8765"
DEFAULT_HEARTBEAT_INTERVAL = 5.0


class VelkoWorker:
    def __init__(
        self,
        worker_id: str,
        coordinator: str,
        capabilities: List[str],
        model: str,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
    ):
        self.worker_id = worker_id
        self.coordinator = coordinator.rstrip("/")
        self.capabilities = capabilities
        self.model = model
        self.heartbeat_interval = heartbeat_interval
        self.hostname = socket.gethostname()
        self.os_name = platform.system().lower()
        self.running = True

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            self.coordinator + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def register(self) -> None:
        payload = {
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "os": self.os_name,
            "capabilities": self.capabilities,
            "model": self.model,
            "python": platform.python_version(),
            "pid": os.getpid(),
        }

        result = self._post("/register", payload)

        if not result.get("ok"):
            raise RuntimeError("registration_failed")

        print(
            f"[REGISTERED] {self.worker_id} "
            f"hostname={self.hostname} "
            f"os={self.os_name} "
            f"model={self.model}",
            flush=True,
        )

    def heartbeat(self) -> None:
        result = self._post(
            "/heartbeat",
            {
                "worker_id": self.worker_id,
                "status": "online",
                "load": 0,
            },
        )

        if not result.get("ok"):
            raise RuntimeError(
                result.get("error", "heartbeat_failed")
            )

    def heartbeat_loop(self) -> None:
        while self.running:
            try:
                self.heartbeat()
                print(
                    f"[HEARTBEAT] {self.worker_id} OK",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"[HEARTBEAT ERROR] {exc}",
                    flush=True,
                )

                try:
                    self.register()
                except Exception as register_exc:
                    print(
                        f"[REGISTER RETRY ERROR] {register_exc}",
                        flush=True,
                    )

            time.sleep(self.heartbeat_interval)

    def run(self) -> None:
        while self.running:
            try:
                self.register()
                break
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                RuntimeError,
            ) as exc:
                print(
                    f"[REGISTER RETRY] {exc}",
                    flush=True,
                )
                time.sleep(5)

        thread = threading.Thread(
            target=self.heartbeat_loop,
            daemon=True,
        )
        thread.start()

        print(
            f"[READY] {self.worker_id} attend des missions.",
            flush=True,
        )

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.running = False
            print(
                f"\n[STOP] {self.worker_id}",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VELKO distributed worker service"
    )

    parser.add_argument(
        "--worker-id",
        required=True,
    )

    parser.add_argument(
        "--coordinator",
        default=DEFAULT_COORDINATOR,
    )

    parser.add_argument(
        "--model",
        default="qwen3.5:9b",
    )

    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        default=[],
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    worker = VelkoWorker(
        worker_id=args.worker_id,
        coordinator=args.coordinator,
        capabilities=args.capabilities,
        model=args.model,
    )

    worker.run()


if __name__ == "__main__":
    main()
