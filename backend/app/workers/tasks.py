"""Celery tasks. Thin: they call pipeline/services and are safe to re-run.

Names are explicit ("<area>.<action>") so they stay stable when code moves; "posting.*" tasks
are routed to the posting queue, everything else to the pipeline queue.
"""

from app.workers.celery_app import celery_app


@celery_app.task(name="system.ping")
def ping() -> str:
    """Proves a worker is consuming jobs end to end."""
    return "pong"
