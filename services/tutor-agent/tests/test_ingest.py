from pathlib import Path

import pytest

from ingest import MAX_UPLOAD_BYTES, IngestError, extract_text

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_bytes()


def test_extracts_text_from_a_real_pdf():
    text = extract_text(_read("good.pdf"), "application/pdf")
    assert "Mitochondria" in text
    assert "ATP" in text


def test_plain_text_passes_through():
    assert extract_text(b"just some notes", "text/plain") == "just some notes"


def test_utf8_text_is_decoded():
    assert "café" in extract_text("café notes".encode("utf-8"), "text/plain")


def test_corrupt_pdf_raises_ingest_error():
    with pytest.raises(IngestError) as exc:
        extract_text(_read("corrupt.pdf"), "application/pdf")
    assert "pdf" in str(exc.value).lower()


def test_encrypted_pdf_raises_ingest_error():
    with pytest.raises(IngestError) as exc:
        extract_text(_read("encrypted.pdf"), "application/pdf")
    assert "password" in str(exc.value).lower() or "protect" in str(exc.value).lower()


def test_pdf_with_no_extractable_text_raises_ingest_error():
    """A scanned PDF is images only — we cannot make cards from it."""
    with pytest.raises(IngestError) as exc:
        extract_text(_read("empty.pdf"), "application/pdf")
    assert "no text" in str(exc.value).lower() or "scanned" in str(exc.value).lower()


def test_empty_upload_raises_ingest_error():
    with pytest.raises(IngestError):
        extract_text(b"", "application/pdf")


def test_oversized_upload_raises_ingest_error():
    with pytest.raises(IngestError) as exc:
        extract_text(b"x" * (MAX_UPLOAD_BYTES + 1), "text/plain")
    assert "large" in str(exc.value).lower()


def test_unsupported_content_type_raises_ingest_error():
    with pytest.raises(IngestError) as exc:
        extract_text(b"\x89PNG\r\n", "image/png")
    assert "support" in str(exc.value).lower()


def test_content_type_with_charset_suffix_is_accepted():
    assert extract_text(b"notes", "text/plain; charset=utf-8") == "notes"


def test_pdf_detected_by_magic_bytes_when_content_type_is_generic():
    """Browsers sometimes send application/octet-stream for a PDF."""
    text = extract_text(_read("good.pdf"), "application/octet-stream")
    assert "Mitochondria" in text


def test_ingest_error_message_is_user_facing():
    """The message goes straight to the learner, so it must not be a traceback."""
    with pytest.raises(IngestError) as exc:
        extract_text(_read("corrupt.pdf"), "application/pdf")
    message = str(exc.value)
    assert "Traceback" not in message
    assert message[0].isupper()


def test_whitespace_only_text_upload_raises():
    with pytest.raises(IngestError):
        extract_text(b"   \n\t  ", "text/plain")
