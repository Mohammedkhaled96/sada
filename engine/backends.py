"""Two interchangeable Whisper backends behind one interface.

`faster-whisper` is what we want to ship: CTranslate2 is roughly 4x faster than
the reference implementation and needs no torch at runtime. But its weights are
only distributed through huggingface.co, which is TLS-blocked on this network.

`openai-whisper` reads OpenAI's own `.pt` checkpoints straight off
openaipublic.azureedge.net and bundles its own tiktoken vocabulary, so it
touches no blocked host at all. It is slower and needs torch, which makes it a
poor thing to ship but the right thing for measuring accuracy now.

Both expose:
    detect_language(audio=..., vad_filter=...) -> (lang, prob, all_probs)
    stream_segments(audio, language, opts)     -> iterator of segment dicts

so engine/langdetect.py and the pipeline work unchanged against either.
"""
from __future__ import annotations

from pathlib import Path

from engine.paths import MODELS_DIR

# name -> (faster-whisper repo id, openai checkpoint name)
MODEL_NAMES = {
    "turbo": ("large-v3-turbo", "large-v3-turbo"),
    "large-v3": ("large-v3", "large-v3"),
    "medium": ("medium", "medium"),
    "small": ("small", "small"),
    "tiny": ("tiny", "tiny"),
}

# OpenAI publishes each checkpoint under the sha256 of the file itself.
OPENAI_SHA = {
    "tiny": "65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9",
    "small": "9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794",
    "medium": "345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1",
    "large-v3": "e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb",
    "large-v3-turbo": "aff26ae408abcba5fbf8813c21e62b0941638c5f6eebfb145be0c9839262a19a",
}
OPENAI_CDN = "https://openaipublic.azureedge.net/main/whisper/models"


def openai_url(name: str) -> str:
    return f"{OPENAI_CDN}/{OPENAI_SHA[name]}/{name}.pt"


# --------------------------------------------------------------------------
# faster-whisper (CTranslate2)
# --------------------------------------------------------------------------
class FasterWhisperBackend:
    name = "faster-whisper"
    supports_resume = True              # honours clip_timestamps

    def __init__(self, model_key: str, device: str, compute_type: str,
                 cpu_threads: int):
        from faster_whisper import WhisperModel

        repo = MODEL_NAMES[model_key][0]
        local = MODELS_DIR / f"ct2-{repo}"
        self.model = WhisperModel(
            str(local) if local.exists() else repo,
            device=device, compute_type=compute_type,
            cpu_threads=cpu_threads, num_workers=1,
        )
        self.label = f"{repo} / {compute_type}"

    def detect_language(self, audio=None, vad_filter=False, **kw):
        return self.model.detect_language(audio=audio, vad_filter=vad_filter, **kw)

    def stream_segments(self, audio, language: str, opts: dict):
        gen, _info = self.model.transcribe(
            audio, language=language, task="transcribe", **opts)
        for s in gen:
            yield {
                "id": s.id,
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "text": s.text.strip(),
                "avg_logprob": round(s.avg_logprob, 4),
                "no_speech_prob": round(s.no_speech_prob, 4),
                "compression_ratio": round(s.compression_ratio, 3),
            }


# --------------------------------------------------------------------------
# openai-whisper (reference implementation, torch)
# --------------------------------------------------------------------------
class OpenAIWhisperBackend:
    name = "openai-whisper"
    # clip_timestamps is still dropped before reaching transcribe(), because the
    # reference implementation has no such option - but stream_segments honours
    # it itself by skipping chunks, so resuming is safe here.
    supports_resume = True

    # openai-whisper names some options differently and does not support others.
    DROP = {"vad_filter", "vad_parameters", "repetition_penalty",
            "no_repeat_ngram_size", "max_new_tokens", "chunk_length",
            "multilingual", "hotwords", "language_detection_threshold",
            "language_detection_segments", "max_initial_timestamp",
            "prompt_reset_on_temperature", "clip_timestamps"}
    RENAME = {"log_prob_threshold": "logprob_threshold"}

    def __init__(self, model_key: str, device: str, cpu_threads: int):
        import torch
        import whisper

        torch.set_num_threads(cpu_threads)
        ckpt = MODEL_NAMES[model_key][1]
        local = MODELS_DIR / f"{ckpt}.pt"
        self.model = whisper.load_model(
            str(local) if local.exists() else ckpt,
            device="cuda" if device == "cuda" else "cpu",
            download_root=str(MODELS_DIR),
        )
        self.whisper = whisper
        self.label = f"{ckpt} / torch-{'fp16' if device == 'cuda' else 'fp32'}"
        self._fp16 = device == "cuda"

    def _mel(self, clip):
        w = self.whisper
        mel = w.log_mel_spectrogram(clip, n_mels=self.model.dims.n_mels)
        return w.pad_or_trim(mel, w.audio.N_FRAMES).to(self.model.device)

    def detect_language(self, audio=None, vad_filter=False, **kw):
        """vad_filter is accepted and ignored - the reference model has no VAD."""
        _tokens, probs = self.model.detect_language(self._mel(audio))
        if isinstance(probs, list):
            probs = probs[0]
        best = max(probs, key=probs.get)
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])[:5]
        return best, float(probs[best]), ranked

    def stream_segments(self, audio, language: str, opts: dict):
        """Transcribe chunk by chunk so segments arrive as the work happens.

        `transcribe()` blocks until the entire file is done and only then returns
        every segment. Handed a whole file that meant one progress report at the
        very start — "0 percent, 0.0x real time" — then silence for the whole run,
        which is both useless and misleading. We split on silence ourselves and
        call it per chunk, which makes progress, speed and the estimate real, and
        lets us skip chunks that a previous run already finished.
        """
        from engine.chunking import SAMPLE_RATE, plan_chunks

        clean = {}
        for k, v in opts.items():
            if k in self.DROP:
                continue
            clean[self.RENAME.get(k, k)] = v
        clean["verbose"] = None             # keep the built-in bar off our output

        # We implement clip_timestamps ourselves by dropping earlier chunks.
        clip = opts.get("clip_timestamps")
        skip_before = float(clip[0]) if clip else 0.0

        chunks = plan_chunks(audio)
        next_id = 0
        for start_sample, end_sample in chunks:
            start_s = start_sample / SAMPLE_RATE
            end_s = end_sample / SAMPLE_RATE
            if end_s <= skip_before:
                continue                     # already done by an earlier run

            result = self.model.transcribe(
                audio[start_sample:end_sample], language=language,
                task="transcribe", fp16=self._fp16, **clean,
            )
            for s in result["segments"]:
                seg_start = start_s + s["start"]
                seg_end = start_s + s["end"]
                if seg_end <= skip_before:
                    continue
                # Whisper can run a segment past the clip it was given; clamp so
                # timestamps stay inside the chunk and progress never goes
                # backwards on the next one.
                yield {
                    "id": next_id,
                    "start": round(min(seg_start, end_s), 3),
                    "end": round(min(seg_end, end_s), 3),
                    "text": s["text"].strip(),
                    "avg_logprob": round(s.get("avg_logprob", 0.0), 4),
                    "no_speech_prob": round(s.get("no_speech_prob", 0.0), 4),
                    "compression_ratio": round(s.get("compression_ratio", 0.0), 3),
                }
                next_id += 1


def load_backend(which: str, model_key: str, dev) -> object:
    """which: "auto" | "faster-whisper" | "openai-whisper".

    "auto" prefers faster-whisper and falls back to openai-whisper when the
    CTranslate2 weights are not available (which is the case while
    huggingface.co is blocked and no converted copy exists locally)."""
    if model_key not in MODEL_NAMES:
        raise ValueError(f"unknown model {model_key!r}; pick from {list(MODEL_NAMES)}")

    if which in ("auto", "faster-whisper"):
        try:
            return FasterWhisperBackend(model_key, dev.device, dev.compute_type,
                                        dev.cpu_threads)
        except Exception as exc:
            if which == "faster-whisper":
                raise
            print(f"  faster-whisper unavailable ({type(exc).__name__}), "
                  f"falling back to openai-whisper", flush=True)

    return OpenAIWhisperBackend(model_key, dev.device, dev.cpu_threads)
