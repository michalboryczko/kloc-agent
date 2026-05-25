"""Tests for `runner/tools/project_files.py:read_project_file`.

Each case constructs a fake `/projects` tree under `tmp_path`, points
`KLOC_PROJECTS_DIR` at it, and calls the tool directly. The tool is
decorated with Strands' `@tool` when available, but the underlying
function remains callable; tests exercise that surface.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit


def _call(tool, **kwargs) -> str:
    """Call the tool whether it's a Strands-decorated wrapper or the
    plain-Python fallback. The Strands decorator typically keeps the
    underlying callable accessible; if not, fall through to the wrapper."""
    if callable(tool):
        try:
            return tool(**kwargs)
        except TypeError:
            pass
    inner = getattr(tool, "func", None) or getattr(tool, "__wrapped__", None)
    if inner is not None:
        return inner(**kwargs)
    raise AssertionError("read_project_file is not callable")


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("KLOC_PROJECTS_DIR", str(root))
    return root


@pytest.fixture
def kyc(projects_dir):
    proj = projects_dir / "kyc"
    src = proj / "src"
    src.mkdir(parents=True)
    (src / "A.php").write_text('<?php\necho "hi from kyc";\n', encoding="utf-8")
    (src / "Multi.txt").write_text(
        "line-one\nline-two\nline-three\nline-four\nline-five\n",
        encoding="utf-8",
    )
    return proj


def test_happy_path_full_read(kyc) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(read_project_file, project_name="kyc", path="src/A.php")
    assert "<?php" in out
    assert "hi from kyc" in out


def test_line_slice_inclusive(kyc) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(
        read_project_file,
        project_name="kyc",
        path="src/Multi.txt",
        start_line=2,
        end_line=4,
    )
    assert out == "line-two\nline-three\nline-four\n"


def test_invalid_project_name_short_circuits(projects_dir) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(
        read_project_file, project_name="..bad..", path="anything"
    )
    assert out.startswith("error: invalid project_name")


def test_invalid_project_name_uppercase(projects_dir) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(read_project_file, project_name="Kyc", path="src/A.php")
    assert out.startswith("error: invalid project_name")


def test_traversal_via_double_dot(kyc, projects_dir) -> None:
    from runner.tools.project_files import read_project_file

    (projects_dir / "other").mkdir()
    (projects_dir / "other" / "secret").write_text("nope", encoding="utf-8")

    out = _call(
        read_project_file, project_name="kyc", path="../other/secret"
    )
    assert "escape" in out.lower(), out


def test_symlink_to_sibling_rejected(kyc, projects_dir) -> None:
    from runner.tools.project_files import read_project_file

    (projects_dir / "other-proj").mkdir()
    sibling = projects_dir / "other-proj" / "secret.txt"
    sibling.write_text("nope", encoding="utf-8")

    danger = kyc / "danger"
    os.symlink(sibling, danger)

    out = _call(read_project_file, project_name="kyc", path="danger")
    assert "escape" in out.lower(), out


def test_symlink_to_outside_volume_rejected(kyc, tmp_path) -> None:
    from runner.tools.project_files import read_project_file

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    danger = kyc / "outside-link"
    os.symlink(outside, danger)

    out = _call(
        read_project_file, project_name="kyc", path="outside-link"
    )
    assert "escape" in out.lower(), out


def test_unmounted_project(projects_dir) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(
        read_project_file, project_name="missing", path="src/A.php"
    )
    assert "not mounted" in out.lower(), out


def test_directory_rejected(kyc) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(read_project_file, project_name="kyc", path="src")
    assert "not a regular file" in out, out


def test_binary_file_rejected(kyc) -> None:
    from runner.tools.project_files import read_project_file

    bin_file = kyc / "bin.dat"
    bin_file.write_bytes(b"\x00\x01\x02\xff\xfe\xfd" * 16)
    out = _call(read_project_file, project_name="kyc", path="bin.dat")
    assert "binary" in out, out


def test_oversize_without_slice_denied(monkeypatch, kyc) -> None:
    """When the runtime cap is small and no slice is requested, the
    runner-side tool surfaces the same oversize error string the
    BeforeToolCall evaluator would have produced. Either layer can
    catch this; both refusing is also acceptable."""
    from runner.tools import project_files

    big = kyc / "big.txt"
    big.write_text("a" * 5000, encoding="utf-8")

    monkeypatch.setattr(project_files, "_max_bytes", lambda: 1024)
    out = _call(
        project_files.read_project_file, project_name="kyc", path="big.txt"
    )
    assert "cap" in out.lower(), out


def test_oversize_with_slice_allowed(monkeypatch, kyc) -> None:
    """A line-slice request bypasses the whole-file cap because the
    slice is read line-by-line and only the requested range is
    returned. A 5 MiB file with a 50-line slice MUST be permitted."""
    from runner.tools import project_files

    big = kyc / "big.txt"
    big.write_text("hello\nworld\n" * 1000, encoding="utf-8")

    monkeypatch.setattr(project_files, "_max_bytes", lambda: 1024)
    out = _call(
        project_files.read_project_file,
        project_name="kyc",
        path="big.txt",
        start_line=1,
        end_line=2,
    )
    assert out == "hello\nworld\n"


def test_empty_path_rejected(kyc) -> None:
    from runner.tools.project_files import read_project_file

    out = _call(read_project_file, project_name="kyc", path="")
    assert out.startswith("error: invalid path"), out
