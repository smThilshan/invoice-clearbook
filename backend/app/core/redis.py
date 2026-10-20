"""Redis connection for app-side checks. Celery manages its own broker connections."""

import logging
from functools import lru_cache

from redis import Redis, RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 2


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=_TIMEOUT_SECONDS,
        socket_timeout=_TIMEOUT_SECONDS,
    )


def ping_redis() -> bool:
    try:
        return bool(get_redis().ping())
    except RedisError as exc:
        # One line, no traceback: probes poll this often.
        logger.warning("redis ping failed: %s", type(exc).__name__)
        return False
