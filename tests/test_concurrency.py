import pytest

from app.exceptions.domain import ScreeningBusyError, ServerBusyError
from app.services.concurrency import HeldRun, RunGuard


def test_a_screening_is_held_by_one_request_at_a_time():
    guard = RunGuard(max_concurrent_runs=2)
    with guard.exclusive("t1"):
        assert guard.is_held("t1")
        with pytest.raises(ScreeningBusyError), guard.exclusive("t1"):
            pass
        with guard.exclusive("t2"):  # other screenings are not affected
            pass
    assert not guard.is_held("t1")


def test_run_slots_cap_concurrent_runs():
    guard = RunGuard(max_concurrent_runs=1)
    with guard.run_slot(), pytest.raises(ServerBusyError), guard.run_slot():
        pass
    with guard.run_slot():  # released afterwards
        pass


def test_a_hold_that_cannot_take_the_screening_gives_its_slot_back():
    guard = RunGuard(max_concurrent_runs=1)
    with guard.exclusive("t1"), pytest.raises(ScreeningBusyError):
        guard.hold("t1")
    guard.hold("t2").close()  # the slot taken before the busy screening was refused is free again


def test_a_held_run_releases_its_hold_when_closed_without_being_read():
    guard = RunGuard(max_concurrent_runs=1)
    run = HeldRun((e for e in [{"type": "result"}]), guard.hold("t1"))
    assert guard.is_held("t1")
    run.close()
    assert not guard.is_held("t1")
    guard.hold("t1").close()  # slot and screening both free
