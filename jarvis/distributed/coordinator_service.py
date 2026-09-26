from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from .event_log import EventLog
from .task_store import TaskStore


HOST = "0.0.0.0"
PORT = 8765
HEARTBEAT_TIMEOUT = 30.0

EVENTS = EventLog()
TASKS = TaskStore(
    lease_seconds=30.0,
    max_attempts=3,
    event_sink=EVENTS.append,
)


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
                "current_task_id": existing.get("current_task_id"),
                "current_task_started_at": existing.get("current_task_started_at"),
                "completed_tasks": existing.get("completed_tasks", 0),
                "failed_tasks": existing.get("failed_tasks", 0),
                "last_error": existing.get("last_error"),
            }

            EVENTS.append("WORKER_REGISTERED", worker_id=worker_id, details={"hostname": payload.get("hostname")})
            EVENTS.append("WORKER_ONLINE", worker_id=worker_id)

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
            for field in ("current_task_id", "current_task_started_at", "current_attempt", "lease_expires_at", "last_error"):
                if field in payload:
                    worker[field] = payload[field]
            EVENTS.append("HEARTBEAT", worker_id=worker_id, details={"status": worker["status"]})

            return {
                "ok": True,
                "worker_id": worker_id,
            }

    def get_worker(self, worker_id: str) -> Dict[str, Any]:
        with self.lock:
            worker = self.workers.get(worker_id)
            return dict(worker) if worker else {}

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()

        with self.lock:
            worker_items = [(worker_id, dict(worker)) for worker_id, worker in self.workers.items()]

        workers = {}
        for worker_id, item in worker_items:

            age = now - item["last_heartbeat"]

            if age > HEARTBEAT_TIMEOUT:
                was_offline = item.get("status") == "offline"
                item["status"] = "offline"
                if not was_offline:
                    EVENTS.append(
                        "WORKER_OFFLINE",
                        worker_id=worker_id,
                        details={"heartbeat_age": round(age, 2)},
                    )

            item["heartbeat_age"] = round(age, 2)
            item["current_task"] = item.get("current_task_id")
            task_id = item.get("current_task_id")
            if task_id:
                task = TASKS.get(task_id)
                if task:
                    item["current_task"] = task
                    item["lease_remaining"] = max(0.0, (task.get("lease_expires_at") or now) - now)
                    item["task_duration"] = max(0.0, now - (task.get("started_at") or now))
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
        payload: Any,
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
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send(
                200,
                {
                    "ok": True,
                    "service": "velko-coordinator",
                    "leader": "m4-local",
                },
            )
            return

        if path == "/status":
            snapshot = STATE.snapshot()
            snapshot["tasks"] = TASKS.list()
            snapshot["event_count"] = len(EVENTS.recent(limit=5000))
            self._send(200, {"ok": True, **snapshot})
            return

        if path == "/events":
            params = parse_qs(parsed.query)
            since = params.get("since", [None])[0]
            limit = int(params.get("limit", [500])[0])
            self._send(200, {"ok": True, "events": EVENTS.recent(float(since) if since else None, limit)})
            return

        if path == "/workers":
            self._send(
                200,
                STATE.snapshot(),
            )
            return

        if path == "/tasks":
            self._send(
                200,
                {
                    "ok": True,
                    "tasks": TASKS.list(),
                },
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

            if self.path == "/tasks":
                prompt = payload.get("prompt")

                if not prompt:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "prompt_required",
                        },
                    )
                    return

                task = TASKS.create(
                    prompt=prompt,
                    required_capabilities=payload.get(
                        "required_capabilities",
                        [],
                    ),
                    dependencies=payload.get(
                        "dependencies",
                        [],
                    ),
                    metadata=payload.get(
                        "metadata",
                        {},
                    ),
                )

                self._send(
                    201,
                    {
                        "ok": True,
                        "task": task,
                    },
                )
                return

            if self.path == "/claim":
                worker_id = payload.get("worker_id")

                if not worker_id:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "worker_id_required",
                        },
                    )
                    return

                worker = STATE.get_worker(worker_id)

                if not worker:
                    self._send(
                        404,
                        {
                            "ok": False,
                            "error": "worker_not_registered",
                        },
                    )
                    return

                task = TASKS.claim(
                    worker_id=worker_id,
                    capabilities=worker.get(
                        "capabilities",
                        [],
                    ),
                )

                if task:
                    with STATE.lock:
                        worker = STATE.workers.get(worker_id)
                        if worker:
                            worker["current_task_id"] = task["task_id"]
                            worker["current_task_started_at"] = task.get("started_at")
                            worker["current_attempt"] = task.get("attempts")
                            worker["lease_expires_at"] = task.get("lease_expires_at")
                            worker["status"] = "busy"

                self._send(
                    200,
                    {
                        "ok": True,
                        "task": task,
                    },
                )
                return

            if self.path == "/renew":
                task_id = payload.get("task_id")
                worker_id = payload.get("worker_id")

                if not task_id or not worker_id:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "task_id_and_worker_id_required",
                        },
                    )
                    return

                renewed = TASKS.renew(
                    task_id,
                    worker_id,
                )

                self._send(
                    200 if renewed else 409,
                    {
                        "ok": renewed,
                        "task_id": task_id,
                    },
                )
                return

            if self.path == "/progress":
                task_id = payload.get("task_id")
                worker_id = payload.get("worker_id")

                if not task_id or not worker_id:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "task_id_and_worker_id_required",
                        },
                    )
                    return

                updated = TASKS.progress(
                    task_id,
                    worker_id,
                    payload.get("progress"),
                )

                self._send(
                    200 if updated else 409,
                    {
                        "ok": updated,
                        "task_id": task_id,
                    },
                )
                return

            if self.path == "/complete":
                task_id = payload.get("task_id")
                worker_id = payload.get("worker_id")

                if not task_id or not worker_id:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "task_id_and_worker_id_required",
                        },
                    )
                    return

                completed = TASKS.complete(
                    task_id,
                    worker_id,
                    payload.get("result"),
                )

                if completed:
                    with STATE.lock:
                        worker = STATE.workers.get(worker_id)
                        if worker:
                            worker["current_task_id"] = None
                            worker["current_task_started_at"] = None
                            worker["completed_tasks"] = worker.get("completed_tasks", 0) + 1
                            worker["status"] = "online"

                self._send(
                    200 if completed else 409,
                    {
                        "ok": completed,
                        "task_id": task_id,
                    },
                )
                return

            if self.path == "/failed":
                task_id = payload.get("task_id")
                worker_id = payload.get("worker_id")

                if not task_id or not worker_id:
                    self._send(
                        400,
                        {
                            "ok": False,
                            "error": "task_id_and_worker_id_required",
                        },
                    )
                    return

                error = str(payload.get("error", "worker_reported_failure"))
                error_type = str(payload.get("error_type", "TERMINAL")).upper()
                if error_type not in {"TRANSIENT", "TERMINAL"}:
                    error_type = "TERMINAL"
                failed = TASKS.fail(
                    task_id,
                    worker_id,
                    error,
                    error_type=error_type,
                )
                if failed:
                    with STATE.lock:
                        worker = STATE.workers.get(worker_id)
                        if worker:
                            worker["current_task_id"] = None
                            worker["current_task_started_at"] = None
                            worker["failed_tasks"] = worker.get("failed_tasks", 0) + 1
                            worker["last_error"] = error
                            worker["status"] = "online"

                self._send(
                    200 if failed else 409,
                    {
                        "ok": failed,
                        "task_id": task_id,
                    },
                )
                return

            self._send(
                404,
                {"error": "not_found"},
            )

        except json.JSONDecodeError:
            self._send(
                400,
                {
                    "ok": False,
                    "error": "invalid_json",
                },
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
    print()
    print("Task API : enabled")
    print(f"Lease    : {TASKS.lease_seconds:.0f}s")
    print(f"Retries  : {TASKS.max_attempts}")
    print()
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
