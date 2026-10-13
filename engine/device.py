"""Device selection: prefer NVIDIA GPU, fall back to CPU automatically."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DeviceChoice:
    device: str          # "cuda" | "cpu"
    compute_type: str    # "float16" | "int8"
    cpu_threads: int
    label: str           # human readable, shown in the tray / CLI


def _gpu_name() -> str | None:
    """Return the first CUDA device name, or None when there is no usable GPU."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return None
    except Exception:
        return None

    try:
        import subprocess

        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        name = out.stdout.strip().splitlines()[0].strip()
        return name or "NVIDIA GPU"
    except Exception:
        return "NVIDIA GPU"


def pick_device(override: str = "auto") -> DeviceChoice:
    """override: "auto" | "cuda" | "cpu". Leave two cores for the OS so the
    machine stays usable while a multi-hour file is transcribing."""
    threads = max(4, (os.cpu_count() or 4) - 2)

    if override == "cpu":
        return DeviceChoice("cpu", "int8", threads, f"CPU: {threads} threads (forced)")

    gpu = _gpu_name() if override != "cuda" else (_gpu_name() or "NVIDIA GPU")
    if override == "cuda" or gpu:
        return DeviceChoice("cuda", "float16", threads, f"GPU: {gpu or 'NVIDIA GPU'}")

    return DeviceChoice("cpu", "int8", threads, f"CPU: {threads} threads")
