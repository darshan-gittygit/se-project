"""
LLMs often wrap JSON responses in markdown code fences (```json ... ```)
even when told not to. This strips that safely before parsing, so every
agent can just call parse_json_response(raw_text) instead of repeating
this cleanup everywhere.
"""
import json
import re


def parse_json_response(raw_text: str) -> dict:
    text = raw_text.strip()

    # Strip ```json ... ``` or ``` ... ``` fences if present
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Model did not return valid JSON. Raw response:\n{raw_text}"
        ) from e
