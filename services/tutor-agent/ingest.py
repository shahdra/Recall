"""Turn an upload into plain text for the Card-Generator.

Every failure raises ``IngestError`` carrying a message written for the learner,
not for a log: they chose the file, so they are the one who can fix the problem.
"""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
"""10 MB. Generous for lecture notes, small enough to keep a pod's memory sane."""

PDF_MAGIC = b"%PDF"


class IngestError(Exception):
    """An upload we cannot turn into study text. The message is user-facing."""


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, OSError, ValueError) as exc:
        logger.warning("unreadable PDF: %s", exc)
        raise IngestError(
            "That PDF looks corrupted and I couldn't read it. "
            "Try re-saving it, or paste the text instead."
        ) from exc

    if reader.is_encrypted:
        # A blank owner password is common and harmless to try.
        try:
            if reader.decrypt("") == 0:
                raise IngestError(
                    "That PDF is password-protected, so I can't read it. "
                    "Remove the password, or paste the text instead."
                )
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(
                "That PDF is password-protected, so I can't read it. "
                "Remove the password, or paste the text instead."
            ) from exc

    try:
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, OSError, ValueError, KeyError) as exc:
        logger.warning("failed extracting PDF pages: %s", exc)
        raise IngestError(
            "I couldn't pull the text out of that PDF. Try pasting the text instead."
        ) from exc

    text = "\n".join(pages).strip()
    if not text:
        raise IngestError(
            "That PDF has no text I can read — it may be a scan or images. "
            "Try pasting the text instead."
        )
    return text


def _extract_plain(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    if not text.strip():
        raise IngestError("That upload was empty — there's nothing to make cards from.")
    return text.strip()


def extract_text(data: bytes, content_type: str) -> str:
    """Extract study text from an upload.

    Args:
        data: Raw file bytes.
        content_type: The declared MIME type. Trusted only as a hint — PDFs are
            also detected by magic bytes, since browsers sometimes send
            ``application/octet-stream``.

    Returns:
        Non-empty plain text, stripped.

    Raises:
        IngestError: With a message suitable for showing the learner directly.
    """
    if not data:
        raise IngestError("That upload was empty — there's nothing to make cards from.")

    if len(data) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise IngestError(
            f"That file is too large (limit {limit_mb} MB). "
            "Try splitting it, or paste just the section you're studying."
        )

    base_type = (content_type or "").split(";")[0].strip().lower()

    if base_type == "application/pdf" or data[:4] == PDF_MAGIC:
        return _extract_pdf(data)

    if base_type in ("text/plain", "text/markdown", "application/octet-stream", ""):
        return _extract_plain(data)

    raise IngestError(
        f"I don't support {base_type} files yet. "
        "Upload a PDF or paste your text instead."
    )
