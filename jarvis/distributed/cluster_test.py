"""Autonomous validation campaign for a running VELKO distributed cluster."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class ClusterClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_task(self, task_id: str, timeout: float) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            tasks = self.request("GET", "/tasks").get("tasks", [])
            task = next((item for item in tasks if item["task_id"] == task_id), None)
            if task and task["status"] in {"done", "failed"}:
                return task
            time.sleep(0.25)
        raise TimeoutError("task_timeout:" + task_id)

    def wait_claim(self, task_id: str, worker_id: str, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = self.request("GET", "/events?limit=500").get("events", [])
            if any(event.get("event") == "TASK_CLAIMED" and event.get("task_id") == task_id and event.get("worker_id") == worker_id for event in events):
                return
            time.sleep(0.25)
        raise TimeoutError("claim_timeout:{}:{}".format(task_id, worker_id))

    def control(self, worker_id: str, task_id: str, action: str) -> Dict[str, Any]:
        return self.request("POST", "/test/control", {
            "test_mode": True,
            "worker_id": worker_id,
            "task_id": task_id,
            "action": action,
        })


class Campaign:
    def __init__(self, client: ClusterClient, timeout: float, fault_injection: bool = False):
        self.client = client
        self.timeout = timeout
        self.results: List[Tuple[str, bool, str]] = []
        self.started_at = time.time()
        self.fault_injection = fault_injection

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.results.append((name, condition, detail if not condition else ""))
        suffix = " — " + detail if detail and not condition else ""
        print("{:<32} {}{}".format(name, "PASS" if condition else "FAIL", suffix))

    def task(self, prompt: str, metadata: Optional[Dict[str, Any]] = None, dependencies: Optional[List[str]] = None) -> Dict[str, Any]:
        return self.client.request("POST", "/tasks", {
            "prompt": prompt,
            "metadata": metadata or {},
            "dependencies": dependencies or [],
        })["task"]

    def run(self) -> int:
        print("=" * 68)
        print("             VELKO DISTRIBUTED CLUSTER TEST")
        print("=" * 68)
        try:
            health = self.client.request("GET", "/health")
            self.check("Coordinator health", bool(health.get("ok")))
            status = self.client.request("GET", "/status")
            workers = status.get("workers", {})
            online = [worker for worker in workers.values() if worker.get("status") in {"online", "busy"}]
            self.check("Worker registration", bool(online), "no registered worker")
            self.check("Worker heartbeat", all(worker.get("heartbeat_age", 999) < 35 for worker in online), "stale heartbeat")
            self.check("Capability discovery", all(worker.get("capabilities") for worker in online), "missing capabilities")
            protocol_ready = all(worker.get("worker_version", "0") >= "1.1" for worker in online)
            self.check("Worker protocol", protocol_ready, "legacy workers supported by coordinator-side control")
            if not online:
                self.check("Distributed campaign", False, "no workers; real multi-machine tests are PENDING")
                return self.finish()

            basic = self.task("Reply with the word READY.")
            done = self.client.wait_task(basic["task_id"], self.timeout)
            self.check("Task creation", True)
            self.check("Task claim", done.get("attempts", 0) >= 1, "no claim evidence")
            self.check("Ollama execution", done.get("status") == "done", done.get("error", ""))

            parallel = [self.task("Reply READY after a short computation.") for _ in range(max(2, len(online) * 2))]
            parallel_done = [self.client.wait_task(item["task_id"], self.timeout) for item in parallel]
            worker_ids = {item.get("result", {}).get("worker_id") for item in parallel_done if isinstance(item.get("result"), dict)}
            self.check("Parallel execution", all(item["status"] == "done" for item in parallel_done), "one or more tasks failed")
            self.check("Dynamic pull", len(worker_ids) >= min(2, len(online)), "fewer workers observed than available")

            first = self.task("DAG A")
            second = self.task("DAG B")
            final = self.task("DAG C", dependencies=[first["task_id"], second["task_id"]])
            self.client.wait_task(first["task_id"], self.timeout)
            self.client.wait_task(second["task_id"], self.timeout)
            final_done = self.client.wait_task(final["task_id"], self.timeout)
            self.check("Dependencies / DAG", final_done["status"] == "done")

            events = self.client.request("GET", "/events?since={}".format(self.started_at)).get("events", [])
            names = {event["event"] for event in events}
            self.check("Lease renewal", "LEASE_RENEWED" in names, "no renewal evidence")
            claim_count = sum(event["event"] == "TASK_CLAIMED" and event.get("task_id") == basic["task_id"] for event in events)
            completion_count = sum(event["event"] == "TASK_COMPLETED" and event.get("task_id") == basic["task_id"] for event in events)
            self.check("Duplicate claim guard", claim_count == 1, "claim count={}".format(claim_count))
            self.check("No lost tasks", all(item["status"] in {"done", "failed"} for item in parallel_done + [final_done]))
            self.check("No duplicated completion", completion_count == 1, "completion count={}".format(completion_count))
            if self.fault_injection and {"rtx3080", "gtx1080"}.issubset(workers):
                self.run_failover("rtx3080", "gtx1080")
                self.run_failover("gtx1080", "rtx3080")
            else:
                for name in ("Transient retry", "Worker failure", "Lease expiration", "Automatic requeue", "RTX -> GTX failover", "GTX -> RTX failover"):
                    self.check(name, False, "PENDING: requires --fault-injection and both Windows hosts")
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            self.check("Campaign execution", False, str(exc))
        return self.finish()

    def run_failover(self, source: str, target: str) -> None:
        task = self.task("Failover probe from {} to {}".format(source, target), metadata={
            "test_mode": True,
            "test_control_required": True,
            "test_fault_seconds": 35,
            "test_preferred_worker": source,
        })
        self.client.wait_claim(task["task_id"], source, self.timeout)
        self.client.control(source, task["task_id"], "drop_lease")
        self.client.wait_task(task["task_id"], self.timeout)
        events = self.client.request("GET", "/events?since={}".format(self.started_at)).get("events", [])
        task_events = [event for event in events if event.get("task_id") == task["task_id"]]
        claimed_by = [event.get("worker_id") for event in task_events if event["event"] == "TASK_CLAIMED"]
        has_expiration = any(event["event"] == "LEASE_EXPIRED" for event in task_events)
        has_requeue = any(event["event"] == "TASK_REQUEUED" for event in task_events)
        self.check("Worker failure", source in claimed_by)
        self.check("Lease expiration", has_expiration)
        self.check("Automatic requeue", has_requeue)
        failover = source in claimed_by and target in claimed_by and claimed_by.index(target) > claimed_by.index(source)
        self.check("{} -> {} failover".format(source.upper(), target.upper()), failover)
        self.check("Transient retry", any(event["event"] == "TASK_RETRY" for event in task_events))

    def finish(self) -> int:
        passed = sum(1 for _, ok, _ in self.results if ok)
        total = len(self.results)
        print("\nTOTAL\n{}/{} PASS".format(passed, total))
        print("CLUSTER READY FOR VELKO OPERATOR" if passed == total and total else "CLUSTER VALIDATION INCOMPLETE")
        return 0 if passed == total and total else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the VELKO distributed cluster validation campaign")
    parser.add_argument("--coordinator", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--no-fault-injection", action="store_true", help="Skip the explicit remote failover probes.")
    args = parser.parse_args(argv)
    return Campaign(ClusterClient(args.coordinator, min(args.timeout, 30.0)), args.timeout, not args.no_fault_injection).run()


if __name__ == "__main__":
    sys.exit(main())
