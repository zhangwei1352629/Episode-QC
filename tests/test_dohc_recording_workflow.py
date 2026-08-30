from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

import episode_qc.playback as playback
from episode_qc.playback import (
    prepare_episode_cache,
    read_cached_camera_frame,
    read_cached_motion_frame,
)
from episode_qc.workspace import episode_detail, scan_data_source


JPEG_A = b"\xff\xd8frame-a\xff\xd9"
JPEG_B = b"\xff\xd8frame-b\xff\xd9"


def test_content_detected_jpeg_episode_prepares_camera_and_smpl_cache(tmp_path: Path):
    root = tmp_path / "OpenTasks"
    episode_root = root / "Cafe-Refill-17"
    (episode_root / "cam0").mkdir(parents=True)
    (episode_root / "t265_left").mkdir()
    (episode_root / "cam0" / "000001.jpg").write_bytes(JPEG_A)
    (episode_root / "cam0" / "000002.jpg").write_bytes(JPEG_B)
    (episode_root / "t265_left" / "000001.jpg").write_bytes(JPEG_A)
    (episode_root / "states.jsonl").write_text(
        '{"frame_id":1,"timestamp_ns":0}\n', encoding="utf-8"
    )
    joints = np.zeros((2, 24, 3), dtype=np.float32)
    joints[1, 23] = [0.3, 0.4, 0.5]
    np.savez(
        episode_root / "smpl_skeleton.npz",
        joints=joints,
        frame_ids=np.asarray([10, 11], dtype=np.int64),
    )

    db_path = tmp_path / "workspace.db"
    indexed = scan_data_source(db_path, root, task_kind="ego_omniego")

    assert indexed["discovered"] == 1
    assert indexed["ready"] == 1
    episode = indexed["episodes"][0]
    assert episode["relative_path"] == "Cafe-Refill-17"
    assert episode["episode_name"] == "Cafe-Refill-17"
    assert episode["camera_count"] == 2
    assert episode["mocap_available"] is True
    detail = episode_detail(db_path, episode["id"])
    assert {stream["adapter_id"] for stream in detail["streams"]} == {
        "dohc_jpeg_directory_v1",
        "dohc_smpl_npz_v1",
    }

    manifest = prepare_episode_cache(
        db_path, episode["id"], tmp_path / "playback-cache", mode="full"
    )

    assert [camera["message_count"] for camera in manifest["cameras"]] == [2, 1]
    assert manifest["motion"]["available"] is True
    assert manifest["motion"]["joint_names"] == playback.SMPL_24_JOINT_NAMES
    frame = read_cached_motion_frame(manifest["manifest_path"], 40_000_000)
    assert frame["sequence"] == 11
    assert frame["positions"][23] == pytest.approx([0.3, 0.4, 0.5])
    camera = manifest["cameras"][0]
    image = read_cached_camera_frame(
        manifest["manifest_path"], camera["stream_id"], 0
    )
    assert image["jpeg"] == JPEG_A


def test_hybrid_episode_prepares_mp4_and_t265_segment_cameras_without_fake_body_pose(
    tmp_path: Path,
    monkeypatch,
):
    root = tmp_path / "OpenTasks"
    episode_root = root / "Park-Litter-009"
    (episode_root / "cam0").mkdir(parents=True)
    (episode_root / "t265" / "segments").mkdir(parents=True)
    (episode_root / "cam0" / "cam0-00000.mp4").write_bytes(b"fake-mp4")
    (episode_root / "manifest.json").write_text(
        json.dumps({
            "storage_format": "hybrid-h264-jpeg-segment-v1",
            "batch_count": 2,
            "streams": {
                "cam0": {
                    "fps": 30,
                    "frame_count": 2,
                    "segments": [{"path": "cam0/cam0-00000.mp4"}],
                }
            },
        }),
        encoding="utf-8",
    )
    (episode_root / "session.json").write_text(
        json.dumps({"data_duration_seconds": 1.0}), encoding="utf-8"
    )
    (episode_root / "t265" / "manifest.json").write_text(
        json.dumps({
            "storage_format": "jpeg-segment-v1",
            "segments": [{"name": "segment-000000.bin"}],
            "streams": {
                "t265_left": {"record_count": 1},
                "t265_right": {"record_count": 1},
                "t265_pose": {"record_count": 1},
            },
        }),
        encoding="utf-8",
    )
    segment = _segment_record(4, 1, 1_000_000_000, JPEG_A)
    segment += _segment_record(5, 1, 1_000_100_000, JPEG_B)
    (episode_root / "t265" / "segments" / "segment-000000.bin").write_bytes(segment)

    def fake_transcode(_source: Path, destination: Path) -> None:
        destination.write_bytes(JPEG_A + JPEG_B)

    monkeypatch.setattr(playback, "_transcode_media_to_mjpeg", fake_transcode)
    db_path = tmp_path / "workspace.db"
    indexed = scan_data_source(db_path, root, task_kind="ego_omniego")

    assert indexed["discovered"] == 1
    assert indexed["ready"] == 1
    episode = indexed["episodes"][0]
    assert episode["relative_path"] == "Park-Litter-009"
    assert episode["camera_count"] == 3
    assert episode["mocap_available"] is False

    manifest = prepare_episode_cache(
        db_path, episode["id"], tmp_path / "playback-cache", mode="full"
    )

    assert sorted(camera["message_count"] for camera in manifest["cameras"]) == [1, 1, 2]
    assert manifest["motion"]["available"] is False
    assert not any("pose" in error.lower() for error in manifest["decode_errors"])


def _segment_record(stream_id: int, batch_id: int, capture_time_ns: int, payload: bytes) -> bytes:
    header = bytearray(48)
    header[:4] = b"DHSG"
    header[4] = 1
    header[5] = stream_id
    struct.pack_into("<H", header, 6, 48)
    struct.pack_into("<Q", header, 8, batch_id)
    struct.pack_into("<Q", header, 16, batch_id)
    struct.pack_into("<Q", header, 24, capture_time_ns)
    struct.pack_into("<I", header, 40, len(payload))
    struct.pack_into("<I", header, 44, zlib.crc32(payload) & 0xFFFFFFFF)
    trailer = bytearray(16)
    trailer[:4] = b"DHSC"
    struct.pack_into("<Q", trailer, 4, batch_id)
    struct.pack_into(
        "<I",
        trailer,
        12,
        zlib.crc32(payload, zlib.crc32(header)) & 0xFFFFFFFF,
    )
    return bytes(header) + payload + bytes(trailer)
