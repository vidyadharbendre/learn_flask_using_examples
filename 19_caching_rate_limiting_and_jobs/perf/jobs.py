"""
Day 19 — Work that must not happen inside a request.
====================================================

The rule
--------
**A request should do one thing and return.** Anything slow, retryable, or not
needed for the response belongs elsewhere: sending email, generating a PDF,
resizing an image, calling three partner APIs, rebuilding a report.

Why it matters concretely: a WSGI worker is **occupied** for the whole duration
of a request. With four workers and a view that takes ten seconds, you can serve
0.4 requests per second, and the fifth visitor waits. Moving that work off the
request path is usually the single largest performance win available.

What this module is, and is not
-------------------------------
This is a **teaching implementation** using a thread pool. It is honest about
its limits, which are exactly the reasons real systems use Celery or RQ:

=========================  ==================  =============================
                           This thread pool    A real queue (Celery / RQ)
=========================  ==================  =============================
Survives a restart         ❌ jobs are lost    ✅ stored in Redis/RabbitMQ
Retries on failure         ❌                  ✅ with backoff
Scales past one process    ❌                  ✅ many workers, many machines
Scheduling / cron          ❌                  ✅
Visibility                 in-memory only      dashboards, dead-letter queues
Extra infrastructure       none                a broker to run and monitor
=========================  ==================  =============================

Use threads for genuinely fire-and-forget work in a small app. Use a real queue
the moment losing a job would matter — and "did the receipt email actually get
sent?" is a question you will be asked.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

JobStatus = Literal["queued", "running", "done", "failed"]


@dataclass
class Job:
    """A unit of background work and its outcome.

    Attributes:
        id: Opaque identifier the client polls with.
        name: What kind of work this is.
        status: Current state.
        result: The return value, once finished.
        error: The failure message, if it failed.
        queued_at / started_at / finished_at: Timestamps.
    """

    id: str
    name: str
    status: JobStatus = "queued"
    result: Any = None
    error: str = ""
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation, including elapsed time.
        """
        elapsed = None
        if self.started_at:
            end = self.finished_at or time.time()
            elapsed = round(end - self.started_at, 2)

        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "elapsed_s": elapsed,
            "queued_at": datetime.fromtimestamp(self.queued_at, tz=timezone.utc).isoformat(),
        }


class JobRunner:
    """A minimal background-job runner backed by a thread pool.

    Attributes:
        jobs: Every job submitted in this process's lifetime.
    """

    def __init__(self, max_workers: int = 2) -> None:
        """Create the runner.

        Args:
            max_workers: How many jobs may run concurrently.

        Note:
            The pool is **bounded**. An unbounded ``threading.Thread(...)``
            per request means a traffic spike spawns thousands of threads and
            the process dies of memory exhaustion — the failure mode that makes
            naive "just use a thread" advice dangerous. A bounded pool queues
            instead, which degrades gracefully.
        """
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="job"
        )
        self._lock = threading.Lock()
        self.jobs: dict[str, Job] = {}

    def submit(self, name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Job:
        """Queue a callable and return immediately.

        Args:
            name: A label for the work.
            func: The callable to run.
            *args: Positional arguments for ``func``.
            **kwargs: Keyword arguments for ``func``.

        Returns:
            Job: The job record, already registered so the client can poll it.

        Note:
            The response goes out in milliseconds while the work continues in
            the background. This is the **202 Accepted** pattern: "I have taken
            responsibility for this; here is where to check on it."
        """
        job = Job(id=uuid.uuid4().hex[:12], name=name)
        with self._lock:
            self.jobs[job.id] = job

        self._executor.submit(self._run, job, func, *args, **kwargs)
        return job

    def _run(self, job: Job, func: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Execute one job, recording its outcome.

        Args:
            job: The job record to update.
            func: The callable to run.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Note:
            The blanket ``except Exception`` is correct **here** and almost
            nowhere else. An exception escaping a pool worker is swallowed by
            the executor: the job would appear stuck in ``running`` forever with
            no trace anywhere. Catching it and recording ``failed`` is what
            makes the failure visible.

            Note also that background code has **no request context**. It cannot
            touch ``request``, ``session`` or ``g``; anything it needs must be
            passed in as an argument. Code that needs the database must push an
            application context of its own.
        """
        job.status = "running"
        job.started_at = time.time()
        try:
            job.result = func(*args, **kwargs)
            job.status = "done"
        except Exception as error:  # noqa: BLE001 - must not vanish
            job.status = "failed"
            job.error = f"{type(error).__name__}: {error}"
        finally:
            job.finished_at = time.time()

    def get(self, job_id: str) -> Job | None:
        """Look up a job.

        Args:
            job_id: The identifier returned by :meth:`submit`.

        Returns:
            Job | None: The job, or ``None`` when unknown.
        """
        with self._lock:
            return self.jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        """Return the most recently queued jobs.

        Args:
            limit: How many to return.

        Returns:
            list[Job]: Newest first.
        """
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j.queued_at, reverse=True)[:limit]


runner = JobRunner(max_workers=2)


def build_report(cities: list[str]) -> dict[str, Any]:
    """Assemble a multi-city report — the kind of work that must not block.

    Args:
        cities: Cities to include.

    Returns:
        dict[str, Any]: The finished report.

    Note:
        Sequentially this costs ``len(cities) × 350ms``. Inside a request that
        is unacceptable; as a background job the user gets an id immediately and
        polls for the result.
    """
    from .upstream import fetch_weather

    rows = [fetch_weather(city) for city in cities]
    return {
        "cities": len(rows),
        "average_temperature_c": round(
            sum(row["temperature_c"] for row in rows) / len(rows), 1
        ) if rows else 0,
        "rows": rows,
    }
