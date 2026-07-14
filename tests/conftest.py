import pytest

from app.core.model_client import ModelCallError


@pytest.fixture(autouse=True)
def disable_real_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def _disabled_create(system_prompt: str, user_prompt: str) -> str:
        raise ModelCallError("Real model calls are disabled in tests")

    monkeypatch.setattr("app.core.model_client.default_client.create", _disabled_create)
