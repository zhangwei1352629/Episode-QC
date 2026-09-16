"""Local progressive-video preview derived from a QC playback cache.

The resulting MP4 is a disposable view artifact.  Annotation coordinates keep
using the original cache's frame index and timestamp mapping.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

import imageio_ffmpeg


class StreamPreviewError(RuntimeError):
    pass


def build_stream_preview(
    manifest_path: Path,
    manifest: dict[str, object],
    output_root: Path,
) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True)
    cameras: list[dict[str, object]] = []
    for camera in manifest.get("cameras", []):
        if not isinstance(camera, dict) or not camera.get("index"):
            continue
        stream_id = str(camera.get("stream_id") or "")
        if not stream_id:
            continue
        target = output_root / f"{stream_id}.mp4"
        if not target.is_file():
            _encode_camera(manifest_path, camera, target)
        cameras.append(
            {
                "stream_id": stream_id,
                "frame_offsets_ns": [int(entry[0]) for entry in camera["index"]],
                "frame_indices": [int(entry[3]) for entry in camera["index"]],
                "fps": 30,
                "file": target.name,
                "size_bytes": target.stat().st_size,
            }
        )
    if not cameras:
        raise StreamPreviewError("当前 Episode 没有可生成视频流的相机帧")
    payload = {"schema_version": 1, "transport": "mp4_range_v1", "cameras": cameras}
    (output_root / "stream_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return payload


def _encode_camera(manifest_path: Path, camera: dict[str, object], target: Path) -> None:
    frames_file = manifest_path.parent / str(camera["frames_file"])
    if not frames_file.is_file():
        raise StreamPreviewError(f"相机帧缓存不存在：{frames_file}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stream-frames-", dir=target.parent) as temp:
        temp_root = Path(temp)
        with frames_file.open("rb") as source:
            for ordinal, entry in enumerate(camera["index"]):
                source.seek(int(entry[1]))
                jpeg = source.read(int(entry[2]))
                (temp_root / f"{ordinal:06d}.jpg").write_bytes(jpeg)
        temporary_target = target.with_suffix(".tmp.mp4")
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "30", "-start_number", "0", "-i", str(temp_root / "%06d.jpg"),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-g", "1", "-keyint_min", "1", "-sc_threshold", "0", "-bf", "0",
            "-movflags", "+faststart", str(temporary_target),
        ]
        try:
            subprocess.run(command, check=True, timeout=300)
            temporary_target.replace(target)
        except (OSError, subprocess.SubprocessError) as exc:
            temporary_target.unlink(missing_ok=True)
            raise StreamPreviewError(f"视频流预览生成失败：{exc}") from exc
