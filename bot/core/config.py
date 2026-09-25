"""Настройки из окружения: docker compose подаёт .env через env_file (ТЗ, раздел 15).

- Секреты — SecretStr; ошибка проверки не печатает значения (hide_input_in_errors, ТЗ, Сек12).
- Все значения подаёт load_settings — одно место, удобное тестам; сама модель окружение не читает.
- Опечатку в имени ключа ловит find_key_typos, иначе ключ молча игнорировался бы (перенесено из загрузчика).
"""
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import AfterValidator, BeforeValidator, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Настройки не годятся для запуска. В тексте — имена полей, без значений."""


def _require_value(secret: SecretStr) -> SecretStr:
    if not secret.get_secret_value().strip():
        raise ValueError("пустое значение")
    return secret


NonEmptySecret = Annotated[SecretStr, AfterValidator(_require_value)]


def _upper_log_level(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


# Имена совпадают с уровнями loguru (bot.core.logging.LOGURU_LEVELS): неверное имя роняет
# запуск здесь, ConfigError-ом с полем log_level, а не позже — тихим падением setup_logging.
LogLevel = Annotated[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], BeforeValidator(_upper_log_level)]


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    bot_token: NonEmptySecret
    admin_id: int
    open_to_all: bool = False
    log_level: LogLevel = "INFO"
    data_dir: Path = Path("data")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                   file_secret_settings):
        return (init_settings,)

    def secret_values(self) -> tuple[str, ...]:
        """Значения всех секретов — их маскирует журнал."""
        values = (getattr(self, name) for name in type(self).model_fields)
        return tuple(value.get_secret_value() for value in values if isinstance(value, SecretStr))


SettingsType = TypeVar("SettingsType", bound=CoreSettings)


def load_settings(settings_class: type[SettingsType], environ: Mapping[str, str] | None = None) -> SettingsType:
    source = os.environ if environ is None else environ
    _refuse_typos(source.keys(), set(settings_class.model_fields))
    try:
        return settings_class(**_values_for(settings_class, source))
    except ValidationError as error:
        fields = ", ".join(sorted({str(item["loc"][0]) for item in error.errors()}))
        raise ConfigError(f"нет или неверно заданы: {fields}") from None


def _values_for(settings_class: type[CoreSettings], source: Mapping[str, str]) -> dict[str, str]:
    wanted = {name.upper(): name for name in settings_class.model_fields}
    return {wanted[key.upper()]: value for key, value in source.items() if key.upper() in wanted}


def _refuse_typos(keys: Iterable[str], known: set[str]) -> None:
    typos = find_key_typos(keys, known)
    if typos:
        details = ", ".join(f"{bad} → похоже на {good}" for bad, good in typos)
        raise ConfigError(f"опечатка в именах ключей: {details}")


def find_key_typos(keys: Iterable[str], known: set[str]) -> list[tuple[str, str]]:
    known_upper = sorted(name.upper() for name in known)
    typos = []
    for key in keys:
        upper = key.upper()
        near = None if upper in known_upper else next((name for name in known_upper if within_one_edit(upper, name)), None)
        if near:
            typos.append((key, near))
    return typos


def within_one_edit(first: str, second: str) -> bool:
    """Одна правка: вставка, удаление, замена символа или перестановка двух соседних."""
    if abs(len(first) - len(second)) > 1:
        return False
    if len(first) == len(second):
        return _one_substitution_or_swap(first, second)
    shorter, longer = sorted((first, second), key=len)
    return any(longer[:index] + longer[index + 1:] == shorter for index in range(len(longer)))


def _one_substitution_or_swap(first: str, second: str) -> bool:
    mismatches = [index for index, (a, b) in enumerate(zip(first, second)) if a != b]
    if len(mismatches) <= 1:
        return True
    if len(mismatches) != 2:
        return False
    left, right = mismatches
    return right == left + 1 and first[left] == second[right] and first[right] == second[left]
