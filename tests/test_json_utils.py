import pytest
from app.json_utils import parse_json_response


def test_plain_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}


def test_fence_without_language():
    assert parse_json_response('```\n{"a": 1}\n```') == {"a": 1}


def test_invalid_raises_value_error():
    with pytest.raises(ValueError):
        parse_json_response("not json at all")
