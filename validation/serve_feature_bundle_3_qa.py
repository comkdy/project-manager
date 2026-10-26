"""Disposable bundle 3 QA; fixed local model replies, real application paths.

Stop via .local/feature-bundle-3-qa.stop. The random temporary metadata DB
created by this process is removed on exit; source data stays read-only.
"""
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def serve():
    import uvicorn
    from sqlalchemy import select
    from app import db as database
    from app.analytics.models import AnalysisRun
    from app.models import utcnow
    from serve_feature_bundle_qa import serve as seed_and_serve
    from serve_feature_bundle_2_qa import install_fixture_adapter
    original_run = uvicorn.run

    def launch(*args, **kwargs):
        install_fixture_adapter()
        with database.SessionLocal.begin() as db:
            original = db.scalar(select(AnalysisRun).limit(1))
            db.add(AnalysisRun(connection_key=original.connection_key, kind=original.kind,
                status='failed', request=copy.deepcopy(original.request),
                source_snapshot=copy.deepcopy(original.source_snapshot),
                created_by=original.created_by, completed_at=utcnow(),
                error='임시 검증용 조회 실패', question_ids=[]))
        original_run(*args, **kwargs)
    uvicorn.run = launch
    seed_and_serve()


def main():
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import URL
    from cryptography.fernet import Fernet
    runtime = json.loads((ROOT / '.local/runtime.json').read_text(encoding='utf-8-sig'))
    if runtime.get('marker') != 'wafer-local-demo-v1' or runtime.get('port') != 55432:
        raise RuntimeError('Unrecognized local runtime')
    name = 'qa_bundle3_' + uuid.uuid4().hex[:12]
    stop = ROOT / '.local/feature-bundle-3-qa.stop'
    stop.unlink(missing_ok=True)
    def url(user, password, database):
        return URL.create('postgresql+psycopg', username=user, password=password,
            host='127.0.0.1', port=runtime['port'], database=database).render_as_string(hide_password=False)
    env = dict(os.environ)
    env.update(APP_DATABASE_URL=url('wafer_metadata', runtime['app_password'], name),
        SOURCE_DATABASE_URL=url('wafer_source', runtime['source_password'], 'wafer_fab_demo'),
        DEMO_MODE='true', AUTH_MODE='local_admin', LLM_BASE_URL='', LLM_MODEL='',
        DB_CREDENTIALS_KEY=Fernet.generate_key().decode(), LLM_CREDENTIALS_KEY=Fernet.generate_key().decode(),
        TABLE_IGNORE='[]', ALLOWED_ORIGINS='http://127.0.0.1:8001', PYTHONPATH=str(ROOT / 'backend'))
    with psycopg.connect(host='127.0.0.1', port=runtime['port'], dbname='postgres', user='wafer_local_admin',
        password=runtime['admin_password'], autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER wafer_metadata').format(sql.Identifier(name)))
        child = None
        try:
            with (ROOT / '.local/feature-bundle-3-qa.log').open('w', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                print('Temporary bundle 3 metadata created; waiting for QA stop file.', flush=True)
                while child.poll() is None and not stop.exists():
                    time.sleep(0.3)
                if child.poll() not in (None, 0):
                    print('QA startup failed; inspect .local/feature-bundle-3-qa.log.', flush=True)
                    return 1
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
            if not name.startswith('qa_bundle3_') or len(name) != 23:
                raise RuntimeError('Refusing unexpected cleanup target')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            print('Temporary bundle 3 metadata removed.', flush=True)
    return 0


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve()
    else:
        sys.exit(main())
