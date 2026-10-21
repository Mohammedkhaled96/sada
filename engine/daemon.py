"""Resident transcription service on 127.0.0.1.

Word presses a shortcut and expects work to start *now*. Loading the model takes
30-60 seconds, so a process launched per file would spend most of its life
loading weights. This daemon loads the model once, keeps it in RAM, and runs
jobs from a queue.

Built on the standard library rather than FastAPI/uvicorn: VBA only needs plain
GET and POST, and dropping those dependencies keeps the shipped exe far smaller.

Endpoints (every one accepts `?format=text` for VBA, which has no JSON parser):
    GET    /health              is the model loaded, which backend, which device
    POST   /jobs                {"path": "...", "timestamps": false} -> job id
    GET    /jobs/<id>           state, percent, language, eta
    GET    /jobs/<id>/text      the transcript (only once state is done)
    DELETE /jobs/<id>           cancel
    POST   /shutdown            stop the service

Auth: a token generated at startup and written to a file only this user can
read, or taken from SADA_TOKEN. Loopback is not a trust boundary on its own - any
local process, and some web pages, can reach 127.0.0.1 - so every endpoint
except /health requires it. Binding anything other than loopback is an explicit
choice (--host), meant for a container where the port mapping decides exposure.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.backends import load_backend
from engine.device import pick_device
from engine.job import Cancelled, Progress, run_transcription

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# One definition for both, from engine.paths. This file used to carry its
# own copy of the state directory, which is how the endpoint file and the
# settings file could disagree about where the program keeps its data.
from engine.paths import OUT_DIR, STATE_DIR


# --------------------------------------------------------------------------
# job bookkeeping
# --------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    path: str
    timestamps: bool = False
    language: str | None = None
    hotwords: str | None = None
    resume: bool = False
    progress: Progress = field(default_factory=Progress)
    meta: dict | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        p = self.progress
        return {
            "id": self.id,
            "file": Path(self.path).name,
            "state": p.state,
            "percent": round(p.percent, 1),
            "position_seconds": round(p.position, 1),
            "duration_seconds": round(p.duration, 1),
            "language": p.language,
            "language_confidence": round(p.language_confidence, 3),
            "realtime_factor": p.realtime_factor,
            "eta_seconds": round(p.eta_seconds, 0),
            "segments": p.segments,
            "message": p.message,
            "error": self.error or "",
            "rtl": bool(self.meta and self.meta.get("rtl")),
            "words": (self.meta or {}).get("words", 0),
        }


class Service:
    """Owns the model, the queue and the worker thread."""

    def __init__(self, model_key: str = "turbo", backend: str = "auto",
                 device: str = "auto"):
        self.model_key = model_key
        self.backend_pref = backend
        self.device_pref = device
        self.backend = None
        self.device = pick_device(device)
        self.ready = threading.Event()
        self.load_error: str | None = None
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.q: queue.Queue[str] = queue.Queue()
        self.current: str | None = None
        # A fixed token can be supplied so a container's clients know it in
        # advance; otherwise one is generated per run.
        self.token = os.environ.get("SADA_TOKEN") or secrets.token_urlsafe(24)
        self.started = time.time()

        threading.Thread(target=self._load, daemon=True, name="model-loader").start()
        threading.Thread(target=self._worker, daemon=True, name="job-worker").start()

    # ------------------------------------------------------------------ model
    def _load(self) -> None:
        try:
            self.backend = load_backend(self.backend_pref, self.model_key, self.device)
        except Exception as exc:
            if self.device.device == "cuda":
                try:                                  # GPU present but unusable
                    self.device = pick_device("cpu")
                    self.backend = load_backend(self.backend_pref, self.model_key,
                                                self.device)
                except Exception as exc2:
                    self.load_error = f"{type(exc2).__name__}: {exc2}"
            else:
                self.load_error = f"{type(exc).__name__}: {exc}"
        finally:
            self.ready.set()

    def health(self) -> dict:
        return {
            "ready": self.backend is not None,
            "loading": not self.ready.is_set(),
            "error": self.load_error or "",
            "backend": getattr(self.backend, "name", ""),
            "model": getattr(self.backend, "label", self.model_key),
            "device": self.device.device,
            "device_label": self.device.label,
            "uptime_seconds": round(time.time() - self.started),
            "queued": self.q.qsize(),
            "current": self.current or "",
        }

    # ------------------------------------------------------------------ jobs
    def submit(self, **kw) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], **kw)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
            # keep memory bounded over a long-running session
            while len(self.order) > 50:
                self.jobs.pop(self.order.pop(0), None)
        self.q.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _worker(self) -> None:
        """One job at a time: transcription saturates the CPU, so running two
        in parallel makes both slower and can exhaust RAM on a long file."""
        self.ready.wait()
        while True:
            job_id = self.q.get()
            job = self.get(job_id)
            if job is None or job.cancel.is_set():
                self.q.task_done()
                continue
            if self.backend is None:
                job.error = self.load_error or "model failed to load"
                job.progress.state = "error"
                self.q.task_done()
                continue

            self.current = job_id
            try:
                job.meta = run_transcription(
                    job.path, self.backend, OUT_DIR,
                    language=job.language,
                    timestamps=job.timestamps,
                    hotwords=job.hotwords,
                    resume=job.resume,
                    on_progress=lambda p, j=job: setattr(j, "progress", p),
                    cancel=job.cancel,
                )
            except Cancelled:
                job.progress.state = "cancelled"
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {exc}"
                job.progress.state = "error"
                job.progress.message = job.error
            finally:
                self.current = None
                self.q.task_done()


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
def to_text(obj) -> str:
    """KEY=VALUE lines. VBA has no JSON parser, and hand-rolling one in VBA is
    how you get a transcription tool that breaks on an apostrophe."""
    if isinstance(obj, dict):
        return "".join(f"{k}={'' if v is None else v}\n" for k, v in obj.items())
    return str(obj)


class Handler(BaseHTTPRequestHandler):
    server_version = "Sada/1.0"
    protocol_version = "HTTP/1.1"
    service: Service = None          # set on the server instance

    def log_message(self, fmt, *args):      # keep the console clean
        pass

    # ---------------------------------------------------------------- helpers
    def _send(self, code: int, payload, as_text: bool) -> None:
        body = (to_text(payload) if as_text
                else json.dumps(payload, ensure_ascii=False)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",
                         "text/plain; charset=utf-8" if as_text
                         else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        given = (self.headers.get("X-Token")
                 or parse_qs(urlparse(self.path).query).get("token", [""])[0])
        return secrets.compare_digest(given or "", self.service.token)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {k: v[0] for k, v in parse_qs(raw).items()}

    # ------------------------------------------------------------------ verbs
    def do_GET(self) -> None:
        u = urlparse(self.path)
        as_text = parse_qs(u.query).get("format", [""])[0] == "text"
        parts = [p for p in u.path.split("/") if p]

        if parts == ["health"]:
            return self._send(200, self.service.health(), as_text)

        if not self._authed():
            return self._send(401, {"error": "bad or missing token"}, as_text)

        if len(parts) >= 2 and parts[0] == "jobs":
            job = self.service.get(parts[1])
            if job is None:
                return self._send(404, {"error": "no such job"}, as_text)
            if len(parts) == 2:
                return self._send(200, job.snapshot(), as_text)
            if len(parts) == 3 and parts[2] == "text":
                if job.progress.state != "done":
                    return self._send(409, {"error": f"state is {job.progress.state}"},
                                      as_text)
                body = (job.meta or {}).get("text", "").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)

        self._send(404, {"error": "unknown endpoint"}, as_text)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        as_text = parse_qs(u.query).get("format", [""])[0] == "text"
        parts = [p for p in u.path.split("/") if p]

        if not self._authed():
            return self._send(401, {"error": "bad or missing token"}, as_text)

        if parts == ["jobs"]:
            data = self._body()
            path = (data.get("path") or "").strip().strip('"')
            if not path:
                return self._send(400, {"error": "path is required"}, as_text)
            if not Path(path).exists():
                return self._send(400, {"error": f"file not found: {path}"}, as_text)
            job = self.service.submit(
                path=path,
                timestamps=str(data.get("timestamps", "")).lower() in ("1", "true", "yes"),
                language=(data.get("language") or None),
                hotwords=(data.get("hotwords") or None),
                resume=str(data.get("resume", "")).lower() in ("1", "true", "yes"),
            )
            return self._send(201, {"id": job.id, "state": job.progress.state}, as_text)

        if parts == ["shutdown"]:
            self._send(200, {"ok": True}, as_text)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        self._send(404, {"error": "unknown endpoint"}, as_text)

    def do_DELETE(self) -> None:
        u = urlparse(self.path)
        as_text = parse_qs(u.query).get("format", [""])[0] == "text"
        parts = [p for p in u.path.split("/") if p]

        if not self._authed():
            return self._send(401, {"error": "bad or missing token"}, as_text)

        if len(parts) == 2 and parts[0] == "jobs":
            job = self.service.get(parts[1])
            if job is None:
                return self._send(404, {"error": "no such job"}, as_text)
            job.cancel.set()
            return self._send(200, {"id": job.id, "cancelled": True}, as_text)

        self._send(404, {"error": "unknown endpoint"}, as_text)


def free_port(host: str = DEFAULT_HOST, start: int = DEFAULT_PORT,
              tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket() as s:
            if s.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError(f"no free port in {start}..{start + tries}")


def write_endpoint_file(host: str, port: int, token: str) -> Path:
    """Word reads this to find the service. Written with user-only permissions
    because it holds the token."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = STATE_DIR / "endpoint.json"
    f.write_text(json.dumps({"url": f"http://{host}:{port}", "token": token,
                             "pid": os.getpid()}), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    return f


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(prog="daemon", description=__doc__)
    p.add_argument("--model", default="turbo")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "faster-whisper", "openai-whisper"])
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--port", type=int, default=0, help="0 picks a free port")
    p.add_argument("--host", default=os.environ.get("SADA_HOST", DEFAULT_HOST),
                   help="bind address; use 0.0.0.0 only inside a container, "
                        "where the port mapping controls who can reach it")
    p.add_argument("--print-token", action="store_true",
                   help="print the auth token, for a container's logs")
    p.add_argument("--out", default=None,
                   help="directory for transcripts (default: SADA_OUT_DIR, "
                        "else ./out beside the project)")
    args = p.parse_args()

    global OUT_DIR
    if args.out:
        OUT_DIR = Path(args.out)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    svc = Service(args.model, args.backend, args.device)
    host = args.host
    port = args.port or free_port(host)

    Handler.service = svc
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True

    ep = write_endpoint_file(host, port, svc.token)
    print(f"listening on http://{host}:{port}")
    if args.print_token:
        print(f"token         {svc.token}")
    print(f"endpoint file {ep}")
    print(f"device        {svc.device.label}")
    print(f"transcripts   {OUT_DIR}")
    print("loading the model in the background; GET /health to check", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
        ep.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
