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
from typing import Any, Dict, List, Optional


DEFAULT_COORDINATOR = "http://100.120.13.54:8765"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_HEARTBEAT_INTERVAL = 5.0
DEFAULT_CLAIM_INTERVAL = 2.0
DEFAULT_RENEW_INTERVAL = 10.0


class VelkoWorker:
    def __init__(
        self,
        worker_id: str,
        coordinator: str,
        capabilities: List[str],
        model: str,
        ollama_url: str = DEFAULT_OLLAMA,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        claim_interval: float = DEFAULT_CLAIM_INTERVAL,
        renew_interval: float = DEFAULT_RENEW_INTERVAL,
    ):
        self.worker_id = worker_id
        self.coordinator = coordinator.rstrip("/")
        self.capabilities = capabilities
        self.model = model
        self.ollama_url = ollama_url.rstrip("/")
        self.heartbeat_interval = heartbeat_interval
        self.claim_interval = claim_interval
        self.renew_interval = renew_interval

        self.hostname = socket.gethostname()
        self.os_name = platform.system().lower()

        self.running = True
        self.busy = False

    def _post(
        self,
        path: str,
        payload: Dict[str, Any],
        timeout: float = 10.0,
    ) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            self.coordinator + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )

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
                "status": (
                    "busy"
                    if self.busy
                    else "online"
                ),
                "load": (
                    1
                    if self.busy
                    else 0
                ),
            },
        )

        if not result.get("ok"):
            raise RuntimeError(
                result.get(
                    "error",
                    "heartbeat_failed",
                )
            )

    def heartbeat_loop(self) -> None:
        while self.running:
            try:
                self.heartbeat()

                print(
                    f"[HEARTBEAT] {self.worker_id} "
                    f"{'BUSY' if self.busy else 'OK'}",
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
                        "[REGISTER RETRY ERROR] "
                        f"{register_exc}",
                        flush=True,
                    )

            time.sleep(
                self.heartbeat_interval
            )

    def claim_task(
        self,
    ) -> Optional[Dict[str, Any]]:
        result = self._post(
            "/claim",
            {
                "worker_id": self.worker_id,
            },
        )

        if not result.get("ok"):
            raise RuntimeError(
                result.get(
                    "error",
                    "claim_failed",
                )
            )

        task = result.get("task")

        if not task:
            return None

        print(
            f"[CLAIM] {task['task_id']} "
            f"attempt={task.get('attempts')}",
            flush=True,
        )

        return task

    def renew_task(
        self,
        task_id: str,
    ) -> bool:
        result = self._post(
            "/renew",
            {
                "worker_id": self.worker_id,
                "task_id": task_id,
            },
        )

        return bool(result.get("ok"))

    def renew_loop(
        self,
        task_id: str,
        stop_event: threading.Event,
    ) -> None:
        while (
            self.running
            and not stop_event.wait(
                self.renew_interval
            )
        ):
            try:
                renewed = self.renew_task(
                    task_id
                )

                if not renewed:
                    print(
                        f"[LEASE LOST] {task_id}",
                        flush=True,
                    )
                    return

                print(
                    f"[RENEW] {task_id}",
                    flush=True,
                )

            except Exception as exc:
                print(
                    f"[RENEW ERROR] "
                    f"{task_id}: {exc}",
                    flush=True,
                )

    def execute_ollama(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        started = time.time()

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": task["prompt"],
                }
            ],
            "stream": False,
            "think": False,
            "keep_alive": "10m",
        }

        request = urllib.request.Request(
            self.ollama_url + "/api/chat",
            data=json.dumps(
                payload
            ).encode("utf-8"),
            headers={
                "Content-Type":
                    "application/json"
            },
            method="POST",
        )

        print(
            f"[OLLAMA] {task['task_id']} "
            f"model={self.model}",
            flush=True,
        )

        with urllib.request.urlopen(
            request,
            timeout=1800,
        ) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )

        message = data.get(
            "message",
            {},
        )

        content = message.get(
            "content",
            "",
        )

        duration = time.time() - started

        eval_count = data.get(
            "eval_count"
        )

        eval_duration = data.get(
            "eval_duration"
        )

        tokens_per_second = None

        if (
            eval_count is not None
            and eval_duration
        ):
            tokens_per_second = (
                eval_count
                / (
                    eval_duration
                    / 1_000_000_000
                )
            )

        return {
            "content": content,
            "model": data.get(
                "model",
                self.model,
            ),
            "worker_id": self.worker_id,
            "hostname": self.hostname,
            "duration_seconds": round(
                duration,
                3,
            ),
            "eval_count": eval_count,
            "tokens_per_second": (
                round(
                    tokens_per_second,
                    2,
                )
                if tokens_per_second
                is not None
                else None
            ),
        }

    def complete_task(
        self,
        task_id: str,
        result: Dict[str, Any],
    ) -> None:
        response = self._post(
            "/complete",
            {
                "worker_id": self.worker_id,
                "task_id": task_id,
                "result": result,
            },
        )

        if not response.get("ok"):
            raise RuntimeError(
                "task_complete_rejected"
            )

        print(
            f"[COMPLETE] {task_id}",
            flush=True,
        )

    def fail_task(
        self,
        task_id: str,
        error: str,
    ) -> None:
        response = self._post(
            "/failed",
            {
                "worker_id": self.worker_id,
                "task_id": task_id,
                "error": error,
            },
        )

        if not response.get("ok"):
            raise RuntimeError(
                "task_failure_rejected"
            )

        print(
            f"[FAILED] {task_id}: {error}",
            flush=True,
        )

    def execute_task(
        self,
        task: Dict[str, Any],
    ) -> None:
        task_id = task["task_id"]
        stop_event = threading.Event()

        renew_thread = threading.Thread(
            target=self.renew_loop,
            args=(
                task_id,
                stop_event,
            ),
            daemon=True,
        )

        self.busy = True
        renew_thread.start()

        try:
            result = self.execute_ollama(
                task
            )

            self.complete_task(
                task_id,
                result,
            )

        except Exception as exc:
            print(
                f"[TASK ERROR] "
                f"{task_id}: {exc}",
                flush=True,
            )

            try:
                self.fail_task(
                    task_id,
                    str(exc),
                )
            except Exception as fail_exc:
                print(
                    "[FAILED REPORT ERROR] "
                    f"{fail_exc}",
                    flush=True,
                )

        finally:
            stop_event.set()
            renew_thread.join(
                timeout=2
            )
            self.busy = False

    def work_loop(self) -> None:
        while self.running:
            try:
                task = self.claim_task()

                if task is None:
                    time.sleep(
                        self.claim_interval
                    )
                    continue

                self.execute_task(task)

            except urllib.error.HTTPError as exc:
                print(
                    f"[CLAIM HTTP ERROR] "
                    f"{exc.code} {exc.reason}",
                    flush=True,
                )
                time.sleep(
                    self.claim_interval
                )

            except Exception as exc:
                print(
                    f"[WORK ERROR] {exc}",
                    flush=True,
                )
                time.sleep(
                    self.claim_interval
                )

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

        heartbeat_thread = threading.Thread(
            target=self.heartbeat_loop,
            daemon=True,
        )
        heartbeat_thread.start()

        print(
            f"[READY] {self.worker_id} "
            "attend des missions.",
            flush=True,
        )

        try:
            self.work_loop()

        except KeyboardInterrupt:
            self.running = False

            print(
                f"\n[STOP] {self.worker_id}",
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "VELKO distributed worker service"
        )
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
        "--ollama-url",
        default=DEFAULT_OLLAMA,
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
        ollama_url=args.ollama_url,
    )

    worker.run()


if __name__ == "__main__":
    main()
