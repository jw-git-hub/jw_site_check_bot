"""Настройки сайт-чекера (ТЗ, раздел 15). Общие поля — в core.config.CoreSettings."""
from bot.core.config import CoreSettings, NonEmptySecret


class Settings(CoreSettings):
    pagespeed_api_key: NonEmptySecret
    user_daily_limit: int = 10
    global_daily_limit: int = 500
    check_workers: int = 2
    queue_max: int = 30
