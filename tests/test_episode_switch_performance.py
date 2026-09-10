import time
from episode_qc.workspace import scan_data_source, connect_workspace, _episode_rows, _parsed_frozen_schema
from test_workspace_v1 import _write_sample_episode


def test_single_episode_query_skips_unrelated_history(tmp_path):
    root = tmp_path / 'source'
    _write_sample_episode(root / 'episode_000001')
    _write_sample_episode(root / 'episode_000002')
    db = tmp_path / 'workspace.db'
    result = scan_data_source(db, root)
    target, other = [e['id'] for e in result['episodes']]
    with connect_workspace(db) as c:
        c.executemany('INSERT INTO annotation (id,episode_id,label_set_key,label_schema_version,label_code,scope,start_offset_ns,end_offset_ns,target_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                      [(f'noise-{i}',other,'test','1','label','episode',0,1,'global','now','now') for i in range(20000)])
        measurements = []
        for where in ['WHERE e.id= ?', 'WHERE e.id = ?']:
            steps = [0]
            def progress():
                steps[0] += 1
                return 0
            c.set_progress_handler(progress, 100)
            started = time.perf_counter()
            rows = _episode_rows(c, where, (target,))
            measurements.append((rows, steps[0], (time.perf_counter()-started)*1000))
        c.set_progress_handler(None, 0)
    assert measurements[0][0] == measurements[1][0]
    assert measurements[1][1] < measurements[0][1] / 10
    print('detail SQL baseline_ms=%.3f optimized_ms=%.3f' % (measurements[0][2], measurements[1][2]))


def test_schema_parse_cache_uses_content_version():
    assert _parsed_frozen_schema('{"version":1}') != _parsed_frozen_schema('{"version":2}')


def test_read_ahead_only_queues_one_successor(monkeypatch):
    import threading
    from unittest.mock import Mock
    from types import SimpleNamespace
    from episode_qc.web_server import EpisodeQcWebApplication
    import episode_qc.web_server as web
    app = EpisodeQcWebApplication.__new__(EpisodeQcWebApplication)
    app.paths = SimpleNamespace(db_path='unused')
    app._jobs_lock = threading.Lock()
    app._foreground_episode_id = 'current'
    app._playback_queue = Mock()
    monkeypatch.setattr(web, 'playback_window', lambda *args: [
        {'id': 'current', 'cache_status': 'ready'},
        {'id': 'next', 'cache_status': 'missing'},
        {'id': 'later', 'cache_status': 'missing'},
    ])
    app._schedule_read_ahead('current')
    app._playback_queue.replace.assert_called_once_with([('next', 'priority')])
