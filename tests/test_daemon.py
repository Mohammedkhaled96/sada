"""Start the daemon for real and exercise every endpoint over HTTP.

Uses the tiny model so the run is quick; the point is the service contract, not
transcription quality.

Run:  PYTHONUTF8=1 .venv/Scripts/python.exe tests/test_daemon.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = ROOT / ".venv" / "Scripts" / "python.exe"
AUDIO = Path("E:/test.mp3")
STATE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Sada"
ENDPOINT = STATE / "endpoint.json"

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{PASS if cond else FAIL}] {name}" + (f"  — {detail}" if detail else ""),
          flush=True)
    if not cond:
        failures.append(name)


def req(method: str, url: str, token: str | None = None, body: dict | None = None):
    """Returns (status, text). Never raises on an HTTP error status."""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if token:
        r.add_header("X-Token", token)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def main() -> int:
    if not AUDIO.exists():
        print(f"need {AUDIO} to run this test")
        return 2

    ENDPOINT.unlink(missing_ok=True)
    print("starting the daemon...", flush=True)
    proc = subprocess.Popen(
        [str(PY), "-m", "engine.daemon", "--model", "tiny",
         "--backend", "openai-whisper"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    try:
        # ---------------------------------------------- it advertises itself
        for _ in range(100):
            if ENDPOINT.exists():
                break
            if proc.poll() is not None:
                print("daemon died:\n" + (proc.stdout.read() if proc.stdout else ""))
                return 1
            time.sleep(0.2)
        check("writes an endpoint file", ENDPOINT.exists(), str(ENDPOINT))
        if not ENDPOINT.exists():
            return 1

        ep = json.loads(ENDPOINT.read_text(encoding="utf-8"))
        url, token = ep["url"], ep["token"]
        check("endpoint file carries url, token and pid",
              all(k in ep for k in ("url", "token", "pid")), url)
        check("bound to loopback only", url.startswith("http://127.0.0.1:"))
        check("token is not guessable", len(token) >= 24, f"{len(token)} chars")

        # ------------------------------------------------------------- health
        code, txt = req("GET", f"{url}/health")
        check("health needs no token", code == 200, f"HTTP {code}")
        h = json.loads(txt)
        check("health reports the device", bool(h.get("device_label")),
              h.get("device_label", ""))

        code, txt = req("GET", f"{url}/health?format=text")
        check("health speaks KEY=VALUE for VBA",
              code == 200 and "device=" in txt and "=" in txt.splitlines()[0],
              txt.splitlines()[0] if txt else "")

        # --------------------------------------------------------------- auth
        code, _ = req("POST", f"{url}/jobs", body={"path": str(AUDIO)})
        check("rejects a job with no token", code == 401, f"HTTP {code}")
        code, _ = req("POST", f"{url}/jobs", token="wrong-" + "x" * 20,
                      body={"path": str(AUDIO)})
        check("rejects a job with a wrong token", code == 401, f"HTTP {code}")

        # ----------------------------------------------------- bad submissions
        code, _ = req("POST", f"{url}/jobs", token=token, body={})
        check("rejects a job with no path", code == 400, f"HTTP {code}")
        code, _ = req("POST", f"{url}/jobs", token=token,
                      body={"path": "E:/does-not-exist-xyz.mp3"})
        check("rejects a missing file", code == 400, f"HTTP {code}")
        code, _ = req("GET", f"{url}/jobs/deadbeef", token=token)
        check("unknown job is 404", code == 404, f"HTTP {code}")

        # --------------------------------------------- wait for the model load
        print("waiting for the model to load...", flush=True)
        for _ in range(600):
            h = json.loads(req("GET", f"{url}/health")[1])
            if h["ready"] or h["error"]:
                break
            time.sleep(1)
        check("model loads", h["ready"], h.get("error") or h.get("model", ""))
        if not h["ready"]:
            return 1

        # ------------------------------------------------------- a real job
        code, txt = req("POST", f"{url}/jobs", token=token,
                        body={"path": str(AUDIO), "timestamps": False})
        check("accepts a job", code == 201, f"HTTP {code} {txt}")
        job_id = json.loads(txt)["id"]

        code, txt = req("GET", f"{url}/jobs/{job_id}/text", token=token)
        check("refuses the text before it is done", code == 409, f"HTTP {code}")

        states, last = [], None
        for _ in range(300):
            s = json.loads(req("GET", f"{url}/jobs/{job_id}", token=token)[1])
            if s["state"] != last:
                last = s["state"]
                states.append(last)
            if s["state"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.5)

        check("job reaches done", s["state"] == "done",
              f"{s['state']} {s.get('error', '')}")
        check("passes through the real states",
              "transcribing" in states, " -> ".join(states))
        check("detects arabic", s["language"] == "ar",
              f"{s['language']} {s['language_confidence']:.0%}")
        check("flags rtl", s["rtl"] is True)
        check("reports 100 percent", s["percent"] >= 99, f"{s['percent']}%")
        check("counts words", s["words"] > 20, f"{s['words']} words")

        code, text = req("GET", f"{url}/jobs/{job_id}/text", token=token)
        check("serves the transcript", code == 200 and len(text) > 50,
              f"{len(text)} chars")
        check("transcript is arabic utf-8", "السلام" in text, text[:40])

        code, txt = req("GET", f"{url}/jobs/{job_id}?format=text", token=token)
        check("job status speaks KEY=VALUE too",
              code == 200 and "state=done" in txt,
              [l for l in txt.splitlines() if l.startswith("state=")][:1])

        # ------------------------------------------------------------- cancel
        code, txt = req("POST", f"{url}/jobs", token=token, body={"path": str(AUDIO)})
        cid = json.loads(txt)["id"]
        code, _ = req("DELETE", f"{url}/jobs/{cid}", token=token)
        check("accepts a cancel", code == 200, f"HTTP {code}")
        for _ in range(120):
            s2 = json.loads(req("GET", f"{url}/jobs/{cid}", token=token)[1])
            if s2["state"] in ("cancelled", "done", "error"):
                break
            time.sleep(0.5)
        check("cancel takes effect", s2["state"] == "cancelled", s2["state"])

        # ----------------------------------------------------------- shutdown
        code, _ = req("POST", f"{url}/shutdown", token=token)
        check("accepts shutdown", code == 200, f"HTTP {code}")
        for _ in range(50):
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        check("process exits", proc.poll() is not None, f"rc={proc.poll()}")
        check("removes its endpoint file on exit", not ENDPOINT.exists())

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)}")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all daemon checks passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
