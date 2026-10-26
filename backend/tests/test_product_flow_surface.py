"""Removed operational features must not be reachable in the production app."""
from fastapi.testclient import TestClient
from app.main import app

def test_backup_and_operations_are_not_registered():
    paths={getattr(route,'path','') for route in app.routes}
    assert not any(path.startswith(('/api/admin/recovery','/api/admin/operations')) for path in paths)

def test_removed_urls_cannot_render_the_app_or_execute_actions():
    # No lifespan: this test must not initialize or modify any database.
    client=TestClient(app)
    for path in ('/api/admin/recovery','/api/admin/recovery/old/verify','/api/admin/operations'):
        assert client.get(path).status_code==404
        assert client.post(path,json={}).status_code in (404,405)
