"""
Thin wrapper around the google-genai SDK.
Every agent calls generate_text() instead of touching the SDK directly -
this keeps model config, retry, and fallback handling in one place.

Google has been retiring free-tier model names mid-project (2.5-flash,
then 2.5-flash-lite both got deprecated for new keys within days of each
other), so instead of hardcoding one model, we try a prioritized list and
fall back automatically. This also smooths over transient 503s, since a
different model is often not experiencing the same demand spike.
"""
import time
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

MAX_RETRIES_PER_MODEL = 2
RETRY_DELAY_SECONDS = 2  # doubles each retry (simple exponential backoff)


def _candidate_models() -> list[str]:
    models = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]
    return models


def generate_text(prompt: str, temperature: float = 0.3) -> str:
    """
    Sends a single prompt to Gemini and returns the raw text response.
    Tries the configured model first, then falls back through
    FALLBACK_MODELS if it hits a 404 (retired model name) or keeps
    hitting 503 (transient demand spike) after a couple of retries.
    """
    last_error = None

    for model_name in _candidate_models():
        for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
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
                return text
            except genai_errors.ClientError as e:
                # 404 = model name retired/invalid - no point retrying same model, move to next
                last_error = e
                break
            except genai_errors.ServerError as e:
                # 503 = transient - retry same model a couple times before moving on
                last_error = e
                if attempt < MAX_RETRIES_PER_MODEL:
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                continue

    raise RuntimeError(
        f"Gemini API failed across all candidate models {_candidate_models()}: {last_error}"
    )
