"""Answer key (decision 63): how many documents the accountant marked correct does today's code
read right? No model call: each one's saved model answer goes through today's code.

    make answer-key                         # measure, compare with the saved baseline
    make answer-key ARGS=--save             # ... and make this run the new baseline
    make answer-key ARGS=--show-values      # also print expected and read values (your terminal)

Prints field names and file names only by default, so the output can be shared without
exposing invoice data. Run it with --save on the code before a change, then without --save on
the change: a document that got worse must be explained before the change is committed.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select

from app.adapters.storage import NotFoundError, Storage, get_storage
from app.adapters.storage.keys import answer_key_run
from app.core.db import session_scope
from app.models import Document
from app.services import answer_key
from app.services.answer_key import FIELDS


def measure(client_id: str, documents: list[Document], session: Any, storage: Storage,
            show_values: bool, save: bool) -> int:  # fmt: skip
    wrong: dict[str, dict[str, tuple[str, str]]] = {}
    files: dict[str, str] = {}
    for document in documents:
        expected = answer_key.answer_of(storage, document)
        if expected is None:
            continue
        key = str(document.id)
        files[key] = PurePosixPath(document.source_path).name
        wrong[key] = answer_key.differences(expected, answer_key.replay(session, storage, document))
    if not wrong:
        return 0

    baseline = _baseline(storage, client_id)
    before: dict[str, list[str]] = baseline.get("wrong", {}) if baseline else {}
    total = len(wrong)
    both = [key for key in wrong if key in before]  # in the answer key at both runs
    since = f"baseline {baseline['at'][:16]}" if baseline else "no baseline yet"
    print(f"\nAnswer key, client {client_id}: {total} documents ({since})")
    print(f"{'':22}{'now':>9}{'baseline':>11}")
    right_before = sum(not before[key] for key in both)
    _row("Fully correct", sum(not w for w in wrong.values()), total, right_before, len(both))
    now_fields = Counter(field for w in wrong.values() for field in w)
    old_fields = Counter(field for key in both for field in before[key])
    for field in FIELDS:
        _row(field, total - now_fields[field], total, len(both) - old_fields[field], len(both))

    worse = {k: sorted(set(w) - set(before[k])) for k, w in wrong.items() if k in before}
    better = {k: sorted(set(before[k]) - set(w)) for k, w in wrong.items() if k in before}
    _list("WORSE than the baseline", {k: v for k, v in worse.items() if v}, files)
    _list("Better than the baseline", {k: v for k, v in better.items() if v}, files)
    print(f"\nStill wrong ({sum(bool(w) for w in wrong.values())}):")
    for key, fields in wrong.items():
        if fields:
            print(f"  {files[key]:32.32} {key[:8]}  {', '.join(fields)}")
            if show_values:
                for field, (want, got) in fields.items():
                    print(f"      {field}: expected {want}, read {got}")

    if save:
        run = {
            "at": datetime.now(UTC).isoformat(),
            "wrong": {k: sorted(w) for k, w in wrong.items()},
        }
        storage.put(answer_key_run(client_id), json.dumps(run, indent=1).encode())
        print("\nSaved as the new baseline.")
    return sum(bool(v) for v in worse.values())


def _row(label: str, right: int, total: int, right_before: int, total_before: int) -> None:
    before = f"{right_before}/{total_before}" if total_before else "-"
    print(f"  {label:20}{right:>4}/{total:<4}{before:>11}")


def _list(title: str, changed: dict[str, list[str]], files: dict[str, str]) -> None:
    print(f"\n{title} ({len(changed)}):")
    for key, fields in changed.items():
        print(f"  {files[key]:32.32} {key[:8]}  {', '.join(fields)}")


def _baseline(storage: Storage, client_id: str) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(storage.get(answer_key_run(client_id)))
    except NotFoundError:
        return None
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--save", action="store_true", help="make this run the new baseline")
    parser.add_argument("--show-values", action="store_true", help="print expected and read values")
    args = parser.parse_args()
    storage = get_storage()
    worse = 0
    with session_scope() as session:
        documents = list(session.scalars(select(Document).order_by(Document.created_at)))
        clients = sorted({d.client_id for d in documents})
        if not any(answer_key.saved(storage, d) for d in documents):
            print("No document is in the answer key yet: mark some correct in the review screen.")
            return 0
        for client_id in clients:
            mine = [d for d in documents if d.client_id == client_id]
            worse += measure(client_id, mine, session, storage, args.show_values, args.save)
        session.rollback()  # measuring only: nothing is written to the database
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
