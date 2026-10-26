"""Disposable browser QA app. Source data is read-only; live metadata is untouched.

Stop by creating .local/feature-bundle-qa.stop. Only the randomly named temporary
metadata database created by this process is removed on exit.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def serve():
    from datetime import timedelta
    from sqlalchemy import select, text
    from app import db as database
    from app.bootstrap import initialize
    from app.database_settings import load_active_source
    from app.source_discovery import refresh_sources
    from app.models import AppProject, Dashboard, DatasetVersion, User
    from app.query import preview_dataset, source_connection
    from app.schemas import CardSpec, DatasetSpec
    from app.analytics.models import migrate_analytics
    from app.analytics.service import create_run
    from app.analytics.schemas import AnalysisRequest
    from app.analytics.data import catalog
    from app.semantic.collector_models import CollectedDraft, KnowledgeAnswer, KnowledgeQuestion
    from app.semantic.collector_schemas import ExtractedKnowledge
    from app.semantic.collector_service import grounded_proposal
    import uvicorn

    initialize()
    migrate_analytics(database.engine)
    binding = load_active_source()
    with database.use_source(binding):
        with database.SessionLocal.begin() as db:
            sources = refresh_sources(db, None)
            source = next(s for s in sources if s['table_name'] == 'epm_data')
        spec = DatasetSpec(source_id=source['id'], raw_sql='SELECT lot_cd, value, end_dt_tm FROM public.epm_data')
        preview = preview_dataset(spec)
        with source_connection() as connection:
            start, end = connection.execute(text('SELECT min(end_dt_tm), max(end_dt_tm) FROM public.epm_data')).one()
            lot_cd = connection.scalar(text('SELECT min(lot_cd) FROM public.epm_data'))
        with database.SessionLocal.begin() as db:
            admin = db.scalar(select(User).where(User.role == 'admin'))
            project = AppProject(id=1000999, name='검증 프로젝트', lot_cds=[lot_cd], description='Disposable QA')
            db.add(project)
            for identity, name, owner in [('qa-shared', '검증 공통 측정', None), ('qa-project', '검증 전용 측정', project.id)]:
                db.add(DatasetVersion(id=identity, version=1, name=name, project_id=owner,
                                      spec=preview['spec'], columns=preview['columns']))
            db.flush()
            cards = [CardSpec(id='qa-average', title='검증 평균', dataset_id='qa-shared', dataset_version=1,
                              kind='bar', x='lot_cd', y='value', aggregate='avg', period={'use_dashboard': False}),
                     CardSpec(id='qa-count', title='검증 행수', dataset_id='qa-shared', dataset_version=1,
                              kind='kpi', aggregate='count', period={'use_dashboard': False})]
            for board_id, owner in [('common', None), (f'project-{project.id}', project.id)]:
                board = db.get(Dashboard, board_id)
                if board is None:
                    board = Dashboard(id=board_id, project_id=owner)
                    db.add(board)
                board.cards = [card.model_dump() for card in cards]
                board.layout = [{'i': card.id, 'x': index * 6, 'y': 0, 'w': 6, 'h': 4} for index, card in enumerate(cards)]
            series = next(s for s in catalog(db)['series'] if s['family'] == 'epm')
            midpoint = start + (end - start) / 2
            request = AnalysisRequest(kind='trend', family='epm', parameter=series['parameter'],
                baseline_start=start, baseline_end=midpoint, current_start=midpoint,
                current_end=end + timedelta(seconds=1), retest_policy='all_events')
            run = create_run(db, request, admin)
            question = db.get(KnowledgeQuestion, run.question_ids[0])
            answer = KnowledgeAnswer(question_id=question.id, text='최소 유효 측정 수는 3개, 평균 변화량 기준은 0.5입니다.',
                                     user_id=admin.id, status='ready')
            db.add(answer)
            db.flush()
            extracted = ExtractedKnowledge(name='검증 PARA 변화 기준', fields={'description': {
                'value': answer.text, 'answer_id': answer.id, 'quote': answer.text}})
            proposal, evidence = grounded_proposal(question, [{'id': answer.id, 'text': answer.text}], extracted)
            draft = CollectedDraft(question_id=question.id, answer_id=answer.id, proposal=proposal,
                                   original_proposal=proposal, evidence=evidence)
            db.add(draft)
            question.status, question.revision = 'review', question.revision + 1
            db.flush()
            ids = {'run_id': run.id, 'question_id': question.id, 'draft_id': draft.id,
                   'dataset_id': 'qa-shared', 'project_id': project.id}
        (ROOT / '.local/feature-bundle-qa-ids.json').write_text(json.dumps(ids), encoding='utf-8')
    print('Disposable feature QA ready at http://127.0.0.1:8001', flush=True)
    uvicorn.run('app.main:app', host='127.0.0.1', port=8001, access_log=False)


def main():
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import URL
    from cryptography.fernet import Fernet

    runtime = json.loads((ROOT / '.local/runtime.json').read_text(encoding='utf-8-sig'))
    if runtime.get('marker') != 'wafer-local-demo-v1' or runtime.get('port') != 55432:
        raise RuntimeError('Unrecognized local runtime')
    name = 'qa_feature_' + uuid.uuid4().hex[:12]
    stop = ROOT / '.local/feature-bundle-qa.stop'
    stop.unlink(missing_ok=True)
    def url(user, password, database):
        return URL.create('postgresql+psycopg', username=user, password=password, host='127.0.0.1',
                          port=runtime['port'], database=database).render_as_string(hide_password=False)
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
            with (ROOT / '.local/feature-bundle-qa.log').open('w', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'],
                                         cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                print('Temporary metadata created; waiting for QA stop file.', flush=True)
                while child.poll() is None and not stop.exists():
                    time.sleep(0.3)
                if child.poll() not in (None, 0):
                    print('QA startup failed; inspect .local/feature-bundle-qa.log.', flush=True)
                    return 1
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
            if not name.startswith('qa_feature_') or len(name) != 23:
                raise RuntimeError('Refusing unexpected cleanup target')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            print('Temporary QA metadata removed.', flush=True)
    return 0


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve()
    else:
        sys.exit(main())
