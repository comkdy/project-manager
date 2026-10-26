"""Process-local observations; never change job outcomes or persist liveness in backups."""
from dataclasses import dataclass
from threading import Lock
import time
import uuid

from .models import utcnow

@dataclass(frozen=True)
class WorkerSpec:
    title: str
    idle_seconds: int
    busy_seconds: int

SPECS={
    'analysis': WorkerSpec('분석 처리',30,1200),
    'collection': WorkerSpec('질문·답변 처리',90,1200),
    'schedule': WorkerSpec('정기 분석 예약 확인',60,120),
}
_lock=Lock()
_states={}

def start(name):
    with _lock:
        _states[name]=dict(generation=uuid.uuid4().hex,active=True,busy=False,started=None,
            finished=None,success=None,tick=None,clock=time.monotonic())

def stop(name):
    with _lock:
        if name in _states:_states[name]['active']=False

def begin(name):
    with _lock:
        state=_states.get(name)
        if not state or not state['active']:return None
        token=(state['generation'],uuid.uuid4().hex)
        state.update(busy=True,started=utcnow(),tick=token,clock=time.monotonic())
        return token

def finish(name,token,success):
    with _lock:
        state=_states.get(name)
        if token is not None and state and state['active'] and state['tick']==token:
            state.update(busy=False,finished=utcnow(),success=success,clock=time.monotonic())

def run(name,operation):
    token=begin(name)
    try:
        result=operation()
    except Exception:
        finish(name,token,False)
        raise
    finish(name,token,True)
    return result

def snapshot():
    now=time.monotonic()
    with _lock:states={name:dict(value) for name,value in _states.items()}
    result=[]
    for name,spec in SPECS.items():
        state=states.get(name)
        status,message,observed='unknown','현재 서버에서 실행 확인 기록이 없습니다.',None
        if state:
            observed=state['started'] if state['busy'] else state['finished']
            elapsed=max(0,now-state['clock'])
            if not state['active']:status,message='attention','현재 서버의 처리 루프가 중지되었습니다.'
            elif elapsed>(spec.busy_seconds if state['busy'] else spec.idle_seconds):
                status,message='attention','처리 시작 후 응답이 오래 없습니다.' if state['busy'] else '최근 실행 확인 기록이 오래되었습니다.'
            elif state['busy']:status,message='info','처리 중입니다. 완료 여부는 작업별 기록에서 확인하세요.'
            elif state['success'] is False:status,message='attention','최근 처리 루프에서 오류가 발생했습니다. 다음 확인 결과와 작업별 상태를 확인하세요.'
            elif state['success'] is True:status,message='ok','최근 처리 루프가 정상적으로 반환되었습니다.'
            else:status,message='info','서버 시작 후 첫 실행을 기다립니다.'
        result.append(dict(id='worker_'+name,title=spec.title,scope='현재 요청을 처리한 서버',status=status,
            message=message,observed_at=observed,action='knowledge' if name=='collection' else 'schedules' if name=='schedule' else 'activity',
            guidance='개별 작업의 성공을 보장하는 표시는 아닙니다. 계속 응답이 없으면 서버 실행 상태를 확인하세요.'))
    return result
