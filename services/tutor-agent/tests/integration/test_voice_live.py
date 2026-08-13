"""Live Deepgram transcription checks.

Marked ``integration`` so the default unit run — and CI — skips them: they need a
real DEEPGRAM_API_KEY and make network calls. Run them when changing voice.py or
upgrading the Deepgram SDK, since neither the fake-client tests nor CI would
catch a real API contract change.

    pytest tests/integration/test_voice_live.py -m integration -v

Fixture audio is generated on demand with macOS ``say`` + ``afconvert``, so no
binary blobs live in the repo.
"""

import os
import pathlib
import subprocess

import pytest

from voice import build_client, transcribe

pytestmark = pytest.mark.integration

SPOKEN_TEXT = "Mitochondria are the powerhouse of the cell"


def _require_key():
    if not os.environ.get("DEEPGRAM_API_KEY"):
        pytest.skip("DEEPGRAM_API_KEY not set")


@pytest.fixture(scope="module")
def wav_audio(tmp_path_factory):
    """Synthesize real speech as 16 kHz mono WAV."""
    if not pathlib.Path("/usr/bin/say").exists():
        pytest.skip("macOS 'say' not available")

    tmp = tmp_path_factory.mktemp("audio")
    aiff, wav = tmp / "s.aiff", tmp / "s.wav"
    subprocess.run(["say", "-o", str(aiff), SPOKEN_TEXT], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
        check=True,
    )
    return wav.read_bytes()


@pytest.fixture(scope="module")
def client():
    _require_key()
    return build_client()


def test_transcribes_real_speech(client, wav_audio):
    text = transcribe(wav_audio, client)
    assert "mitochondria" in text.lower()
    assert "cell" in text.lower()


def test_unsupported_audio_degrades_to_empty_string(client):
    """A real 400 from Deepgram must surface as "" so the UI asks the user to type."""
    assert transcribe(b"definitely-not-audio" * 50, client) == ""


def test_empty_audio_short_circuits(client):
    assert transcribe(b"", client) == ""
