from __future__ import annotations

import time

from .protocol import Task
from .queue import TaskQueue
from .registry import WorkerRegistry
from .scheduler import Scheduler
from .executor import DistributedExecutor


def main():
    registry = WorkerRegistry()

    print("\nVELKO — vérification du cluster...")
    registry.healthcheck_all()

    online = registry.online_workers()

    print(
        f"Workers disponibles : "
        f"{', '.join(w.worker_id for w in online)}"
    )

    queue = TaskQueue()

    prompts = [
        (
            "Analyse en 5 points les avantages d'une architecture "
            "logicielle distribuée. Réponse concise."
        ),
        (
            "Écris un exemple Python très court d'une file de tâches "
            "thread-safe. Réponse concise."
        ),
        (
            "Donne 5 tests importants pour un scheduler distribué. "
            "Réponse concise."
        ),
        (
            "Explique en 5 points comment détecter un worker défaillant "
            "dans un cluster. Réponse concise."
        ),
        (
            "Donne 5 règles pour éviter les conflits Git lorsque "
            "plusieurs agents travaillent en parallèle. Réponse concise."
        ),
        (
            "Explique brièvement le principe du work stealing dans "
            "un système distribué. Réponse concise."
        ),
    ]

    for prompt in prompts:
        queue.add(
            Task(
                prompt=prompt,
                required_capabilities=set(),
            )
        )

    scheduler = Scheduler(
        registry,
        queue,
    )

    executor = DistributedExecutor(
        registry,
        queue,
        scheduler,
    )

    print("\n=== MISSION DISTRIBUÉE ===\n")

    started = time.perf_counter()
    stats = executor.run()
    duration = time.perf_counter() - started

    print("\n=== RÉSULTAT ===")

    for worker_id, count in stats.items():
        print(
            f"{worker_id:<10} : "
            f"{count} tâche(s)"
        )

    print(
        f"\nTemps global : {duration:.2f}s"
    )

    print(
        f"Tâches terminées : "
        f"{sum(stats.values())}/{len(queue.tasks)}"
    )

    print("\n=== DISTRIBUTION ===")

    for task in queue.tasks.values():
        result = task.result

        worker = (
            result.worker_id
            if result
            else "NONE"
        )

        print(
            f"{task.task_id[:8]} "
            f"→ {worker:<10} "
            f"[{task.status.value}]"
        )


if __name__ == "__main__":
    main()
