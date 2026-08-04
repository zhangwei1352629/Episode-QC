from __future__ import annotations

from pathlib import Path

import pytest

from episode_qc.playback import prepare_episode_cache, read_cached_motion_frame
from episode_qc.workspace import episode_detail, scan_data_source


def test_record_bvh_can_be_indexed_and_prepared_for_qc(tmp_path: Path):
    episode_root = tmp_path / "asset" / "episodes" / "episode_000001"
    episode_root.mkdir(parents=True)
    (episode_root / "motion.bvh").write_text(
        """HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Yrotation Xrotation
  JOINT Head
  {
    OFFSET 0 100 0
    CHANNELS 3 Zrotation Yrotation Xrotation
    End Site
    {
      OFFSET 0 20 0
    }
  }
}
MOTION
Frames: 2
Frame Time: 0.010000
100 200 300 0 0 0 0 0 0
100 200 300 90 0 0 0 0 0
""",
        encoding="utf-8",
    )
    (episode_root / "metadata.json").write_text('{"episode_id":"EP-1"}\n', encoding="utf-8")
    db_path = tmp_path / "workspace.db"

    indexed = scan_data_source(db_path, tmp_path / "asset")

    assert indexed["discovered"] == 1
    assert indexed["ready"] == 1
    episode = indexed["episodes"][0]
    assert episode["mocap_available"] is True
    assert episode["duration_ns"] == 20_000_000
    detail = episode_detail(db_path, episode["id"])
    assert detail["episode"]["summary_path"].endswith("metadata.json")
    assert detail["streams"][0]["adapter_id"] == "bvh_v1"
    assert detail["streams"][0]["message_count"] == 2

    manifest = prepare_episode_cache(db_path, episode["id"], tmp_path / "playback-cache")

    assert manifest["motion"]["available"] is True
    assert manifest["motion"]["joint_names"] == ["Hips", "Head"]
    assert manifest["motion"]["parent_indices"] == [-1, 0]
    first = read_cached_motion_frame(manifest["manifest_path"], 0)
    second = read_cached_motion_frame(manifest["manifest_path"], 10_000_000)
    assert first["positions"][0] == pytest.approx([1.0, 2.0, 3.0])
    assert first["positions"][1] == pytest.approx([1.0, 3.0, 3.0])
    assert second["positions"][1] == pytest.approx([0.0, 2.0, 3.0], abs=1e-6)
    assert second["frame_index"] == 1
