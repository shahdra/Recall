"""Pytest fixture discovery for the unit tests.

The test doubles themselves live in ``fakes.py`` so test modules can import them
by an unambiguous name. Two files called ``conftest.py`` — one here and one in
``integration/`` — made ``from conftest import FakeLLM`` resolve to whichever
pytest happened to import first, which broke collection nondeterministically.
"""

from fakes import (  # noqa: F401  (re-exported for pytest fixture discovery)
    BUCKET,
    FakeLLM,
    FakeMCPTool,
    FakeMessage,
    client,
    fake_llm,
    mcp_tools,
)
