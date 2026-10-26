from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from ..auth import get_current_user, require_admin
from .. import db as database
from ..models import Audit, utcnow
from .models import BindingHead, BindingVersion, KnowledgeContext, KnowledgeItem, KnowledgeVersion
from .schemas import BindingReview, ContextRequest, CreateItem, Review, SaveBinding, UpdateItem, ValidateRequest
from .service import (binding_for, binding_issues, capture_binding, catalog_snapshot, definition_issues,
                      get_semantic_context, item_json, review_json, scoped_item, visible_items)

router = APIRouter(prefix="/api/semantic", tags=["semantic knowledge"])


def audit(db, user, action, item_id, **details):
    db.add(Audit(user_id=user.id, action="semantic." + action, entity_id=item_id, details=details))


def content_for(content, kind):
    try:
        return content.for_kind(kind)
    except ValidationError as exc:
        raise HTTPException(422, "정의 필드를 확인하세요: " + "; ".join(
            f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()
        )) from None


def require_revision(actual, expected):
    if actual != expected:
        raise HTTPException(409, "다른 사용자가 변경했습니다. 최신 버전을 불러온 후 다시 저장하세요.")


def require_database():
    if not database.source_database_key():
        raise HTTPException(422, "DB를 먼저 연결하세요.")


@router.get("/catalog")
def catalog(user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        snapshot = catalog_snapshot(db)
        return {
            "connection_key": database.source_database_key(), "workspace_token": database.workspace_token(),
            "schema_revision": snapshot["revision"],
            "sources": [{**s, "columns": [{"name": n, "type": t} for n, t in s["columns"].items()]}
                        for s in snapshot["sources"].values() if s["visible"]],
        }


@router.get("/items")
def list_items(user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        snapshot = catalog_snapshot(db)
        values = []
        evaluation_inventory = None
        for item in visible_items(db):
            number = item.head_version if user.role == "admin" else item.published_version
            if item.kind == "evaluation":
                from .evaluations import evaluation_origin
                from .collector_service import inventory, question_visible
                if evaluation_inventory is None:
                    evaluation_inventory = inventory(db)
                origin = evaluation_origin(db, item.id, number)
                if not origin or not question_visible(origin, evaluation_inventory, db=db):
                    continue
            if number:
                version = db.get(KnowledgeVersion, (item.id, number))
                value = item_json(db, item, version, snapshot, published=user.role != "admin", evaluation_inventory=evaluation_inventory)
                if user.role == "admin" or not value["validation"]["binding_issues"]:
                    values.append(value)
        return {"items": values, "connection_key": database.source_database_key(),
                "workspace_token": database.workspace_token()}


@router.post("/items", status_code=201)
def create_item(body: CreateItem, user=Depends(require_admin)):
    if body.kind == "evaluation":
        raise HTTPException(422, "평가 사례 등록에서 원문과 데이터셋을 연결한 뒤 검토하세요.")
    if body.scope == "database":
        require_database()
    with database.SessionLocal.begin() as db:
        item = KnowledgeItem(kind=body.kind, connection_key=database.source_database_key() if body.scope == "database" else None)
        db.add(item)
        db.flush()
        version = KnowledgeVersion(item_id=item.id, version=1, content=body.content.for_kind(body.kind), created_by=user.id)
        db.add(version)
        db.flush()
        audit(db, user, "create", item.id, version=1)
        return item_json(db, item, version, catalog_snapshot(db))


@router.post("/items/{item_id}/versions", status_code=201)
def save_version(item_id: str, body: UpdateItem, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        item = scoped_item(db, item_id, lock=True)
        require_revision(item.revision, body.expected_revision)
        content = content_for(body.content, item.kind)
        if item.kind == "evaluation":
            from .evaluation_results import computed_content_issues
            issues = computed_content_issues(db, item.id, content)
            previous = db.get(KnowledgeVersion, (item.id, item.head_version))
            if content["details"].get("terms", []) != previous.content["details"].get("terms", []):
                issues.append("평가 사례의 용어 연결은 확인 요청의 자연어 답변으로 보완하세요.")
            if issues:
                raise HTTPException(422, " / ".join(issues))
        item.head_version += 1
        item.revision += 1
        version = KnowledgeVersion(item_id=item.id, version=item.head_version, content=content, created_by=user.id)
        db.add(version)
        db.flush()
        # A new definition never silently inherits approval or a physical mapping.
        audit(db, user, "version", item.id, version=version.version)
        return item_json(db, item, version, catalog_snapshot(db))


@router.get("/items/{item_id}/versions")
def history(item_id: str, user=Depends(require_admin)):
    with database.SessionLocal() as db:
        item = scoped_item(db, item_id)
        snapshot = catalog_snapshot(db)
        versions = db.scalars(select(KnowledgeVersion).where(KnowledgeVersion.item_id == item.id)
                              .order_by(KnowledgeVersion.version.desc()))
        return [item_json(db, item, v, snapshot) for v in versions]


@router.post("/items/{item_id}/review")
def review(item_id: str, body: Review, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        item = scoped_item(db, item_id, lock=True)
        require_revision(item.revision, body.expected_revision)
        version = db.get(KnowledgeVersion, (item.id, body.version))
        if not version:
            raise HTTPException(404, "정의 버전을 찾을 수 없습니다.")
        if body.decision == "approve":
            if body.version != item.head_version or version.status != "draft":
                raise HTTPException(409, "최신 초안만 승인할 수 있습니다.")
            issues = definition_issues(db, item, version, catalog_snapshot(db, live=item.kind == "query"))
            if item.kind == "evaluation":
                from .evaluation_results import computed_content_issues
                issues.extend(computed_content_issues(db, item.id, version.content))
            if issues:
                raise HTTPException(422, "승인 전 확인이 필요합니다. " + " / ".join(issues))
            item.published_version = version.version
            version.status = "approved"
        else:
            if version.status == "retired":
                raise HTTPException(409, "이미 폐기된 버전입니다.")
            version.status = "retired"
            if item.published_version == version.version:
                item.published_version = None
        version.reviewed_by, version.reviewed_at, version.review_note = user.id, utcnow(), body.note
        item.revision += 1
        audit(db, user, "review", item.id, version=version.version, decision=body.decision, note=body.note)
        return item_json(db, item, version, catalog_snapshot(db))


@router.post("/items/{item_id}/bindings", status_code=201)
def save_binding(item_id: str, body: SaveBinding, user=Depends(require_admin)):
    require_database()
    with database.SessionLocal.begin() as db:
        # Lock the item even for the first binding; no first-insert race.
        item = scoped_item(db, item_id, lock=True)
        definition = db.get(KnowledgeVersion, (item.id, body.definition_version))
        if not definition or definition.status == "retired":
            raise HTTPException(404, "매핑할 정의 버전을 찾을 수 없습니다.")
        head, _ = binding_for(db, item.id, definition.version)
        require_revision(head.revision if head else 0, body.expected_revision)
        snapshot = catalog_snapshot(db, live=True)
        content = body.content.model_dump()
        if not head:
            head = BindingHead(item_id=item.id, definition_version=definition.version,
                               connection_key=database.source_database_key(), head_version=1, revision=1)
            db.add(head)
        else:
            head.head_version += 1
            head.revision += 1
        binding = BindingVersion(item_id=item.id, definition_version=definition.version,
                                 connection_key=database.source_database_key(), version=head.head_version,
                                 content=content, schema_snapshot=capture_binding(content, snapshot, include_all_columns=item.kind == "query"), created_by=user.id)
        db.add(binding)
        db.flush()
        audit(db, user, "binding.save", item.id, definition_version=definition.version,
              binding_version=binding.version, connection_key=database.source_database_key())
        return item_json(db, item, definition, snapshot)


@router.get("/items/{item_id}/bindings")
def binding_history(item_id: str, definition_version: int, user=Depends(require_admin)):
    with database.SessionLocal() as db:
        item = scoped_item(db, item_id)
        values = db.scalars(select(BindingVersion).where(
            BindingVersion.item_id == item.id, BindingVersion.definition_version == definition_version,
            BindingVersion.connection_key == database.source_database_key()).order_by(BindingVersion.version.desc()))
        return [{"version": v.version, "content": v.content, **review_json(v)} for v in values]


@router.post("/items/{item_id}/bindings/review")
def review_binding(item_id: str, body: BindingReview, user=Depends(require_admin)):
    with database.SessionLocal.begin() as db:
        item = scoped_item(db, item_id, lock=True)
        head, _ = binding_for(db, item.id, body.definition_version)
        if not head:
            raise HTTPException(404, "매핑을 찾을 수 없습니다.")
        require_revision(head.revision, body.expected_revision)
        _, binding = binding_for(db, item.id, body.definition_version, pinned=body.version)
        definition = db.get(KnowledgeVersion, (item.id, body.definition_version))
        if not binding:
            raise HTTPException(404, "매핑 버전을 찾을 수 없습니다.")
        snapshot = catalog_snapshot(db, live=True)
        if body.decision == "approve":
            if body.version != head.head_version or binding.status != "draft":
                raise HTTPException(409, "최신 매핑 초안만 승인할 수 있습니다.")
            if definition.status != "approved":
                raise HTTPException(422, "업무 정의를 먼저 승인하세요.")
            issues = definition_issues(db, item, definition, snapshot) + binding_issues(item, definition, binding, snapshot)
            if issues:
                raise HTTPException(422, "매핑 승인 전 확인이 필요합니다. " + " / ".join(issues))
            binding.status, head.published_version = "approved", binding.version
        else:
            if binding.status == "retired":
                raise HTTPException(409, "이미 폐기된 매핑입니다.")
            binding.status = "retired"
            if head.published_version == binding.version:
                head.published_version = None
        binding.reviewed_by, binding.reviewed_at, binding.review_note = user.id, utcnow(), body.note
        head.revision += 1
        audit(db, user, "binding.review", item.id, definition_version=body.definition_version,
              binding_version=body.version, connection_key=database.source_database_key(), decision=body.decision, note=body.note)
        return item_json(db, item, definition, snapshot)


@router.post("/items/{item_id}/validate")
def validate(item_id: str, body: ValidateRequest, user=Depends(require_admin)):
    with database.SessionLocal() as db:
        item = scoped_item(db, item_id)
        version = db.get(KnowledgeVersion, (item.id, body.version or item.head_version))
        if not version:
            raise HTTPException(404, "정의 버전을 찾을 수 없습니다.")
        return item_json(db, item, version, catalog_snapshot(db, live=body.live_schema))


@router.post("/context")
def context(body: ContextRequest, user=Depends(get_current_user)):
    with database.SessionLocal.begin() as db:
        return get_semantic_context(db, body, user)


@router.get("/contexts/{context_id}")
def read_context(context_id: str, user=Depends(get_current_user)):
    with database.SessionLocal() as db:
        value = db.get(KnowledgeContext, context_id)
        if not value or value.user_id != user.id or value.connection_key != database.source_database_key():
            raise HTTPException(404, "현재 사용자·DB의 컨텍스트를 찾을 수 없습니다.")
        snapshot = catalog_snapshot(db, live=True)
        for entry in value.payload["items"]:
            item = scoped_item(db, entry["id"])
            version = db.get(KnowledgeVersion, (item.id, entry["version"]))
            _, binding = binding_for(db, item.id, version.version,
                                     pinned=entry["binding"]["version"] if entry["binding"] else None, published=True)
            if (version.status != "approved" or (binding and binding.status != "approved")
                    or definition_issues(db, item, version, snapshot) or binding_issues(item, version, binding, snapshot)):
                raise HTTPException(409, "정의·매핑 또는 스키마가 변경되었습니다. 지식을 다시 조회하세요.")
        return value.payload
