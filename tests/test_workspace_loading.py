import json
from types import SimpleNamespace
import episode_qc.workspace as workspace
from episode_qc.web_server import EpisodeQcWebApplication
from test_workspace_v1 import _write_sample_episode


def test_batch_metadata_parsed_once_per_request_and_updates_visible(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    for i in range(3):
        _write_sample_episode(root / f'episode_{i:06d}')
    db = tmp_path / 'workspace.db'
    result = workspace.scan_data_source(db, root)
    raw = json.dumps({'padding': 'x' * 100000})
    with workspace.connect_workspace(db) as c:
        c.execute('UPDATE qc_task SET metadata_json=?', (raw,))
    original = workspace._loads
    calls = []
    def loads(value, fallback):
        if value == raw:
            calls.append(1)
        return original(value, fallback)
    monkeypatch.setattr(workspace, '_loads', loads)
    with workspace.connect_workspace(db) as c:
        rows = workspace._episode_rows(c, 'WHERE ds.task_id = ?', (result['task_id'],))
        assert len(rows) == 3
        assert len(calls) == 1
        workspace._episode_rows(c, 'WHERE ds.task_id = ?', (result['task_id'],))
        assert len(calls) == 2  # no process-wide stale snapshot


def test_workspace_summary_omits_bulk_history_but_detail_preserves_it(tmp_path):
    root = tmp_path / 'source'
    _write_sample_episode(root / 'episode_000001')
    db = tmp_path / 'workspace.db'
    result = workspace.scan_data_source(db, root)
    episode_id = result['episodes'][0]['id']
    history = {'attempt_version': 1, 'annotation_count': 1,
               'annotations': [{'comment': 'original audit history'}]}
    with workspace.connect_workspace(db) as c:
        c.execute('UPDATE qc_task SET metadata_json=?', (json.dumps({'padding': 'x' * 100000}),))
        c.execute('UPDATE episode SET previous_review_json=?, review_history_count=1', (json.dumps(history),))
    app = EpisodeQcWebApplication.__new__(EpisodeQcWebApplication)
    app.paths = SimpleNamespace(db_path=db, default_label_schema=tmp_path/'absent')
    state = app.get_workspace_state(result['task_id'])
    assert 'metadata' not in state['tasks'][0]
    assert 'metadata' not in state['selected_task']
    assert state['episodes'][0]['previous_review']['annotation_count'] == 1
    assert 'annotations' not in state['episodes'][0]['previous_review']
    assert workspace.episode_detail(db, episode_id)['episode']['previous_review'] == history
    with workspace.connect_workspace(db) as c:
        assert json.loads(c.execute('SELECT metadata_json FROM qc_task').fetchone()[0])['padding']
