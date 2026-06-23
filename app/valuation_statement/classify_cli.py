"""Operator-facing CLI: classify one PDF and print the matched fingerprints.

Run as a module so the relative imports resolve:

    python -m app.valuation_statement.classify_cli <file.pdf>

The classifier is content-only; the file basename is ignored even when
present on the command line. Prints the matched `DocumentType` and the
fingerprint regexes that fired, so the operator can see why a new
sample was (or was not) recognised.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.valuation_statement.classifier import (
    DocumentType,
    classify_text,
    read_first_page_text,
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2

    pdf_path = Path(args[0])
    if not pdf_path.is_file():
        print(f"error: {pdf_path}: not a regular file", file=sys.stderr)
        return 1

    pdf_bytes = pdf_path.read_bytes()
    page1_text = read_first_page_text(pdf_bytes)
    document_type, matched = classify_text(page1_text)

    print(f"document_type: {document_type.value}")
    if document_type == DocumentType.UNKNOWN:
        print("matched_fingerprints: (none)")
        return 0
    print("matched_fingerprints:")
    for pattern in matched:
        print(f"  - {pattern}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
