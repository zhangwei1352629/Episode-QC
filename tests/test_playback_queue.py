import threading

from episode_qc.playback_queue import PlaybackQueue


def test_queue_replaces_obsolete_pending_work_and_bounds_lookahead():
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    seen = []
    def run(episode, mode):
        seen.append((episode, mode))
        if episode == "running":
            started.set()
            assert release.wait(2)
        if episode == "next2":
            done.set()
    worker = PlaybackQueue(run)
    try:
        worker.replace([("running", "priority"), ("obsolete", "full")])
        assert started.wait(2)
        worker.replace([(item, "priority") for item in ["current", "next1", "next2", "too_far"]])
        release.set()
        assert done.wait(2)
        assert seen == [(item, "priority") for item in ["running", "current", "next1", "next2"]]
    finally:
        release.set()
        worker.close()


def test_closed_queue_does_not_accept_work():
    worker = PlaybackQueue(lambda *_: (_ for _ in ()).throw(AssertionError()))
    worker.close()
    worker.replace([("episode", "full")])
    assert worker._thread is None


def test_failed_preload_does_not_block_next_episode():
    done = threading.Event()
    def run(episode, mode):
        if episode == "bad":
            raise ValueError("bad source")
        done.set()
    worker = PlaybackQueue(run)
    try:
        worker.replace([("bad", "priority"), ("good", "priority")])
        assert done.wait(2)
    finally:
        worker.close()
