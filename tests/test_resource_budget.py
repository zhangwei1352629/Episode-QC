import pytest
from episode_qc.resource_budget import DownloadBudget


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def test_interaction_slows_download_and_idle_restores_rate():
    clock = Clock()
    budget = DownloadBudget(1, 4, clock=clock, sleep=clock.sleep)
    budget.consume(1024**2)
    assert clock.now == pytest.approx(100.25)
    budget.touch()
    budget.consume(1024**2)
    assert clock.now == pytest.approx(101.25)
    clock.now += 31
    budget.consume(2 * 1024**2)
    assert clock.now == pytest.approx(132.5)


def test_repeated_frame_requests_do_not_discard_inflight_tokens():
    clock = Clock()
    budget = DownloadBudget(1, 4, clock=clock, sleep=clock.sleep)
    budget.touch()
    clock.now += 0.25
    budget.touch()
    budget.consume(1024**2)
    assert clock.now == pytest.approx(101)
