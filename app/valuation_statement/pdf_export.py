"""Convert a docx byte-string to a pdf byte-string via LibreOffice headless.

LibreOffice's `soffice --headless --convert-to pdf` reads a file off disk
and writes the output file to a directory; we shuffle bytes through a
per-call tempdir to keep the interface pure (bytes in → bytes out).

`soffice` is expected on PATH inside the container (Dockerfile-Commander
installs `libreoffice-core` + `libreoffice-writer`). When it's missing
(e.g. local dev without LibreOffice), `LibreOfficeUnavailable` surfaces a
clean 503 from the endpoint.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


logger = logging.getLogger(__name__)


class LibreOfficeUnavailable(RuntimeError):
    """soffice is not on PATH — PDF export is not configured."""


class LibreOfficeConversionFailed(RuntimeError):
    """soffice ran but did not produce the expected output PDF."""


SOFFICE_TIMEOUT_SECONDS = 60


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    soffice = _resolve_soffice()
    with tempfile.TemporaryDirectory(prefix="vardeutlatande_") as workdir:
        work = Path(workdir)
        src = work / "input.docx"
        src.write_bytes(docx_bytes)

        env = os.environ.copy()
        # soffice writes a per-user profile dir under $HOME by default; in
        # a slim container that's often `/`, which has no perms.
        env["HOME"] = str(work)

        try:
            result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--norestore",
                    "--nolockcheck",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(work),
                    str(src),
                ],
                capture_output=True,
                env=env,
                timeout=SOFFICE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LibreOfficeConversionFailed(
                f"soffice timed out after {SOFFICE_TIMEOUT_SECONDS}s"
            ) from exc

        if result.returncode != 0:
            logger.error(
                "soffice returned %d. stderr=%r stdout=%r",
                result.returncode, result.stderr, result.stdout,
            )
            raise LibreOfficeConversionFailed(
                f"soffice exited {result.returncode}: {result.stderr.decode(errors='replace')}"
            )

        pdf_path = work / "input.pdf"
        if not pdf_path.exists():
            raise LibreOfficeConversionFailed(
                "soffice exited 0 but did not produce input.pdf"
            )
        return pdf_path.read_bytes()


def _resolve_soffice() -> str:
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path:
            return path
    raise LibreOfficeUnavailable(
        "Neither 'soffice' nor 'libreoffice' was found on PATH. "
        "Install libreoffice-core in the container to enable PDF export."
    )
