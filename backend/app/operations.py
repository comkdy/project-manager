"""Manual read-only diagnostics. No model request, job dispatch or repair here."""
from datetime import timedelta, timezone
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from . import db as database, llm_settings, recovery, worker_observation
from .auth import require_admin
from .database_settings import check_connection
from .models import LlmCredential, User, utcnow
from .analytics.models import AnalysisRun, AnalysisSchedule
from .semantic.collector_models import CollectedDraft, CollectionSettings, KnowledgeAnswer, KnowledgeQuestion
from .operations_contract import OperationCheck, OperationsStatus

router=APIRouter(prefix='/api/admin/operations',tags=['operational diagnostics'])
SOURCE_SCOPE='현재 원천 DB · 모든 프로젝트'

def check(identity,title,status,message,guidance,action=None,scope=SOURCE_SCOPE,observed_at=None):
    return OperationCheck(id=identity,title=title,status=status,message=message,guidance=guidance,
        action=action,scope=scope,observed_at=observed_at)

def utc(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value

def job_checks(db,now):
    key=database.source_database_key()
    counts=dict(db.execute(select(AnalysisRun.status,func.count()).where(AnalysisRun.connection_key==key).group_by(AnalysisRun.status)).all())
    stale=db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.connection_key==key,
        AnalysisRun.status.in_(['running','cancelling']),or_(AnalysisRun.lease_until.is_(None),AnalysisRun.lease_until<now)))
    waiting=db.scalar(select(func.count()).select_from(AnalysisRun).where(AnalysisRun.connection_key==key,
        AnalysisRun.status=='queued',AnalysisRun.created_at<now-timedelta(minutes=5)))
    jobs=check('analysis_queue','분석 대기·실행','attention' if stale or waiting or counts.get('failed') else 'ok',
        f"대기 {counts.get('queued',0)}건 · 진행 {counts.get('running',0)+counts.get('cancelling',0)}건 · 실패 {counts.get('failed',0)}건. 5분 이상 대기 {waiting}건 · 실행 확인 기한 경과 {stale}건.",
        '실패 건수는 보존된 과거 기록도 포함합니다. 오래 대기한 작업과 연결된 재시도 결과를 확인하세요. 점검은 작업 상태를 바꾸지 않습니다.','activity')
    pending=db.scalar(select(func.count()).select_from(CollectedDraft).join(KnowledgeQuestion,KnowledgeQuestion.id==CollectedDraft.question_id)
        .where(KnowledgeQuestion.connection_key==key,CollectedDraft.status=='pending'))
    answers=db.scalar(select(func.count()).select_from(KnowledgeAnswer).join(KnowledgeQuestion).where(KnowledgeQuestion.connection_key==key,KnowledgeAnswer.status=='failed'))
    needs=counts.get('needs_input',0)
    knowledge=check('knowledge_flow','질문·승인 연결','attention' if pending or needs or answers else 'ok',
        f'기준 확인 상태로 남은 분석 {needs}건 · 승인 대기 초안 {pending}건 · 처리 실패 답변 기록 {answers}건.',
        '과거 분석·실패 답변도 포함합니다. 결과·질문 확인에서 연결 기록을 보세요. 승인 후 조건을 확인하고 실행하며, 새 분석을 완료해도 원래 기록은 보존합니다.','activity')
    cfg=db.get(CollectionSettings,key)
    collection=check('collection','지식 수집 설정','attention' if cfg and cfg.last_status in {'error','failed'} else 'info' if not cfg or not cfg.enabled else 'ok',
        ('자동 수집 사용 중' if cfg and cfg.enabled else '자동 수집 꺼짐')+(' · 최근 수집 오류' if cfg and cfg.last_status in {'error','failed'} else ''),
        '자동 수집이 꺼져 있어도 수동 질문·답변·승인은 사용할 수 있습니다. 질문함에서 수집 설정과 실패 답변을 확인하세요.','knowledge',observed_at=cfg.last_run if cfg else None)
    enabled=db.scalar(select(func.count()).select_from(AnalysisSchedule).where(AnalysisSchedule.connection_key==key,AnalysisSchedule.enabled.is_(True)))
    paused=db.scalar(select(func.count()).select_from(AnalysisSchedule).where(AnalysisSchedule.connection_key==key,AnalysisSchedule.enabled.is_(False)))
    overdue=db.scalar(select(func.count()).select_from(AnalysisSchedule).where(AnalysisSchedule.connection_key==key,AnalysisSchedule.enabled.is_(True),
        or_(AnalysisSchedule.next_run_at.is_(None),AnalysisSchedule.next_run_at<now-timedelta(minutes=2))))
    other=db.scalar(select(func.count()).select_from(AnalysisSchedule).where(AnalysisSchedule.connection_key!=key,AnalysisSchedule.enabled.is_(True)))
    schedules=check('schedules','정기 분석 예약','attention' if overdue else 'info' if paused or other else 'ok',
        f'현재 DB 예약 {enabled}개 · 일시정지 {paused}개 · 2분 이상 지연 또는 다음 시각 누락 {overdue}개. 다른 DB 예약 {other}개.',
        '일시정지는 사용자 선택일 수 있습니다. 지연 시 선행 작업과 실행 제한을 확인하세요. 다른 DB의 정기 분석은 해당 DB를 활성화해야 실행됩니다.','schedules',scope='관리 DB 전체 · 현재/다른 원천 구분')
    return [jobs,knowledge,collection,schedules]

def llm_check(db,user):
    base,model=llm_settings.llm_configuration()
    item=db.get(LlmCredential,user.id)
    status,message='unknown','공통 AI 서버 또는 모델이 설정되지 않았습니다.'
    observed=None
    if base and model:
        if item is None:message='내 API 키가 등록되지 않았습니다.'
        else:
            try:
                value=json.loads(llm_settings.cipher().decrypt(item.encrypted_key.encode()).decode())
                if value.get('user_id')!=user.id or not isinstance(value.get('key'),str) or not value['key']:raise ValueError()
                current=item.checked_config==llm_settings.config_fingerprint(base,model)
                observed=item.checked_at if current else None
                status='attention' if current and item.check_status=='error' else 'info'
                message=('최근 연결 확인에 실패했습니다.' if status=='attention' else
                    '키를 읽을 수 있습니다. 저장된 최근 연결 확인은 성공입니다.' if current and item.check_status=='connected' else
                    '키를 읽을 수 있습니다. 현재 설정의 연결 확인 기록은 없습니다.')
            except Exception:
                status,message='attention','저장된 내 API 키를 해독할 수 없습니다. 암호화 설정 또는 키 재등록을 확인하세요.'
    return check('llm','내 AI 연결 준비',status,message,
        '점검은 AI 서버를 호출하지 않습니다. 프로필 설정에서 직접 연결을 확인하세요. 다른 사용자의 키나 AI 응답 품질은 확인하지 않습니다.','llm_settings',scope='현재 사용자 · 공통 AI 서버 설정',observed_at=observed)

def backup_check(user,now):
    try:
        state=recovery.status(user)
        if not state.supported:
            return check('backup','백업·복구 검증','unknown','현재 환경은 앱의 로컬 백업 검증 대상이 아닙니다.',
                '이 환경의 별도 백업·복원 절차를 확인하세요.','recovery',scope='관리 DB 전체')
        latest=state.items[0] if state.items else None
        verified=next((item for item in state.items if item.status=='verified' and item.checked_at),None)
        age=(now-utc(verified.created_at)).total_seconds() if verified else None
        status='attention'
        message='검증이 완료된 백업 기록이 없습니다.'
        if state.busy:status,message='info','백업 또는 복구 검증이 진행 중입니다. 완료 후 다시 점검하세요.'
        elif latest and latest.status!='verified':message='최근 백업 작업에 확인이 필요합니다. 백업 상세의 검사 결과를 확인하세요.'
        elif age is not None:
            status='attention' if age<0 or age>7*86400 else 'ok'
            message=('백업 생성 시각이 미래입니다. 서버 시각을 확인하세요.' if age<0 else
                f'검증된 최근 백업은 {int(age//86400)}일 전에 생성됐습니다.' if age>7*86400 else
                '최근 7일 이내에 생성된 백업의 복구 검증 완료 기록이 있습니다.')
        if state.backup_count>=recovery.LIMIT:
            status='attention';message+=' 보관 한도에 도달했습니다.'
        return check('backup','백업·복구 검증',status,message,
            '생성 시각 기준 7일은 점검 안내 기준입니다. 파일 내용과 현재 암호화 키를 다시 확인하려면 복구 검증을 실행하세요. 재검증은 백업에 최신 데이터를 추가하지 않습니다.',
            'recovery',scope='관리 DB 전체 · 최근 20개 기록 기준',observed_at=latest.checked_at if latest else None)
    except HTTPException:
        return check('backup','백업·복구 검증','unknown','백업 기록을 읽지 못했습니다.',
            '관리 DB와 보관 폴더를 확인한 뒤 백업 화면에서 다시 확인하세요.','recovery',scope='관리 DB 전체')

@router.get('',response_model=OperationsStatus)
def status(user=Depends(require_admin)):
    now=utcnow()
    try:
        with database.SessionLocal() as db:
            actor=db.get(User,user.id)
            if not actor or not actor.active or actor.role!='admin':raise HTTPException(403,'현재 관리자 권한이 필요합니다.')
            checks=job_checks(db,now)
            checks.append(llm_check(db,actor))
    except SQLAlchemyError:
        raise HTTPException(503,'관리 DB 점검에 실패했습니다. 연결을 확인한 뒤 다시 점검하세요.') from None
    checks.insert(0,check('metadata','관리 DB 조회','ok','권한과 작업·일정 기록을 읽을 수 있습니다.',
        '현재 조회 성공이며 저장 성능이나 장애 복구 완료를 의미하지 않습니다.',scope='관리 DB 전체'))
    binding=database.current_source()
    if not binding.key:
        source=check('source','원천 연결·읽기 전용 권한','unknown','활성 원천 DB가 없습니다.','DB 설정에서 조회할 DB를 등록·활성화하세요.','database')
    else:
        try:
            check_connection()
            source=check('source','원천 연결·읽기 전용 권한','ok','기존 조회 경로에서 연결과 읽기 전용 계정 검사를 통과했습니다.',
                '테이블 측정값과 데이터 최신성, 저장된 모든 SQL의 성공 여부를 확인한 것은 아닙니다.','database')
        except (HTTPException,SQLAlchemyError):
            source=check('source','원천 연결·읽기 전용 권한','attention','현재 원천 연결 또는 읽기 전용 권한 검사가 실패했습니다.',
                'DB 설정에서 연결·등록 정보·조회 계정 권한을 확인하세요. 저장된 분석·질문·승인은 유지됩니다.','database')
    checks.insert(1,source)
    checks.extend(OperationCheck.model_validate(item) for item in worker_observation.snapshot())
    checks.append(backup_check(user,now))
    checks.append(check('off_device','암호화 키·외부 보관','unknown','별도 보관 여부는 앱에서 확인할 수 없습니다.',
        '백업 파일과 암호화 키를 각각 안전한 외부 장소에 보관하세요. 현재 PC의 복구 검증만으로 외부 보관이 완료되지는 않습니다.','recovery',scope='운영자 직접 확인'))
    return OperationsStatus(workspace_token=database.workspace_token(),checked_at=utcnow(),checks=checks,notes=[
        '수동 점검 시점의 결과입니다. 자동 복구·재시도·예약 변경·승인·AI 호출·백업 생성은 수행하지 않습니다.',
        '위 프로젝트 선택과 무관합니다. 작업·질문은 현재 원천 DB 전체, 백업은 관리 DB 전체를 확인합니다.',
        '처리기 관측은 현재 응답 서버에만 해당하며 재시작하면 초기화됩니다. 다른 서버의 상태나 개별 작업 성공을 보장하지 않습니다.',
    ])
