import asyncio

import pytest

from bot.site_check.queue import CheckJob, CheckQueue
from bot.site_check.url_input import Target
from tests.fakes import FakeClock


def job(user_id: int) -> CheckJob:
    target = Target("https", False, f"site{user_id}.org", f"site{user_id}.org", "/", "")
    return CheckJob(user_id, user_id, user_id, 100 + user_id, "ru", "direct", target, False, False)


async def wait_until(condition, attempts: int = 200) -> None:
    for _ in range(attempts):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("не дождался")


@pytest.fixture
async def held():
    """Очередь, где проверки ждут, пока тест не отпустит."""
    release = asyncio.Event()
    started: list[int] = []

    async def run(check: CheckJob) -> None:
        started.append(check.user_id)
        await release.wait()

    queue = CheckQueue(workers=2, max_size=2, run=run, clock=FakeClock())
    queue.start()
    yield queue, release, started
    release.set()
    await queue.stop()


def put(queue: CheckQueue, user_id: int) -> None:
    queue.reserve(user_id, f"site{user_id}.org")
    queue.submit(job(user_id))


async def test_free_worker_means_nobody_ahead(held):
    queue, _, _ = held
    assert queue.ahead_now() == 0


async def test_positions_count_running_and_waiting(held):
    queue, _, started = held
    put(queue, 1)
    put(queue, 2)
    await wait_until(lambda: len(started) == 2)
    assert queue.ahead_now() == 2
    put(queue, 3)
    assert queue.ahead_now() == 3


async def test_busy_user_is_released_after_the_check(held):
    queue, release, started = held
    put(queue, 1)
    await wait_until(lambda: started == [1])
    assert queue.busy_display(1) == "site1.org"
    release.set()
    await wait_until(queue.is_idle)
    assert queue.busy_display(1) is None


async def test_queue_refuses_beyond_its_size(held):
    queue, _, started = held
    put(queue, 1)
    put(queue, 2)
    await wait_until(lambda: len(started) == 2)
    put(queue, 3)
    put(queue, 4)
    assert queue.is_full()
    with pytest.raises(asyncio.QueueFull):
        queue.submit(job(5))


async def test_failing_check_does_not_stop_the_worker():
    done: list[int] = []

    async def run(check: CheckJob) -> None:
        if check.user_id == 1:
            raise RuntimeError("упала")
        done.append(check.user_id)

    queue = CheckQueue(workers=1, max_size=5, run=run, clock=FakeClock())
    queue.start()
    put(queue, 1)
    put(queue, 2)
    await wait_until(lambda: done == [2])
    assert queue.busy_display(1) is None
    await queue.stop()


def test_estimate_in_minutes_by_rounds_of_workers():
    queue = CheckQueue(workers=2, max_size=5, run=None, clock=FakeClock())
    assert queue.estimate_minutes(2) == 1  # один круг по 40 секунд
    assert queue.estimate_minutes(5) == 2  # три круга — 120 секунд
