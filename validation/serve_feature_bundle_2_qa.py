"""Temporary UI QA with a deterministic local model adapter; no external LLM calls.

Real metadata/query/approval/analysis paths are used. Stop via the task-specific
stop file; cleanup only drops the random database created by this process.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def install_fixture_adapter():
    from sqlalchemy import select
    from starlette.concurrency import run_in_threadpool
    from app import assistant, db as database, llm_settings, main
    from app.chat_contracts import OUTPUTS
    from app.models import User, utcnow
    from app.semantic.models import KnowledgeItem, KnowledgeVersion
    from app.semantic.schemas import Content

    with database.SessionLocal.begin() as db:
        admin = db.scalar(select(User).where(User.role == 'admin'))
        for name, domain, description in [
            ('검증 평균 기준', '품질', '검증지표는 value의 NULL을 제외한 산술평균입니다.'),
            ('검증 합계 기준', '공정', '검증지표는 value의 NULL을 제외한 합계입니다.'),
        ]:
            item = KnowledgeItem(kind='term', connection_key=None, published_version=1)
            db.add(item)
            db.flush()
            db.add(KnowledgeVersion(item_id=item.id, version=1, status='approved', created_by=admin.id,
                reviewed_by=admin.id, reviewed_at=utcnow(), review_note='임시 검증 자료',
                content=Content(name=name, domain=domain, aliases=['검증지표'], description=description).model_dump()))

    def status(_):
        return {'server_configured': True, 'configured': True, 'key_registered': True,
                'base_url': 'http://127.0.0.1/deterministic-qa', 'model': 'local-qa-fixture',
                'status': 'connected', 'checked_at': None, 'message': '임시 검증용 고정 응답'}
    main.credential_status = llm_settings.credential_status = status
    assistant.llm_configuration = lambda: ('http://127.0.0.1/deterministic-qa', 'local-qa-fixture')
    assistant.load_key = lambda _: ('unused-local-qa-value', 'fixture')
    assistant.validate_llm_url = lambda _: 'http://127.0.0.1/deterministic-qa'

    async def run(adapter, skill, extra=None, validate=None):
        text = adapter.request.message
        if skill == 'route':
            if '분석' in text or '추세' in text:
                value = {'kind': 'analysis', 'analysis_kind': 'trend', 'family': 'epm', 'parameter': 'PARA1'}
                dates = ['2026-09-01T00:00:00Z', '2026-09-15T00:00:00Z', '2026-09-15T00:00:00Z', '2026-10-01T00:00:00Z']
                if all(date in text for date in dates):
                    value.update(zip(('baseline_start', 'baseline_end', 'current_start', 'current_end'), dates))
                    value['evidence'] = {key: text for key in ('baseline_start', 'baseline_end', 'current_start', 'current_end')}
                    if '모든 측정' in text:
                        value['retest_policy'] = 'all_events'
                        value['evidence']['retest_policy'] = '모든 측정'
            else:
                value = {'kind': 'route', 'intent': 'card' if '카드' in text else 'query'}
        elif skill == 'query':
            source = next(source for source in adapter.context['sources'] if source['table_name'] == 'epm_data')
            value = {'kind': 'query', 'explanation': '승인된 검증 평균 기준을 참고한 읽기 전용 SQL입니다.',
                     'name': '검증 평균 SQL', 'source_id': source['id'],
                     'sql': 'SELECT lot_cd, AVG(value) AS mean_value FROM public.epm_data GROUP BY lot_cd ORDER BY lot_cd'}
        elif skill == 'card':
            value = {'kind': 'card', 'explanation': '기존 공통 데이터셋을 재사용하는 검증 평균 카드입니다.',
                     'card': {'id': 'qa-new-card', 'title': '승인 기준 평균 카드', 'dataset_id': 'qa-shared',
                              'dataset_version': 1, 'kind': 'bar', 'x': 'lot_cd', 'y': 'value',
                              'aggregate': 'avg', 'period': {'use_dashboard': False}}}
        elif skill == 'dataset':
            value = {'kind': 'dataset', 'explanation': '검증 SQL 데이터셋입니다.', 'name': '검증 평균 SQL'}
        else:
            raise AssertionError('Unexpected QA skill')
        parsed = OUTPUTS[skill].validate_python(value)
        checked = await run_in_threadpool(validate, parsed) if validate else None
        return parsed, checked

    assistant.ModelAdapter.run = run


def serve():
    import uvicorn
    from serve_feature_bundle_qa import serve as seed_and_serve
    original_run = uvicorn.run
    def launch(*args, **kwargs):
        install_fixture_adapter()
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
    name = 'qa_bundle2_' + uuid.uuid4().hex[:12]
    stop = ROOT / '.local/feature-bundle-2-qa.stop'
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
            with (ROOT / '.local/feature-bundle-2-qa.log').open('w', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                print('Temporary bundle 2 metadata created; waiting for QA stop file.', flush=True)
                while child.poll() is None and not stop.exists():
                    time.sleep(0.3)
                if child.poll() not in (None, 0):
                    print('QA startup failed; inspect .local/feature-bundle-2-qa.log.', flush=True)
                    return 1
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
            if not name.startswith('qa_bundle2_') or len(name) != 23:
                raise RuntimeError('Refusing unexpected cleanup target')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            print('Temporary bundle 2 metadata removed.', flush=True)
    return 0


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve()
    else:
        sys.exit(main())
