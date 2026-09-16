import threading

from episode_qc import web_server


class RecordingQueue:
    def __init__(self):
        self.jobs = None

    def replace(self, jobs):
        self.jobs = list(jobs)


def _application_with_queue(queue):
    app = web_server.EpisodeQcWebApplication.__new__(web_server.EpisodeQcWebApplication)
    app.paths = type("Paths", (), {"db_path": "unused.sqlite3"})()
    app._jobs_lock = threading.Lock()
    app._foreground_episode_id = "ep-current"
    app._playback_queue = queue
    return app


def test_read_ahead_prioritizes_two_successors_before_full_foreground(monkeypatch):
    queue = RecordingQueue()
    app = _application_with_queue(queue)

    monkeypatch.setattr(
        web_server,
        "playback_window",
        lambda _db_path, _episode_id: [
            {"id": "ep-current", "cache_status": "partial"},
            {"id": "ep-next-1", "cache_status": "stale"},
            {"id": "ep-next-2", "cache_status": "failed"},
        ],
    )

    app._schedule_read_ahead("ep-current")

    assert queue.jobs == [
        ("ep-next-1", "priority"),
        ("ep-next-2", "priority"),
        ("ep-current", "full"),
    ]


def test_read_ahead_skips_successors_that_already_have_playable_cache(monkeypatch):
    queue = RecordingQueue()
    app = _application_with_queue(queue)

    monkeypatch.setattr(
        web_server,
        "playback_window",
        lambda _db_path, _episode_id: [
            {"id": "ep-current", "cache_status": "ready"},
            {"id": "ep-next-1", "cache_status": "partial"},
            {"id": "ep-next-2", "cache_status": "ready"},
        ],
    )

    app._schedule_read_ahead("ep-current")

    assert queue.jobs == []
