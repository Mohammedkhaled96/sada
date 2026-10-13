"""A queue of transcription jobs, so the user is never made to wait.

Before this there was one worker and one job. Cancelling set a flag and the next
request was refused until the cancelled job noticed and unwound, which can take a
while inside a model call — so cancelling appeared to lock the program.

Now submitting always succeeds. A cancelled job keeps unwinding in the
background while the next one waits its turn, and every job is listed with
exactly what it is doing.

Jobs run **one at a time on purpose**: transcription saturates the CPU and holds
the model's working memory, so two at once makes both slower and risks running
the machine out of RAM. Queued is honest; parallel would be a lie.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.i18n import t
from engine.job import Cancelled, Progress, run_transcription

# queued -> running -> done | cancelled | error
#                   \-> cancelling -> cancelled
ACTIVE_STATES = ("queued", "running", "cancelling")


@dataclass
class Job:
    id: int
    path: str
    language: str | None
    timestamps: bool
    hotwords: str | None
    insert_mode: str
    rtl_auto: bool
    state: str = "queued"
    progress: Progress = field(default_factory=Progress)
    meta: dict | None = None
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    inserted: bool = False

    @property
    def filename(self) -> str:
        return Path(self.path).name

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def percent(self) -> float:
        if self.state == "done":
            return 100.0
        return self.progress.percent


class JobManager:
    """Owns the queue and the single worker thread.

    `on_change(job)` fires on every state or progress change, and `on_finished(job)`
    once a job reaches a terminal state. Both are called from the worker thread,
    so a GUI caller must marshal to its own thread.
    """

    def __init__(self, get_backend, out_dir, on_change=None, on_finished=None,
                 max_history: int = 100):
        self._get_backend = get_backend
        self.out_dir = out_dir
        self.on_change = on_change
        self.on_finished = on_finished
        self.max_history = max_history

        self.jobs: list[Job] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._q: "queue.Queue[int]" = queue.Queue()
        self.current_id: int | None = None
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run_loop, daemon=True,
                                        name="job-worker")
        self._worker.start()

    # -------------------------------------------------------------- submitting
    def submit(self, path: str, *, language=None, timestamps=False, hotwords=None,
               insert_mode="cursor", rtl_auto=True) -> Job:
        """Always accepted. Never blocked by a job that is running or unwinding
        after a cancel - that was the whole complaint."""
        job = Job(id=next(self._ids), path=str(path), language=language,
                  timestamps=timestamps, hotwords=hotwords,
                  insert_mode=insert_mode, rtl_auto=rtl_auto)
        with self._lock:
            self.jobs.append(job)
            # Trim finished jobs only; never drop something still running.
            if len(self.jobs) > self.max_history:
                keep = [j for j in self.jobs if j.active]
                done = [j for j in self.jobs if not j.active]
                self.jobs = (done[-(self.max_history - len(keep)):] + keep
                             if self.max_history > len(keep) else keep)
        self._q.put(job.id)
        self._changed(job)
        return job

    # ---------------------------------------------------------------- querying
    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs if j.id == job_id), None)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self.jobs)

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.active]

    @property
    def queued_count(self) -> int:
        with self._lock:
            return sum(1 for j in self.jobs if j.state == "queued")

    @property
    def busy(self) -> bool:
        return self.current_id is not None

    # --------------------------------------------------------------- cancelling
    def cancel(self, job_id: int) -> bool:
        """Returns whether there was something to cancel.

        A queued job goes straight to cancelled. A running one is marked
        `cancelling` and left to unwind on its own; submitting stays open
        throughout.
        """
        job = self.get(job_id)
        if job is None or not job.active:
            return False
        job.cancel.set()
        if job.state == "queued":
            job.state = "cancelled"
            job.finished = time.time()
            self._changed(job)
            self._finished(job)
        else:
            job.state = "cancelling"
            self._changed(job)
        return True

    def cancel_all(self) -> int:
        return sum(1 for j in self.active() if self.cancel(j.id))

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if j.active]
            return before - len(self.jobs)

    # ------------------------------------------------------------------ worker
    def _changed(self, job: Job) -> None:
        if self.on_change:
            try:
                self.on_change(job)
            except Exception:
                pass            # a broken listener must not kill the queue

    def _finished(self, job: Job) -> None:
        if self.on_finished:
            try:
                self.on_finished(job)
            except Exception:
                pass

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            job = self.get(job_id)
            if job is None or job.cancel.is_set():
                if job is not None and job.state in ("queued", "cancelling"):
                    job.state = "cancelled"
                    job.finished = time.time()
                    self._changed(job)
                    self._finished(job)
                self._q.task_done()
                continue

            backend = self._get_backend()
            if backend is None:
                job.state = "error"
                job.error = t("job_no_model")
                job.finished = time.time()
                self._changed(job)
                self._finished(job)
                self._q.task_done()
                continue

            self.current_id = job.id
            job.state = "running"
            job.started = time.time()
            self._changed(job)

            def on_progress(p, j=job):
                j.progress = p
                self._changed(j)

            try:
                job.meta = run_transcription(
                    job.path, backend, self.out_dir,
                    language=job.language,
                    timestamps=job.timestamps,
                    hotwords=job.hotwords,
                    resume=True,
                    on_progress=on_progress,
                    cancel=job.cancel,
                )
                job.state = "done"
            except Cancelled:
                job.state = "cancelled"
            except FileNotFoundError:
                job.state = "error"
                job.error = t("file_not_found")
            except Exception as exc:
                job.state = "error"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished = time.time()
                self.current_id = None
                self._changed(job)
                self._finished(job)
                self._q.task_done()

    def shutdown(self) -> None:
        self._stop.set()
