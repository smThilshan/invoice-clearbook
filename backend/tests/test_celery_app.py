import pytest
from celery import Celery
from pydantic import ValidationError

from app.core.config import Settings
from app.workers.celery_app import (
    EXTRACT_QUEUE,
    PIPELINE_QUEUE,
    POSTING_QUEUE,
    create_celery_app,
)
from app.workers.tasks import ping


def make_app(**overrides: object) -> Celery:
    return create_celery_app(Settings(_env_file=None, **overrides))


def test_queues_and_routing() -> None:
    app = make_app()
    router = app.amqp.router

    # quick jobs first, model extraction last (decision 31)
    assert [q.name for q in app.conf.task_queues] == [PIPELINE_QUEUE, POSTING_QUEUE, EXTRACT_QUEUE]
    for queue in app.conf.task_queues:  # each queue on its own exchange, never shared
        assert (queue.exchange.name, queue.routing_key) == (queue.name, queue.name)
    assert router.route({}, "posting.create_bill")["queue"].name == POSTING_QUEUE
    assert router.route({}, "pipeline.extract_document")["queue"].name == EXTRACT_QUEUE
    assert router.route({}, "pipeline.read_document")["queue"].name == PIPELINE_QUEUE
    assert router.route({}, "pipeline.verify_document")["queue"].name == PIPELINE_QUEUE
    assert router.route({}, "system.ping")["queue"].name == PIPELINE_QUEUE


def test_worker_takes_quick_jobs_before_model_extraction() -> None:
    """kombu's Redis channel ordered the queues as a set: random per worker start (decision 31)."""
    from kombu.utils.scheduling import priority_cycle

    from app.workers.redis_transport import Channel, Transport

    with make_app().connection_for_read() as connection:
        assert type(connection.transport) is Transport  # built, not connected
    channel = Channel.__new__(Channel)  # the order logic only: no Redis connection
    channel._active_queues = [EXTRACT_QUEUE, "other", POSTING_QUEUE, PIPELINE_QUEUE, EXTRACT_QUEUE]
    channel.active_fanout_queues = set()
    channel._queue_cycle = priority_cycle()

    channel._update_queue_cycle()

    assert channel._queue_cycle.consume(4) == [
        PIPELINE_QUEUE,
        POSTING_QUEUE,
        EXTRACT_QUEUE,
        "other",
    ]


def test_reliability_settings() -> None:
    conf = make_app(job_time_limit_seconds=900, job_visibility_timeout_seconds=1200).conf

    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_ignore_result is True
    assert conf.task_time_limit == 900
    assert conf.broker_transport_options == {
        "visibility_timeout": 1200,
        "queue_order_strategy": "priority",
    }
    assert conf.accept_content == ["json"]


def test_broker_url_from_settings() -> None:
    app = make_app(redis_url="redis://queue-host:6380/2")

    assert app.conf.broker_url == "redis://queue-host:6380/2"


@pytest.mark.parametrize("visibility", [1800, 600])
def test_visibility_timeout_must_exceed_time_limit(visibility: int) -> None:
    with pytest.raises(ValidationError, match="VISIBILITY_TIMEOUT"):
        Settings(
            _env_file=None, job_time_limit_seconds=1800, job_visibility_timeout_seconds=visibility
        )


def test_ping_task_runs() -> None:
    assert ping.name == "system.ping"
    assert ping.apply().get() == "pong"


def test_worker_command_listens_in_queue_order() -> None:
    from pathlib import Path

    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()

    assert "-Q pipeline,posting,extract " in makefile
