"""Tests for model fallback / retry logic in gemini_client (SDK is mocked)."""
import json
import types

import requests
from google.genai import errors as genai_errors

import app.gemini_client as gc


def _api_error(cls, code, status):
    r = requests.Response()
    r.status_code = code
    r._content = json.dumps({"error": {"code": code, "message": "x", "status": status}}).encode()
    r.headers["Content-Type"] = "application/json"
    return cls(code, r)


def _install_fake(monkeypatch, behaviour):
    """behaviour(model_name) -> text, or raises. Records models tried."""
    tried = []

    def fake_generate_content(model, contents, config):
        tried.append(model)
        out = behaviour(model)
        return types.SimpleNamespace(text=out)

    fake_client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=fake_generate_content))
    monkeypatch.setattr(gc, "_client", fake_client)
    monkeypatch.setattr(gc.time, "sleep", lambda s: None)
    return tried


def test_falls_back_when_primary_model_is_404(monkeypatch):
    def behaviour(model):
        if model == gc.GEMINI_MODEL:
            raise _api_error(genai_errors.ClientError, 404, "NOT_FOUND")
        return "hello"

    tried = _install_fake(monkeypatch, behaviour)
    assert gc.generate_text("hi") == "hello"
    assert tried[0] == gc.GEMINI_MODEL and len(tried) == 2


def test_retries_503_on_same_model_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def behaviour(model):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _api_error(genai_errors.ServerError, 503, "UNAVAILABLE")
        return "ok"

    tried = _install_fake(monkeypatch, behaviour)
    assert gc.generate_text("hi") == "ok"
    assert tried[0] == tried[1] == gc.GEMINI_MODEL


def test_empty_response_moves_to_next_model(monkeypatch):
    def behaviour(model):
        return None if model == gc.GEMINI_MODEL else "recovered"

    _install_fake(monkeypatch, behaviour)
    assert gc.generate_text("hi") == "recovered"


def test_raises_clear_error_when_every_model_fails(monkeypatch):
    def behaviour(model):
        raise _api_error(genai_errors.ClientError, 404, "NOT_FOUND")

    _install_fake(monkeypatch, behaviour)
    try:
        gc.generate_text("hi")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "failed across all candidate models" in str(e)
