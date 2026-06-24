#!/usr/bin/env bash
# Fixture-gate: PRs touching parser branches or the classifier must include a
# new fixture file under tests/fixtures/. Override: fixture-exempt label +
# `Fixture-exempt:` line in the PR body.
#
# Plan §A7 of corpus/agent-excellence-plan-2026-06-23.md (system_3 task #1094).
#
# Usage (local):
#   BASE_REF=origin/main HEAD_REF=HEAD ./scripts/check_fixture_gate.sh
#
# Usage (CI):
#   BASE_REF, HEAD_REF, PR_LABELS, PR_BODY are passed via env from the workflow.
#
# Exit codes: 0 pass, 1 fail (gate violated), 2 misuse (missing env).

set -euo pipefail

BASE_REF="${BASE_REF:-origin/main}"
HEAD_REF="${HEAD_REF:-HEAD}"
PR_LABELS="${PR_LABELS:-}"
PR_BODY="${PR_BODY:-}"

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

# Files that ARM the gate: parser branches or classifier implementation.
# Match any path containing /parsers/ (excluding __init__ and leading-underscore
# helpers) OR a file whose basename starts with "classifier". Exclude test files
# (the gate is about implementation, not test changes).
parser_or_classifier_touched=""
while IFS= read -r path; do
    [ -z "${path}" ] && continue
    case "${path}" in
        tests/*) continue ;;  # test changes don't arm the gate
        */__pycache__/*) continue ;;
    esac
    base="$(basename "${path}")"
    case "${path}" in
        */parsers/*)
            case "${base}" in
                __init__.py|_*.py) continue ;;  # package boilerplate + helpers
            esac
            parser_or_classifier_touched="${parser_or_classifier_touched}${path}"$'\n'
            ;;
    esac
    case "${base}" in
        classifier*.py)
            parser_or_classifier_touched="${parser_or_classifier_touched}${path}"$'\n'
            ;;
    esac
done <<< "${changed_files}"

if [ -z "${parser_or_classifier_touched}" ]; then
    echo "PASS: no parser/classifier implementation changes; gate not armed."
    exit 0
fi

echo "Gate armed by these parser/classifier changes:"
printf '  - %s\n' ${parser_or_classifier_touched}

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

echo "FAIL: PR touches parser/classifier code but adds no fixture under tests/fixtures/." >&2
echo "Either:" >&2
echo "  (a) add ≥1 new fixture file under tests/fixtures/ that exercises the new code path, OR" >&2
echo "  (b) apply the 'fixture-exempt' label AND include a 'Fixture-exempt: <reason>' line in the PR body." >&2
echo "" >&2
echo "Rationale: plan §A7 (corpus/agent-excellence-plan-2026-06-23.md) — every parser branch and classifier output ships with a golden fixture." >&2
exit 1
