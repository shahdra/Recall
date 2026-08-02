"""Chat-model construction with a cross-provider fallback.

The fallback is a different provider family from the primary on purpose. A
fallback that shares the primary's backend shares its outages too, which defeats
the point of having one.

Both defaults are on the course account's Bedrock allowlist (IAM policy
``bedrock-restrict-developers``), and both were verified to support tool calling —
a model that cannot call tools cannot drive the ReAct loop, however well it chats.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "bedrock:amazon.nova-lite-v1:0"
DEFAULT_FALLBACK_MODEL = "bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0"

TEMPERATURE = 0
"""Zero so grading and card generation are reproducible: the same answer should
get the same grade twice, or the learner's schedule becomes arbitrary."""

_UNSET = object()
"""Distinguishes "argument omitted, read the environment" from an explicit
``None``, which means "run without a fallback"."""


DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 0.5


def _default_init(model: str, **kwargs):
    # Imported lazily so tests never need langchain_aws or AWS credentials.
    from langchain.chat_models import init_chat_model

    return init_chat_model(model, **kwargs)


class FallbackChatModel:
    """Retries the primary model, then fails over to a secondary.

    Bedrock builds a chat model lazily: a denied model id, a bad id, and a
    regional outage all construct without complaint and raise on the first
    ``invoke``. Init-time fallback alone therefore never fires in the situation
    it exists for, so the failover has to live on the invoke path.

    Retries use exponential backoff, which covers the throttling that a
    lightweight shared-account model hits most often. ``failure_count`` and
    ``fallback_count`` feed the Prometheus metrics in Task 3.9.
    """

    def __init__(
        self,
        primary,
        secondary=None,
        retries: int = DEFAULT_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ):
        self.primary = primary
        self.secondary = secondary
        self.retries = max(1, retries)
        self.backoff_seconds = backoff_seconds
        self.failure_count = 0
        self.fallback_count = 0

    def bind_tools(self, tools, **kwargs):
        """Bind tools to both models, so either can answer with the same toolset."""
        self.primary = self.primary.bind_tools(tools, **kwargs)
        if self.secondary is not None:
            self.secondary = self.secondary.bind_tools(tools, **kwargs)
        return self

    def invoke(self, messages, **kwargs):
        """Invoke the primary with retries, then the secondary once.

        Raises:
            Exception: The secondary's error if both fail (or the primary's when
                no secondary is configured). Callers — the ReAct loop and the
                sub-agents — already degrade gracefully on exceptions.
        """
        last_error = None

        for attempt in range(self.retries):
            try:
                return self.primary.invoke(messages, **kwargs)
            except Exception as exc:
                last_error = exc
                self.failure_count += 1
                logger.warning(
                    "primary model invoke failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.retries,
                    exc,
                )
                if attempt + 1 < self.retries and self.backoff_seconds:
                    time.sleep(self.backoff_seconds * (2**attempt))

        if self.secondary is None:
            logger.error("primary model failed and no fallback is configured")
            raise last_error

        logger.warning("falling back to secondary model")
        self.fallback_count += 1
        try:
            return self.secondary.invoke(messages, **kwargs)
        except Exception as exc:
            self.failure_count += 1
            logger.error("fallback model also failed: %s", exc)
            raise


def build_llm(
    model: str | None = None,
    fallback: str | None = _UNSET,
    init=_default_init,
):
    """Build a chat model, falling back to a second provider if the first fails.

    Args:
        model: Primary model id. Defaults to ``$MODEL``, then ``DEFAULT_MODEL``.
        fallback: Secondary model id. Omit to read ``$FALLBACK_MODEL`` and then
            ``DEFAULT_FALLBACK_MODEL``; pass ``None`` to run with no fallback.
        init: Injected constructor, so tests need no real provider.

    Returns:
        A :class:`FallbackChatModel` wrapping the primary and, when configured,
        the secondary. Always wrapped — even with no fallback — so the retry path
        applies either way.

    Raises:
        RuntimeError: If the primary fails to initialize and no fallback
            succeeds. Fatal at startup by design: a tutor with no model cannot
            tutor, and failing loudly beats serving errors on every request.
    """
    if model is None:
        model = os.environ.get("MODEL", DEFAULT_MODEL)
    if fallback is _UNSET:
        fallback = os.environ.get("FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)

    primary = None
    try:
        primary = init(model, temperature=TEMPERATURE)
        logger.info("initialized primary model %s", model)
    except Exception as primary_error:
        logger.warning("primary model %s failed to initialize: %s", model, primary_error)
        if not fallback:
            raise RuntimeError(
                f"could not initialize model {model!r} and no fallback is configured"
            ) from primary_error

        # Promote the fallback: with no working primary there is nothing to retry.
        try:
            promoted = init(fallback, temperature=TEMPERATURE)
        except Exception as fallback_error:
            raise RuntimeError(
                f"could not initialize model {model!r} or fallback {fallback!r}"
            ) from fallback_error
        logger.warning("primary unavailable at startup; running on %s", fallback)
        return FallbackChatModel(promoted, None)

    secondary = None
    if fallback:
        try:
            secondary = init(fallback, temperature=TEMPERATURE)
            logger.info("fallback model %s ready", fallback)
        except Exception as exc:
            # Not fatal: the primary works, so serve traffic without a safety net
            # rather than refusing to start.
            logger.warning("fallback model %s unavailable: %s", fallback, exc)

    return FallbackChatModel(primary, secondary)
