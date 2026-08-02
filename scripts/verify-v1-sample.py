#!/usr/bin/env python3
"""Run the V1 import/playback/annotation/export vertical slice on a real dataset."""

from __future__ import annotations

import argparse
import csv
import io
import json
import tempfile
from pathlib import Path

from PIL import Image

from episode_qc.playback import prepare_episode_cache, read_cached_camera_frame, read_cached_motion_frame
from episode_qc.workspace import (
    episode_detail,
    export_workspace,
    import_label_schema,
    initialize_workspace,
    save_annotation,
    scan_data_source,
    update_episode_review,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()

    dataset_root = args.dataset_root.resolve()
    source_files = sorted(dataset_root.rglob("episode.mcap"))
    before = {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in source_files}
    with tempfile.TemporaryDirectory(prefix="episode-qc-v1-verify-") as temporary:
        test_root = Path(temporary)
        db_path = test_root / "workspace" / "workspace.db"
        initialize_workspace(db_path, name="20260717 集成测试", reviewer_name="integration-test")
        imported = scan_data_source(db_path, dataset_root, profile_path=args.profile)
        if imported["failed"]:
            raise AssertionError(f"{imported['failed']} Episode 导入失败")
        import_label_schema(db_path, args.labels)

        episode_summary = min(imported["episodes"], key=lambda item: Path(str(item["mcap_path"])).stat().st_size)
        episode_id = str(episode_summary["id"])
        detail = episode_detail(db_path, episode_id)
        cache = prepare_episode_cache(db_path, episode_id, test_root / "cache")
        if not cache["cameras"]:
            raise AssertionError("测试 Episode 未发现有效相机")
        if not cache["motion"]["available"]:
            raise AssertionError("测试 Episode 的 Mocap Adapter 未产出帧")

        duration = int(detail["episode"]["duration_ns"])
        sample_times = [0, duration // 2, duration]
        frame_checks = []
        for time_ns in sample_times:
            frame = read_cached_camera_frame(cache["manifest_path"], cache["cameras"][0]["stream_id"], time_ns)
            with Image.open(io.BytesIO(frame["jpeg"])) as image:
                image.verify()
            frame_checks.append({"requested_ns": time_ns, "frame_index": frame["frame_index"], "skew_ns": frame["skew_ns"]})
        motion = read_cached_motion_frame(cache["manifest_path"], duration // 2)
        if len(motion["positions"]) != len(motion["joint_names"]):
            raise AssertionError("Mocap 关节名称与位置数量不一致")

        camera = cache["cameras"][0]
        annotation = save_annotation(
            db_path,
            {
                "episode_id": episode_id,
                "label_code": "camera_blur",
                "scope": "time_range",
                "start_offset_ns": min(1_000_000_000, duration // 4),
                "end_offset_ns": min(2_000_000_000, duration // 2),
                "target_type": "camera",
                "target_key": camera["topic"],
                "severity": "normal",
                "action": "keep_with_label",
                "comment": "V1 集成测试标注",
                "attributes": {},
            },
            session_id="integration-test",
        )
        update_episode_review(
            db_path,
            episode_id,
            review_status="completed",
            quality_decision="pass_with_labels",
            last_playhead_ns=duration // 2,
        )
        exported = export_workspace(db_path, test_root / "exports", episode_ids=[episode_id])
        export_dir = Path(str(exported["output_dir"]))
        jsonl_count = len((export_dir / "annotations.jsonl").read_text(encoding="utf-8").splitlines())
        with (export_dir / "annotations.csv").open(encoding="utf-8-sig", newline="") as source:
            csv_count = len(list(csv.DictReader(source)))
        if jsonl_count != csv_count or jsonl_count != 1:
            raise AssertionError("JSONL 与 CSV 标注数量不一致")

        after = {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in source_files}
        if before != after:
            raise AssertionError("集成测试修改了源 MCAP")

        result = {
            "dataset_root": str(dataset_root),
            "episodes_discovered": imported["discovered"],
            "episodes_ready": imported["ready"],
            "episodes_failed": imported["failed"],
            "tested_episode": detail["episode"]["episode_name"],
            "duration_sec": round(duration / 1e9, 3),
            "camera_count": len(cache["cameras"]),
            "camera_frames": {item["display_name"]: item["message_count"] for item in cache["cameras"]},
            "motion_frames": cache["motion"]["message_count"],
            "motion_joints": len(cache["motion"]["joint_names"]),
            "frame_checks": frame_checks,
            "annotation_id": annotation["annotation_id"],
            "export_episode_count": exported["episode_count"],
            "export_annotation_count": exported["annotation_count"],
            "source_files_unchanged": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
