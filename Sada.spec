# PyInstaller build for Sada.
#
#   .venv\Scripts\pyinstaller.exe Sada.spec --noconfirm
#
# Produces dist/Sada/ - a folder that runs anywhere, which is both the portable
# release and the payload the installer wraps.
#
# One-folder, not one-file, on purpose: a one-file build unpacks ~1 GB to a temp
# directory on every launch, which would add many seconds to every start of a
# program that is supposed to feel instant.
#
# The model is NOT bundled. The smallest checkpoint is 461 MB and the recommended
# one is 1.5 GB; downloading it once on first run keeps the installer small and
# lets the weights be changed without reinstalling.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

PROJECT = Path(SPECPATH)

# Assets these packages open by path at runtime. PyInstaller cannot see them by
# following imports, and leaving one out produces a build that works until the
# moment a user actually transcribes something:
#   whisper/assets   the tiktoken vocabularies and the mel filterbank
#   faster_whisper   the Silero VAD model, used for chunking
datas = []
datas += collect_data_files("whisper", includes=["assets/*"])
datas += collect_data_files("faster_whisper", includes=["assets/*"])

# ffmpeg does all decoding. Shipping it means a user needs nothing installed.
ffmpeg = PROJECT / "bin" / "ffmpeg.exe"
if ffmpeg.exists():
    datas.append((str(ffmpeg), "bin"))
else:
    raise SystemExit(
        "bin/ffmpeg.exe is missing. Download the 'essentials' build from\n"
        "https://www.gyan.dev/ffmpeg/builds/ and put ffmpeg.exe in bin/.\n"
        "Without it the application cannot read a single audio file."
    )

binaries = []
binaries += collect_dynamic_libs("torch")

hiddenimports = [
    # torch pulls parts of itself in dynamically
    "torch", "torch._C", "torch.jit",
    # whisper's decoder path
    "whisper", "whisper.audio", "whisper.decoding", "whisper.timing",
    "whisper.tokenizer", "whisper.transcribe", "tiktoken", "tiktoken_ext",
    "tiktoken_ext.openai_public", "numba", "more_itertools", "regex",
    # Silero VAD runs through onnxruntime
    "onnxruntime", "faster_whisper", "faster_whisper.vad",
    # Word automation and the JAWS scripting API, both late-bound through COM
    "win32com", "win32com.client", "pythoncom", "pywintypes",
    "win32timezone",
    # the UI
    "wx", "wx.adv", "wx.lib", "wx.lib.agw",
]

# Dead weight that would otherwise be dragged in by torch and numpy.
excludes = [
    "tkinter", "matplotlib", "pytest", "IPython", "jupyter", "notebook",
    "pandas", "scipy", "sklearn", "PIL", "setuptools", "pip",
    "torch.distributed", "torch.testing", "torchvision", "torchaudio",
    "triton",                      # torch.compile support; never used here
]

a = Analysis(
    ["app/main.py"],
    pathex=[str(PROJECT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sada",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                     # UPX on torch's DLLs is a reliable way to
                                   # produce a build that crashes on launch
    console=False,                 # a tray application; a console window would
                                   # flash on every start
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT / "assets" / "sada.ico")
         if (PROJECT / "assets" / "sada.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Sada",
)

# A second, console-attached executable for the command line and for diagnosing
# a launch problem, since the windowed build above can never print anything.
cli = EXE(
    PYZ(Analysis(
        ["engine/transcribe.py"],
        pathex=[str(PROJECT)],
        binaries=binaries,
        datas=datas,
        hiddenimports=[h for h in hiddenimports if not h.startswith("wx")],
        excludes=excludes,
        noarchive=False,
        optimize=0,
    ).pure),
    a.scripts,
    [],
    exclude_binaries=True,
    name="sada-cli",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
