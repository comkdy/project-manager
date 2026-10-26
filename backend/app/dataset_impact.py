"""Read-only compatibility checks for cards that follow a dataset's latest version."""
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from .catalog import projects_list
from .dataset_impact_contract import DatasetImpact
from .db import SessionLocal, source_database_key
from .dashboard_period import apply_period
from .models import Dashboard, DatasetVersion
from .query import preview_dataset, validate_card
from .schemas import CardSpec, QueryContext
from .table_visibility import TableVisibility, source_visible


def column_changes(previous, proposed):
    before = {c['name']: c['type'] for c in previous}
    after = {c['name']: c['type'] for c in proposed}
    return [dict(name=name, change='removed' if name not in after else 'added' if name not in before else 'type_changed',
                 previous_type=before.get(name), next_type=after.get(name))
            for name in dict.fromkeys([*before, *after]) if before.get(name) != after.get(name)]


def static_issues(card, dataset, catalog):
    try:
        compiled = validate_card(card, dataset=dataset, catalog=catalog)
    except HTTPException as error:
        return [str(error.detail)]
    if card.kind == 'wafer' and 'wafer_id' not in {c['name'] for c in dataset.columns}:
        return ['Wafer map에는 wafer_id가 필요합니다.']
    # Gantt uses the dashboard period only as a display boundary. Its source
    # queries do not apply the ordinary chart date-column predicate.
    if card.kind != 'gantt' and card.period.use_dashboard:
        try:
            apply_period(compiled, card, QueryContext(period_start={'year': 2026, 'week': 1}))
        except HTTPException as error:
            return ['공통 기간을 적용할 때: ' + str(error.detail)]
    return []


def card_impact(raw, previous, proposed, catalog, changes):
    try:
        card = CardSpec.model_validate(raw)
    except ValidationError as error:
        messages = ['.'.join(str(part) for part in issue['loc']) + ': ' + issue['msg'] for issue in error.errors()]
        return 'review', ['기존 카드 설정을 먼저 확인하세요: ' + '; '.join(messages)]
    before = static_issues(card, previous, catalog)
    after = static_issues(card, proposed, catalog)
    if before:
        issues = ['기존 버전에서도 확인이 필요한 설정: ' + issue for issue in before]
        issues.extend('변경 후 확인이 필요한 설정: ' + issue for issue in after)
        status = 'incompatible' if any(issue not in before for issue in after) else 'review'
    elif after:
        status, issues = 'incompatible', after
    else:
        status, issues = 'compatible', []
    if card.editor_mode == 'python':
        if status == 'compatible':
            status = 'review'
        issues.append('Python 스크립트는 실행하지 않았습니다. 새 데이터셋으로 실행 결과를 확인하세요.')
    else:
        referenced = {card.x, card.y, card.color, card.trellis, *(f.column for f in card.filters)}
        referenced.update(t.name for t in card.value_expression or [] if t.kind == 'column')
        if card.aggregate == 'yield':
            referenced.update(('tested_dies', 'passed_dies'))
        if card.kind == 'gantt' and card.gantt:
            from .gantt import PLAN_COLUMNS
            referenced.update(PLAN_COLUMNS)
            referenced.update(f.column for f in [*card.gantt.line_filters, *card.gantt.key_filters])
        changed_types = [c['name'] for c in changes if c['change'] == 'type_changed' and c['name'] in referenced]
        if changed_types and status == 'compatible':
            status = 'review'
            issues.append('사용 중인 컬럼의 타입이 바뀝니다. 필터 값과 표시 결과를 확인하세요: ' + ', '.join(changed_types))
        if card.kind == 'gantt' and card.gantt and card.gantt.data_mode == 'tables':
            issues.append('변경 SQL은 표시 공정 선택에 반영됩니다. 현재 상태·TAT·이력은 연결된 원천 테이블을 그대로 사용합니다.')
    return status, issues


def dataset_impact(dataset_id, body):
    projects = {project['id']: project['name'] for project in projects_list()}
    if body.project_id is not None and body.project_id not in projects:
        raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
    with SessionLocal() as db:
        previous = db.scalar(select(DatasetVersion).where(DatasetVersion.id == dataset_id)
                             .order_by(DatasetVersion.version.desc()).limit(1))
        if previous is None:
            raise HTTPException(404, '데이터셋을 찾을 수 없습니다.')
        if previous.version != body.expected_version:
            raise HTTPException(409, '데이터셋에 새 버전이 있습니다. 최신 버전을 확인하세요.')
        catalog = TableVisibility.load(db)
        source = catalog.sources.get(previous.spec.get('source_id'))
        if source is None or not source_visible(source) or not catalog.allows_spec(previous.spec):
            raise HTTPException(404, '앱에서 제외된 테이블을 사용하는 데이터셋입니다.')
        if previous.project_id != body.project_id:
            raise HTTPException(422, '데이터셋의 저장 범위는 버전 변경으로 바꿀 수 없습니다. 프로젝트 전용 데이터셋으로 새로 저장하세요.')
        preview = preview_dataset(body.spec, QueryContext(project_id=previous.project_id))
        proposed = DatasetVersion(id=dataset_id, version=previous.version + 1, name=body.name,
                                  project_id=previous.project_id, spec=preview['spec'], columns=preview['columns'])
        changes = column_changes(previous.columns, proposed.columns)
        impacts = []
        boards = db.scalars(select(Dashboard).where(Dashboard.connection_key == source_database_key()).order_by(Dashboard.id))
        for board in boards:
            if board.project_id is not None and board.project_id not in projects:
                continue
            if previous.project_id is not None and board.project_id != previous.project_id:
                continue
            for raw in board.cards:
                if raw.get('dataset_id') != dataset_id or raw.get('kind') == 'heatmap':
                    continue
                # Auxiliary Gantt tables are physical source references, not
                # references to datasets that happen to have the same ID.
                if raw.get('kind') == 'gantt' and (raw.get('gantt') or {}).get('data_mode') != 'dataset':
                    linked = [(raw.get('gantt') or {}).get(role + '_source_id') for role in ('status', 'tat', 'history')]
                    if any(identity not in catalog.sources or identity in catalog.hidden_sources for identity in linked):
                        continue
                status, issues = card_impact(raw, previous, proposed, catalog, changes)
                impacts.append(dict(dashboard_id=board.id, dashboard_version=board.version,
                    project_id=board.project_id, project_name=projects.get(board.project_id),
                    card_id=raw['id'], card_title=raw['title'], kind=raw['kind'], status=status, issues=issues))
        # A version saved while the preview query ran invalidates this report.
        latest = db.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.id == dataset_id))
        if latest != body.expected_version:
            raise HTTPException(409, '확인 중 데이터셋에 새 버전이 저장되었습니다. 최신 버전으로 다시 확인하세요.')
        return DatasetImpact.model_validate(dict(dataset_id=dataset_id, current_version=previous.version,
            proposed_version=proposed.version, project_id=previous.project_id, validation_mode='static',
            column_changes=changes, cards=impacts,
            notes=['카드는 저장 후 최신 데이터셋을 사용합니다.',
                   '현재 확인 가능한 설정 오류를 표시합니다. 오류를 수정하면 추가 문제가 드러날 수 있습니다.',
                   '카드 설정의 호환성을 확인했습니다. 결과값·업무 의미와 Python 실행 결과는 별도 확인이 필요합니다.']))
