"""Redis transport that serves the worker's queues in QUEUE_ORDER (decision 31).

kombu 5.6's Redis channel gives its queue cycle a *set* of queue names, so with
`queue_order_strategy: priority` the BRPOP order is the set's iteration order, which Python's
hash randomisation changes on every worker start. On some starts the extract queue came first:
reads and checks then waited behind every model extraction, and no document reached review
until the whole run had been read by the model (seen 2026-10-26). This channel hands the cycle
the queues in QUEUE_ORDER instead; queues not listed there follow in the order consumed.
"""

from kombu.transport import redis

from app.workers.celery_app import QUEUE_ORDER


class Channel(redis.Channel):
    def _update_queue_cycle(self) -> None:
        rank = {name: index for index, name in enumerate(QUEUE_ORDER)}
        consumed = [
            q for q in dict.fromkeys(self._active_queues) if q not in self.active_fanout_queues
        ]
        ordered = sorted(consumed, key=lambda queue: rank.get(queue, len(rank)))
        self._queue_cycle.update(ordered)  # type: ignore[attr-defined]  # set in __init__


class Transport(redis.Transport):
    Channel = Channel
