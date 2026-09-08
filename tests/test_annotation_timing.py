import json
import pytest

from episode_qc.annotation_timing import annotation_timing, positive_duration_ns
from episode_qc.workspace import (
    connect_workspace, episode_detail, import_label_schema, save_annotation,
    scan_data_source, sync_flow_task_timing, update_episode_review,
)
from test_workspace_v1 import _write_sample_episode, _write_label_schema


@pytest.fixture
def flow_episode(tmp_path):
    root = tmp_path / "data"
    _write_sample_episode(root / "episode_000001")
    db = tmp_path / "workspace.db"
    job = {"code": "QCJ-BOUNDS", "episodes": [{"episode_id": "EP1", "relative_path": "episode_000001", "duration_seconds": "1.999123456"}]}
    scanned = scan_data_source(db, root, origin="flow", flow_job_code=job["code"], task_metadata={"flow_job": job})
    episode = scanned["episodes"][0]["id"]
    import_label_schema(db, _write_label_schema(tmp_path / "labels.yaml"))
    with connect_workspace(db) as con:
        con.execute("UPDATE label_definition SET scopes_json=? WHERE code='camera_blur'", (json.dumps(["time_range", "time_point", "episode"]),))
    payload = {"episode_id": episode, "label_code": "camera_blur", "scope": "time_range", "start_offset_ns": 1_000_000_000, "end_offset_ns": 1_999_123_456, "target_type": "camera", "target_key": "/camera/ego_head/image/jpeg"}
    return db, episode, job, payload


def test_detail_keeps_playback_duration_and_exposes_annotation_limit(flow_episode):
    db, episode, _, _ = flow_episode
    detail = episode_detail(db, episode)["episode"]
    assert detail["duration_ns"] > 2_000_000_000
    assert detail["annotation_duration_ns"] == detail["flow_duration_ns"] == 1_999_123_456


@pytest.mark.parametrize("mode", ["library", "open"])
@pytest.mark.parametrize("scope", ["time_range", "time_point"])
def test_save_rejects_even_one_ns_over_flow_limit(flow_episode, mode, scope):
    db, episode, _, payload = flow_episode
    if mode == "open":
        with connect_workspace(db) as con:
            con.execute("UPDATE qc_task SET annotation_mode='open'")
        payload.update(label_name="拿起物品", annotation_type="action")
    payload.update(scope=scope, end_offset_ns=1_999_123_457)
    if scope == "time_point":
        payload["start_offset_ns"] = payload["end_offset_ns"]
    with pytest.raises(ValueError, match="可标注终点"):
        save_annotation(db, payload)
    assert episode_detail(db, episode)["annotations"] == []


def test_exact_limit_accepted_and_update_cannot_reintroduce_overrun(flow_episode):
    db, episode, _, payload = flow_episode
    saved = save_annotation(db, payload)
    payload["end_offset_ns"] += 1
    with pytest.raises(ValueError, match="时间越界"):
        save_annotation(db, payload, annotation_id=saved["annotation_id"])
    assert episode_detail(db, episode)["annotations"][0]["end_offset_ns"] == 1_999_123_456


@pytest.mark.parametrize("mode", ["library", "open"])
def test_whole_episode_uses_annotation_limit(flow_episode, mode):
    db, episode, _, payload = flow_episode
    with connect_workspace(db) as con:
        con.execute("UPDATE qc_task SET annotation_mode=?", (mode,))
    payload.update(scope="episode", label_name="整体", end_offset_ns=9_000_000_000)
    saved = save_annotation(db, payload)
    assert saved["start_offset_ns"] == 0
    assert saved["end_offset_ns"] == 1_999_123_456


def test_server_snapshot_refresh_preflights_existing_annotations_without_mutation(flow_episode):
    db, episode, job, payload = flow_episode
    first = save_annotation(db, payload)
    second = save_annotation(db, payload)
    job["episodes"][0]["duration_seconds"] = "1.9"
    sync_flow_task_timing(db, job)
    before = episode_detail(db, episode)
    with pytest.raises(ValueError, match="修正以下标注") as error:
        update_episode_review(db, episode, review_status="completed", quality_decision="pass_with_labels")
    assert str(error.value).count("camera_blur") == 2
    after = episode_detail(db, episode)
    assert before["annotations"] == after["annotations"]
    assert after["episode"]["review_status"] != "completed"


@pytest.mark.parametrize("duration", [None, 0, -1, "NaN", "Infinity", "bad"])
def test_missing_or_invalid_flow_duration_blocks_writes(flow_episode, duration):
    db, episode, job, payload = flow_episode
    job["episodes"][0]["duration_seconds"] = duration
    sync_flow_task_timing(db, job)
    assert episode_detail(db, episode)["episode"]["annotation_timing_error"]
    with pytest.raises(ValueError, match="Flow 时长"):
        save_annotation(db, payload)


def test_path_matching_is_exact_and_handles_windows_primary_file():
    job = {"episodes": [{"relative_path": "episodes/one", "primary_file": "episode.mcap", "duration_seconds": "17.889"}]}
    assert annotation_timing(20_000_000_000, r".\episodes\one\episode.mcap", job, requires_flow=True)["annotation_duration_ns"] == 17_889_000_000
    assert annotation_timing(10_000_000_000, "episodes/one", job, requires_flow=True)["annotation_duration_ns"] == 10_000_000_000
    assert annotation_timing(20, "other/one", job, requires_flow=True)["annotation_timing_error"]
    job["episodes"] *= 2
    assert annotation_timing(20, "episodes/one", job, requires_flow=True)["annotation_timing_error"]


def test_standalone_unaffected_and_decimal_precision():
    assert annotation_timing(123, "a", None)["annotation_duration_ns"] == 123
    assert positive_duration_ns("38.661") == 38_661_000_000


@pytest.mark.parametrize("flow_seconds,local,old_end", [
    ("38.661", 38866183529, 38797504476),
    ("40.757", 40954415429, 40912274733),
    ("16.954", 17163789748, 17091017351),
])
def test_production_tail_overruns_rejected_at_save(flow_episode, flow_seconds, local, old_end):
    db, episode, job, payload = flow_episode
    with connect_workspace(db) as con:
        con.execute("UPDATE episode SET duration_ns=? WHERE id=?", (local, episode))
    job["episodes"][0]["duration_seconds"] = flow_seconds
    sync_flow_task_timing(db, job)
    payload["end_offset_ns"] = old_end
    with pytest.raises(ValueError, match="时间越界"):
        save_annotation(db, payload)


def test_redo_cannot_restore_annotation_outside_refreshed_bounds(flow_episode):
    from episode_qc.workspace import undo_annotation_change, redo_annotation_change
    db, episode, job, payload = flow_episode
    save_annotation(db, payload, session_id="test")
    undo_annotation_change(db, session_id="test")
    job["episodes"][0]["duration_seconds"] = "1.9"
    sync_flow_task_timing(db, job)
    with pytest.raises(ValueError, match="越界"):
        redo_annotation_change(db, session_id="test")
    assert episode_detail(db, episode)["annotations"] == []


def test_http_detail_and_save_share_flow_bound(tmp_path):
    from urllib.error import HTTPError
    from test_web_server import running_server, request_json
    with running_server(tmp_path) as (server, url):
        root = tmp_path / "data"
        _write_sample_episode(root / "episode_000001")
        db = server.application.paths.db_path
        job = {"code": "QCJ-HTTP-BOUND", "episodes": [{"episode_id": "EP1", "relative_path": "episode_000001", "duration_seconds": "1.999"}]}
        result = scan_data_source(db, root, origin="flow", flow_job_code=job["code"], task_metadata={"flow_job": job})
        episode = result["episodes"][0]["id"]
        import_label_schema(db, _write_label_schema(tmp_path / "labels.yaml"))
        _, detail = request_json(f"{url}/api/episodes/{episode}")
        assert detail["episode"]["annotation_duration_ns"] == 1_999_000_000
        payload = {"episode_id": episode, "scope": "time_range", "label_code": "camera_blur", "target_type": "camera", "start_offset_ns": 0, "end_offset_ns": 2_000_000_000}
        with pytest.raises(HTTPError) as error:
            request_json(f"{url}/api/annotations", method="POST", payload={"payload": payload})
        assert error.value.code == 400
        assert "可标注终点" in json.loads(error.value.read())["error"]
        payload["end_offset_ns"] = 1_999_000_000
        status, _ = request_json(f"{url}/api/annotations", method="POST", payload={"payload": payload})
        assert status == 200
