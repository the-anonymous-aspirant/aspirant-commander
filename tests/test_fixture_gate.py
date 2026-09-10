"""The fixture gate must arm, and must fail loudly when it stops (system_3 #5665).

`scripts/check_fixture_gate.sh` armed on `*/parsers/*` and `classifier*.py`
until the #1113 refactor deleted both — it replaced the classifier-then-per-
type-parser dispatch with one strategy chain per slot — and the gate was not
moved with them. For months it passed unconditionally while reading exactly
like a gate that was protecting something. Nothing caught it, because a gate
that never arms and a gate that always passes produce identical output.

These tests pin the two halves of that failure separately: that the declared
arming paths still exist in this repo (the alarm a future refactor trips), and
that the gate actually arms, fails and passes on the diffs it is meant to.

The arming set is read out of the script rather than restated here. A second
copy of the list would drift from the first exactly as the patterns drifted
from the tree, and the test would then be pinning its own copy.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_fixture_gate.sh"

# A file the gate must NOT arm on: it carries the extraction to the operator
# but decides nothing about what is found. This is #5663's shape.
UNARMED_PATH = "app/valuation_statement/routes.py"


def _declared_arming_paths() -> list[str]:
    """Read ARMING_PATHS out of the shell script itself."""
    body = GATE_SCRIPT.read_text()
    match = re.search(r"^ARMING_PATHS=\((.*?)^\)", body, re.MULTILINE | re.DOTALL)
    assert match, "ARMING_PATHS array not found in check_fixture_gate.sh"
    paths = re.findall(r'"([^"]+)"', match.group(1))
    assert paths, "ARMING_PATHS parsed as empty"
    return paths


def _run_gate(
    changed: list[str], added: list[str] | None = None, labels: str = "", body: str = ""
) -> subprocess.CompletedProcess:
    """Run the real script in file-list mode — no git repo, no fixtures on disk."""
    return subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "CHANGED_FILES": "\n".join(changed),
            "ADDED_FILES": "\n".join(added or []),
            "PR_LABELS": labels,
            "PR_BODY": body,
        },
    )


# --- the alarm ---------------------------------------------------------------


def test_every_declared_arming_path_exists():
    """#5665 was exactly this assertion going unmade for months.

    If a refactor moves or renames one of these, this fails and somebody has to
    decide where the gate points now, instead of the gate quietly disarming.
    """
    missing = [p for p in _declared_arming_paths() if not (REPO_ROOT / p).is_file()]
    assert not missing, (
        f"check_fixture_gate.sh arms on paths that no longer exist: {missing}. "
        "The gate is disarmed for those paths. Re-point ARMING_PATHS at where "
        "the strategies live now."
    )


def test_the_unarmed_control_path_exists():
    """A negative control that silently stopped existing would prove nothing."""
    assert (REPO_ROOT / UNARMED_PATH).is_file()


# --- the behaviour -----------------------------------------------------------


@pytest.mark.parametrize("armed", _declared_arming_paths())
def test_every_declared_path_actually_arms_the_gate(armed: str):
    """Declaring a path and the script matching it are different facts."""
    result = _run_gate([armed])

    assert result.returncode == 1, f"{armed} did not arm the gate: {result.stdout}"
    assert armed in result.stdout
    assert "adds no fixture" in result.stderr


def test_touching_a_strategy_with_a_new_fixture_passes():
    armed = _declared_arming_paths()[0]

    result = _run_gate(
        [armed, "tests/fixtures/new_layout.json"],
        added=["tests/fixtures/new_layout.json"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "new fixture present" in result.stdout


def test_an_existing_fixture_does_not_satisfy_the_gate():
    """The gate wants a NEW fixture; a touched one is not coverage of new code."""
    armed = _declared_arming_paths()[0]

    result = _run_gate([armed, "tests/fixtures/old_layout.json"], added=[])

    assert result.returncode == 1, result.stdout + result.stderr


def test_a_non_strategy_change_does_not_arm_the_gate():
    """#5663's shape: diagnostics plumbing, no strategy change, no fixture owed."""
    result = _run_gate([UNARMED_PATH, "app/models.py"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate not armed" in result.stdout


def test_test_only_changes_do_not_arm_the_gate():
    result = _run_gate(["tests/test_something.py"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate not armed" in result.stdout


def test_the_deleted_parser_paths_no_longer_arm_anything():
    """The old patterns are gone, not merely unmatched by today's tree.

    Left in place they would silently re-arm on any future directory that
    happened to be called `parsers/`, which is not a rule anyone decided.
    """
    result = _run_gate(
        ["app/valuation_statement/parsers/fastighet.py", "app/valuation_statement/classifier.py"]
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate not armed" in result.stdout


def test_fixture_exempt_needs_both_the_label_and_the_justification():
    armed = _declared_arming_paths()[0]

    label_only = _run_gate([armed], labels="fixture-exempt")
    assert label_only.returncode == 1
    assert "no 'Fixture-exempt: <reason>' line" in label_only.stderr

    both = _run_gate(
        [armed],
        labels="fixture-exempt",
        body="Fixture-exempt: refactor only, no new branch semantics.",
    )
    assert both.returncode == 0, both.stdout + both.stderr
    assert "fixture-exempt label with justification" in both.stdout


def test_file_list_mode_announces_itself():
    """CI must never land in file-list mode without the log saying so."""
    result = _run_gate([UNARMED_PATH])

    assert "file-list mode" in result.stdout
