# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project = Path(SPECPATH).resolve().parent

datas = [(str(project / "assets" / "audio_to_txt.ico"), "assets")]
sherpa_datas, sherpa_binaries, sherpa_hiddenimports = collect_all("sherpa_onnx")
dnd_datas, dnd_binaries, dnd_hiddenimports = collect_all("tkinterdnd2")
datas.extend(sherpa_datas)
datas.extend(dnd_datas)

a = Analysis(
    [str(project / "transcribe_gui.py")],
    pathex=[str(project)],
    binaries=sherpa_binaries + dnd_binaries,
    datas=datas,
    hiddenimports=sherpa_hiddenimports + dnd_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
