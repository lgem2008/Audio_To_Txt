# -*- mode: python ; coding: utf-8 -*-
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata


project = Path(SPECPATH).resolve().parent
sherpa = Path(find_spec("sherpa_onnx").origin).parent
dnd = Path(find_spec("tkinterdnd2").origin).parent

datas = [(str(project / "assets" / "audio_to_txt.ico"), "assets")]
datas.extend([
    (str(project / "assets" / "ffmpeg" / "COPYING.LGPLv2.1"), "assets/ffmpeg"),
    (str(project / "assets" / "ffmpeg" / "BUILD-INFO.txt"), "assets/ffmpeg"),
    (str(dnd / "tkdnd" / "win-x64"), "tkinterdnd2/tkdnd/win-x64"),
])
datas.extend(copy_metadata("sherpa-onnx"))
datas.extend(copy_metadata("tkinterdnd2"))

a = Analysis(
    [str(project / "transcribe_gui.py")],
    pathex=[str(project)],
    binaries=[
        (str(project / "assets" / "ffmpeg" / "ffmpeg.exe"), "assets/ffmpeg"),
        (str(sherpa / "lib" / "onnxruntime.dll"), "sherpa_onnx/lib"),
    ],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["av"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AudioToTxtLite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project / "assets" / "audio_to_txt.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AudioToTxtLite",
)
