"""
Thin wrapper around the google-genai SDK.
Every agent calls generate_text() instead of touching the SDK directly -
this keeps model config, retry, and fallback handling in one place.

Google has been retiring free-tier model names mid-project (2.5-flash,
then 2.5-flash-lite both got deprecated for new keys within days of each
other), so instead of hardcoding one model, we try a prioritized list and
fall back automatically. This also smooths over transient 503s, since a
different model is often not experiencing the same demand spike.

Which model actually answered is recorded for the current request (see
track_models), so the API can report it instead of the configured name.

Wiring check (sends one tiny prompt): python -m app.gemini_client
"""
import contextvars
import re
import time
from contextlib import contextmanager

from google import genai
from google.genai import errors as genai_errors
from app.config import GEMINI_API_KEY, GEMINI_MODEL

_client = genai.Client(api_key=GEMINI_API_KEY)

# Primary model comes from .env (GEMINI_MODEL). These are tried after it,
# in order, if the primary fails. Update this list if Google retires more
# names - check the error message, it names the current replacement.
FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-latest",
]

MAX_RETRIES_PER_MODEL = 2   # attempts per model on 503
RETRY_DELAY_SECONDS = 2     # doubles each retry (simple exponential backoff)

RATE_LIMIT_ATTEMPTS = 3     # attempts per model on 429 before trying the next model
RATE_LIMIT_BASE_DELAY = 2   # seconds; 2, 4, ... unless the API says how long to wait
RATE_LIMIT_MAX_DELAY = 10   # never block a request longer than this per wait
RATE_LIMIT_SKIP_AFTER = 60  # a hint longer than this is a long (e.g. daily) quota: don't wait, try the next model

_models_used: contextvars.ContextVar[list | None] = contextvars.ContextVar("requora_models_used", default=None)


@contextmanager
def track_models():
    """Collects the name of the model that answered each generate_text call made inside the block."""
    calls: list[str] = []
    token = _models_used.set(calls)
    try:
        yield calls
    finally:
        _models_used.reset(token)


def _candidate_models() -> list[str]:
    models = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]
    return models


def _rate_limit_delay(error: Exception, attempt: int) -> float | None:
    """
    Seconds to wait after a 429: the API's RetryInfo hint if present, else
    2, 4, ... seconds; capped at RATE_LIMIT_MAX_DELAY. Returns None when the
    hint is longer than RATE_LIMIT_SKIP_AFTER (a daily quota says "retry in
    21h"), meaning waiting on this model is pointless.
    """
    delay = RATE_LIMIT_BASE_DELAY * 2 ** (attempt - 1)
    details = getattr(error, "details", None) or {}
    for d in (details.get("error", {}) if isinstance(details, dict) else {}).get("details", []) or []:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay", ""))) if isinstance(d, dict) else None
        if m:
            delay = float(m.group(1))
            if delay > RATE_LIMIT_SKIP_AFTER:
                return None
    return min(delay, RATE_LIMIT_MAX_DELAY)


def generate_text(prompt: str, temperature: float = 0.3) -> str:
    """
    Sends a single prompt to Gemini and returns the raw text response.
    Per model: a 429 (rate limit) is retried with backoff up to
    RATE_LIMIT_ATTEMPTS times, a 503 up to MAX_RETRIES_PER_MODEL times;
    a 404 (retired name), other 4xx errors, an empty reply, or exhausted
    retries move on to the next model in FALLBACK_MODELS.
    """
    last_error = None

    for model_name in _candidate_models():
        attempt = 0
        while True:
            attempt += 1
            try:
                response = _client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"temperature": temperature},
                )
                text = response.text
                if not text:
                    # Empty/blocked response (e.g. safety filter) - treat it like a
                    # failure of this model and try the next one instead of crashing
                    # later when the JSON parser receives None.
                    last_error = RuntimeError(f"{model_name} returned an empty response")
                    break
                calls = _models_used.get()
                if calls is not None:
                    calls.append(model_name)
                return text
            except genai_errors.ClientError as e:
                last_error = e
                if getattr(e, "code", None) == 429 and attempt < RATE_LIMIT_ATTEMPTS:
                    delay = _rate_limit_delay(e, attempt)
                    if delay is not None:
                        time.sleep(delay)
                        continue
                break  # 404 / other 4xx / long quota / rate limit still hit: try the next model
            except genai_errors.ServerError as e:
                last_error = e
                if attempt < MAX_RETRIES_PER_MODEL:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue
                break

    raise RuntimeError(
        f"Gemini API failed across all candidate models {_candidate_models()}: {last_error}"
    )


if __name__ == "__main__":
    with track_models() as used:
        reply = generate_text("Reply with exactly one word: Working")
    print(f"{used[-1]} says: {reply.strip()}")
