"""Rebuild the bundled Windows x64 FFmpeg with Python 3.12.

Run from any directory with:
    python packaging/build_ffmpeg.py --jobs 6

Versions, download hashes, and compiler options are pinned in ffmpeg-build.json.
Downloads and intermediate files stay in build/ffmpeg-slim. The finished EXE,
license, and build information are written to assets/ffmpeg.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile


PROJECT = Path(__file__).resolve().parent.parent
WORK = PROJECT / "build" / "ffmpeg-slim"


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def download_toolchain(url: str, target: Path):
    """Use bounded range requests for the large GitHub release asset."""
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        total = int(response.headers["Content-Range"].split("/")[-1])

    chunks = target.parent / (target.name + ".chunks")
    chunks.mkdir(exist_ok=True)
    chunk_size = 2 * 1024 * 1024

    def fetch(index: int):
        start = index * chunk_size
        end = min(total - 1, start + chunk_size - 1)
        chunk = chunks / f"{index:04d}"
        if chunk.is_file() and chunk.stat().st_size == end - start + 1:
            return chunk
        separator = "&" if "?" in url else "?"
        chunk_url = f"{url}{separator}chunk={index}"
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    chunk_url, headers={"Range": f"bytes={start}-{end}"},
                )
                with urllib.request.urlopen(request, timeout=35) as response:
                    content_range = response.headers.get("Content-Range", "")
                    if content_range != f"bytes {start}-{end}/{total}":
                        raise ValueError("The download server returned an unexpected range")
                    data = response.read()
                if len(data) != end - start + 1:
                    raise ValueError("Incomplete toolchain download chunk")
                chunk.write_bytes(data)
                return chunk
            except (OSError, ValueError):
                if attempt == 2:
                    raise

    count = (total + chunk_size - 1) // chunk_size
    with ThreadPoolExecutor(max_workers=6) as pool, target.open("wb") as output:
        for chunk in pool.map(fetch, range(count)):
            with chunk.open("rb") as source:
                shutil.copyfileobj(source, output)


def download(artifact: dict, ranged: bool = False) -> Path:
    destination = WORK / "downloads" / artifact["filename"]
    if not destination.is_file():
        print(f"Downloading {artifact['filename']}", flush=True)
        temporary = destination.with_name(destination.name + ".part")
        if ranged:
            download_toolchain(artifact["url"], temporary)
        else:
            with urllib.request.urlopen(artifact["url"], timeout=60) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
        if sha256(temporary) != artifact["sha256"]:
            raise ValueError(f"Download SHA256 mismatch: {temporary}")
        temporary.replace(destination)
    if sha256(destination) != artifact["sha256"]:
        raise ValueError(f"Cached SHA256 mismatch: {destination}")
    return destination


def run_logged(command: list[str], directory: Path, environment: dict, name: str):
    log = WORK / name
    with log.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            command, cwd=directory, env=environment,
            stdout=output, stderr=subprocess.STDOUT,
        )
    if result.returncode:
        print("\n".join(log.read_text(errors="replace").splitlines()[-30:]))
        raise RuntimeError(f"Command failed. See {log}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(6, os.cpu_count() or 1))
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info < (3, 12):
        parser.error("Use Windows and Python 3.12 or later")
    if args.jobs < 1:
        parser.error("--jobs must be positive")

    config = json.loads(Path(__file__).with_name("ffmpeg-build.json").read_text())
    for name in ["downloads", "toolchain", "source", "compile", "tmp"]:
        (WORK / name).mkdir(parents=True, exist_ok=True)
    archives = {
        name: download(config[name], ranged=name == "toolchain")
        for name in ["ffmpeg", "toolchain", "nasm", "extractor"]
    }

    source = WORK / "source" / f"FFmpeg-n{config['ffmpeg']['version']}"
    toolbin = WORK / "toolchain" / "w64devkit" / "bin"
    nasmbin = WORK / "toolchain" / f"nasm-{config['nasm']['version']}"
    environment = os.environ.copy()

    # Mark only completed extractions, so an interrupted run can be retried.
    marker = WORK / "source" / f".ffmpeg-{config['ffmpeg']['version']}.extracted"
    if not marker.is_file():
        with tarfile.open(archives["ffmpeg"]) as archive:
            archive.extractall(WORK / "source", filter="data")
        marker.touch()
    marker = WORK / "toolchain" / f".w64devkit-{config['toolchain']['version']}.extracted"
    if not marker.is_file():
        run_logged(
            [str(archives["extractor"]), "x", str(archives["toolchain"]),
             f"-o{WORK / 'toolchain'}", "-y", "-bso0", "-bsp0"],
            WORK, environment, "extract-toolchain.log",
        )
        marker.touch()
    marker = WORK / "toolchain" / f".nasm-{config['nasm']['version']}.extracted"
    if not marker.is_file():
        with zipfile.ZipFile(archives["nasm"]) as archive:
            archive.extractall(WORK / "toolchain")
        marker.touch()

    environment["PATH"] = os.pathsep.join([str(toolbin), str(nasmbin), environment["PATH"]])
    environment["LC_ALL"] = "C"
    # Avoid backslash parsing errors and inheriting the enclosing project's Git version.
    for name in ["TMPDIR", "TMP", "TEMP"]:
        environment[name] = (WORK / "tmp").as_posix()
    environment["revision"] = config["ffmpeg"]["version"]

    codecs = (source / "libavcodec" / "allcodecs.c").read_text()
    audio_section = codecs.split("/* audio codecs */", 1)[1].split("/* subtitles */", 1)[0]
    decoders = re.findall(r"extern const FFCodec ff_(\w+)_decoder;", audio_section)
    if not decoders:
        raise ValueError("No native audio decoders found in the pinned FFmpeg source")
    options = [option.format(audio_decoders=",".join(decoders)) for option in config["configure_options"]]
    (WORK / "configure-options.json").write_text(json.dumps(options, indent=2) + "\n")

    print("Configuring FFmpeg. See build/ffmpeg-slim/configure.log", flush=True)
    run_logged(
        [str(toolbin / "sh.exe"), str(source / "configure"), *options],
        WORK / "compile", environment, "configure.log",
    )
    print(f"Compiling with {args.jobs} workers. See build/ffmpeg-slim/compile.log", flush=True)
    run_logged(
        [str(toolbin / "make.exe"), f"-j{args.jobs}", "V=1", "ffmpeg.exe"],
        WORK / "compile", environment, "compile.log",
    )
    executable = WORK / "compile" / "ffmpeg.exe"
    subprocess.run([str(executable), "-version"], check=True, stdout=subprocess.DEVNULL)

    output = PROJECT / "assets" / "ffmpeg"
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "ffmpeg.exe.new"
    shutil.copy2(executable, temporary)
    temporary.replace(output / "ffmpeg.exe")
    shutil.copy2(source / "COPYING.LGPLv2.1", output / "COPYING.LGPLv2.1")
    info = [
        f"FFmpeg {config['ffmpeg']['version']} - AudioToTxt audio extraction build",
        f"Source: {config['ffmpeg']['url']}",
        f"Source SHA256: {config['ffmpeg']['sha256']}",
        f"EXE SHA256: {sha256(executable)}",
        f"Compiler: w64devkit {config['toolchain']['version']}, NASM {config['nasm']['version']}",
        "License: LGPL version 2.1 or later. See COPYING.LGPLv2.1",
        "Rebuild: python packaging/build_ffmpeg.py --jobs 6",
        "", "Configure options:", *options, "",
    ]
    (output / "BUILD-INFO.txt").write_text("\n".join(info), encoding="utf-8")
    print(f"Built {output / 'ffmpeg.exe'} ({executable.stat().st_size / 1024**2:.2f} MiB)")


if __name__ == "__main__":
    main()
