from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from episode_qc.stream_preview import build_stream_preview


def test_stream_preview_preserves_original_frame_mapping(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    frames_path = cache_root / "camera.frames"
    frames: list[bytes] = []
    for color in ((220, 20, 20), (20, 220, 20)):
        image_path = tmp_path / f"{color[0]}.jpg"
        Image.new("RGB", (32, 24), color).save(image_path, quality=95)
        frames.append(image_path.read_bytes())
    offsets: list[int] = []
    with frames_path.open("wb") as handle:
        for ordinal, payload in enumerate(frames):
            offsets.append(handle.tell())
            handle.write(payload)
    index = [
        [1_000_000_000, offsets[0], len(frames[0]), 7],
        [1_050_000_000, offsets[1], len(frames[1]), 9],
    ]
    manifest_path = cache_root / "stream_index.json"
    manifest = {
        "cameras": [{
            "stream_id": "str_" + "a" * 24,
            "frames_file": frames_path.name,
            "index": index,
        }]
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    preview = build_stream_preview(manifest_path, manifest, cache_root / "streaming")

    assert preview["transport"] == "mp4_range_v1"
    camera = preview["cameras"][0]
    assert camera["frame_offsets_ns"] == [1_000_000_000, 1_050_000_000]
    assert camera["frame_indices"] == [7, 9]
    assert (cache_root / "streaming" / camera["file"]).stat().st_size > 0
    saved = json.loads((cache_root / "streaming" / "stream_manifest.json").read_text())
    assert saved == preview
