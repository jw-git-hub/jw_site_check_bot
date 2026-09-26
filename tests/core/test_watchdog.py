import threading

import pytest
from aiogram.methods import GetMe, GetUpdates

from bot.core.watchdog import EVENT_LOOP, POLLING, Heartbeat, PollingPulse, start_watchdog


class ManualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_watchdog_exits_when_polling_is_silent(tmp_path):
    clock = ManualClock()
    heartbeat = Heartbeat(clock)
    heartbeat.beat(EVENT_LOOP)
    heartbeat.beat(POLLING)
    exits: list[int] = []

    def sleep(seconds):
        clock.now += seconds
        heartbeat.beat(EVENT_LOOP)  # цикл событий жив, опрос молчит

    thread = start_watchdog(heartbeat, limit=30, every=10, exit_process=exits.append, alive_file=tmp_path / "alive",
                            sleep=sleep)
    thread.join(timeout=5)
    assert exits == [1]


def test_watchdog_touches_alive_file_while_all_is_well(tmp_path):
    heartbeat = Heartbeat()
    exits: list[int] = []
    rounds: list[float] = []
    first_round_done = threading.Event()

    def sleep(seconds):
        if rounds:
            first_round_done.set()
            threading.Event().wait()  # дальше поток-демон ждёт вечно и тестам не мешает
        rounds.append(seconds)
        heartbeat.beat(EVENT_LOOP)
        heartbeat.beat(POLLING)

    start_watchdog(heartbeat, exit_process=exits.append, alive_file=tmp_path / "alive", sleep=sleep)
    assert first_round_done.wait(timeout=5)
    assert (tmp_path / "alive").exists()
    assert exits == []


async def test_polling_pulse_beats_even_when_request_fails():
    clock = ManualClock()
    heartbeat = Heartbeat(clock)
    heartbeat.beat(POLLING)

    async def failing(bot, method):
        raise RuntimeError("нет сети")

    clock.now = 500.0
    with pytest.raises(RuntimeError):
        await PollingPulse(heartbeat)(failing, None, GetUpdates(timeout=10))
    assert heartbeat.silent((POLLING,), limit=120) == []


async def test_other_requests_do_not_count_as_polling():
    clock = ManualClock()
    heartbeat = Heartbeat(clock)
    heartbeat.beat(POLLING)

    async def answer(bot, method):
        return True

    clock.now = 500.0
    await PollingPulse(heartbeat)(answer, None, GetMe())
    assert heartbeat.silent((POLLING,), limit=120) == [POLLING]
