import os
import pytest
from episode_qc.isolated_work import IsolatedWork
from episode_qc.workspace import initialize_workspace


def test_spawned_index_uses_separate_process_and_propagates_errors(tmp_path):
    work = IsolatedWork()
    try:
        worker_pid = work._pools['index'].submit(os.getpid).result(timeout=15)
        assert worker_pid != os.getpid()
        foreground_pid = work._pools['playback'].submit(os.getpid).result(timeout=15)
        background_pid = work._pools['playback_background'].submit(os.getpid).result(timeout=15)
        assert len({worker_pid, foreground_pid, background_pid, os.getpid()}) == 4
        root = tmp_path / 'source'
        root.mkdir()
        db = tmp_path / 'workspace.db'
        initialize_workspace(db)
        result = work.call('index', db, root, episode_files=[])
        assert result['scanned_episode_count'] == 0
        with pytest.raises(ValueError):
            work.call('index', db, root, episode_files=['../missing.mcap'])
        # A validation failure must not kill the worker or require retrying writes.
        assert work.call('index', db, root, episode_files=[])['ready'] == 0
    finally:
        work.close()
