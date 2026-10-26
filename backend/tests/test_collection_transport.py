"""Real transport parsing with bounded recovery; no live provider calls."""
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.semantic import collector_config as llm
from app.semantic.collector_schemas import GeneratedQuestions


def transport(monkeypatch, handler, host="openrouter.ai"):
    monkeypatch.setattr(llm, "configuration", lambda: (
        SimpleNamespace(llm_api_key=SecretStr("test-collector-secret")), "https://" + host, "fixture"))
    monkeypatch.setattr(llm, "validate_llm_url", lambda _: "https://" + host + "/chat/completions")
    real = httpx.Client
    requests = []
    def send(request):
        requests.append(json.loads(request.content))
        return handler(len(requests), request)
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(send), **kwargs))
    return requests


def reply(content, reason="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": reason, "message": {"content": content}}]})


@pytest.mark.parametrize("first", [None, "", '{"questions":[]}', '{"questions":['])
def test_length_stop_discards_even_parseable_partial_output_and_recovers_once(monkeypatch, first):
    requests = transport(monkeypatch, lambda n, _: reply(first, "length") if n == 1 else reply('{"questions":[]}'))
    assert llm.complete("instruction", {"synthetic": True}, GeneratedQuestions).questions == []
    assert [r["max_tokens"] for r in requests] == [5000, 10000]
    assert requests[0]["messages"] == requests[1]["messages"]
    assert requests[0]["reasoning"] == {"effort": "minimal"}
    assert requests[0]["response_format"] == {"type": "json_object"}


def test_repeated_length_failure_is_bounded_and_explained(monkeypatch):
    requests = transport(monkeypatch, lambda *_: reply(None, "length"))
    with pytest.raises(llm.CollectionError, match="길이 한도.*자동 재시도"):
        llm.complete("instruction", {}, GeneratedQuestions)
    assert len(requests) == 2


@pytest.mark.parametrize("host", ["internal.example", "openrouter.ai.example"])
def test_internal_compatible_servers_do_not_get_vendor_specific_options(monkeypatch, host):
    requests = transport(monkeypatch, lambda *_: reply('{"questions":[]}'), host)
    llm.complete("instruction", {}, GeneratedQuestions)
    assert len(requests) == 1
    assert not {"reasoning", "response_format"} & requests[0].keys()


@pytest.mark.parametrize("status,message", [(401, "인증"), (403, "권한"), (429, "사용 한도"), (503, "일시적인 오류"), (400, "요청 설정")])
def test_http_errors_have_safe_actionable_diagnostics_without_blind_retries(monkeypatch, status, message):
    requests = transport(monkeypatch, lambda *_: httpx.Response(status, text="test-collector-secret"))
    with pytest.raises(llm.CollectionError, match=message) as exc:
        llm.complete("instruction", {}, GeneratedQuestions)
    assert "test-collector-secret" not in str(exc.value)
    assert len(requests) == 1


@pytest.mark.parametrize("content,message", [(None, "반환하지"), ("", "반환하지"), ("{}", ""), ('{"questions":[],"extra":1}', "형식"), ("test-collector-secret", "형식")])
def test_invalid_or_empty_responses_cannot_become_knowledge(monkeypatch, content, message):
    requests = transport(monkeypatch, lambda *_: reply(content))
    if content == "{}":  # GeneratedQuestions intentionally permits no questions.
        assert llm.complete("instruction", {}, GeneratedQuestions).questions == []
    else:
        with pytest.raises(llm.CollectionError, match=message) as exc:
            llm.complete("instruction", {}, GeneratedQuestions)
        assert "test-collector-secret" not in str(exc.value)
    assert len(requests) == 1


def test_timeout_keeps_diagnostic_safe(monkeypatch):
    def timeout(*_):
        raise httpx.ReadTimeout("test-collector-secret")
    transport(monkeypatch, timeout)
    with pytest.raises(llm.CollectionError, match="대기 시간") as exc:
        llm.complete("instruction", {}, GeneratedQuestions)
    assert "test-collector-secret" not in str(exc.value)
