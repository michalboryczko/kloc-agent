"""Parity check between `tests/fixtures/audit_events.py` constants and
dev-1's `src.db.models.AuditEventType` Literal.

Catches the C3 drift class: a typo on either side (e.g. `tool_call.crash`
vs `tool_call.crashed`) would let scenario assertions silently miss real
audit rows. Failing this test is a real bug in the implementation OR a
real bug in the test constants — both warrant immediate attention.
"""
from __future__ import annotations

import typing

import pytest

from src.db.models import AUDIT_EVENT_TYPES as MODELS_AUDIT_EVENT_TYPES
from src.db.models import AuditEventType
from tests.fixtures.audit_events import (
    ALL_EVENTS as FIXTURES_ALL_EVENTS,
)


pytestmark = pytest.mark.unit


def _literal_values(literal_type: typing.Any) -> frozenset[str]:
    """Pull the literal values out of `Literal[...]` regardless of how
    typing resolves it on this Python version."""
    return frozenset(typing.get_args(literal_type))


def test_fixtures_constants_match_models_frozenset_byte_for_byte():
    """tests/fixtures/audit_events.py:ALL_EVENTS must be set-equal to
    src/db/models.py:AUDIT_EVENT_TYPES."""
    extra_in_fixtures = FIXTURES_ALL_EVENTS - MODELS_AUDIT_EVENT_TYPES
    extra_in_models = MODELS_AUDIT_EVENT_TYPES - FIXTURES_ALL_EVENTS
    assert not extra_in_fixtures, (
        f"audit_events.py declares names not in src/db/models.py:"
        f" {sorted(extra_in_fixtures)}"
    )
    assert not extra_in_models, (
        f"src/db/models.py declares names not in audit_events.py:"
        f" {sorted(extra_in_models)}"
    )


def test_models_literal_matches_models_frozenset():
    """Catch the case where dev-1 updates one but forgets the other —
    AuditEventType Literal and AUDIT_EVENT_TYPES frozenset MUST agree."""
    literal_values = _literal_values(AuditEventType)
    assert literal_values == MODELS_AUDIT_EVENT_TYPES, (
        f"AuditEventType Literal != AUDIT_EVENT_TYPES frozenset.\n"
        f"  In Literal not frozenset: {sorted(literal_values - MODELS_AUDIT_EVENT_TYPES)}\n"
        f"  In frozenset not Literal: {sorted(MODELS_AUDIT_EVENT_TYPES - literal_values)}"
    )


def test_locked_vocabulary_has_exactly_fourteen_names():
    """Locked vocabulary is enumerated. If we ever add or remove an
    event type, this test forces an explicit decision."""
    assert len(MODELS_AUDIT_EVENT_TYPES) == 14, (
        f"expected 14 locked audit event names, got "
        f"{len(MODELS_AUDIT_EVENT_TYPES)}: {sorted(MODELS_AUDIT_EVENT_TYPES)}"
    )


def test_dotted_namespace_subgroup_intact():
    """`tool_call.*` is a deliberate sub-namespace within the otherwise
    underscore_separated vocabulary. Catch accidental renames like
    `tool_call_crashed` (underscore) breaking out of the namespace."""
    dotted = {ev for ev in MODELS_AUDIT_EVENT_TYPES if "." in ev}
    expected = {
        "tool_call.started",
        "tool_call.completed",
        "tool_call.denied",
        "tool_call.crashed",
    }
    assert dotted == expected, (
        f"tool_call.* namespace drifted; got {sorted(dotted)}, "
        f"expected {sorted(expected)}"
    )
