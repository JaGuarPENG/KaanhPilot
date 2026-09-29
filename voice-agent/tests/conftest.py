"""Keep offline tests independent of the user's real API credentials and shell settings."""
import os

import pytest

from voice_agent.config import Settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(monkeypatch):
    fields = set(Settings.model_fields)
    for name in list(os.environ):
        if name.lower() in fields:
            monkeypatch.delenv(name, raising=False)
