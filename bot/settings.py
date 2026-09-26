"""Настройки сайт-чекера (ТЗ, раздел 15). Общие поля — в core.config.CoreSettings."""
from pydantic import PositiveInt

from bot.core.config import CoreSettings, NonEmptySecret


class Settings(CoreSettings):
    pagespeed_api_key: NonEmptySecret
    user_daily_limit: PositiveInt = 10
    global_daily_limit: PositiveInt = 500
    check_workers: PositiveInt = 2
    queue_max: PositiveInt = 30
