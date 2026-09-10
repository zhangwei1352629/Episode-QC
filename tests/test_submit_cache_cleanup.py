import json
import threading
from unittest.mock import Mock

import pytest

from episode_qc.web_server import EpisodeQcWebApplication


def application():
    app = EpisodeQcWebApplication.__new__(EpisodeQcWebApplication)
    app._platform_lock = threading.RLock()
    app._workspace_write_lock = threading.RLock()
    app._platform_result_jobs = set()
    app._platform_ownership_errors = {}
    app._platform_claim_lock = Mock(return_value=threading.Lock())
    app._submit_platform_job_once = Mock(return_value={"job": {"status": "completed"}})
    app._delete_verified_submitted_cache = Mock()
    return app


def test_submit_keeps_cache_without_opt_in():
    app = application()
    app.submit_platform_job("QCJ-1")
    app._delete_verified_submitted_cache.assert_not_called()


def test_failed_submit_never_deletes_cache():
    app = application()
    app._submit_platform_job_once.side_effect = ValueError("Flow rejected")
    with pytest.raises(ValueError):
        app.submit_platform_job("QCJ-1", delete_cache=True)
    app._delete_verified_submitted_cache.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_cleanup_status_does_not_change_successful_submit(failure):
    app = application()
    if failure:
        app._delete_verified_submitted_cache.side_effect = OSError("file busy")
    response = app.submit_platform_job("QCJ-1", delete_cache=True)
    assert response["job"]["status"] == "completed"
    assert response["cache_cleanup"]["status"] == ("failed" if failure else "deleted")


@pytest.mark.parametrize("mismatch", [False, True])
def test_delete_requires_live_result_and_nas_readback(tmp_path, mismatch):
    app = application()
    manager = Mock(cache_root=tmp_path)
    path = tmp_path / "ready" / "QCJ-1" / ".qc-cache.json"
    path.parent.mkdir(parents=True)
    result = {"status": "completed", "result_id": "R1", "result_sha256": "sha", "result_nas_path": "/nas/result.json", "result_manifest": {"files": []}}
    path.write_text(json.dumps({**result, "result_synced": True}))
    manager._state_path.return_value = path
    app._quality_cache_manager = Mock(return_value=manager)
    app._require_flow_client = Mock(return_value=Mock(job=Mock(return_value={**result, **({"result_id": "other"} if mismatch else {})})))
    if mismatch:
        with pytest.raises(ValueError):
            EpisodeQcWebApplication._delete_verified_submitted_cache(app, "QCJ-1", {"job": result})
        manager.evict.assert_not_called()
    else:
        EpisodeQcWebApplication._delete_verified_submitted_cache(app, "QCJ-1", {"job": result})
        manager._verify_result_readback.assert_called_once()
        manager.evict.assert_called_once_with("QCJ-1")
