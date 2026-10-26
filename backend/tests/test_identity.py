"""Identity acceptance tests against the isolated demo metadata PostgreSQL."""
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select

from app import sso_adapter
from app.bootstrap import initialize
from app.db import SessionLocal, Settings, settings
from app.models import Audit, LoginSession, User, utcnow


@pytest.fixture
def identity(monkeypatch):
    if not settings.demo_mode or not settings.app_database_url:
        pytest.skip("Identity tests require isolated DEMO_MODE PostgreSQL")
    from app.main import app
    prefix = "qa-id-" + uuid.uuid4().hex[:12]
    monkeypatch.setattr(settings, "auth_mode", "local_admin")
    monkeypatch.setattr(settings, "sso_cookie_name", "company_sso")
    monkeypatch.setattr(settings, "sso_bootstrap_admin_employee_id", "")
    initialize()
    with TestClient(app, client=("127.0.0.1", 50100), headers={"Origin": "http://127.0.0.1:8000"}) as client:
        yield client, prefix
    with SessionLocal.begin() as db:
        ids = list(db.scalars(select(User.id).where(or_(User.employee_id.startswith(prefix), User.username.startswith(prefix)))))
        if ids:
            db.execute(delete(Audit).where(or_(Audit.user_id.in_(ids), Audit.entity_id.in_(ids))))
            db.execute(delete(User).where(User.id.in_(ids)))


def use_sso(monkeypatch, client, identity, calls=None):
    monkeypatch.setattr(settings, "auth_mode", "sso")
    client.cookies.set("company_sso", "opaque-verified-test-cookie")
    def verify(cookie):
        if calls is not None:
            calls.append(("verify", cookie))
        return identity
    def profile(value):
        if calls is not None:
            calls.append(("profile", value))
        return sso_adapter.EmployeeProfile(value, "테스트 사용자", "공정 개발")
    monkeypatch.setattr(sso_adapter, "verify_sso_cookie", verify)
    monkeypatch.setattr(sso_adapter, "fetch_employee_profile", profile)


def test_local_admin_automatic_and_password_login_disabled(identity):
    client, _ = identity
    result = client.get("/api/auth/session")
    assert result.status_code == 200
    session = result.json()
    assert session["mode"] == "local_admin" and session["status"] == "authenticated"
    assert session["user"]["username"] == "pm-demo-admin"
    assert session["user"]["name"] == "테스트 관리자"
    assert session["user"]["employee_id"] is None and session["user"]["role"] == "admin"
    assert "password" not in result.text and not result.headers.get("set-cookie")
    assert client.post("/api/auth/login", json={"username":"admin", "password":"ignored"}).status_code == 410
    assert client.get("/api/admin/users").status_code == 200


def test_local_admin_requires_demo_and_loopback(identity, monkeypatch):
    client, _ = identity
    from app.main import app
    with TestClient(app, client=("192.0.2.5", 50101)) as remote:
        assert remote.get("/api/auth/me", headers={"X-Forwarded-For":"127.0.0.1"}).status_code == 403
        assert remote.get("/api/auth/session").json()["status"] == "disabled"
    monkeypatch.setattr(settings, "demo_mode", False)
    assert client.get("/api/auth/me").status_code == 503
    assert client.get("/api/auth/session").json()["status"] == "unavailable"


def test_invalid_auth_mode_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_mode="invalid")


def test_missing_unimplemented_and_forged_sso_fail_closed(identity, monkeypatch):
    client, _ = identity
    monkeypatch.setattr(settings, "auth_mode", "sso")
    assert client.get("/api/auth/session").json()["status"] == "unauthenticated"
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("company_sso", "pm-demo-admin")
    assert client.get("/api/auth/me").status_code == 503
    assert client.get("/api/auth/session").json()["status"] == "unavailable"
    def reject(cookie):
        raise HTTPException(401, "인증 실패")
    monkeypatch.setattr(sso_adapter, "verify_sso_cookie", reject)
    assert client.get("/api/auth/me", headers={"X-Role":"admin", "X-Employee-Id":"pm-demo-admin"}).status_code == 401
    monkeypatch.setattr(settings, "sso_cookie_name", "")
    assert client.get("/api/auth/me").status_code == 503


def test_legacy_session_is_preserved_but_cannot_authenticate(identity, monkeypatch):
    client, prefix = identity
    token = "legacy-" + prefix
    with SessionLocal.begin() as db:
        old = User(username=prefix, password_hash="legacy-hash-preserved", role="admin", profile_source="legacy")
        db.add(old)
        db.flush()
        old_id = old.id
        db.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=old.id, expires_at=utcnow()+timedelta(hours=1)))
    initialize()
    monkeypatch.setattr(settings, "auth_mode", "sso")
    client.cookies.set("wafer_session", token)
    assert client.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        assert db.get(User, old_id).password_hash == "legacy-hash-preserved"
        assert db.get(LoginSession, hashlib.sha256(token.encode()).hexdigest()) is not None
    monkeypatch.setattr(settings, "auth_mode", "local_admin")
    assert client.patch(f"/api/admin/users/{old_id}", json={"role":"viewer", "expected_version":1}).status_code == 403


def test_first_sso_visit_provisions_viewer_and_directory_cache_preserves_access(identity, monkeypatch):
    client, prefix = identity
    calls = []
    use_sso(monkeypatch, client, prefix, calls)
    first = client.get("/api/auth/session").json()["user"]
    assert first["role"] == "viewer" and first["name"] == "테스트 사용자" and first["team"] == "공정 개발"
    assert first["employee_id"] == prefix and first["profile_source"] == "sso"
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/datasets/preview", json={"spec":{"source_id":"wafers"}}).status_code == 403
    with SessionLocal.begin() as db:
        user = db.get(User, first["id"])
        user.role, user.version = "editor", 2
    assert client.get("/api/auth/me").json()["role"] == "editor"
    assert sum(kind == "profile" for kind, _ in calls) == 1
    assert sum(kind == "verify" for kind, _ in calls) == 4
    with SessionLocal.begin() as db:
        db.get(User, first["id"]).profile_synced_at = utcnow()-timedelta(hours=2)
    monkeypatch.setattr(sso_adapter, "fetch_employee_profile", lambda value: sso_adapter.EmployeeProfile(value, "바뀐 이름", "바뀐 팀"))
    updated = client.get("/api/auth/me").json()
    assert updated["name"] == "바뀐 이름" and updated["team"] == "바뀐 팀"
    assert updated["role"] == "editor" and updated["version"] == 2
    with SessionLocal.begin() as db:
        db.get(User, first["id"]).active = False
    assert client.get("/api/auth/me").status_code == 403
    assert client.get("/api/auth/session").json()["status"] == "disabled"


@pytest.mark.parametrize("failure", ["error", "mismatch", "empty_name", "invalid_id"])
def test_profile_or_verification_failure_does_not_provision(identity, monkeypatch, failure):
    client, prefix = identity
    use_sso(monkeypatch, client, prefix)
    if failure == "error":
        def fail(value):
            raise RuntimeError("secret-cookie-or-internal-url")
        monkeypatch.setattr(sso_adapter, "fetch_employee_profile", fail)
    elif failure == "mismatch":
        monkeypatch.setattr(sso_adapter, "fetch_employee_profile", lambda value: sso_adapter.EmployeeProfile("different", "Name", "Team"))
    elif failure == "empty_name":
        monkeypatch.setattr(sso_adapter, "fetch_employee_profile", lambda value: sso_adapter.EmployeeProfile(value, " ", "Team"))
    else:
        monkeypatch.setattr(sso_adapter, "verify_sso_cookie", lambda cookie: "untrusted/id")
    response = client.get("/api/auth/me")
    assert response.status_code == 503 and "secret-cookie" not in response.text
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.employee_id==prefix)) is None


def test_bootstrap_admin_is_preauthorized_but_still_requires_verified_sso(identity, monkeypatch):
    client, prefix = identity
    monkeypatch.setattr(settings, "sso_bootstrap_admin_employee_id", prefix)
    monkeypatch.setattr(settings, "auth_mode", "sso")
    initialize()
    assert client.get("/api/auth/me").status_code == 401
    use_sso(monkeypatch, client, prefix)
    first = client.get("/api/auth/me").json()
    assert first["role"] == "admin"
    assert client.patch(f"/api/admin/users/{first['id']}", json={"role":"viewer", "expected_version":first["version"]}).status_code == 409
    assert client.patch(f"/api/admin/users/{first['id']}", json={"active":False, "expected_version":first["version"]}).status_code == 409
    with SessionLocal.begin() as db:
        user = db.get(User, first["id"])
        user.role, user.active = "viewer", False
    initialize()
    with SessionLocal() as db:
        user = db.get(User, first["id"])
        assert user.role == "viewer" and user.active is False
    assert client.get("/api/auth/me").status_code == 403


def test_user_management_versions_last_admin_and_directory_owned_fields(identity):
    client, prefix = identity
    created = client.post("/api/admin/users", json={"employee_id":prefix,"role":"admin","active":True})
    assert created.status_code == 201, created.text
    user = created.json()
    assert not user["name"] and user["employee_id"] == prefix
    assert client.post("/api/admin/users", json={"employee_id":prefix}).status_code == 409
    for forbidden in ({"username":"spoof"}, {"password":"forbidden"}, {"name":"spoof"}, {"team":"spoof"}):
        assert client.patch(f"/api/admin/users/{user['id']}", json={"expected_version":1, **forbidden}).status_code == 422
    with SessionLocal() as db:
        admins = db.scalar(select(func.count()).select_from(User).where(User.role=="admin",User.active.is_(True),User.profile_source=="sso"))
    if admins == 1:
        assert client.patch(f"/api/admin/users/{user['id']}", json={"role":"viewer","expected_version":1}).status_code == 409
    other = client.post("/api/admin/users", json={"employee_id":prefix+"-2","role":"admin"})
    assert other.status_code == 201
    body = {"role":"editor","active":False,"expected_version":1}
    changed = client.patch(f"/api/admin/users/{user['id']}", json=body)
    assert changed.status_code == 200 and changed.json()["version"] == 2
    assert client.patch(f"/api/admin/users/{user['id']}", json=body).status_code == 409
    with SessionLocal() as db:
        audit = db.scalar(select(Audit).where(Audit.entity_id==user["id"],Audit.action=="user.update"))
        assert audit.details["before"] == {"role":"admin","active":True}
        assert audit.details["after"] == {"role":"editor","active":False}


def test_concurrent_management_updates_have_one_winner(identity):
    client, prefix = identity
    user = client.post("/api/admin/users", json={"employee_id":prefix,"role":"viewer"}).json()
    def change(role):
        return client.patch(f"/api/admin/users/{user['id']}", json={"role":role,"expected_version":1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(change, ["editor", "admin"]))
    assert sorted(result) == [200, 409]


def test_sso_writes_require_trusted_origin(identity, monkeypatch):
    client, prefix = identity
    use_sso(monkeypatch, client, prefix)
    client.headers.pop("origin")
    assert client.post("/api/query", json={}).status_code == 403
    assert client.post("/api/query", json={}, headers={"Origin":"https://untrusted.example"}).status_code == 403
    assert client.post("/api/query", json={}, headers={"Origin":"http://127.0.0.1:8000","Sec-Fetch-Site":"cross-site"}).status_code == 403
