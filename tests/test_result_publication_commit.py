import json

import pytest

from episode_qc.platform_workflow import QualityCacheError, QualityCacheManager


@pytest.fixture
def submission(tmp_path):
    asset = tmp_path / "asset"
    asset.mkdir()
    latest = asset / "qc_result.json"
    latest.write_text('{"result_id":"previous-accepted"}\n')
    job = {
        "code": "QCJ-COMMIT", "asset_id": "AST-COMMIT",
        "status": "in_progress", "source_uri": str(asset),
        "result_upload_uri": str(tmp_path / "results" / "AST-COMMIT" / "QCJ-COMMIT"),
        "episodes": [{"episode_id": "EP-1"}],
    }
    cache = QualityCacheManager(tmp_path / "cache", reserve_bytes=0)
    state = cache.cache_root / "ready" / job["code"] / ".qc-cache.json"
    cache._write_json_atomic(state, {
        "job_code": job["code"], "asset_id": job["asset_id"],
        "cache_complete": True, "episodes": [{"episode_id": "EP-1", "status": "ready"}],
    })
    values = {"episode_results": [{"episode_id": "EP-1", "decision": "pass", "annotation_count": 0}]}
    return cache, job, state, latest, values


@pytest.mark.parametrize("failure", ["rejected", "incomplete", "lost_response"])
def test_unaccepted_result_preserves_previous_asset_result(submission, failure):
    cache, job, state, latest, values = submission
    previous = latest.read_bytes()

    class Client:
        def submit_result(self, *args, **kwargs):
            assert latest.read_bytes() == previous
            if failure == "rejected":
                raise ValueError("Flow rejected the result")
            if failure == "lost_response":
                raise OSError("response lost after commit")
            return {"status": "in_progress"}

    with pytest.raises((ValueError, OSError, QualityCacheError)):
        cache.submit_result(Client(), job, **values)
    assert latest.read_bytes() == previous
    assert json.loads(state.read_text())["pending_result"]


def test_mirror_failure_after_acceptance_is_retryable(submission, monkeypatch):
    cache, job, state, latest, values = submission
    previous = latest.read_bytes()
    ids = []

    class Client:
        def submit_result(self, *args, **kwargs):
            ids.append(kwargs["result_id"])
            return {"status": "completed"}

    def fail_latest(*args, **kwargs):
        raise OSError("NAS offline")

    with monkeypatch.context() as patch:
        patch.setattr(cache, "_publish_latest_result_copy", fail_latest)
        with pytest.raises(QualityCacheError):
            cache.submit_result(Client(), job, **values)
    assert latest.read_bytes() == previous
    assert not json.loads(state.read_text()).get("result_synced")
    cache.submit_result(Client(), {**job, "status": "completed"}, **values)
    assert ids[0] == ids[1]
    assert json.loads(latest.read_text())["result_id"] == ids[0]
    assert json.loads(state.read_text())["result_synced"]


def test_spot_check_never_replaces_current_result(submission):
    cache, job, state, latest, values = submission
    previous = latest.read_bytes()

    class Client:
        def submit_result(self, *args, **kwargs):
            return {"status": "completed"}

    cache.submit_result(Client(), {**job, "affects_current_result": False}, **values)
    assert latest.read_bytes() == previous
    assert json.loads(state.read_text())["result_synced"]
