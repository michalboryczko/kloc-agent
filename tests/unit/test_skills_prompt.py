"""Tests for `runner.agent_factory._load_skills_prompt`.

Confirms the manual inliner reads every SKILL.md body off disk, wraps each
in a `<skill><name/><description/><body/></skill>` block, frames the whole
list with the `<available_skills>` header, and quietly skips files whose
YAML frontmatter is malformed (matching `agentskills.discover_skills`
operator semantics).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


_VALID_ALPHA = """\
---
name: alpha-skill
description: "First valid skill used by the inliner test."
---

# Alpha skill body

Use this skill when the question mentions alpha.
"""


_VALID_BETA = """\
---
name: beta-skill
description: "Second valid skill used by the inliner test."
---

# Beta skill body

Steps:
1. Do the beta thing.
2. Cite file:line for any claim.
"""


_BROKEN = """\
---
description: missing name field — frontmatter must include `name`
---

# Broken skill body

This file has invalid frontmatter and should be skipped.
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_skills_prompt_inlines_bodies_and_skips_broken(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from runner.agent_factory import _load_skills_prompt

    _write(tmp_path / "alpha-skill" / "SKILL.md", _VALID_ALPHA)
    _write(tmp_path / "beta-skill" / "SKILL.md", _VALID_BETA)
    _write(tmp_path / "broken-skill" / "SKILL.md", _BROKEN)

    with caplog.at_level(logging.WARNING):
        prompt = _load_skills_prompt(tmp_path)

    assert prompt.startswith("<available_skills>\n")
    assert prompt.rstrip().endswith("</available_skills>")

    assert "<name>alpha-skill</name>" in prompt
    assert "<name>beta-skill</name>" in prompt
    assert "# Alpha skill body" in prompt
    assert "# Beta skill body" in prompt
    assert "Cite file:line for any claim." in prompt

    assert "broken-skill" not in prompt
    assert "# Broken skill body" not in prompt

    # The leading frontmatter blocks of the valid skills must not appear
    # in the inlined output: name + description already live in the
    # `<name>` / `<description>` tags, and re-emitting them inside
    # `<body>` would mislead the model about the skill body's actual
    # surface area.
    assert "name: alpha-skill" not in prompt
    assert "name: beta-skill" not in prompt
    assert prompt.count("---\n") == 0

    assert any(
        "agentskills" in r.name or "skills" in r.message.lower()
        for r in caplog.records
    )


def test_load_skills_prompt_returns_empty_for_empty_dir(tmp_path: Path) -> None:
    from runner.agent_factory import _load_skills_prompt

    assert _load_skills_prompt(tmp_path) == ""


def test_strip_frontmatter_passes_through_when_no_frontmatter() -> None:
    from runner.agent_factory import _strip_frontmatter

    body = "# Skill without frontmatter\n\nJust body content.\n"
    assert _strip_frontmatter(body) == body


def test_strip_frontmatter_removes_leading_block() -> None:
    from runner.agent_factory import _strip_frontmatter

    raw = "---\nname: x\ndescription: y\n---\n\n# Body\n"
    assert _strip_frontmatter(raw) == "# Body\n"
