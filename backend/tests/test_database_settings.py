import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app import credential_store, database_settings, db as database
from app.auth import require_admin
from app.db import SessionLocal, settings
from app.main import app
from app.models import Audit, Dashboard, DatabaseMount, DatabaseProfile, DatabaseProfileRemoval, DatasetVersion, Source
from test_project_datasets import api


def test_saved_mount_survives_removing_legacy_environment_settings(api, connections, monkeypatch):
    saved = api.post("/api/admin/databases", json=connections)
    assert saved.status_code == 200, saved.text
    with SessionLocal() as db:
        mount = db.get(DatabaseMount, 1)
        assert mount is not None
        expected_id = mount.profile_id
    before = api.get("/api/bootstrap").json()
    monkeypatch.setattr(settings, "source_database_url", "")
    monkeypatch.setattr(settings, "table_ignore", [])
    monkeypatch.setattr(database, "source_engine", None)
    monkeypatch.setattr(database, "active_source", None)
    result = api.get("/api/database-status").json()
    assert result["status"] == "connected" and result["profile_id"] == expected_id
    after = api.get("/api/bootstrap").json()
    assert after["workspace_token"] == before["workspace_token"]
    assert after["datasets"] == before["datasets"] and after["projects"] == before["projects"]


@pytest.fixture
def connections(api, monkeypatch, tmp_path):
    """Use isolated demo credentials and restore all connection metadata."""
    env_key = database.database_key(settings.source_database_url)
    with SessionLocal.begin() as db:
        mount = db.get(DatabaseMount, 1)
        if mount and mount.profile_id != env_key:
            pytest.skip("An active non-demo DB must not be changed by integration tests")
        saved_mount = {"profile_id": mount.profile_id, "revision": mount.revision} if mount else None
        env_profile = db.get(DatabaseProfile, env_key)
        saved_profile = {c.name: getattr(env_profile, c.name) for c in DatabaseProfile.__table__.columns} if env_profile else None
        original_profiles = set(db.scalars(select(DatabaseProfile.id)))
        original_sources = set(db.scalars(select(Source.id)))
        original_dashboards = set(db.scalars(select(Dashboard.id)))
        original_audits = set(db.scalars(select(Audit.id)))
        original_removals = set(db.scalars(select(DatabaseProfileRemoval.id)))
        db.execute(delete(DatabaseMount))
        db.execute(delete(DatabaseProfile).where(DatabaseProfile.id == env_key))
    monkeypatch.setattr(credential_store, "KEY_FILE", tmp_path / "db.key")
    monkeypatch.setattr(settings, "db_credentials_key", "")
    database.active_source = None
    env = database_settings.environment_profile()
    body = {**database_settings.profile_json(env), "profile_id": env.id, "password": None}
    body.pop("id")
    yield body
    if database.active_source and database.active_source.engine:
        database.active_source.engine.dispose()
    database.active_source = None
    with SessionLocal.begin() as db:
        ids = list(db.scalars(select(Source.id).where(Source.id.not_in(original_sources))))
        db.execute(delete(DatasetVersion).where(DatasetVersion.id.in_(ids)))
        db.execute(delete(Source).where(Source.id.in_(ids)))
        db.execute(delete(Dashboard).where(Dashboard.id.not_in(original_dashboards)))
        db.execute(delete(DatabaseMount))
        db.execute(delete(DatabaseProfile).where((DatabaseProfile.id.not_in(original_profiles)) | (DatabaseProfile.id == env_key)))
        if saved_profile:
            db.add(DatabaseProfile(**saved_profile))
            db.flush()
        if saved_mount:
            db.add(DatabaseMount(id=1, **saved_mount))
        db.execute(delete(Audit).where(Audit.action.like("database.%"), Audit.id.not_in(original_audits)))
        db.execute(delete(DatabaseProfileRemoval).where(DatabaseProfileRemoval.id.not_in(original_removals)))


def test_connection_check_does_not_mount_and_never_returns_password(api, connections):
    before = api.get("/api/admin/databases").json()
    response = api.post("/api/admin/databases/test", json=connections)
    assert response.status_code == 200, response.text
    assert response.json()["table_count"] > 0
    assert api.get("/api/admin/databases").json() == before
    assert "password" not in api.get("/api/admin/databases").text
    with SessionLocal() as db:
        assert db.get(DatabaseMount, 1) is None


@pytest.fixture
def second_connection(connections, monkeypatch):
    real_engine = database.make_engine
    name = "qa_registered_" + uuid.uuid4().hex[:12]

    def simulated_db(url, **kwargs):
        if make_url(url).database == name:
            url = settings.source_database_url
        return real_engine(url, **kwargs)

    # Two workspace identities, both using the isolated read-only demo transport.
    monkeypatch.setattr(database, "make_engine", simulated_db)
    return dict(connections, profile_id=None, database=name,
                password=make_url(settings.source_database_url).password)


@pytest.mark.parametrize("operation", ["register", "activate", "visibility"])
def test_slow_settings_validation_does_not_block_active_workspace_reads(
        api, connections, second_connection, monkeypatch, operation):
    original = database_settings.load_active_source()
    entered, release = Event(), Event()
    name = "database_tables" if operation == "register" else "refresh_sources"
    original_probe = getattr(database_settings, name)

    def delayed_probe(*args, **kwargs):
        entered.set()
        assert release.wait(10), "Test did not release the settings probe"
        return original_probe(*args, **kwargs)

    monkeypatch.setattr(database_settings, name, delayed_probe)

    def change_settings():
        with database.use_source(original):
            user = SimpleNamespace(id="qa-settings-concurrency")
            if operation == "visibility":
                return database_settings.set_table_visibility(connections["profile_id"],
                    database_settings.TableVisibilityInput(
                        schema_name="public", table_name="equipment", visible=True), user)
            return database_settings.save_or_activate(user,
                body=database_settings.ConnectionInput(**second_connection),
                activate=operation == "activate")

    with ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(change_settings)
        try:
            assert entered.wait(5), "Settings validation was not reached"
            # The previous workspace remains available until publication.
            reading = pool.submit(database_settings.load_active_source)
            assert reading.result(timeout=2).key == original.key
        finally:
            release.set()
        result = changing.result(timeout=15)
    assert result.get("status", "connected") == "connected"
    current = database_settings.load_active_source()
    assert current.key == (result["profile_id"] if operation == "activate" else original.key)


def test_register_then_activate_keeps_exactly_one_active_and_restores_workspace(api, connections, second_connection):
    a = api.get("/api/admin/databases").json()
    a_board = api.get("/api/dashboards").json()
    a_datasets = api.get("/api/bootstrap").json()["datasets"]
    saved = api.post("/api/admin/databases", json=second_connection)
    assert saved.status_code == 200, saved.text
    b = saved.json()
    assert b["active"] is False
    assert b["workspace_token"] == a["workspace_token"]
    listed = api.get("/api/admin/databases").json()
    assert listed["active_id"] == a["active_id"]
    assert {p["id"] for p in listed["profiles"]} == {a["active_id"], b["profile_id"]}
    assert "password" not in str(listed)
    assert api.get("/api/dashboards").json() == a_board
    with SessionLocal() as db:
        assert db.get(DatabaseMount, 1) is None
        assert db.get(DatabaseProfile, b["profile_id"]).encrypted_url.startswith("gAAAA")
    switched = api.post(f"/api/admin/databases/{b['profile_id']}/activate")
    assert switched.status_code == 200, switched.text
    assert switched.json()["active"]
    assert api.get("/api/admin/databases").json()["active_id"] == b["profile_id"]
    assert api.get("/api/dashboards").json()["id"] != a_board["id"]
    assert api.get("/api/dashboards", headers={"X-Workspace-Token": a["workspace_token"]}).status_code == 409
    with SessionLocal() as db:
        assert [m.profile_id for m in db.scalars(select(DatabaseMount))] == [b["profile_id"]]
    # Stored credentials suffice; the administrator need not reopen/retype the form.
    returned = api.post(f"/api/admin/databases/{a['active_id']}/activate")
    assert returned.status_code == 200, returned.text
    database.active_source = None
    assert api.get("/api/admin/databases").json()["active_id"] == a["active_id"]
    assert api.get("/api/dashboards").json() == a_board
    assert api.get("/api/bootstrap").json()["datasets"] == a_datasets


def test_failed_saved_profile_activation_preserves_selection_and_profiles(api, connections, second_connection, monkeypatch):
    registered = api.post("/api/admin/databases", json=second_connection).json()
    before = api.get("/api/admin/databases").json()

    def fail_discovery(db):
        raise HTTPException(503, "Connection unavailable")

    monkeypatch.setattr(database_settings, "refresh_sources", fail_discovery)
    failed = api.post(f"/api/admin/databases/{registered['profile_id']}/activate")
    assert failed.status_code == 503
    assert api.get("/api/admin/databases").json() == before
    assert api.post("/api/admin/databases/does-not-exist/activate").status_code == 404


def test_save_active_profile_reapplies_settings_and_failed_save_keeps_credentials(api, connections):
    saved = api.post("/api/admin/databases", json=connections)
    assert saved.status_code == 200, saved.text
    assert saved.json()["active"]
    before = api.get("/api/admin/databases").json()
    with SessionLocal() as db:
        ciphertext = db.get(DatabaseProfile, connections["profile_id"]).encrypted_url
    bad = dict(connections, password="qa-wrong-password-do-not-echo")
    rejected = api.post("/api/admin/databases", json=bad)
    assert rejected.status_code == 503
    assert bad["password"] not in rejected.text
    assert api.get("/api/admin/databases").json() == before
    with SessionLocal() as db:
        assert db.get(DatabaseProfile, connections["profile_id"]).encrypted_url == ciphertext


def test_cannot_unregister_active_db_even_with_stale_request_binding(api, connections, second_connection):
    key = connections["profile_id"]
    # Protect the environment connection before it has a persisted mount row.
    assert api.delete(f"/api/admin/databases/{key}").status_code == 409
    b = api.post("/api/admin/databases", json=second_connection).json()["profile_id"]
    assert api.post(f"/api/admin/databases/{b}/activate").status_code == 200
    before = api.get("/api/admin/databases").json()
    assert api.delete(f"/api/admin/databases/{b}").status_code == 409
    # Another admin might activate B after a delete request captured A's binding.
    with database.use_source(database.SourceBinding(None, key)):
        with pytest.raises(HTTPException) as error:
            database_settings.delete_database_registration(b)
    assert error.value.status_code == 409
    assert api.get("/api/admin/databases").json() == before


def test_unregister_removes_credentials_without_source_access_and_preserves_workspace(api, connections, second_connection, monkeypatch):
    b = api.post("/api/admin/databases", json=second_connection).json()["profile_id"]
    assert api.post(f"/api/admin/databases/{b}/activate").status_code == 200
    b_board = api.get("/api/dashboards").json()
    b_datasets = api.get("/api/bootstrap").json()["datasets"]
    assert api.post(f"/api/admin/databases/{connections['profile_id']}/activate").status_code == 200
    before = api.get("/api/admin/databases").json()
    # Deletion must still work when this profile cannot be decrypted or reached.
    with monkeypatch.context() as patch:
        def no_connection(*args, **kwargs):
            raise AssertionError("Unregister must not contact a source database")
        patch.setattr(database_settings, "profile_url", no_connection)
        patch.setattr(database, "make_engine", no_connection)
        removed = api.delete(f"/api/admin/databases/{b}")
        assert removed.status_code == 200, removed.text
    with SessionLocal() as db:
        assert db.get(DatabaseProfile, b) is None
        assert db.get(DatabaseProfileRemoval, b) is not None
        assert db.get(Dashboard, b_board["id"]) is not None
        for dataset in b_datasets:
            assert db.get(DatasetVersion, (dataset["id"], dataset["version"])) is not None
    after = api.get("/api/admin/databases").json()
    assert b not in {p["id"] for p in after["profiles"]}
    assert after["active_id"] == before["active_id"]
    assert after["workspace_token"] == before["workspace_token"]
    assert api.get(f"/api/admin/databases/{b}/tables").status_code == 404
    assert api.post(f"/api/admin/databases/{b}/activate").status_code == 404
    assert api.delete(f"/api/admin/databases/{b}").status_code == 404
    # Normal re-registration with credentials recovers the retained workspace.
    assert api.post("/api/admin/databases", json=second_connection).status_code == 200
    assert api.post(f"/api/admin/databases/{b}/activate").status_code == 200
    assert api.get("/api/dashboards").json() == b_board
    assert api.get("/api/bootstrap").json()["datasets"] == b_datasets
    assert api.get("/api/database-status").json()["status"] == "connected"


def test_unregistered_env_profile_does_not_reappear_until_explicit_registration(api, connections, second_connection):
    a = connections["profile_id"]
    # Keep a per-DB visibility preference along with the existing query history.
    toggle = {"schema_name": "public", "table_name": "equipment", "visible": False}
    assert api.put(f"/api/admin/databases/{a}/table-visibility", json=toggle).status_code == 200
    b = api.post("/api/admin/databases", json=second_connection).json()["profile_id"]
    assert api.post(f"/api/admin/databases/{b}/activate").status_code == 200
    assert api.delete(f"/api/admin/databases/{a}").status_code == 200
    database.active_source = None
    assert a not in {p["id"] for p in api.get("/api/admin/databases").json()["profiles"]}
    assert api.post(f"/api/admin/databases/{a}/activate").status_code == 404
    # Saving a different DB must not reimport the deleted environment profile.
    assert api.post("/api/admin/databases", json=second_connection).status_code == 200
    assert a not in {p["id"] for p in api.get("/api/admin/databases").json()["profiles"]}
    assert api.post("/api/admin/databases", json=connections).status_code == 404
    registration = dict(connections, profile_id=None, password=make_url(settings.source_database_url).password)
    assert api.post("/api/admin/databases", json=registration).status_code == 200
    assert api.get("/api/admin/databases").json()["active_id"] == b
    tables = api.get(f"/api/admin/databases/{a}/tables").json()["tables"]
    assert not next(t for t in tables if t["table_name"] == "equipment")["visible"]
    with SessionLocal() as db:
        assert db.get(DatabaseProfileRemoval, a) is None


def test_mount_restores_on_restart_and_enforces_workspace_token(api, connections):
    old_token = api.get("/api/database-status").json()["workspace_token"]
    result = api.post("/api/admin/databases/mount", json=connections)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["workspace_token"] != old_token
    with SessionLocal() as db:
        saved = db.get(DatabaseProfile, body["profile_id"])
        assert saved.encrypted_url.startswith("gAAAA")
        assert "postgresql" not in saved.encrypted_url
        restored = credential_store.decrypt_url(saved.encrypted_url)
        assert restored.password == make_url(settings.source_database_url).password
        assert db.get(DatabaseMount, 1).profile_id == saved.id
    # Simulate a fresh worker/process loading the active saved connection.
    database.active_source = None
    recovered = api.get("/api/database-status").json()
    assert recovered["status"] == "connected"
    assert recovered["workspace_token"] == body["workspace_token"]
    assert api.get("/api/dashboards", headers={"X-Workspace-Token": old_token}).status_code == 409
    assert api.get("/api/dashboards", headers={"X-Workspace-Token": body["workspace_token"]}).status_code == 200


def test_failed_mount_keeps_active_database_and_rolls_back_metadata(api, connections, monkeypatch):
    first = api.post("/api/admin/databases/mount", json=connections)
    assert first.status_code == 200, first.text
    before = api.get("/api/admin/databases").json()
    bad = dict(connections, password="qa-wrong-password-do-not-echo")
    failure = api.post("/api/admin/databases/mount", json=bad)
    assert failure.status_code == 503
    assert bad["password"] not in failure.text
    assert api.get("/api/admin/databases").json() == before
    original = database_settings.refresh_sources
    def fail_after_discovery(db):
        original(db)
        raise HTTPException(503, "Discovery failed")
    monkeypatch.setattr(database_settings, "refresh_sources", fail_after_discovery)
    failure = api.post("/api/admin/databases/mount", json=connections)
    assert failure.status_code == 503
    assert api.get("/api/admin/databases").json() == before


def test_table_switch_hides_and_restores_without_losing_history(api, connections):
    source = api.get("/api/bootstrap").json()
    original_dataset = next(d for d in source["datasets"] if d["id"] == "equipment")
    key = database.database_key(settings.source_database_url)
    path = f"/api/admin/databases/{key}/table-visibility"
    toggle = {"schema_name": "public", "table_name": "equipment", "visible": False}
    hidden = api.put(path, json=toggle)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["active"]
    assert "equipment" not in {d["id"] for d in api.get("/api/bootstrap").json()["datasets"]}
    assert api.post("/api/query", json={"dataset_id": "equipment", "dataset_version": 1}).status_code == 404
    tables = api.get(f"/api/admin/databases/{key}/tables").json()["tables"]
    assert not next(t for t in tables if t["table_name"] == "equipment")["visible"]
    with SessionLocal() as db:
        assert db.get(DatasetVersion, ("equipment", original_dataset["version"])) is not None
    shown = api.put(path, json=dict(toggle, visible=True))
    assert shown.status_code == 200, shown.text
    restored = next(d for d in api.get("/api/bootstrap").json()["datasets"] if d["id"] == "equipment")
    assert restored == original_dataset
    with SessionLocal() as db:
        assert db.get(DatabaseProfile, key).ignored_tables == []


def test_switch_databases_and_inflight_requests_keep_their_own_binding(api, connections, monkeypatch):
    a = api.post("/api/admin/databases/mount", json=connections).json()
    a_binding = database.active_source
    a_board = api.get("/api/dashboards").json()
    a_datasets = api.get("/api/bootstrap").json()["datasets"]
    real_engine = database.make_engine
    b_name = "qa_mount_" + uuid.uuid4().hex[:12]
    def simulated_db(url, **kwargs):
        if make_url(url).database == b_name:
            url = settings.source_database_url
        return real_engine(url, **kwargs)
    # Use the real read-only demo DB transport for the second workspace identity.
    monkeypatch.setattr(database, "make_engine", simulated_db)
    b_input = dict(connections, profile_id=None, database=b_name,
                   password=make_url(settings.source_database_url).password)
    b = api.post("/api/admin/databases/mount", json=b_input)
    assert b.status_code == 200, b.text
    assert b.json()["profile_id"] != a["profile_id"]
    b_board = api.get("/api/dashboards").json()
    assert b_board["id"] != a_board["id"] and not b_board["cards"]
    assert api.post("/api/query", json={"dataset_id": "equipment", "dataset_version": 1}).status_code == 404
    with database.use_source(a_binding):
        assert database.source_database_key() == a["profile_id"]
        assert database.current_source().engine is a_binding.engine
        with database_settings.source_connection() as connection:
            assert connection.dialect.name == "postgresql"
    returned = api.post("/api/admin/databases/mount", json=connections)
    assert returned.status_code == 200, returned.text
    assert api.get("/api/dashboards").json() == a_board
    assert api.get("/api/bootstrap").json()["datasets"] == a_datasets


def test_admin_permissions_validation_and_credentials_not_forwarded(api, connections):
    def denied():
        raise HTTPException(403, "관리자만 사용할 수 있습니다.")
    app.dependency_overrides[require_admin] = denied
    try:
        for path, body in [("/api/admin/databases/test", connections), ("/api/admin/databases/mount", connections),
                           ("/api/admin/databases", connections)]:
            assert api.post(path, json=body).status_code == 403
        assert api.get("/api/admin/databases").status_code == 403
        key = connections["profile_id"]
        assert api.post(f"/api/admin/databases/{key}/activate").status_code == 403
        assert api.delete(f"/api/admin/databases/{key}").status_code == 403
        assert api.get(f"/api/admin/databases/{key}/tables").status_code == 403
        assert api.put(f"/api/admin/databases/{key}/table-visibility",
                       json={"schema_name": "public", "table_name": "equipment", "visible": False}).status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin)
    assert api.post("/api/admin/databases/test", json=dict(connections, host="another-host")).status_code == 422
    for changes in ({"host": "host/db?options=evil"}, {"port": 0}, {"sslmode": "arbitrary"}, {"username": "\x00"}):
        assert api.post("/api/admin/databases/test", json=dict(connections, **changes)).status_code == 422


def test_writable_account_cannot_mount_and_missing_key_has_no_fallback(api, connections, tmp_path, monkeypatch):
    url = make_url(settings.app_database_url)
    writable = {"host": url.host, "port": url.port, "database": url.database,
                "username": url.username, "password": url.password}
    rejected = api.post("/api/admin/databases/mount", json=writable)
    assert rejected.status_code == 503
    assert "조회 전용" in rejected.text
    mounted = api.post("/api/admin/databases/mount", json=connections)
    assert mounted.status_code == 200, mounted.text
    key = mounted.json()["profile_id"]
    monkeypatch.setattr(credential_store, "KEY_FILE", tmp_path / "missing.key")
    database.active_source = None
    status = api.get("/api/database-status").json()
    assert status["status"] == "error" and status["connection_key"] == key
    assert database.active_source.engine is None
    assert api.get("/api/admin/databases").status_code == 200


def test_url_preserves_password_special_characters(connections):
    secret = "literal:@/?#%'; \" $data"
    body = database_settings.ConnectionInput(**dict(connections, password=secret))
    with SessionLocal() as db:
        url = database_settings.connection_url(db, body)
    assert url.password == secret
    assert make_url(url.render_as_string(hide_password=False)).password == secret


def test_legacy_ignore_moves_to_ui_and_can_be_overridden(api, connections, monkeypatch):
    monkeypatch.setattr(settings, "table_ignore", ["equipment"])
    key = connections["profile_id"]
    tables = api.get(f"/api/admin/databases/{key}/tables").json()["tables"]
    assert not next(t for t in tables if t["table_name"] == "equipment")["visible"]
    result = api.put(f"/api/admin/databases/{key}/table-visibility",
                     json={"schema_name": "public", "table_name": "equipment", "visible": True})
    assert result.status_code == 200, result.text
    # The persisted switch wins even when the old env entry still exists.
    assert "equipment" in {d["id"] for d in api.get("/api/bootstrap").json()["datasets"]}
    database.active_source = None
    assert "equipment" in {d["id"] for d in api.get("/api/bootstrap").json()["datasets"]}


def test_table_visibility_handles_quoted_names_without_collisions():
    from app.table_visibility import table_ignored
    with database.use_source(database.SourceBinding(None, "test", ignored_tables=(["a.b", "c"],))):
        assert table_ignored("a.b", "c")
        assert not table_ignored("a", "b.c")
