from .registry import WorkerRegistry
from .protocol import WorkerStatus


def main():
    registry = WorkerRegistry()

    print()
    print("=" * 68)
    print("VELKO DISTRIBUTED CLUSTER — HEALTH CHECK")
    print("=" * 68)

    workers = registry.healthcheck_all()

    online = 0

    for worker in workers:
        print()

        if worker.status == WorkerStatus.ONLINE:
            online += 1
            marker = "PASS"
        else:
            marker = "FAIL"

        print(
            f"[{marker}] "
            f"{worker.worker_id} — {worker.name}"
        )

        print(
            f"       status   : {worker.status.value}"
        )

        print(
            f"       endpoint : {worker.ollama_url}"
        )

        print(
            f"       model    : {worker.model}"
        )

        if worker.latency_ms is not None:
            print(
                f"       latency  : "
                f"{worker.latency_ms:.1f} ms"
            )

        if worker.error:
            print(
                f"       error    : {worker.error}"
            )

    print()
    print("-" * 68)
    print(
        f"CLUSTER STATUS : "
        f"{online}/{len(workers)} WORKERS ONLINE"
    )
    print("-" * 68)
    print()


if __name__ == "__main__":
    main()
