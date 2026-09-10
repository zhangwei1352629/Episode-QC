import episode_qc.workspace as workspace
from test_workspace_v1 import _write_sample_episode


def test_full_scan_does_not_mark_concurrently_imported_episode_missing(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    _write_sample_episode(root / 'episode_000001')
    db = tmp_path / 'workspace.db'
    workspace.scan_data_source(db, root)
    discover = workspace._discover_episode_mcaps

    def racing_discovery(*args, **kwargs):
        candidates = discover(*args, **kwargs)
        _write_sample_episode(root / 'episode_000002')
        workspace.scan_data_source(db, root, episode_files=['episode_000002/episode.mcap'])
        return candidates

    monkeypatch.setattr(workspace, '_discover_episode_mcaps', racing_discovery)
    workspace.scan_data_source(db, root)
    with workspace.connect_workspace(db) as connection:
        rows = connection.execute('SELECT import_status FROM episode').fetchall()
    assert len(rows) == 2
    assert all(row['import_status'] == 'ready' for row in rows)


def test_full_scan_still_marks_truly_missing_file(tmp_path):
    root = tmp_path / 'source'
    _write_sample_episode(root / 'episode_000001')
    db = tmp_path / 'workspace.db'
    workspace.scan_data_source(db, root)
    (root / 'episode_000001/episode.mcap').unlink()
    workspace.scan_data_source(db, root)
    with workspace.connect_workspace(db) as connection:
        assert connection.execute('SELECT import_status FROM episode').fetchone()[0] == 'source_missing'
