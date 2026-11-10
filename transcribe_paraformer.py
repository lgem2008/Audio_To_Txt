"""Minimal offline Paraformer + Silero VAD transcription CLI."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SAMPLE_RATE = 16_000
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else ROOT
_BUNDLE_MODEL = ROOT / "models" / "paraformer-zh-small" / "sherpa-onnx-paraformer-zh-small-2024-03-09"
_APP_MODEL = APP_ROOT / "models" / "paraformer-zh-small" / "sherpa-onnx-paraformer-zh-small-2024-03-09"
_BUNDLE_VAD = ROOT / "models" / "vad" / "silero_vad.onnx"
_APP_VAD = APP_ROOT / "models" / "vad" / "silero_vad.onnx"
DEFAULT_MODEL = _BUNDLE_MODEL if (_BUNDLE_MODEL / "model.int8.onnx").is_file() else _APP_MODEL
DEFAULT_VAD = _BUNDLE_VAD if _BUNDLE_VAD.is_file() else _APP_VAD


class TranscriptionCancelled(Exception):
    """Raised when the user requests a cooperative stop."""


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled("转写已停止")


def decode_audio(path: Path, cancel_event=None):
    """Decode audio or a video's audio track to mono, 16 kHz float32 samples."""
    import av
    import numpy as np

    chunks = []
    with av.open(str(path)) as container:
        streams = list(container.streams)
        stream = next((item for item in streams if item.type == "audio"), None)
        if stream is None:
            if any(item.type == "video" for item in streams):
                raise ValueError("视频文件中没有音轨，无法转写")
            raise ValueError("文件中没有可读取的音轨")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            _check_cancelled(cancel_event)
            chunks.extend(part.to_ndarray().reshape(-1) for part in resampler.resample(frame))
        chunks.extend(part.to_ndarray().reshape(-1) for part in resampler.resample(None))
    _check_cancelled(cancel_event)
    if not chunks:
        raise ValueError("音频为空或无法解码")
    return np.concatenate(chunks).astype(np.float32, copy=False)


def detect_speech(audio, vad_model: Path, progress=None, cancel_event=None):
    """Return Silero VAD speech regions as sample offsets."""
    import numpy as np
    import sherpa_onnx

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(vad_model)
    config.silero_vad.threshold = 0.5
    config.silero_vad.min_silence_duration = 0.45
    config.silero_vad.min_speech_duration = 0.12
    config.silero_vad.max_speech_duration = 20
    config.sample_rate = SAMPLE_RATE

    window = config.silero_vad.window_size
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)
    regions = []
    total_blocks = max(1, (len(audio) + window - 1) // window)
    for block_index, offset in enumerate(range(0, len(audio), window), 1):
        _check_cancelled(cancel_event)
        block = audio[offset:offset + window]
        if len(block) < window:
            block = np.pad(block, (0, window - len(block)))
        vad.accept_waveform(np.ascontiguousarray(block, dtype=np.float32))
        while not vad.empty():
            segment = vad.front
            start = int(segment.start)
            end = min(len(audio), start + len(segment.samples))
            if end > start:
                regions.append({"start": start, "end": end})
            vad.pop()
        if progress:
            progress("语音检测", block_index, total_blocks)
    _check_cancelled(cancel_event)
    while not vad.empty():
        segment = vad.front
        start = int(segment.start)
        end = min(len(audio), start + len(segment.samples))
        if end > start:
            regions.append({"start": start, "end": end})
        vad.pop()
    return regions


def group_regions(regions, max_gap_seconds=0.65, max_span_seconds=20.0):
    """Join nearby VAD regions into readable utterance-sized chunks."""
    if not regions:
        return []
    grouped = [{"start": int(regions[0]["start"]), "end": int(regions[0]["end"])}]
    for region in regions[1:]:
        current = grouped[-1]
        gap = (int(region["start"]) - current["end"]) / SAMPLE_RATE
        span = (int(region["end"]) - current["start"]) / SAMPLE_RATE
        if gap <= max_gap_seconds and span <= max_span_seconds:
            current["end"] = int(region["end"])
        else:
            grouped.append({"start": int(region["start"]), "end": int(region["end"])})
    return grouped


def timestamp(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def transcribe(audio_path: Path, model_dir: Path, vad_model: Path, threads: int, progress=None, cancel_event=None):
    import numpy as np
    import sherpa_onnx

    if progress:
        progress("读取音频", 0, 1)
    _check_cancelled(cancel_event)
    audio = decode_audio(audio_path, cancel_event)
    _check_cancelled(cancel_event)
    if progress:
        progress("语音检测", 0, 1)
    regions = group_regions(detect_speech(audio, vad_model, progress, cancel_event))
    _check_cancelled(cancel_event)
    if not regions:
        return []
    if progress:
        progress("识别语音", 0, len(regions))

    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(model_dir / "model.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=max(1, threads),
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
        debug=False,
    )
    results = []
    total = len(regions)
    for index, region in enumerate(regions, 1):
        _check_cancelled(cancel_event)
        stream = recognizer.create_stream()
        samples = np.ascontiguousarray(audio[region["start"]:region["end"]], dtype=np.float32)
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        text = stream.result.text.strip()
        if text:
            results.append((region["start"] / SAMPLE_RATE, text))
        if progress:
            progress("识别语音", index, total)
        print(f"转写进度：{index}/{total}", end="\r", flush=True)
    print(" " * 28, end="\r", flush=True)
    _check_cancelled(cancel_event)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="本地 Paraformer + Silero VAD 音频/视频转文字")
    parser.add_argument("audio", type=Path, help="音频或视频文件路径")
    parser.add_argument("-o", "--output", type=Path, help="输出 TXT 路径（默认与输入文件同目录）")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL, help="Paraformer 模型目录")
    parser.add_argument("--vad-model", type=Path, default=DEFAULT_VAD, help="Silero VAD onnx 文件")
    parser.add_argument("--threads", type=int, default=4, help="CPU 线程数，默认 4")
    args = parser.parse_args()

    audio_path = args.audio.expanduser().resolve()
    model_dir = args.model_dir.expanduser().resolve()
    vad_model = args.vad_model.expanduser().resolve()
    if not audio_path.is_file():
        parser.error(f"找不到输入文件：{audio_path}")
    if not (model_dir / "model.int8.onnx").is_file() or not (model_dir / "tokens.txt").is_file():
        parser.error(f"Paraformer 模型不完整：{model_dir}")
    if not vad_model.is_file():
        parser.error(f"找不到 Silero VAD 模型：{vad_model}")

    output = (args.output or audio_path.with_suffix(".txt")).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    print(f"输入文件：{audio_path.name}")
    print("本地读取音轨、VAD 分段并使用 Paraformer 识别（音视频不会上传）")
    try:
        rows = transcribe(audio_path, model_dir, vad_model, args.threads)
        if not rows:
            print("没有检测到可识别的语音。")
            return 1
        output.write_text(
            "\n\n".join(f"{timestamp(start)}\n{text}" for start, text in rows) + "\n",
            encoding="utf-8",
        )
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"转写失败：{error}", file=sys.stderr)
        return 1

    print(f"完成：{len(rows)} 段，{sum(len(text) for _, text in rows)} 字")
    print(f"耗时：{time.perf_counter() - started:.1f} 秒")
    print(f"结果：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
