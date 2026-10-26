"""Local metadata backup and isolated restore drill. Never overwrite a live DB."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from . import db as database
from .auth import require_admin
from .models import User, utcnow
from .recovery_contract import BackupView, RecoveryRequest, RecoveryStatus

ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = ROOT / '.local' / 'metadata-backups'
LOCK = 7126002040
LIMIT = 50
router = APIRouter(prefix='/api/admin/recovery', tags=['metadata recovery'])
NOTES = [
    '관리 DB 전체의 프로젝트·데이터셋·카드·분석·질문·승인·일정·알림을 백업합니다. 선택한 원천 DB와 무관하며 원천 측정값·브라우저 편집 초안은 포함하지 않습니다.',
    '임시 DB에 복원하여 내용과 연결을 비교하고 자동 실행을 멈춘 뒤 임시 DB를 제거합니다. 기존 DB·원천·일정은 변경하지 않습니다.',
    '암호화 키와 .env는 백업에 넣지 않습니다. DB 접속 정보·개인 LLM 키를 복원하려면 해당 암호화 키를 별도로 보관해야 합니다.',
    '백업은 이 PC에 저장됩니다. 백업 폴더와 암호화 키를 각각 별도의 안전한 장소에 보관하세요. 이 화면은 외부 보관 여부를 확인하지 않습니다.',
    '백업 파일 자체는 암호화하지 않습니다. 기존에 암호화된 접속 정보는 암호화된 상태로 보관하며, 폴더 접근 권한은 이 PC의 운영체제 설정을 따릅니다.',
    '백업 이후의 변경은 포함하지 않습니다. 자동 삭제하지 않으며 최대 50개, 화면에는 최근 20개를 표시합니다.',
]

class RecoveryError(Exception):
    pass

def configuration():
    """Validate the managed runtime before any subprocess or CREATE DATABASE."""
    try:
        settings = database.settings
        url = make_url(settings.app_database_url)
        runtime = json.loads((ROOT / '.local/runtime.json').read_text(encoding='utf-8-sig'))
        if (not settings.demo_mode or settings.auth_mode != 'local_admin'
                or url.drivername not in {'postgresql', 'postgresql+psycopg'}
                or url.host != '127.0.0.1' or url.port != 55432 or url.username != 'wafer_metadata'
                or not re.fullmatch(r'(wafer_app|qa_bundle[0-9]+_[a-f0-9]{12}|audit_midterm_[a-f0-9]{12})', url.database or '')
                or url.query or runtime['marker'] != 'wafer-local-demo-v1' or runtime['port'] != 55432
                or Path(runtime['bin_dir']).resolve() != (ROOT / '.local/pgsql/bin').resolve()
                or Path(runtime['data_dir']).resolve() != (ROOT / '.local/pgdata').resolve()): raise ValueError()
        for name in ('pg_dump.exe', 'pg_restore.exe'):
            if not (Path(runtime['bin_dir']) / name).is_file(): raise ValueError()
        return url, runtime
    except (OSError, ValueError, KeyError, TypeError):
        raise RecoveryError('이 작업공간에서 관리하는 로컬 PostgreSQL에서 사용할 수 있습니다. 다른 운영 환경은 별도의 백업·복원 절차를 사용하세요.') from None

def connect(url, **extra):
    return psycopg.connect(host=url.host, port=url.port, user=url.username, password=url.password,
        dbname=url.database, connect_timeout=5, options='-c timezone=UTC -c datestyle=ISO,YMD', **extra)

def admin_connect(runtime):
    return psycopg.connect(host='127.0.0.1', port=55432, user='wafer_local_admin',
        password=runtime['admin_password'], dbname='postgres', connect_timeout=5, autocommit=True)

def storage():
    return BACKUP_ROOT / database.database_key(database.settings.app_database_url)

def folder(identity):
    if not re.fullmatch('[a-f0-9]{32}', identity): raise RecoveryError('백업 식별자를 확인하세요.')
    root = storage()
    target = root / identity
    if (not BACKUP_ROOT.resolve().is_relative_to(ROOT.resolve())
            or not root.resolve().is_relative_to(BACKUP_ROOT.resolve())
            or not target.resolve().is_relative_to(root.resolve())):
        raise RecoveryError('백업 저장 위치를 확인하세요.')
    return target

def write_record(record):
    path = folder(record['id'])
    path.mkdir(parents=True, exist_ok=True)
    temporary = path / ('record-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as out:
        os.chmod(temporary, 0o600)
        json.dump(record, out, ensure_ascii=False, indent=2)
    temporary.replace(path / 'record.json')

def records():
    root = storage()
    if not root.exists(): return []
    if not root.resolve().is_relative_to(BACKUP_ROOT.resolve()): raise RecoveryError('백업 저장 위치를 확인하세요.')
    values = []
    for path in root.iterdir():
        if not re.fullmatch('[a-f0-9]{32}', path.name): continue
        safe = folder(path.name) / 'record.json'
        if not safe.resolve().is_relative_to(folder(path.name).resolve()): raise RecoveryError('백업 기록 위치를 확인하세요.')
        if not safe.is_file(): continue
        record = json.loads(safe.read_text(encoding='utf-8'))
        if record['id'] != path.name or record['database_key'] != root.name: raise RecoveryError('백업 기록을 확인하세요.')
        values.append(record)
    return sorted(values, key=lambda r: (r['created_at'], r['id']), reverse=True)

def view(record, busy):
    status = record['status']
    if status == 'running' and not busy: status = 'interrupted'
    return BackupView(id=record['id'], status=status, stage=record['stage'], created_at=record['created_at'],
        checked_at=record.get('checked_at'), size_bytes=record.get('size_bytes', 0),
        table_count=len(record.get('tables', {})), row_count=sum(v['rows'] for v in record.get('tables', {}).values()),
        checks=record.get('checks', []), message=('작업이 중단되었습니다. 저장된 백업이 있으면 복구 검증을 다시 실행하세요.' if status == 'interrupted' else record.get('message', '')),
        can_verify=not busy and bool(record.get('sha256')), folder=str(folder(record['id']).relative_to(ROOT)))

def acquire(url):
    connection = connect(url, autocommit=True)
    if not connection.execute('SELECT pg_try_advisory_lock(%s)', (LOCK,)).fetchone()[0]:
        connection.close(); return None
    return connection

def live_admin(user):
    with database.SessionLocal() as db:
        actor = db.get(User, user.id)
        if not actor or not actor.active or actor.role != 'admin': raise HTTPException(403, '현재 관리자 권한이 필요합니다.')

@router.get('', response_model=RecoveryStatus)
def status(user=Depends(require_admin)):
    live_admin(user)
    try: url, _ = configuration()
    except RecoveryError as error:
        return RecoveryStatus(workspace_token=database.workspace_token(), supported=False, reason=str(error),
            busy=False, backup_count=0, can_create=False, items=[], notes=NOTES)
    try:
        lock = acquire(url); busy = lock is None
        try: items = records()
        finally:
            if lock: lock.close()
        return RecoveryStatus(workspace_token=database.workspace_token(), supported=True, reason='', busy=busy,
            backup_count=len(items), can_create=not busy and len(items)<LIMIT,
            items=[view(r, busy) for r in items[:20]], notes=NOTES)
    except (OSError, ValueError, KeyError, RecoveryError, psycopg.Error):
        raise HTTPException(503, '백업 상태를 확인하지 못했습니다. 관리 DB와 백업 폴더를 확인하세요.') from None

def start(body, user, tasks, identity=None):
    live_admin(user)
    if body.workspace_token != database.workspace_token(): raise HTTPException(409, '작업공간이 변경되었습니다. 최신 상태에서 다시 요청하세요.')
    lock = None
    try:
        url, runtime = configuration(); lock = acquire(url); items = records()
        operation = 'verify:'+identity if identity else 'create'
        previous = next((r for r in items if any(request['id'] == body.request_id for request in r.get('requests', []))), None)
        if previous:
            request = next(request for request in previous['requests'] if request['id'] == body.request_id)
            if request['operation'] != operation: raise RecoveryError('요청 식별자가 다른 작업에 사용되었습니다.')
            return view(previous, lock is None)
        if not lock: raise HTTPException(409, '이미 백업 또는 복구 검증이 진행 중입니다.')
        if identity:
            record = next((r for r in items if r['id'] == identity), None)
            if not record or not record.get('sha256'): raise HTTPException(404, '검증할 백업 파일이 없습니다.')
        else:
            if len(items)>=LIMIT: raise HTTPException(409, '백업 50개가 보관되어 있습니다. 별도 보관 후 운영자가 보관 폴더를 정리하세요.')
            record = dict(id=uuid.uuid4().hex, database_key=database.database_key(url), created_at=utcnow().isoformat())
        # A lock-free running record belongs to an interrupted process. Persist this
        # before admitting another job so the status page cannot mistake it for that job.
        for stale in items:
            if stale['status'] == 'running':
                stale.update(status='interrupted', message='이전 작업이 중단되었습니다. 저장된 백업을 다시 검증하세요.')
                write_record(stale)
        record.setdefault('requests', []).append({'id': body.request_id, 'operation': operation})
        record.update(status='running', stage='복구 검증 준비' if identity else '백업 준비', request_id=body.request_id,
            checked_at=None, checks=[], message='관리 DB 전체를 대상으로 작업합니다.')
        write_record(record)
        tasks.add_task(work, record, url, runtime, lock, not bool(identity))
        lock = None
        return view(record, True)
    except HTTPException: raise
    except RecoveryError as error: raise HTTPException(422, str(error)) from None
    except (OSError, ValueError, KeyError, psycopg.Error):
        raise HTTPException(503, '백업 작업을 시작하지 못했습니다. 관리 DB와 백업 폴더를 확인하세요.') from None
    finally:
        if lock: lock.close()

@router.post('', response_model=BackupView, status_code=202)
def create(body: RecoveryRequest, tasks: BackgroundTasks, user=Depends(require_admin)):
    return start(body, user, tasks)

@router.post('/{identity}/verify', response_model=BackupView, status_code=202)
def verify(identity: str, body: RecoveryRequest, tasks: BackgroundTasks, user=Depends(require_admin)):
    return start(body, user, tasks, identity)

def file_digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()

def run_client(name, runtime, url, args):
    # Credentials go only to the child environment, never arguments or logs.
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith('PG')}
    env.update(PGHOST=url.host, PGPORT=str(url.port), PGUSER=url.username, PGPASSWORD=url.password or '',
        PGDATABASE=url.database, PGCONNECT_TIMEOUT='5', PGOPTIONS='-c statement_timeout=300000')
    result = subprocess.run([str(Path(runtime['bin_dir']) / name), '--no-password', *args],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode: raise RecoveryError('PostgreSQL 백업·복원 도구 실행에 실패했습니다. 연결·디스크 공간·도구 버전을 확인하세요.')

def inventory(connection):
    """Stream canonical rows in PK order within the exported snapshot."""
    names = [r[0] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename") if r[0].startswith('app_')]
    required = {'app_users','app_analysis_runs','app_analysis_schedules','app_analysis_notifications','app_semantic_questions','app_semantic_versions'}
    if not required<=set(names): raise RecoveryError('필수 앱 관리 테이블이 없습니다. 앱 초기화와 버전을 확인하세요.')
    tables = {}
    for name in names:
        primary = [r[0] for r in connection.execute("""SELECT a.attname FROM pg_index i JOIN pg_attribute a
            ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
            WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY a.attnum""", ('public.'+name,))]
        if not primary: raise RecoveryError('기본 키가 없는 관리 테이블은 이 백업 검증에서 지원하지 않습니다.')
        query = sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY {}').format(sql.Identifier('public',name), sql.SQL(',').join(map(sql.Identifier,primary)))
        count, digest = 0, hashlib.sha256()
        with connection.cursor(name='backup_'+uuid.uuid4().hex) as cursor:
            cursor.execute(query)
            for row in cursor:
                digest.update(row[0].encode('utf-8'));digest.update(b'\n');count+=1
        tables[name]={'rows':count,'sha256':digest.hexdigest()}
    return tables

def relation_issues(connection):
    queries = {
        '분석 질문': """SELECT count(*) FROM app_analysis_runs r CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(NULLIF(r.question_ids::jsonb,'null'::jsonb),'[]'::jsonb)) q(id)
            LEFT JOIN app_semantic_questions k ON k.id=q.id WHERE k.id IS NULL""",
        '승인 근거': """SELECT count(*) FROM app_analysis_runs r CROSS JOIN LATERAL jsonb_array_elements(COALESCE(NULLIF(r.request::jsonb->'knowledge_refs','null'::jsonb),'[]'::jsonb)) v(ref)
            LEFT JOIN app_semantic_versions k ON k.item_id=v.ref->>'item_id' AND k.version::text=v.ref->>'version' WHERE k.item_id IS NULL""",
        '승인 답변': """SELECT count(*) FROM app_semantic_collected_drafts d LEFT JOIN app_semantic_versions v ON v.item_id=d.item_id AND v.version=d.item_version
            WHERE d.status='approved' AND v.item_id IS NULL""",
        '일정 실행': """SELECT count(*) FROM app_analysis_schedules s CROSS JOIN LATERAL unnest(ARRAY[s.origin_run_id,s.last_run_id,s.outcome_run_id]) ref(id)
            LEFT JOIN app_analysis_runs r ON r.id=ref.id WHERE ref.id IS NOT NULL AND r.id IS NULL""",
        '재시도·비교': """SELECT count(*) FROM app_analysis_runs a CROSS JOIN LATERAL unnest(ARRAY[a.retry_of,a.retry_run_id,a.comparison_run_id]) ref(id)
            LEFT JOIN app_analysis_runs r ON r.id=ref.id WHERE ref.id IS NOT NULL AND r.id IS NULL""",
        '알림 근거': """SELECT count(*) FROM app_analysis_notifications n LEFT JOIN app_analysis_schedules s ON s.id=n.schedule_id
            LEFT JOIN app_analysis_runs r ON r.id=n.run_id LEFT JOIN app_analysis_runs b ON b.id=n.comparison_run_id
            WHERE s.id IS NULL OR (n.run_id IS NOT NULL AND r.id IS NULL) OR (n.comparison_run_id IS NOT NULL AND b.id IS NULL)""",
    }
    return {name:connection.execute(query).fetchone()[0] for name,query in queries.items()}

def credential_checks(connection):
    from .credential_store import cipher as db_cipher
    from .llm_settings import cipher as llm_cipher
    result=[]
    for label,query,factory in (
        ('DB 접속 정보','SELECT id, encrypted_url FROM app_database_profiles',db_cipher),
        ('개인 LLM 키','SELECT user_id, encrypted_key FROM app_llm_credentials',llm_cipher)):
        rows=connection.execute(query).fetchall();ok=True
        try:
            cipher=factory() if rows else None
            for owner,ciphertext in rows:
                plain=cipher.decrypt(ciphertext.encode()).decode()
                if label=='개인 LLM 키':
                    payload=json.loads(plain)
                    if payload.get('user_id')!=owner or not payload.get('key'):raise ValueError()
                else:make_url(plain)
        except Exception:ok=False
        result.append(dict(label=label+' 복호화',status='passed' if ok else 'attention',message=(
            f'{len(rows)}건을 현재 암호화 키로 확인했습니다. 키는 별도 보관이 필요합니다.' if rows and ok else
            '저장된 암호화 정보가 없습니다.' if not rows else '암호화 키가 없거나 일치하지 않습니다. 별도 보관한 키를 확인하세요.')))
    return result

def freeze_automation(connection):
    connection.execute("UPDATE app_analysis_schedules SET enabled=false,next_run_at=NULL")
    connection.execute("UPDATE app_semantic_collection_settings SET enabled=false,next_run=NULL,lease_token=NULL,lease_until=NULL,last_status=CASE WHEN last_status IN ('running','cancelling') THEN 'cancelled' ELSE last_status END")
    connection.execute("UPDATE app_analysis_runs SET status=CASE WHEN status='cancelling' THEN 'cancelled' ELSE 'failed' END, lease_until=NULL, revision=revision+1 WHERE status IN ('queued','running','cancelling')")
    connection.execute("UPDATE app_semantic_answers SET status='failed',lease_token=NULL,lease_until=NULL WHERE status IN ('queued','processing')")
    return connection.execute("SELECT (SELECT count(*) FROM app_analysis_schedules WHERE enabled)+(SELECT count(*) FROM app_semantic_collection_settings WHERE enabled OR last_status IN ('running','cancelling'))+(SELECT count(*) FROM app_analysis_runs WHERE status IN ('queued','running','cancelling'))+(SELECT count(*) FROM app_semantic_answers WHERE status IN ('queued','processing'))").fetchone()[0]==0

def cleanup(runtime,record):
    name=record.get('temporary_database')
    if not name:return
    if not re.fullmatch('restorecheck_[a-f0-9]{32}',name):raise RecoveryError('임시 복구 DB 식별자를 확인하세요.')
    with admin_connect(runtime) as admin:
        row=admin.execute("SELECT shobj_description(oid,'pg_database'),pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",(name,)).fetchone()
        if row:
            if row!=('project-manager-recovery:'+record['id'],'wafer_metadata'):raise RecoveryError('임시 DB 소유 표식이 다릅니다. 자동 정리를 중단했습니다.')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
    record.pop('temporary_database',None)

def backup(record,url,runtime):
    path=folder(record['id'])/'metadata.dump'
    if path.exists() or path.is_symlink(): raise RecoveryError('백업 파일이 이미 존재합니다. 덮어쓰지 않습니다.')
    record['stage']='관리 데이터 백업';write_record(record)
    with connect(url) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        source.execute("SET LOCAL statement_timeout='300s'")
        snapshot=source.execute('SELECT pg_export_snapshot()').fetchone()[0]
        record['tables']=inventory(source);record['relation_issues']=relation_issues(source)
        run_client('pg_dump.exe',runtime,url,['--format=custom','--no-owner','--no-privileges',
            '--table=public.app_*','--snapshot='+snapshot,'--file='+str(path)])
    os.chmod(path,0o600)
    record.update(sha256=file_digest(path),size_bytes=path.stat().st_size);write_record(record)

def restore_check(record,url,runtime):
    path=folder(record['id'])/'metadata.dump'
    if not path.is_file() or not path.resolve().is_relative_to(folder(record['id']).resolve()) or file_digest(path)!=record.get('sha256'):
        raise RecoveryError('백업 파일이 없거나 무결성 값이 다릅니다. 원본 백업 파일을 확인하세요.')
    cleanup(runtime,record)
    name='restorecheck_'+uuid.uuid4().hex
    record.update(stage='임시 DB 복원 검증',temporary_database=name);write_record(record)
    with admin_connect(runtime) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER wafer_metadata TEMPLATE template0').format(sql.Identifier(name)))
        admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(name),sql.Literal('project-manager-recovery:'+record['id'])))
    target=url.set(database=name)
    run_client('pg_restore.exe',runtime,target,['--exit-on-error','--no-owner','--no-privileges','--dbname='+name,str(path)])
    record['stage']='복원 내용·흐름 연결 확인';write_record(record)
    with connect(target) as restored:
        restored.execute("SET LOCAL statement_timeout='300s'")
        if inventory(restored)!=record['tables']:raise RecoveryError('복원된 테이블·내용이 백업 시점과 일치하지 않습니다.')
        issues=relation_issues(restored)
        if issues!=record['relation_issues']:raise RecoveryError('분석·질문·승인·일정 연결이 백업 시점과 다릅니다.')
        count=sum(issues.values())
        record['checks']=[dict(label='전체 테이블·내용',status='passed',message='백업 시점의 행 수와 전체 내용 해시가 일치합니다.'),
            dict(label='분석·질문·승인·일정·비교 연결',status='attention' if count else 'passed',message=(
                '복원 전후 연결이 동일합니다. 백업 원본에 이미 없는 참조 '+str(count)+'건이 있습니다: '+', '.join(k for k,v in issues.items() if v)) if count else '연결된 기록과 승인 버전·재시도·알림 근거를 확인했습니다.')]
        record['checks']+=credential_checks(restored)
        if not freeze_automation(restored):raise RecoveryError('임시 복원 DB의 자동 실행 중단 확인에 실패했습니다.')
        record['checks'].append(dict(label='복원 시 자동 실행 중단',status='passed',message='임시 복원본의 예약·대기 작업을 중단했습니다. 원래 앱의 일정은 유지됩니다.'))

def work(record,url,runtime,lock,create_new):
    try:
        if create_new:backup(record,url,runtime)
        restore_check(record,url,runtime)
        record.update(status='attention' if any(c['status']=='attention' for c in record['checks']) else 'verified',
            checked_at=utcnow().isoformat(),stage='검증 완료',message='백업의 임시 복원 검증을 완료했습니다. 암호화 키와 외부 보관은 별도로 준비하세요.')
    except Exception as error:
        record.update(status='failed',stage='작업 실패',checked_at=None,
            message=str(error) if isinstance(error,RecoveryError) else '백업·복원 검증에 실패했습니다. 연결·백업 폴더·디스크 공간을 확인하고 다시 시도하세요.')
    finally:
        try:cleanup(runtime,record)
        except Exception:record.update(status='failed',message='임시 복구 DB 정리가 완료되지 않았습니다. 다시 검증하여 정리를 재시도하세요.',checked_at=None)
        try:write_record(record)
        finally:lock.close()
