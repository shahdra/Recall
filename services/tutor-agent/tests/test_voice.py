"""Tests for voice transcription.

Voice is a convenience, never a requirement: every failure path returns an empty
string so the caller can say "I couldn't hear that, please type it". The typed
answer path always works, so a transcription outage must never block studying.
"""

from voice import DEFAULT_STT_MODEL, transcribe


class FakeAlternative:
    def __init__(self, transcript):
        self.transcript = transcript


class FakeChannel:
    def __init__(self, transcript):
        self.alternatives = [FakeAlternative(transcript)]


class FakeResults:
    def __init__(self, transcript):
        self.channels = [FakeChannel(transcript)]


class FakeResponse:
    def __init__(self, transcript):
        self.results = FakeResults(transcript)


class FakeMedia:
    def __init__(self, transcript=None, raises=None):
        self.transcript = transcript
        self.raises = raises
        self.kwargs = None

    def transcribe_file(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return FakeResponse(self.transcript)


class FakeClient:
    """Mimics client.listen.v1.media.transcribe_file(...)."""

    def __init__(self, transcript=None, raises=None):
        self.media = FakeMedia(transcript, raises)
        self.listen = self
        self.v1 = self

    def __getattr__(self, name):
        # listen / v1 chain resolves back to self
        raise AttributeError(name)


def _client(transcript=None, raises=None):
    c = FakeClient(transcript, raises)
    return c


def test_returns_transcript_on_success():
    assert transcribe(b"audio-bytes", _client("the mitochondria")) == "the mitochondria"


def test_strips_whitespace():
    assert transcribe(b"a", _client("  hello  ")) == "hello"


def test_client_error_returns_empty_string():
    assert transcribe(b"a", _client(raises=RuntimeError("deepgram 503"))) == ""


def test_empty_audio_returns_empty_without_calling_client():
    client = _client("should not be used")
    assert transcribe(b"", client) == ""
    assert client.media.kwargs is None


def test_silence_yields_empty_string():
    """Deepgram returns an empty transcript for silence, which is not an error."""
    assert transcribe(b"a", _client("")) == ""


def test_malformed_response_returns_empty_string():
    class Weird:
        results = None

    class WeirdMedia:
        def transcribe_file(self, **kwargs):
            return Weird()

    class WeirdClient:
        def __init__(self):
            self.media = WeirdMedia()
            self.listen = self
            self.v1 = self

    assert transcribe(b"a", WeirdClient()) == ""


def test_audio_is_passed_as_request_bytes():
    client = _client("ok")
    transcribe(b"my-audio", client)
    assert client.media.kwargs["request"] == b"my-audio"


def test_uses_nova_model_with_smart_format():
    client = _client("ok")
    transcribe(b"a", client)
    assert client.media.kwargs["model"] == DEFAULT_STT_MODEL
    assert client.media.kwargs["smart_format"] is True


def test_default_model_is_a_nova_model():
    assert "nova" in DEFAULT_STT_MODEL


def test_model_can_be_overridden():
    client = _client("ok")
    transcribe(b"a", client, model="nova-2")
    assert client.media.kwargs["model"] == "nova-2"


def test_no_channels_returns_empty_string():
    class NoChannels:
        class results:
            channels = []

    class M:
        def transcribe_file(self, **kwargs):
            return NoChannels()

    class C:
        def __init__(self):
            self.media = M()
            self.listen = self
            self.v1 = self

    assert transcribe(b"a", C()) == ""
