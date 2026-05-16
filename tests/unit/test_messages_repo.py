"""Tests for `src/repos/messages.py:MessageRepo.append`.

Regression: `_next_seq` (SELECT max(seq)+1) + INSERT was not atomic.
Two concurrent inserts for the same `session_id` could read the same
max and the second INSERT trip `uq_messages_session_seq`. Fix:
catch `IntegrityError`, rollback the savepoint, retry up to
`_MAX_SEQ_RETRIES` with a fresh seq read.

These tests drive the retry loop through a fake AsyncSession so we
don't need a live Postgres.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.unit


class _FakeSavepoint:
    def __init__(self) -> None:
        self.rolled_back = False
        self.committed = False

    async def rollback(self) -> None:
        self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True


class _FakeResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    """Minimal async-session double for MessageRepo.append.

    Configurable: `fail_remaining` controls how many `flush()` calls
    raise IntegrityError before succeeding.
    """

    def __init__(self, fail_remaining: int = 0) -> None:
        self.fail_remaining = fail_remaining
        self.added: list[object] = []
        self.flush_calls = 0
        self.savepoints: list[_FakeSavepoint] = []
        self._max_seq = 0

    async def execute(self, stmt):
        return _FakeResult(self._max_seq)

    def add(self, row) -> None:
        self.added.append(row)
        row.id = uuid.uuid4()

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise IntegrityError("INSERT", {}, Exception("dup seq"))
        # Successful flush — bump the simulated max so a subsequent
        # _next_seq call (in another append) reads the next value.
        self._max_seq += 1

    async def begin_nested(self) -> _FakeSavepoint:
        sp = _FakeSavepoint()
        self.savepoints.append(sp)
        return sp


async def test_append_succeeds_on_first_try() -> None:
    from src.repos.messages import MessageRepo

    sess = _FakeSession(fail_remaining=0)
    repo = MessageRepo(sess)  # type: ignore[arg-type]
    row = await repo.append(
        session_id=uuid.uuid4(), role="user", content="hi"
    )
    assert sess.flush_calls == 1
    assert row.content == "hi"
    assert sess.savepoints[0].committed is True


async def test_append_retries_on_integrity_error() -> None:
    from src.repos.messages import MessageRepo

    sess = _FakeSession(fail_remaining=1)
    repo = MessageRepo(sess)  # type: ignore[arg-type]
    row = await repo.append(
        session_id=uuid.uuid4(), role="assistant", content="hello"
    )
    assert sess.flush_calls == 2  # one fail + one success
    assert sess.savepoints[0].rolled_back is True
    assert sess.savepoints[1].committed is True
    assert row is not None


async def test_append_raises_after_max_retries_exhausted() -> None:
    from src.repos.messages import MessageRepo, _MAX_SEQ_RETRIES

    sess = _FakeSession(fail_remaining=_MAX_SEQ_RETRIES + 5)
    repo = MessageRepo(sess)  # type: ignore[arg-type]
    with pytest.raises(IntegrityError):
        await repo.append(
            session_id=uuid.uuid4(), role="user", content="x"
        )
    assert sess.flush_calls == _MAX_SEQ_RETRIES
