"""Knowledge validation and bounded lexical retrieval; no authored SQL execution."""
import hashlib
import json
import re
import unicodedata

from fastapi import HTTPException
from sqlalchemy import select

from .. import db as database
from ..direct_sql import parse_query, referenced_sources
from ..models import DatasetVersion, Source
from ..table_visibility import TableVisibility, source_visible
from .models import BindingHead, BindingVersion, KnowledgeContext, KnowledgeItem, KnowledgeVersion
from .schemas import ContextRequest


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def normalized(value):
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def catalog_snapshot(db, *, live=False):
    sources = list(db.scalars(select(Source).where(Source.connection_key == database.source_database_key())))
    physical = None
    if live:
        from ..source_discovery import database_tables
        physical = {(t["schema_name"], t["table_name"]): t for t in database_tables()}
    result = {}
    for source in sources:
        found = physical.get((source.schema_name, source.table_name)) if physical is not None else None
        columns = found["columns"] if found else source.columns
        result[source.id] = {
            "id": source.id, "schema_name": source.schema_name, "table_name": source.table_name,
            "columns": {c["name"]: c["type"] for c in columns},
            "visible": source_visible(source) and (physical is None or found is not None),
        }
    return {"sources": result, "revision": digest(result), "basis": "live_schema" if live else "discovered_catalog"}


def scoped_item(db, item_id, *, lock=False):
    statement = select(KnowledgeItem).where(KnowledgeItem.id == item_id)
    if lock:
        statement = statement.with_for_update()
    item = db.scalar(statement)
    if not item or item.connection_key not in {None, database.source_database_key()}:
        raise HTTPException(404, "현재 DB 범위에서 지식 항목을 찾을 수 없습니다.")
    return item


def definition_issues(db, item, version, catalog):
    content, issues = version.content, []
    if not content["description"].strip():
        issues.append("업무 설명이 미정입니다.")
    issues.extend(f"미정: {value}" for value in content["unresolved"])
    details = content["details"]
    required = {}
    if item.kind == "metric":
        required = {
            "metric_type": "지표 유형", "formula": "계산식", "grain": "계산 대상 단위",
            "unit": "출력 단위", "time_basis": "시간 기준", "time_window": "기간 규칙",
            "timezone": "시간대", "exclusions": "제외 조건", "retest_policy": "재검사 처리",
            "deduplication": "중복 처리", "aggregation": "재집계 규칙",
        }
        if details.get("metric_type") == "ratio":
            required.update(numerator="분자", denominator="분모", zero_policy="분모 0 처리")
    elif item.kind == "data":
        required = {"grain": "테이블 한 행의 의미"}
    elif item.kind == "relation":
        required = {"cardinality": "카디널리티", "evidence": "관계 확인 근거",
                    "duplication_notes": "중복 집계 주의사항", "join_notes": "연결 조건 설명"}
    elif item.kind == "query":
        required = {"question": "검토한 질문", "sql": "SQL", "assumptions": "전제 조건",
                    "expected_result": "기대 결과·업무 검토 근거"}
        if details.get("sql"):
            try:
                referenced_sources(parse_query(details["sql"]), [
                    s for s in catalog["sources"].values() if s["visible"]
                ])
            except HTTPException as exc:
                issues.append(f"SQL 정적 검증: {exc.detail}")
    elif item.kind == "evaluation":
        from .evaluation_claims import claims_issues
        issues.extend(claims_issues(db, details))
        required = {} if details.get('claims') else {"operation": "평가한 공정"}
        if not details.get('claims') and not any(details.get(key, "").strip() for key in ("inline_observations", "epm_observations", "analysis_result")):
            issues.append("Inline 또는 EPM에서 관측한 결과가 필요합니다.")
    for field, label in required.items():
        if not details.get(field):
            issues.append(f"{label}이(가) 미정입니다. 해당 없으면 그 이유를 명시하세요.")
    for ref in content["references"]:
        target = db.get(KnowledgeItem, ref["item_id"])
        target_version = db.get(KnowledgeVersion, (ref["item_id"], ref["version"]))
        if ref["item_id"] == item.id:
            issues.append("자기 자신을 참조할 수 없습니다.")
        elif (not target or target.connection_key not in {None, database.source_database_key()}
              or not target_version or target_version.status != "approved"):
            issues.append("참조한 정의가 없거나 현재 DB에서 승인된 버전이 아닙니다.")
        elif item.connection_key is None and target.connection_key is not None:
            issues.append("공통 정의는 DB 전용 정의에 의존할 수 없습니다.")
        elif item.kind != "evaluation" and target.kind == "evaluation":
            issues.append("평가 사례는 업무 정의의 필수 참조 대신 사례 분석에서 활용하세요.")
    return list(dict.fromkeys(issues))


def capture_binding(content, catalog, *, include_all_columns=False):
    """Capture physical identity and exact column types at authoring time."""
    snapshot = {}
    for ref in content["sources"]:
        source = catalog["sources"].get(ref["source_id"])
        if not source or not source["visible"]:
            continue
        names = set(source["columns"]) if include_all_columns else set(ref["columns"]) or set(source["columns"])
        names.update(p["column"] for p in content["predicates"] if p["source_id"] == ref["source_id"])
        previous = snapshot.get(source["id"], {}).get("columns", {})
        snapshot[source["id"]] = {
            "schema_name": source["schema_name"], "table_name": source["table_name"],
            "columns": {**previous, **{name: source["columns"].get(name) for name in sorted(names)}},
        }
    return snapshot


def binding_issues(item, definition, binding, catalog):
    if binding is None:
        return [] if item.kind in {"term", "evaluation"} else ["현재 DB의 승인된 물리 매핑이 필요합니다."]
    content, issues = binding.content, []
    sources = catalog["sources"]
    if not content["sources"]:
        issues.append("매핑할 원천 테이블을 지정하세요.")
    bound_ids = {ref["source_id"] for ref in content["sources"]}
    for ref in content["sources"]:
        source = sources.get(ref["source_id"])
        if not source or not source["visible"]:
            issues.append("참조 테이블이 제외되었거나 존재·조회 권한을 확인할 수 없습니다.")
            continue
        old = binding.schema_snapshot.get(ref["source_id"])
        if not old or (source["schema_name"], source["table_name"]) != (old["schema_name"], old["table_name"]):
            issues.append("테이블 매핑을 다시 저장하고 검토해야 합니다.")
            continue
        for column, expected_type in old["columns"].items():
            if column not in source["columns"]:
                issues.append(f"매핑 컬럼이 없습니다: {column}")
            elif expected_type != source["columns"][column]:
                issues.append(f"컬럼 타입이 변경되었거나 확인되지 않았습니다: {column}")
        for column in ref["columns"]:
            if column not in source["columns"]:
                issues.append(f"선택한 컬럼이 없습니다: {column}")
    for predicate in content["predicates"]:
        source = sources.get(predicate["source_id"])
        if predicate["source_id"] not in bound_ids or not source or not source["visible"]:
            issues.append("코드·조건의 테이블을 매핑 목록에 포함하세요.")
            continue
        column_type = source["columns"].get(predicate["column"])
        if column_type is None:
            issues.append(f"조건 컬럼이 없습니다: {predicate['column']}")
            continue
        values = predicate["value"] if isinstance(predicate["value"], list) else [predicate["value"]]
        for value in values:
            if value is None:
                continue
            if re.search(r"int|numeric|decimal|real|double", column_type) and (isinstance(value, bool) or not isinstance(value, (int, float))):
                issues.append(f"숫자형 조건값이 필요합니다: {predicate['column']}")
            elif column_type == "boolean" and not isinstance(value, bool):
                issues.append(f"참/거짓 조건값이 필요합니다: {predicate['column']}")
    if item.kind == "relation":
        left = [r for r in content["sources"] if r["role"] == "left"]
        right = [r for r in content["sources"] if r["role"] == "right"]
        if (len(left) != 1 or len(right) != 1 or not left[0]["columns"]
                or len(left[0]["columns"]) != len(right[0]["columns"])):
            issues.append("관계는 left / right 역할과 같은 개수의 연결 컬럼을 순서대로 지정하세요.")
        else:
            left_source, right_source = sources.get(left[0]["source_id"]), sources.get(right[0]["source_id"])
            if left_source and right_source:
                for lcol, rcol in zip(left[0]["columns"], right[0]["columns"]):
                    lt, rt = left_source["columns"].get(lcol), right_source["columns"].get(rcol)
                    numeric = lambda value: bool(value and re.match(r"(smallint|integer|bigint|numeric|decimal|real|double precision)", value))
                    if lt and rt and lt != rt and not (numeric(lt) and numeric(rt)):
                        issues.append(f"연결 컬럼 타입을 확인하세요: {lcol} / {rcol}")
    if item.kind == "query" and definition.content["details"].get("sql"):
        try:
            used = referenced_sources(parse_query(definition.content["details"]["sql"]),
                                      [s for s in sources.values() if s["visible"]])
            if not {s["id"] for s in used}.issubset(bound_ids):
                issues.append("SQL에서 사용하는 모든 테이블을 매핑에 포함하세요.")
        except HTTPException as exc:
            issues.append(str(exc.detail))
    return list(dict.fromkeys(issues))


def binding_for(db, item_id, definition_version, *, published=False, pinned=None):
    key = database.source_database_key()
    head = db.get(BindingHead, (item_id, definition_version, key))
    number = pinned if pinned is not None else (head.published_version if published else head.head_version) if head else None
    version = db.get(BindingVersion, (item_id, definition_version, key, number)) if number else None
    return head, version


def review_json(value):
    return {"status": value.status, "created_by": value.created_by, "created_at": value.created_at.isoformat(),
            "reviewed_by": value.reviewed_by,
            "reviewed_at": value.reviewed_at.isoformat() if value.reviewed_at else None, "review_note": value.review_note}


def item_json(db, item, version, catalog, *, published=False, evaluation_inventory=None):
    head, binding = binding_for(db, item.id, version.version, published=published)
    definition_problems = definition_issues(db, item, version, catalog)
    mapping_problems = binding_issues(item, version, binding, catalog)
    if published and head and not binding:
        mapping_problems.append("현재 DB 매핑에 승인된 버전이 없습니다.")
    value = {
        "id": item.id, "kind": item.kind, "scope": "database" if item.connection_key else "common",
        "connection_key": item.connection_key, "revision": item.revision, "version": version.version,
        "head_version": item.head_version, "published_version": item.published_version,
        "content": version.content, **review_json(version),
        "binding": None if not binding else {
            "version": binding.version, "revision": head.revision, "published_version": head.published_version,
            "content": binding.content, **review_json(binding),
        },
        "validation": {"definition_issues": definition_problems, "binding_issues": mapping_problems,
                       "schema_revision": catalog["revision"], "basis": catalog["basis"],
                       "sql_execution": "not_run", "business_correctness": "human_review_required"},
    }
    if item.kind == "evaluation":
        from .evaluations import evaluation_context
        value["evaluation_context"] = evaluation_context(db, item.id, version.version, evaluation_inventory)
    return value


def visible_items(db):
    return list(db.scalars(select(KnowledgeItem).where(
        (KnowledgeItem.connection_key.is_(None)) | (KnowledgeItem.connection_key == database.source_database_key())
    ).order_by(KnowledgeItem.created_at.desc(), KnowledgeItem.id)))


def phrase_matches(query, phrase):
    if not phrase:
        return False
    # Latin abbreviations must not match inside unrelated words.
    if re.fullmatch(r"[a-z0-9 _-]+", phrase):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", query))
    return phrase in query


def match_score(content, query, hints, binding):
    reasons, score = [], 0
    for alias in [content["name"], *content["aliases"]]:
        if phrase_matches(query, normalized(alias)):
            score += 20
            reasons.append(f"이름·별칭 일치: {alias}")
    words = {w for w in re.findall(r"[\w]+", query) if len(w) >= 2}
    text = normalized(content["description"] + " " + content["domain"])
    matched = sorted(w for w in words if w in text)
    if matched:
        score += min(len(matched), 5)
        reasons.append("설명 키워드: " + ", ".join(matched))
    if binding and hints.intersection(r["source_id"] for r in binding.content["sources"]):
        # A schema hint ranks relevant matches but never makes unrelated knowledge relevant.
        if score:
            score += 5
            reasons.append("선택 스키마와 연결됨")
    return score, reasons


def resolve_hints(db, body, catalog):
    hints = set(body.schema_hint.source_ids)
    if any(s not in catalog["sources"] or not catalog["sources"][s]["visible"] for s in hints):
        raise HTTPException(422, "현재 DB에서 사용할 수 없는 스키마 힌트입니다.")
    if body.schema_hint.dataset_id:
        statement = select(DatasetVersion).where(DatasetVersion.id == body.schema_hint.dataset_id)
        if body.schema_hint.dataset_version:
            statement = statement.where(DatasetVersion.version == body.schema_hint.dataset_version)
        dataset = db.scalar(statement.order_by(DatasetVersion.version.desc()).limit(1))
        if (not dataset or dataset.project_id not in {None, body.project_id}
                or not TableVisibility.load(db).allows_spec(dataset.spec)
                or dataset.spec["source_id"] not in catalog["sources"]
                or not catalog["sources"][dataset.spec["source_id"]]["visible"]):
            raise HTTPException(404, "현재 범위에서 사용할 수 없는 데이터셋 힌트입니다.")
        hints.add(dataset.spec["source_id"])
    return hints


def get_semantic_context(db, body: ContextRequest, user, *, catalog=None):
    """Common read contract for future Skill adapters. Persists evidence, not SQL."""
    if (body.connection_key != database.source_database_key()
            or body.workspace_token != database.workspace_token()):
        raise HTTPException(409, "DB 범위가 변경되었습니다. 현재 DB에서 다시 조회하세요.")
    if body.project_id is not None:
        from ..catalog import projects_list
        if body.project_id not in {p["id"] for p in projects_list()}:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    catalog = catalog if catalog is not None else catalog_snapshot(db, live=True)
    hints = resolve_hints(db, body, catalog)
    selected = {(r.item_id, r.version) for r in body.selected_definitions}
    pins, conditions = {}, {}
    if body.previous_context_id:
        prior = db.get(KnowledgeContext, body.previous_context_id)
        if (not prior or prior.user_id != user.id or prior.connection_key != body.connection_key
                or prior.project_id != body.project_id):
            raise HTTPException(404, "현재 사용자·DB·프로젝트의 이전 컨텍스트가 아닙니다.")
        # An unresolved choice is not a confirmed definition.
        if prior.payload["resolution"] == "resolved":
            pins = {(e["id"], e["version"]): e["binding"]["version"] if e["binding"] else None
                    for e in prior.payload["items"]}
        conditions = dict(prior.payload["confirmed_conditions"])
    for ref in selected:
        pins = {key: value for key, value in pins.items() if key[0] != ref[0]}
    conditions.update({k: {"value": v, "source": "user_supplied"} for k, v in body.confirmed_conditions.items()})
    candidates, excluded, warnings = {}, [], []
    from ..evaluation_recommendation import scoped_definition_refs
    setup_definitions = scoped_definition_refs(db)
    revisions = []
    query = normalized(body.query)
    for item in visible_items(db):
        # Reports use dataset-scoped case retrieval, not definition resolution.
        if item.kind == "evaluation":
            continue
        numbers = {key[1] for key in pins.keys() | selected if key[0] == item.id}
        if not numbers and item.published_version:
            numbers.add(item.published_version)
        revisions.append((item.id, item.revision))
        for number in numbers:
            if (item.id, number) in setup_definitions:
                continue
            version = db.get(KnowledgeVersion, (item.id, number))
            key = (item.id, number)
            forced = key in pins or key in selected
            if not version or version.status != "approved":
                if forced:
                    excluded.append({"reason": "앞서 선택한 정의가 철회되었거나 승인 상태가 아닙니다."})
                continue
            if body.domain and normalized(version.content["domain"]) != normalized(body.domain) and not forced:
                continue
            head, binding = binding_for(db, item.id, number, published=True, pinned=pins.get(key))
            revisions.append((item.id, number, head.revision if head else 0))
            score, reasons = match_score(version.content, query, hints, binding)
            if forced:
                score += 100
                reasons.insert(0, "명시적으로 선택했거나 이전 요청에서 확정한 정의")
            if not score:
                continue
            issues = definition_issues(db, item, version, catalog) + binding_issues(item, version, binding, catalog)
            if head and not binding:
                issues.append("현재 DB 매핑에 승인된 버전이 없습니다.")
            if key in pins and pins[key] is None and binding is not None:
                issues.append("이전 요청 이후 매핑이 추가되었습니다. 새 컨텍스트로 다시 확인하세요.")
            if binding and binding.status != "approved":
                issues.append("매핑이 승인 상태가 아닙니다.")
            if issues:
                # Do not leak hidden table names or obsolete SQL into LLM context.
                excluded.append({"reason": "관련 지식의 정의·매핑을 재검토해야 합니다."})
                continue
            if forced and item.published_version != number:
                warnings.append("이전 요청에서 확정한 정의 버전을 유지했습니다. 새 승인 버전이 있을 수 있습니다.")
            candidates[key] = (score, reasons, item, version, binding)
    missing_selected = selected - set(candidates)
    if missing_selected:
        raise HTTPException(422, "선택한 정의가 없거나 현재 DB에서 사용할 수 없습니다.")
    # Detect collisions before applying the result limit. max_items=1 must not
    # turn two meanings of an alias into a seemingly unambiguous result.
    clarifications, alias_groups, blocked_keys = [], {}, set()
    for key, (_, _, item, version, _) in candidates.items():
        for alias in [version.content["name"], *version.content["aliases"]]:
            alias = normalized(alias)
            if phrase_matches(query, alias):
                alias_groups.setdefault((item.kind, alias), set()).add(key)
    for (kind, alias), keys in sorted(alias_groups.items()):
        preferred = (keys & selected) or (keys & pins.keys())
        if len(preferred) == 1:
            blocked_keys.update(keys - preferred)
        elif len(keys) > 1:
            clarifications.append({
                "id": digest([kind, alias])[:16], "reason": f"‘{alias}’에 여러 업무 정의가 있습니다.",
                "blocking": True, "options": [{"item_id": k[0], "version": k[1],
                    "name": candidates[k][3].content["name"], "domain": candidates[k][3].content["domain"]} for k in sorted(keys)],
            })
    ranked = sorted((k for k in candidates if k not in blocked_keys), key=lambda k: (-candidates[k][0], k))
    forced_keys = [key for key in ranked if key in selected or key in pins]
    chosen = list(dict.fromkeys(forced_keys + ranked[:body.max_items]))
    if len(chosen) > 30:
        raise HTTPException(422, "확정한 정의가 너무 많습니다. 새 요청에서 범위를 좁혀 주세요.")
    # Dependencies are added as indivisible context, never silently truncated.
    pending = list(chosen)
    while pending:
        key = pending.pop()
        for ref in candidates[key][3].content["references"]:
            dep = (ref["item_id"], ref["version"])
            if dep in chosen:
                continue
            if dep in blocked_keys or dep in setup_definitions:
                excluded.append({"reason": "선택한 정의의 의존 지식이 명시적으로 선택한 의미와 충돌합니다."})
                continue
            item = scoped_item(db, dep[0])
            version = db.get(KnowledgeVersion, dep)
            head, binding = binding_for(db, *dep, published=True)
            issues = definition_issues(db, item, version, catalog) + binding_issues(item, version, binding, catalog)
            if version.status != "approved" or issues or (head and not binding) or (binding and binding.status != "approved"):
                excluded.append({"reason": "관련 정의의 의존 지식을 사용할 수 없습니다."})
                continue
            candidates[dep] = (0, ["선택한 정의의 필수 참조"], item, version, binding)
            chosen.append(dep)
            pending.append(dep)
            revisions.append((item.id, item.revision, head.revision if head else 0))
            if len(chosen) > 30:
                raise HTTPException(422, "관련 지식이 너무 많습니다. 업무 영역이나 질문 범위를 좁혀 주세요.")
    entries = []
    for key in chosen:
        _, reasons, item, version, binding = candidates[key]
        entries.append({
            "id": item.id, "kind": item.kind, "version": version.version,
            "content": version.content, "status": version.status, "reviewed_by": version.reviewed_by,
            "binding": {"version": binding.version, "content": binding.content} if binding else None,
            "match_reasons": reasons,
        })
    # Relevant but unusable evidence is different from a query with no match.
    blocking_gaps = bool(excluded)
    if not entries:
        excluded.append({"reason": "질문에 사용할 수 있는 승인된 지식이 없습니다. 관리자가 정의를 등록·검토해야 합니다."})
    if len(ranked) > body.max_items:
        warnings.append("검색 후보 일부가 입력 한도를 넘었습니다. 필요한 업무 영역을 지정하세요.")
    payload = {
        "contract_version": 1, "query": body.query, "connection_key": body.connection_key,
        "workspace_token": body.workspace_token, "project_id": body.project_id,
        "schema_revision": catalog["revision"], "knowledge_revision": digest(sorted(revisions, key=str)),
        "resolution": "needs_clarification" if clarifications else "missing_knowledge" if excluded else "resolved",
        "items": entries, "clarifications": clarifications, "gaps": excluded, "blocking_gaps": blocking_gaps,
        "warnings": list(dict.fromkeys(warnings)), "confirmed_conditions": conditions,
        "output_constraints": [{"item_id": e["id"], "version": e["version"],
            **{field: e["content"]["details"].get(field) for field in ("grain", "unit", "aggregation")}}
            for e in entries if e["kind"] == "metric"],
        "limitations": ["지식 조회 결과이며 SQL 실행 성공이나 업무 정답을 보증하지 않습니다.",
                        "관계는 참고 지식이며 SQL에 JOIN이나 조건을 자동 삽입하지 않습니다."],
    }
    if len(json.dumps(payload, ensure_ascii=False)) > 70000:
        raise HTTPException(422, "필수 정의를 포함한 지식이 너무 큽니다. 질문 범위를 좁혀 주세요.")
    record = KnowledgeContext(user_id=user.id, connection_key=body.connection_key,
                              project_id=body.project_id, payload=payload)
    db.add(record)
    db.flush()
    payload = {**payload, "context_id": record.id}
    record.payload = payload
    return payload
