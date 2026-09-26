from bot.core.access import OpenGate, is_open_for
from bot.core.throttle import ThrottleMiddleware
from tests.fakes import ADMIN_ID, FakeClock, fake_bot, make_callback, make_message


class Recorder:
    def __init__(self):
        self.handled, self.closed = [], []

    async def handler(self, event, data):
        self.handled.append(event)

    async def on_closed(self, event):
        self.closed.append(event)


def test_open_for_admin_always_and_for_everyone_after_launch(settings):
    assert is_open_for(settings, ADMIN_ID)
    assert not is_open_for(settings, 500)
    assert is_open_for(settings.model_copy(update={"open_to_all": True}), 500)


async def test_gate_stops_strangers_but_lets_start_and_admin_through(settings):
    recorder = Recorder()
    gate = OpenGate(settings, recorder.on_closed)
    await gate(recorder.handler, make_message("example.com", user_id=500), {})
    await gate(recorder.handler, make_message("/start channel", user_id=500), {})
    await gate(recorder.handler, make_message("example.com", user_id=ADMIN_ID), {})
    assert len(recorder.closed) == 1
    assert len(recorder.handled) == 2


async def test_gate_rejects_look_alike_command_but_allows_start_with_botname(settings):
    """«/startx» — не «/start»: не должен обходить режим до запуска (ТЗ, Р4)."""
    recorder = Recorder()
    gate = OpenGate(settings, recorder.on_closed)
    await gate(recorder.handler, make_message("/startx", user_id=500), {})
    await gate(recorder.handler, make_message("/start@jw_site_check_bot fb", user_id=500), {})
    assert len(recorder.closed) == 1
    assert len(recorder.handled) == 1


async def test_gate_handles_whitespace_only_text_without_crashing(settings):
    """Текст из одних пробелов (включая полноширинный «　») не должен ронять разбор команды."""
    recorder = Recorder()
    gate = OpenGate(settings, recorder.on_closed)
    for text in ("   ", " ", "　"):
        await gate(recorder.handler, make_message(text, user_id=500), {})
    assert len(recorder.closed) == 3
    assert len(recorder.handled) == 0


async def test_throttle_blocks_second_message_within_interval_and_notifies_once():
    clock = FakeClock()
    recorder = Recorder()
    sent_notices: list[tuple[int, str | None]] = []

    async def send_notice(chat_id: int, language_code: str | None) -> None:
        sent_notices.append((chat_id, language_code))

    throttle = ThrottleMiddleware(2.0, lambda code: "Слишком часто", send_notice, clock.monotonic)
    bot = fake_bot()
    first, second, third = (make_message("a").as_(bot) for _ in range(3))
    await throttle(recorder.handler, first, {})
    await throttle(recorder.handler, second, {})
    await throttle(recorder.handler, third, {})
    assert len(recorder.handled) == 1
    assert sent_notices == [(second.chat.id, second.from_user.language_code)]
    assert [call.__api_method__ for call in bot.session.calls] == []
    clock.advance(2.5)
    await throttle(recorder.handler, make_message("b").as_(bot), {})
    assert len(recorder.handled) == 2


async def test_throttled_button_is_always_answered():
    clock = FakeClock()
    recorder = Recorder()

    async def send_notice(chat_id: int, text: str) -> None:
        raise AssertionError("send_notice не вызывается для нажатий кнопки")

    throttle = ThrottleMiddleware(0.7, lambda code: "Слишком часто", send_notice, clock.monotonic)
    bot = fake_bot()
    await throttle(recorder.handler, make_callback("again").as_(bot), {})
    await throttle(recorder.handler, make_callback("again").as_(bot), {})
    assert [call.__api_method__ for call in bot.session.calls] == ["answerCallbackQuery"]
