"""Speech-to-text for spoken flashcard answers, via Deepgram.

Voice is a convenience layer over the typed path, so this module never raises:
every failure returns ``""`` and the caller asks the learner to type instead.
Losing a transcription costs one retyped answer; a 500 would cost the session.

Deepgram rather than Whisper because flashcard answers are short utterances,
where Nova's latency and per-minute cost are both better — and it keeps the
LLM provider and the speech provider independent.
"""

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_STT_MODEL = "nova-3"
"""Deepgram's current general model. Good at short, domain-specific utterances,
which is exactly what a spoken flashcard answer is."""


class VoiceUnavailable(Exception):
    """Raised only by ``build_client`` when no API key is configured."""


def build_client(api_key: str | None = None):
    """Construct a Deepgram client.

    Args:
        api_key: Defaults to ``$DEEPGRAM_API_KEY``.

    Raises:
        VoiceUnavailable: If no key is configured. The caller disables the voice
            endpoint and keeps serving the typed path rather than failing to start.
    """
    api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        raise VoiceUnavailable("DEEPGRAM_API_KEY is not set")

    from deepgram import DeepgramClient

    return DeepgramClient(api_key=api_key)


def _first_transcript(response) -> str:
    """Pull the top alternative out of a Deepgram response.

    Defensive about shape: a provider changing its response schema should cost a
    fallback to typing, not a traceback.
    """
    results = getattr(response, "results", None)
    if results is None:
        return ""
    channels = getattr(results, "channels", None) or []
    if not channels:
        return ""
    alternatives = getattr(channels[0], "alternatives", None) or []
    if not alternatives:
        return ""
    return getattr(alternatives[0], "transcript", "") or ""


def transcribe(audio_bytes: bytes, client, model: str = DEFAULT_STT_MODEL) -> str:
    """Transcribe spoken audio to text.

    Args:
        audio_bytes: Raw audio (webm/opus from the browser, wav, mp3, ...).
            Deepgram sniffs the container, so no format hint is needed.
        client: A Deepgram client. Injected so tests need no network.
        model: Deepgram model name.

    Returns:
        The transcript, stripped — or ``""`` if the audio was empty, silent, or
        the service failed. Never raises.
    """
    if not audio_bytes:
        return ""

    try:
        response = client.listen.v1.media.transcribe_file(
            request=audio_bytes,
            model=model,
            smart_format=True,
        )
    except Exception:
        logger.exception("transcription failed")
        return ""

    try:
        return _first_transcript(response).strip()
    except Exception:
        logger.exception("could not parse transcription response")
        return ""
