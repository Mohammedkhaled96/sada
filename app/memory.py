"""Giving memory back between jobs.

A transcription allocates a great deal and then stops needing it: mel
spectrograms, decoded audio, segment dictionaries, the tensors the model built
along the way. Python frees those objects, but the process keeps the pages it
obtained from Windows, so a program left running all day sits on hundreds of
megabytes it has no use for. On a machine that is also running Word, a browser
and a screen reader, that is somebody else's memory.

Two steps, in this order and neither alone is enough:

* `gc.collect()` releases what Python is holding, including the reference
  cycles that a long-lived object graph accumulates;
* `SetProcessWorkingSetSize(-1, -1)` then asks Windows to trim the working set
  - the pages are given back rather than merely marked free inside the
  process.

The second is the one that shows up in Task Manager, and it is also the one
that would be a mistake to do at the wrong moment: trimmed pages are faulted
back in on next use, so doing this while a job is running trades the user's
wait for a number they are not looking at. The caller decides when, and only
calls here when nothing is running.
"""
from __future__ import annotations

import ctypes
import gc
import sys

from engine import applog

# Everything below the collect is Windows, and this module has to import on
# Linux anyway: the container build runs there, and so does CI, which imports
# the text pipeline to test it. `ctypes.wintypes` does not exist off Windows -
# importing it raises - so the whole Windows half is built only when there is
# a Windows to build it for. Elsewhere `release()` collects and reports
# nothing, which is the honest answer rather than a crash.
WINDOWS = sys.platform == "win32"

if WINDOWS:
    from ctypes import wintypes

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]


def _declare():
    """Give the two calls their real signatures, once.

    Without this they are called as though every argument were a C int, and on
    a 64-bit build the handle and the struct pointer are both wider than that.
    `GetProcessMemoryInfo` simply returned zero, which read as "this process
    is using no memory" rather than as the mistake it was.
    """
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE,
                                             ctypes.c_size_t, ctypes.c_size_t]
    k32.SetProcessWorkingSetSize.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(_MemoryCounters),
                                           wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return k32, psapi


try:
    _K32, _PSAPI = _declare() if WINDOWS else (None, None)
except Exception:
    _K32 = _PSAPI = None


def working_set_mb() -> float:
    """How much memory this process is holding, in megabytes.

    0.0 when it cannot be asked - off Windows, or if the call is refused.
    Callers treat that as "unknown", never as "none".
    """
    if _PSAPI is None:
        return 0.0
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(_MemoryCounters)
        if not _PSAPI.GetProcessMemoryInfo(_K32.GetCurrentProcess(),
                                           ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.WorkingSetSize / (1024 * 1024)
    except Exception:
        return 0.0


def release(reason: str = "", trim: bool = True) -> float:
    """Collect, optionally trim, and return how many megabytes went back.

    Never raises. The return value is the point of the measurement: "it did
    something" is worth knowing, and a pass that frees nothing worth reporting
    should be quiet rather than filling the log with a line every few minutes.

    **`trim=False` while the model is in memory.** The trim does not choose
    what to give back - measured here, it took the process from 208 MB to 1 MB
    - and the model's weights are 1.6 GB of pages that are *supposed* to be
    resident. Evicting them buys a smaller number in Task Manager and charges
    the user for it the next time they dictate, when every one of those pages
    faults back in from disk. So the collect runs always and the trim runs
    only when there is nothing hot to evict.
    """
    try:
        before = working_set_mb()
        gc.collect()
        try:
            # -1 for both sizes is the documented way to say "take back what
            # is not in use", not a size of minus one byte.
            if trim and _K32 is not None:
                _K32.SetProcessWorkingSetSize(
                    _K32.GetCurrentProcess(),
                    ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass                        # the collect above still happened
        freed = before - working_set_mb()
        if freed >= 25:                 # worth a line; smaller is just noise
            applog.write(f"released {freed:.0f} MB"
                         f"{f' after {reason}' if reason else ''}, "
                         f"now holding {working_set_mb():.0f} MB",
                         where="memory")
        return freed
    except Exception:
        return 0.0
