"""Account isolation and real credential persistence with a mocked LLM transport."""
import json
from types import SimpleNamespace
import uuid

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import delete, select

from app import llm_settings
from app.auth import get_current_user
from app.db import SessionLocal, settings
from app.models import Audit, LlmCredential, User


@pytest.fixture
def personal(monkeypatch):
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Credential tests require isolated DEMO_MODE PostgreSQL")
    from app.main import app
    monkeypatch.setattr(settings, "auth_mode", "local_admin")
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:9999/v1")
    monkeypatch.setattr(settings, "llm_model", "internal-test")
    monkeypatch.setattr(settings, "llm_credentials_key", Fernet.generate_key().decode())
    previous = app.dependency_overrides.copy()
    users = []
    with TestClient(app, client=("127.0.0.1", 50110), headers={"Origin": "http://127.0.0.1:8000"}) as client:
        with SessionLocal.begin() as db:
            for role in ("editor", "admin"):
                user = User(id=str(uuid.uuid4()), username="qa-llm-" + uuid.uuid4().hex,
                            role=role, active=True, password_hash="")
                db.add(user)
                users.append(user)
        state = SimpleNamespace(client=client, users=users, user=users[0])
        app.dependency_overrides[get_current_user] = lambda: state.user
        try:
            yield state
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
            with SessionLocal.begin() as db:
                ids = [u.id for u in users]
                db.execute(delete(Audit).where(Audit.user_id.in_(ids)))
                db.execute(delete(User).where(User.id.in_(ids)))


def save(state, key="private-test-key"):
    result = state.client.put("/api/llm/credential", json={"api_key": key})
    assert result.status_code == 200, result.text
    return result


def mock_model(monkeypatch, handler):
    original = httpx.AsyncClient
    observed = []
    def transport(request):
        observed.append(request)
        return handler(request)
    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(llm_settings.httpx, "AsyncClient", client)
    return observed


def ok(_):
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})


def test_encrypted_storage_no_secret_responses_or_audits(personal):
    response = save(personal, "sensitive-unique-key")
    assert response.json()["key_registered"] and response.json()["status"] == "unchecked"
    assert "sensitive-unique-key" not in response.text
    assert response.json()["base_url"] == "http://127.0.0.1:9999/v1"
    with SessionLocal() as db:
        encrypted = db.get(LlmCredential, personal.user.id).encrypted_key
        assert "sensitive-unique-key" not in encrypted
        audit = db.scalars(select(Audit).where(Audit.user_id == personal.user.id)).all()
        assert all("sensitive-unique-key" not in json.dumps(a.details) for a in audit)
    assert llm_settings.load_key(personal.user.id)[0] == "sensitive-unique-key"
    assert "sensitive-unique-key" not in personal.client.get("/api/llm/status").text
    assert personal.client.get("/api/llm/status").headers["cache-control"] == "no-store"


def test_keys_owned_by_authenticated_user_even_admin_and_forged_id(personal):
    first, second = personal.users
    save(personal, "first-user-key")
    personal.user = second
    assert personal.client.get("/api/llm/status").json()["key_registered"] is False
    forged = personal.client.put("/api/llm/credential", json={"api_key": "bad", "user_id": first.id})
    assert forged.status_code == 422
    save(personal, "second-user-key")
    personal.client.delete("/api/llm/credential", params={"user_id": first.id})
    assert llm_settings.load_key(first.id)[0] == "first-user-key"
    assert personal.client.get("/api/llm/status").json()["key_registered"] is False
    personal.user = first
    save(personal, "replacement-key")
    assert llm_settings.load_key(first.id)[0] == "replacement-key"


def test_no_global_fallback_and_chat_uses_each_callers_key(personal, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "obsolete-global-key")
    observed = mock_model(monkeypatch, lambda _: httpx.Response(200, json={"choices": [{"message": {
        "content": json.dumps({"kind": "clarification", "question": "어떤 지표인가요?", "options": []})}}]}))
    assert personal.client.get("/api/bootstrap").json()["llm_configured"] is False
    missing = personal.client.post("/api/chat", json={"message": "지표를 보여줘"})
    assert "event: error" in missing.text and "본인 키" in missing.text and not observed
    for user, key in zip(personal.users, ["first-user-key", "second-user-key"]):
        personal.user = user
        save(personal, key)
        assert personal.client.get("/api/bootstrap").json()["llm_configured"] is True
        result = personal.client.post("/api/chat", json={"message": "지표를 보여줘"})
        assert "event: clarification" in result.text, result.text
        assert observed[-1].headers["authorization"] == "Bearer " + key
        assert key not in result.text
        assert key not in observed[-1].content.decode()
    personal.client.delete("/api/llm/credential")
    assert personal.client.get("/api/bootstrap").json()["llm_configured"] is False
    assert "event: error" in personal.client.post("/api/chat", json={"message": "다시"}).text
    assert len(observed) == 2


def test_connection_check_uses_saved_key_and_resets_on_config_or_key_change(personal, monkeypatch):
    save(personal)
    observed = mock_model(monkeypatch, ok)
    result = personal.client.post("/api/llm/check")
    assert result.status_code == 200 and result.json()["status"] == "connected", result.text
    assert result.json()["checked_at"]
    sent = observed[0]
    assert sent.headers["authorization"] == "Bearer private-test-key"
    assert sent.url.path == "/v1/chat/completions"
    assert json.loads(sent.content) == {"model": "internal-test",
        "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 128, "stream": False}
    assert personal.client.get("/api/llm/status").json()["status"] == "connected"
    monkeypatch.setattr(settings, "llm_model", "different-model")
    assert personal.client.get("/api/llm/status").json()["status"] == "unchecked"
    save(personal, "rotated-key")
    assert personal.client.get("/api/llm/status").json()["checked_at"] is None
    result = personal.client.delete("/api/llm/credential").json()
    assert not result["configured"] and result["status"] == "not_registered"


@pytest.mark.parametrize("url", ["http://127.0.0.1:9999/v1", "http://127.0.0.1:9999/v1/",
                                 "http://127.0.0.1:9999/v1/chat/completions",
                                 "http://127.0.0.1:9999/v1/chat/completions/"])
def test_base_and_full_endpoint_use_single_completion_path(personal, monkeypatch, url):
    save(personal)
    monkeypatch.setattr(settings, "llm_base_url", url)
    observed = mock_model(monkeypatch, ok)
    assert personal.client.post("/api/llm/check").json()["status"] == "connected"
    assert str(observed[0].url) == "http://127.0.0.1:9999/v1/chat/completions"


@pytest.mark.parametrize("http_status", [401, 403, 404, 429, 500, 302])
def test_check_errors_are_safe_and_do_not_follow_redirects(personal, monkeypatch, http_status, caplog):
    save(personal)
    observed = mock_model(monkeypatch, lambda _: httpx.Response(http_status,
        text="UPSTREAM private-test-key secret-body", headers={"Location": "https://external.example/"}))
    result = personal.client.post("/api/llm/check")
    assert result.status_code == 200 and result.json()["status"] == "error"
    assert len(observed) == 1
    assert "private-test-key" not in result.text + caplog.text and "UPSTREAM" not in result.text
    if http_status in {401, 403}:
        assert "인증" in result.json()["message"]


@pytest.mark.parametrize("failure", ["timeout", "format"])
def test_check_timeout_and_invalid_format(personal, monkeypatch, failure):
    save(personal)
    def fail(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-test-key", request=request)
        return httpx.Response(200, text="private-test-key malformed")
    mock_model(monkeypatch, fail)
    result = personal.client.post("/api/llm/check")
    assert result.json()["status"] == "error" and "private-test-key" not in result.text


@pytest.mark.parametrize("action", ["replace", "delete"])
def test_stale_check_cannot_validate_replacement_or_resurrect_deleted_key(personal, monkeypatch, action):
    save(personal)
    def rotate(request):
        if action == "replace":
            llm_settings.save_key(llm_settings.KeyInput(api_key="new-key"), personal.user)
        else:
            llm_settings.remove_key(personal.user)
        return ok(request)
    mock_model(monkeypatch, rotate)
    assert personal.client.post("/api/llm/check").status_code == 409
    status = personal.client.get("/api/llm/status").json()
    assert status["status"] == ("unchecked" if action == "replace" else "not_registered")


def test_personal_settings_work_without_source_db_or_current_workspace(personal, monkeypatch):
    def forbidden():
        raise AssertionError("Personal keys must not depend on mounted source DB")
    monkeypatch.setattr("app.main.load_active_source", forbidden)
    personal.client.headers["X-Workspace-Token"] = "old-database-token"
    save(personal)
    assert personal.client.get("/api/llm/status").status_code == 200
    assert personal.client.delete("/api/llm/credential").status_code == 200


def test_cannot_decrypt_other_users_ciphertext(personal):
    first, second = personal.users
    save(personal)
    personal.user = second
    save(personal, "second-key")
    with SessionLocal.begin() as db:
        db.get(LlmCredential, second.id).encrypted_key = db.get(LlmCredential, first.id).encrypted_key
    result = personal.client.post("/api/llm/check")
    assert result.status_code == 503 and "private-test-key" not in result.text


@pytest.mark.parametrize("key", ["", "  ", "abc\r\nX-Injected: key", "한글", "a" * 8193])
def test_invalid_key_rejected_without_echo(personal, key):
    result = personal.client.put("/api/llm/credential", json={"api_key": key})
    assert result.status_code == 422
    assert "X-Injected" not in result.text and "a" * 100 not in result.text


def test_viewer_owns_key_but_cannot_chat(personal, monkeypatch):
    personal.user.role = "viewer"
    save(personal)
    mock_model(monkeypatch, ok)
    assert personal.client.post("/api/llm/check").json()["status"] == "connected"
    assert personal.client.post("/api/chat", json={"message": "카드 추가"}).status_code == 403
    assert personal.client.delete("/api/llm/credential").status_code == 200


def test_missing_server_config_and_key_do_not_call_provider(personal, monkeypatch):
    def forbidden(_):
        raise AssertionError("Must not call LLM without config and key")
    mock_model(monkeypatch, forbidden)
    assert personal.client.post("/api/llm/check").status_code == 400
    save(personal)
    monkeypatch.setattr(settings, "llm_base_url", "")
    assert personal.client.post("/api/llm/check").status_code == 400
    assert personal.client.get("/api/llm/status").json()["configured"] is False


@pytest.mark.parametrize("address", ["169.254.169.254", "0.0.0.0"])
def test_unsafe_endpoint_never_receives_personal_key(personal, monkeypatch, address):
    save(personal)
    monkeypatch.setattr(settings, "llm_base_url", "http://unsafe.example/v1")
    monkeypatch.setattr(llm_settings.socket, "getaddrinfo",
                        lambda *a, **kw: [(2, 1, 6, "", (address, 80))])
    def forbidden(_):
        raise AssertionError("Unsafe endpoint must not receive a key")
    observed = mock_model(monkeypatch, forbidden)
    result = personal.client.post("/api/llm/check").json()
    assert result["status"] == "error" and "LLM 서버로 사용할 수 없습니다" in result["message"]
    assert "event: error" in personal.client.post("/api/chat", json={"message": "조회"}).text
    assert not observed


@pytest.mark.parametrize("address", ["10.20.30.40", "127.0.0.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_configured_private_local_and_public_endpoints_work_in_check_and_chat(personal, monkeypatch, address):
    save(personal)
    endpoint = "https://configured.example/v1/chat/completions"
    monkeypatch.setattr(settings, "llm_base_url", endpoint)
    monkeypatch.setattr(llm_settings.socket, "getaddrinfo",
                        lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    observed = mock_model(monkeypatch, lambda _: httpx.Response(200, json={"choices": [{"message": {
        "content": json.dumps({"kind": "clarification", "question": "어떤 지표인가요?", "options": []})}}]}))
    assert personal.client.post("/api/llm/check").json()["status"] == "connected"
    result = personal.client.post("/api/chat", json={"message": "지표를 보여줘"})
    assert "event: clarification" in result.text, result.text
    assert len(observed) == 2
    assert all(str(request.url) == endpoint for request in observed)
    assert all(request.headers["authorization"] == "Bearer private-test-key" for request in observed)


def test_authentication_and_origin_required_for_personal_keys(personal, monkeypatch):
    from app.main import app
    app.dependency_overrides.pop(get_current_user)
    monkeypatch.setattr(settings, "auth_mode", "sso")
    monkeypatch.setattr(settings, "sso_cookie_name", "company_sso")
    for method, path, body in [("get", "/api/llm/status", None),
                              ("put", "/api/llm/credential", {"api_key": "private-test-key"}),
                              ("delete", "/api/llm/credential", None), ("post", "/api/llm/check", None)]:
        assert personal.client.request(method, path, json=body).status_code == 401
    result = personal.client.put("/api/llm/credential", json={"api_key": "private-test-key"},
                                 headers={"Origin": "https://attacker.example"})
    assert result.status_code == 403
