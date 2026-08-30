from __future__ import annotations

import json
import math
import re
import struct
import zlib
from pathlib import Path
from typing import Iterator


DOHC_DATA_FORMAT = "dohc_jpeg_v1"
DOHC_STORAGE_FORMATS = {
    "hybrid-h264-jpeg-segment-v1",
    "h264-split-mp4-v1",
    "jpeg-stream-v1",
    "jpeg-segment-v1",
}
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_SEGMENT_PAYLOAD_BYTES = 128 * 1024 * 1024
SEGMENT_STREAM_IDS = {"t265_left": 4, "t265_right": 5}
SMPL_ARRAY_NAMES = (
    "joints",
    "joint_positions",
    "smpl_joints",
    "skeleton",
    "skeletons",
    "keypoints3d",
    "keypoints_3d",
    "poses",
)
FRAME_ID_ARRAY_NAMES = ("frame_ids", "frame_id", "frame_indices", "frames")


def _read_json_object(path: Path, *, required: bool = True) -> dict[str, object] | None:
    if not path.is_file():
        if required:
            raise ValueError(f"DOHC 元数据文件不存在: {path.name}")
        return None
    if path.stat().st_size > MAX_METADATA_BYTES:
        raise ValueError(f"DOHC 元数据文件过大: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"DOHC 元数据无法读取: {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"DOHC 元数据根节点必须是对象: {path.name}")
    return value


def _has_camera_directory(directory: Path) -> bool:
    try:
        return any(
            child.is_dir()
            and (re.fullmatch(r"cam\d+", child.name, re.IGNORECASE)
                 or child.name.lower() in {"t265", "t265_left", "t265_right"})
            for child in directory.iterdir()
        )
    except OSError:
        return False


def is_dohc_primary_file(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.name.lower() == "states.jsonl":
        return candidate.is_file() and _has_camera_directory(candidate.parent)
    if candidate.name.lower() != "manifest.json" or not _has_camera_directory(candidate.parent):
        return False
    try:
        manifest = _read_json_object(candidate)
    except ValueError:
        return False
    storage_format = str(manifest.get("storage_format") or manifest.get("format") or "").lower()
    return (
        storage_format in DOHC_STORAGE_FORMATS
        or isinstance(manifest.get("streams"), dict)
        or manifest.get("batch_count") is not None
    )


def discover_dohc_episode_files(root: str | Path) -> list[Path]:
    root_path = Path(root).resolve()
    discovered: dict[Path, Path] = {}
    for manifest_path in root_path.rglob("manifest.json"):
        if any(part.startswith(".") for part in manifest_path.relative_to(root_path).parts):
            continue
        if is_dohc_primary_file(manifest_path):
            discovered[manifest_path.parent.resolve()] = manifest_path.resolve()
    for states_path in root_path.rglob("states.jsonl"):
        if any(part.startswith(".") for part in states_path.relative_to(root_path).parts):
            continue
        parent = states_path.parent.resolve()
        if parent not in discovered and is_dohc_primary_file(states_path):
            discovered[parent] = states_path.resolve()
    episode_roots = set(discovered)
    top_level = {
        episode_root: primary_file
        for episode_root, primary_file in discovered.items()
        if not any(parent in episode_roots for parent in episode_root.parents)
    }
    return sorted(
        top_level.values(),
        key=lambda path: _natural_key(path.parent.relative_to(root_path).as_posix()),
    )


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _non_negative_number(*values: object) -> float | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(result) and result >= 0:
            return result
    return None


def _safe_relative_files(root: Path, raw_paths: list[object]) -> list[str]:
    files: list[str] = []
    for raw in raw_paths:
        value = str(raw or "").strip().replace("\\", "/")
        relative = Path(value)
        if not value or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"DOHC 清单包含不安全路径: {value or '<empty>'}")
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"DOHC 清单路径越界: {value}") from exc
        if target.is_file():
            files.append(relative.as_posix())
    return files


def _skeleton_spec(root: Path, fps: float) -> dict[str, object] | None:
    candidates = sorted(
        (
            path for path in root.glob("*.npz")
            if "smpl" in path.name.lower() or "skeleton" in path.name.lower()
        ),
        key=lambda path: (path.name.lower() != "smpl_skeleton.npz", path.name.lower()),
    )
    if not candidates:
        return None
    import numpy as np

    archive_path = candidates[0]
    try:
        with np.load(archive_path, allow_pickle=False) as archive:
            names = list(archive.files)
            ordered_names = [name for name in SMPL_ARRAY_NAMES if name in names]
            ordered_names.extend(name for name in names if name not in ordered_names)
            array_name = next(
                (
                    name for name in ordered_names
                    if archive[name].ndim == 3
                    and (archive[name].shape[-1] in (3, 4) or archive[name].shape[1] in (3, 4))
                ),
                None,
            )
            if array_name is None:
                raise ValueError("未找到形状为 (帧, 关节, XYZ) 的数组")
            shape = archive[array_name].shape
            frame_count = int(shape[0])
            joint_count = int(shape[1] if shape[-1] in (3, 4) else shape[2])
            frame_id_name = next((name for name in FRAME_ID_ARRAY_NAMES if name in names), None)
    except Exception as exc:
        raise ValueError(f"DOHC SMPL 骨架无法读取: {archive_path.name}: {exc}") from exc
    return {
        "topic": "/dohc/skeleton",
        "stream_key": "human_motion",
        "stream_type": "mocap",
        "display_name": "DOHC SMPL 骨架",
        "encoding": "numpy",
        "schema_name": "smpl.joint_positions",
        "adapter_id": "dohc_smpl_npz_v1",
        "message_count": frame_count,
        "nominal_hz": fps,
        "metadata": {
            "path": archive_path.relative_to(root).as_posix(),
            "array_name": array_name,
            "frame_id_name": frame_id_name,
            "joint_count": joint_count,
        },
    }


def inspect_dohc_recording(primary_file: str | Path) -> dict[str, object]:
    primary = Path(primary_file).resolve()
    if not is_dohc_primary_file(primary):
        raise ValueError(f"不是受支持的 DOHC 采集主文件: {primary}")
    root = primary.parent
    manifest = _read_json_object(primary) if primary.name.lower() == "manifest.json" else {}
    session = _read_json_object(root / "session.json", required=False) or {}
    duration_seconds = _non_negative_number(
        session.get("data_duration_seconds"),
        session.get("duration_seconds"),
        manifest.get("duration_seconds"),
    )
    manifest_streams = manifest.get("streams") if isinstance(manifest.get("streams"), dict) else {}
    default_fps = _non_negative_number(
        next(
            (
                stream.get("fps") for stream in manifest_streams.values()
                if isinstance(stream, dict) and stream.get("fps")
            ),
            None,
        ),
        30,
    ) or 30.0
    stream_specs: list[dict[str, object]] = []
    seen_topics: set[str] = set()

    for stream_name, raw_stream in sorted(manifest_streams.items()):
        if not re.fullmatch(r"cam\d+", str(stream_name), re.IGNORECASE) or not isinstance(raw_stream, dict):
            continue
        raw_segments = raw_stream.get("segments") if isinstance(raw_stream.get("segments"), list) else []
        paths = _safe_relative_files(
            root,
            [item.get("path") for item in raw_segments if isinstance(item, dict)],
        )
        count = int(_non_negative_number(raw_stream.get("frame_count"), 0) or 0)
        if not paths or count <= 0:
            continue
        fps = _non_negative_number(raw_stream.get("fps"), default_fps) or default_fps
        topic = f"/dohc/{str(stream_name).lower()}/image/jpeg"
        stream_specs.append({
            "topic": topic,
            "stream_key": str(stream_name).lower(),
            "stream_type": "camera",
            "display_name": str(stream_name).upper(),
            "encoding": "h264/mp4",
            "schema_name": "dohc.recording.video",
            "adapter_id": "dohc_mp4_v1",
            "message_count": count,
            "nominal_hz": fps,
            "metadata": {"paths": paths, "fps": fps},
        })
        seen_topics.add(topic)

    t265_manifest_path = root / "t265" / "manifest.json"
    t265_manifest = _read_json_object(t265_manifest_path, required=False) or {}
    t265_streams = t265_manifest.get("streams") if isinstance(t265_manifest.get("streams"), dict) else {}
    raw_t265_segments = t265_manifest.get("segments") if isinstance(t265_manifest.get("segments"), list) else []
    segment_paths = _safe_relative_files(
        root,
        [f"t265/segments/{item.get('name')}" for item in raw_t265_segments if isinstance(item, dict)],
    )
    for stream_name in ("t265_left", "t265_right"):
        raw_stream = t265_streams.get(stream_name)
        count = int(_non_negative_number(
            raw_stream.get("record_count") if isinstance(raw_stream, dict) else None,
            0,
        ) or 0)
        if not segment_paths or count <= 0:
            continue
        fps = (count / duration_seconds) if duration_seconds and duration_seconds > 0 else default_fps
        topic = f"/dohc/{stream_name}/image/jpeg"
        stream_specs.append({
            "topic": topic,
            "stream_key": stream_name,
            "stream_type": "camera",
            "display_name": "T265 Left" if stream_name.endswith("left") else "T265 Right",
            "encoding": "jpeg-segment-v1",
            "schema_name": "dohc.recording.jpeg_segment",
            "adapter_id": "dohc_segment_jpeg_v1",
            "message_count": count,
            "nominal_hz": fps,
            "metadata": {
                "paths": segment_paths,
                "stream_id": SEGMENT_STREAM_IDS[stream_name],
                "fps": fps,
            },
        })
        seen_topics.add(topic)

    for directory in sorted(root.iterdir(), key=lambda path: _natural_key(path.name)):
        name = directory.name.lower()
        if not directory.is_dir() or not (
            re.fullmatch(r"cam\d+", name) or name in {"t265_left", "t265_right"}
        ):
            continue
        topic = f"/dohc/{name}/image/jpeg"
        if topic in seen_topics:
            continue
        images = sorted(
            (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}),
            key=lambda path: _natural_key(path.name),
        )
        if not images:
            continue
        fps = _non_negative_number(
            manifest_streams.get(name, {}).get("fps")
            if isinstance(manifest_streams.get(name), dict) else None,
            default_fps,
        ) or default_fps
        stream_specs.append({
            "topic": topic,
            "stream_key": name,
            "stream_type": "camera",
            "display_name": name.upper(),
            "encoding": "jpeg",
            "schema_name": "dohc.recording.jpeg_directory",
            "adapter_id": "dohc_jpeg_directory_v1",
            "message_count": len(images),
            "nominal_hz": fps,
            "metadata": {
                "paths": [path.relative_to(root).as_posix() for path in images],
                "fps": fps,
            },
        })

    skeleton = _skeleton_spec(root, default_fps)
    if skeleton is not None:
        stream_specs.append(skeleton)
    if not stream_specs:
        raise ValueError("DOHC Episode 未发现可播放的相机或 SMPL 骨架流")

    inferred_duration = max(
        (
            (int(spec["message_count"]) / float(spec["nominal_hz"]))
            for spec in stream_specs
            if int(spec["message_count"]) > 0 and float(spec["nominal_hz"]) > 0
        ),
        default=0.0,
    )
    duration_ns = round((duration_seconds if duration_seconds is not None else inferred_duration) * 1_000_000_000)
    for spec in stream_specs:
        count = int(spec["message_count"])
        hz = float(spec["nominal_hz"])
        spec["first_time_ns"] = 0 if count else None
        spec["last_time_ns"] = round((count - 1) * 1_000_000_000 / hz) if count and hz else None
        spec["available"] = 1 if count else 0
    referenced_paths = {primary, root / "session.json", root / "t265" / "manifest.json"}
    for spec in stream_specs:
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
        if metadata.get("path"):
            referenced_paths.add(root / str(metadata["path"]))
        for relative_path in metadata.get("paths") or []:
            referenced_paths.add(root / str(relative_path))
    fingerprint_facts = []
    for path in sorted(
        (path.resolve() for path in referenced_paths if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    ):
        stat = path.stat()
        fingerprint_facts.append(
            [path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns]
        )
    return {
        "root": root,
        "duration_ns": max(0, duration_ns),
        "streams": stream_specs,
        "storage_format": str(manifest.get("storage_format") or "jpeg-directory-v1"),
        "fingerprint_facts": fingerprint_facts,
    }


def iter_segment_jpegs(
    root: str | Path,
    relative_paths: list[str],
    stream_id: int,
) -> Iterator[tuple[int, int, bytes]]:
    root_path = Path(root).resolve()
    first_capture_ns: int | None = None
    for relative_path in relative_paths:
        path = (root_path / relative_path).resolve()
        with path.open("rb") as source:
            while True:
                header = source.read(48)
                if not header:
                    break
                if len(header) != 48 or header[:4] != b"DHSG" or header[4] != 1:
                    raise ValueError(f"DOHC segment 记录头无效: {path.name}")
                if struct.unpack_from("<H", header, 6)[0] != 48:
                    raise ValueError(f"DOHC segment 记录头长度无效: {path.name}")
                current_stream_id = header[5]
                batch_id = struct.unpack_from("<Q", header, 8)[0]
                capture_time_ns = struct.unpack_from("<Q", header, 24)[0]
                payload_len = struct.unpack_from("<I", header, 40)[0]
                payload_crc32 = struct.unpack_from("<I", header, 44)[0]
                if payload_len > MAX_SEGMENT_PAYLOAD_BYTES:
                    raise ValueError(f"DOHC segment payload 超过上限: {path.name}")
                payload = source.read(payload_len)
                trailer = source.read(16)
                if len(payload) != payload_len or len(trailer) != 16 or trailer[:4] != b"DHSC":
                    raise ValueError(f"DOHC segment 记录被截断: {path.name}")
                trailer_batch_id = struct.unpack_from("<Q", trailer, 4)[0]
                record_crc32 = struct.unpack_from("<I", trailer, 12)[0]
                if trailer_batch_id != batch_id:
                    raise ValueError(f"DOHC segment 头尾 batch_id 不一致: {path.name}")
                if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc32:
                    raise ValueError(f"DOHC segment payload CRC32 失败: {path.name}")
                if zlib.crc32(payload, zlib.crc32(header)) & 0xFFFFFFFF != record_crc32:
                    raise ValueError(f"DOHC segment 记录 CRC32 失败: {path.name}")
                if current_stream_id != stream_id:
                    continue
                if len(payload) < 4 or not payload.startswith(b"\xff\xd8") or not payload.endswith(b"\xff\xd9"):
                    raise ValueError(f"DOHC segment 图像不是完整 JPEG: {path.name}")
                if first_capture_ns is None:
                    first_capture_ns = capture_time_ns
                yield max(0, capture_time_ns - first_capture_ns), int(batch_id), payload


def load_smpl_frames(
    root: str | Path,
    metadata: dict[str, object],
    nominal_hz: float,
) -> tuple[list[str], list[int], Iterator[tuple[int, int, list[list[float]], list[bool]]]]:
    import numpy as np

    root_path = Path(root).resolve()
    archive_path = (root_path / str(metadata["path"])).resolve()
    archive = np.load(archive_path, allow_pickle=False)
    array = np.asarray(archive[str(metadata["array_name"])])
    if array.shape[-1] in (3, 4):
        coordinates = array[..., :3]
    elif array.shape[1] in (3, 4):
        coordinates = np.moveaxis(array[:, :3, :], 1, 2)
    else:
        archive.close()
        raise ValueError("DOHC SMPL 骨架缺少 XYZ 坐标轴")
    frame_id_name = metadata.get("frame_id_name")
    frame_ids = (
        np.asarray(archive[str(frame_id_name)]).reshape(-1)
        if frame_id_name and str(frame_id_name) in archive.files
        else np.arange(coordinates.shape[0])
    )
    joint_count = int(coordinates.shape[1])
    names = (
        [
            "pelvis", "left_hip", "right_hip", "spine1", "left_knee", "right_knee",
            "spine2", "left_ankle", "right_ankle", "spine3", "left_foot", "right_foot",
            "neck", "left_collar", "right_collar", "head", "left_shoulder", "right_shoulder",
            "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hand", "right_hand",
        ]
        if joint_count == 24 else [f"joint_{index:02d}" for index in range(joint_count)]
    )
    parents = (
        [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 12, 12, 13, 14, 16, 17, 18, 19, 20, 21]
        if joint_count == 24 else [-1 for _ in range(joint_count)]
    )

    def frames() -> Iterator[tuple[int, int, list[list[float]], list[bool]]]:
        try:
            for index, raw in enumerate(coordinates):
                finite = np.isfinite(raw).all(axis=1)
                cleaned = np.where(np.isfinite(raw), raw, 0.0).astype("float32", copy=False)
                offset_ns = round(index * 1_000_000_000 / nominal_hz) if nominal_hz > 0 else index
                yield offset_ns, int(frame_ids[index]), cleaned.tolist(), finite.tolist()
        finally:
            archive.close()

    return names, parents, frames()
