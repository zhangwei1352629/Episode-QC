import threading
import time
from concurrent.futures import ThreadPoolExecutor
from episode_qc.web_server import EpisodeQcWebApplication


def app_for_poll(callback):
    app = EpisodeQcWebApplication.__new__(EpisodeQcWebApplication)
    app._flow_client = object()
    app.flow_enabled = True
    app._platform_poll_lock = threading.Lock()
    app._platform_poll_future = None
    app._platform_poll_client = None
    app._platform_poll_snapshot = None
    app._platform_poll_executor = ThreadPoolExecutor(max_workers=1)
    app.get_platform_jobs = callback
    return app


def test_slow_refresh_returns_pending_and_does_not_duplicate_work():
    release = threading.Event()
    calls = []
    def refresh():
        calls.append(1)
        release.wait(5)
        return {'connected': True, 'jobs': [{'code': 'one'}]}
    app = app_for_poll(refresh)
    try:
        started = time.monotonic()
        assert app.poll_platform_jobs()['connection_pending']
        assert time.monotonic() - started < 1
        assert app.poll_platform_jobs()['refreshing']
        assert len(calls) == 1
        release.set()
        result = app.poll_platform_jobs()
        assert result['connected'] and result['jobs'][0]['code'] == 'one'
    finally:
        release.set()
        app._platform_poll_executor.shutdown()


def test_old_login_results_are_not_returned_to_new_login():
    release = threading.Event()
    def refresh():
        release.wait(5)
        return {'connected': True, 'jobs': [{'code': 'private-old-user'}]}
    app = app_for_poll(refresh)
    try:
        app.poll_platform_jobs()
        app._flow_client = object()
        release.set()
        result = app.poll_platform_jobs()
        assert result['jobs'] == []
        assert result['connection_pending']
    finally:
        release.set()
        app._platform_poll_executor.shutdown()


def test_error_clears_singleflight_and_can_retry():
    import pytest
    app = app_for_poll(lambda: (_ for _ in ()).throw(ValueError('offline')))
    try:
        with pytest.raises(ValueError, match='offline'):
            app.poll_platform_jobs()
        app.get_platform_jobs = lambda: {'connected': True, 'jobs': []}
        assert app.poll_platform_jobs()['connected']
    finally:
        app._platform_poll_executor.shutdown()
