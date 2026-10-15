# Why dictation uses a different model, and how fast it actually is

Everything here is measured on the machine this feature was built and tested
on, not estimated: Intel 12-core laptop CPU, 10 threads, **no usable GPU**, so
`pick_device` returns CPU. `audio/test.mp3`, Arabic speech, openai-whisper
backend. faster-whisper is not available on this network - huggingface.co is
blocked at the TLS layer, which is why the project uses openai-whisper at all,
and why its roughly fourfold CPU speedup is not on the table.

## The thing that is not obvious

Whisper pads every clip to a **30-second window** before encoding it. So the
cost of a call barely depends on how much audio is in it:

| audio in the call | `large-v3-turbo` | × real time |
|---|---|---|
| 2 s | 12.65 s | 0.16x |
| 5 s | 9.94 s | 0.50x |
| 10 s | 11.56 s | 0.86x |
| 20 s | 21.86 s | 0.91x |

Two seconds of speech costs about as much as twenty.

The first version of dictation cut a phrase at every 600 ms pause, which is
exactly the wrong thing to do: it maximised the number of these calls. Speaking
for twenty seconds produced ten or more phrases, each costing six to twelve
seconds, so the queue fell further behind with every phrase and pressing stop
meant waiting for the whole backlog. That is the "very slow" this document
exists because of.

Phrases are now gathered until there is enough audio to be worth a call -
`dictation_target_s`, 12 seconds by default - and only then does a short pause
end one. A deliberate silence (`long_pause_ms`, 1.4 s) ends a phrase at any
length, so saying one sentence and stopping still returns it promptly. The
maximum is 25 s, which stays inside a single 30-second window.

## Which model

| model | 20 s phrase | × real time | wait after pressing stop | accuracy |
|---|---|---|---|---|
| `large-v3-turbo` | 21.86 s | **0.91x** | ~20 s | best |
| `small` | 15.14 s | **1.32x** | ~8 s | good |
| `tiny` | 2.30 s | **8.69x** | **~1 s** | visible errors |

Anything below 1.0x falls behind the speaker permanently, so `turbo` cannot be
used for live dictation on a CPU however the phrases are cut. `small` is the
default for dictation, and the file-transcription model is left alone - they
are separate settings because they are answering different questions.

What `tiny` and `small` sound like on the same ten seconds:

- `tiny` - *"السلام عليكم ورحمة الله وبركاته بس الله رحمة رحمة الحمد لله رب
  العالمين وصرط و السلام وعلى أشف المرسلين"*
- `small` - *"السلام عليكم ورحمة الله وبركاته بسم الله الرحمن الرحيم الحمد لله
  رب العامين وصلاته السلام على أشرف المرسلين محمد"*

`tiny` mangles "أشرف المرسلين" into "أشف المرسلين" and invents a repetition.
`small` is nearly right. Choose `tiny` only if immediacy matters more than
being correct.

**With a graphics card none of this applies** - every model moves to float16 on
CUDA and the measurements above do not describe that machine.

## Mixing two languages

The first version identified the language once and forced it on every phrase.
For a speaker who mixes Arabic and English, that is destructive rather than
merely imperfect: forced to Arabic, Whisper writes English words in Arabic
letters.

Letting Whisper detect for itself is correct but costs a second encoder pass:

| 10 s phrase | with a language given | detecting it |
|---|---|---|
| `large-v3-turbo` | 9.94 s | 23.40 s |
| `small` | 6.96 s | 11.39 s |

So naming the language is given to a separate, tiny model whose only job that
is - a task small models are good at:

| model | `detect_language` on 10 s | answer |
|---|---|---|
| `tiny` | **0.20 s** | ar, 0.96 |
| `small` | 2.58 s | ar, 0.90 |

`tiny` is thirteen times faster here and, on this clip, more confident than
`small`. So every phrase is named by `tiny` for 0.2 s and transcribed by the
dictation model with that language fixed, and a speaker can switch language
mid-session. Below 0.5 confidence the phrase is passed with no language at all,
so an unclear phrase is not stamped with a guess.

`tiny.pt` is 72 MB. Without it dictation still works; it just settles on one
language for the session, as before.

## Reproducing this

```
python -m engine.fetch tiny
python -m engine.fetch small
```

then time `backend.stream_segments(clip, language, DICTATION_OPTS)` for clips
of a few different lengths, and `backend.detect_language(audio=clip)` for each
model. Warm the model up with one throwaway call first: the first call pays
lazy initialisation and reads about 3 s slower than it should.
