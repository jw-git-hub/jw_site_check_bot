# Образ сайт-чекера (ТЗ, 13.3, С13): Python 3.12, не root, внутри только код бота и зависимости.
# Версия закреплена не ниже 3.12.4: до неё в ipaddress.is_global были неверные таблицы адресов, на которые
# опирается защита сети. Слим-образ ставит ca-certificates сам — им пользуются HTTPS-запросы к сайтам и
# PageSpeed; alpine или apt-get remove/purge его бы убрали, поэтому здесь их нет.
FROM python:3.12.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot/ bot/

RUN groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data

USER 10001:10001
CMD ["python", "-m", "bot"]
