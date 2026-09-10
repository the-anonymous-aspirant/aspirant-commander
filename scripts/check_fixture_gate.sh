#!/usr/bin/env bash
# Fixture-gate: PRs touching an extraction strategy must include a new fixture
# file under tests/fixtures/. Override: fixture-exempt label + `Fixture-exempt:`
# line in the PR body.
#
# Plan §A7 of corpus/agent-excellence-plan-2026-06-23.md (system_3 task #1094).
#
# THE ARMING SET IS DECLARED, NOT PATTERN-MATCHED, AND IT IS PINNED BY A TEST.
# This gate previously armed on `*/parsers/*` and `classifier*.py`. The #1113
# refactor deleted both — it replaced the classifier-then-per-type-parser
# dispatch with one strategy chain per slot — and the gate was not moved with
# them, so for months it passed unconditionally while reading exactly like a
# gate that was protecting something (system_3 #5665). A silently-disarmed
# gate is worse than no gate: it is a promise nobody checks.
#
# So: every path below must EXIST, and tests/test_fixture_gate.py asserts that.
# If a refactor moves or renames one of these files, that test fails and
# somebody has to decide where the gate points now. Adding a new module that
# defines extraction strategies means adding it here; the test cannot detect
# that, which is why this comment says it out loud.
#
# Usage (local):
#   BASE_REF=origin/main HEAD_REF=HEAD ./scripts/check_fixture_gate.sh
#
# Usage (CI):
#   BASE_REF, HEAD_REF, PR_LABELS, PR_BODY are passed via env from the workflow.
#
# Usage (file-list mode, for tests):
#   CHANGED_FILES=$'a.py\nb.py' ADDED_FILES='' ./scripts/check_fixture_gate.sh
#   Both must be set; git is not consulted.
#
# Exit codes: 0 pass, 1 fail (gate violated), 2 misuse (missing env).

set -euo pipefail

BASE_REF="${BASE_REF:-origin/main}"
HEAD_REF="${HEAD_REF:-HEAD}"
PR_LABELS="${PR_LABELS:-}"
PR_BODY="${PR_BODY:-}"

# Where the file lists come from. Normally a git diff; in file-list mode the
# caller supplies them directly, which is how the arming logic is unit-tested
# without a git repo (the service image carries no git, so a git-dependent test
# would skip in the very surface that runs the suite — the same shape of hole
# this gate just came out of).
#
# File-list mode needs BOTH variables and says so on stdout, so a CI run can
# never land in it quietly: the workflow passes BASE_REF/HEAD_REF and neither of
# these, and if that ever changed the log would carry the line.
if [ -n "${CHANGED_FILES+x}" ] && [ -n "${ADDED_FILES+x}" ]; then
    echo "NOTE: file-list mode — using CHANGED_FILES/ADDED_FILES, not a git diff."
    changed_files="${CHANGED_FILES}"
    added_files="${ADDED_FILES}"
else
    if ! git rev-parse --verify "${BASE_REF}" >/dev/null 2>&1; then
        echo "FAIL: base ref '${BASE_REF}' not found in repo" >&2
        exit 2
    fi
    if ! git rev-parse --verify "${HEAD_REF}" >/dev/null 2>&1; then
        echo "FAIL: head ref '${HEAD_REF}' not found in repo" >&2
        exit 2
    fi

    merge_base="$(git merge-base "${BASE_REF}" "${HEAD_REF}")"

    changed_files="$(git diff --name-only --diff-filter=ACMR "${merge_base}" "${HEAD_REF}")"
    added_files="$(git diff --name-only --diff-filter=A "${merge_base}" "${HEAD_REF}")"
fi

# Files that ARM the gate: the modules that decide what a document extracts.
#
#   field_extractor.py  the strategy library itself — CONTENT_GUARDS, the
#                       per-slot Strategy chains, and every predicate they run.
#   extraction.py       the chain runner and the outcome semantics
#                       (extracted / recognised_no_fields / unrecognised /
#                       no_text), i.e. what a miss is called.
#   _context.py         the two text projections every strategy queries; a
#                       change here changes what all of them see.
#
# Deliberately NOT armed: routes.py, api_schemas.py, processed.py, template.py,
# pdf_export.py, transparency.py. Those carry the extraction to the operator;
# they do not decide what it finds. Task #5663 is the worked example — it added
# diagnostic persistence through routes.py and models.py, changed no strategy,
# and should not have been asked for a fixture.
ARMING_PATHS=(
    "app/valuation_statement/field_extractor.py"
    "app/valuation_statement/extraction.py"
    "app/valuation_statement/_context.py"
)

strategy_files_touched=""
while IFS= read -r path; do
    [ -z "${path}" ] && continue
    case "${path}" in
        tests/*) continue ;;  # test changes don't arm the gate
        */__pycache__/*) continue ;;
    esac
    for armed in "${ARMING_PATHS[@]}"; do
        if [ "${path}" = "${armed}" ]; then
            strategy_files_touched="${strategy_files_touched}${path}"$'\n'
            break
        fi
    done
done <<< "${changed_files}"

if [ -z "${strategy_files_touched}" ]; then
    echo "PASS: no extraction-strategy changes; gate not armed."
    exit 0
fi

echo "Gate armed by these extraction-strategy changes:"
printf '  - %s\n' ${strategy_files_touched}

# Check for ≥1 new file under tests/fixtures/
new_fixture=""
while IFS= read -r path; do
    [ -z "${path}" ] && continue
    case "${path}" in
        tests/fixtures/*|tests/*/fixtures/*)
            new_fixture="${path}"
            break
            ;;
    esac
done <<< "${added_files}"

if [ -n "${new_fixture}" ]; then
    echo "PASS: new fixture present (${new_fixture})."
    exit 0
fi

# No fixture. Check for fixture-exempt label + justification in body.
if echo ",${PR_LABELS}," | grep -q ",fixture-exempt,"; then
    if echo "${PR_BODY}" | grep -qE '^[[:space:]]*Fixture-exempt:[[:space:]]*[^[:space:]]'; then
        echo "PASS: fixture-exempt label with justification."
        exit 0
    fi
    echo "FAIL: fixture-exempt label is set but PR body has no 'Fixture-exempt: <reason>' line." >&2
    echo "Add a line like: 'Fixture-exempt: refactor only, no new branch semantics.'" >&2
    exit 1
fi

echo "FAIL: PR changes an extraction strategy but adds no fixture under tests/fixtures/." >&2
echo "Either:" >&2
echo "  (a) add ≥1 new fixture file under tests/fixtures/ that exercises the new code path, OR" >&2
echo "  (b) apply the 'fixture-exempt' label AND include a 'Fixture-exempt: <reason>' line in the PR body." >&2
echo "" >&2
echo "Rationale: plan §A7 (corpus/agent-excellence-plan-2026-06-23.md) — every change to what a document extracts ships with a golden fixture." >&2
exit 1
