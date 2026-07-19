from __future__ import annotations

import json
from pathlib import Path

from episode_qc.mcap_video import VideoFrame, iter_video_frames, list_image_topics


def index_annotation_folder(root_path: str | Path) -> dict[str, object]:
    root = Path(root_path)
    if not root.exists():
        raise FileNotFoundError(f"path does not exist: {root}")

    mcap_paths = _find_mcap_paths(root)
    files = [_index_one_mcap(path) for path in mcap_paths]
    return _folder_index_payload(root, files)


def export_annotation_frame(
    mcap_path: str | Path,
    *,
    topic: str,
    frame_index: int,
    output_path: str | Path,
) -> dict[str, object]:
    if frame_index < 0:
        raise ValueError("frame index must be non-negative")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    for frame in iter_video_frames(mcap_path, topics=[topic], max_frames_per_topic=frame_index + 1):
        if frame.index == frame_index:
            output.write_bytes(frame.jpeg)
            return _frame_payload(frame, output)

    raise IndexError(f"frame {frame_index} not found on topic {topic}")


def annotation_payload_to_json(payload: dict[str, object], *, indent: int = 2) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def _find_mcap_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".mcap" else []
    return sorted(path for path in root.rglob("*.mcap") if path.is_file())


def _index_one_mcap(mcap_path: Path) -> dict[str, object]:
    try:
        topics = list_image_topics(mcap_path)
        return {
            "path": str(mcap_path),
            "episode": mcap_path.parent.name,
            "ok": True,
            "topics": [
                {
                    "name": topic.name,
                    "channel_id": topic.channel_id,
                    "message_count": topic.message_count,
                    "message_encoding": topic.message_encoding,
                    "schema_name": topic.schema_name,
                }
                for topic in topics
            ],
        }
    except Exception as exc:
        return {
            "path": str(mcap_path),
            "episode": mcap_path.parent.name,
            "ok": False,
            "error": str(exc),
            "topics": [],
        }


def _folder_index_payload(root: Path, files: list[dict[str, object]]) -> dict[str, object]:
    ok_files = [file for file in files if file.get("ok")]
    topic_count = 0
    frame_count = 0
    for file in ok_files:
        topics = file.get("topics", [])
        if not isinstance(topics, list):
            continue
        topic_count += len(topics)
        frame_count += sum(int(topic.get("message_count", 0) or 0) for topic in topics if isinstance(topic, dict))

    return {
        "root": str(root),
        "summary": {
            "files": len(files),
            "scanned_files": len(ok_files),
            "failed_files": len(files) - len(ok_files),
            "topics": topic_count,
            "frames": frame_count,
        },
        "files": files,
    }


def _frame_payload(frame: VideoFrame, output_path: Path) -> dict[str, object]:
    return {
        "topic": frame.topic,
        "frame_index": frame.index,
        "log_time_ns": frame.log_time_ns,
        "publish_time_ns": frame.publish_time_ns,
        "sequence": frame.sequence,
        "timestamp_ns": frame.timestamp_ns,
        "frame_id": frame.frame_id,
        "format": frame.format,
        "output_path": str(output_path),
    }
