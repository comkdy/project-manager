"""Retrieve reviewed evaluation reports with their original dataset scope.

This module neither reads measurement rows nor treats a reported association as
a causal result. No vector service or schema migration is needed.
"""
import re

from fastapi import HTTPException
from sqlalchemy import select

from .. import db as database
from .collector_models import CollectedDraft, KnowledgeQuestion
from .models import KnowledgeItem, KnowledgeVersion
from .evaluation_context import is_case_context, public_case_context, dataset_changed
from .service import catalog_snapshot, definition_issues, digest, normalized, phrase_matches


def evaluation_origin(db, item_id, version):
    if version is None:
        return None
    return db.scalar(select(KnowledgeQuestion).join(CollectedDraft,
        CollectedDraft.question_id == KnowledgeQuestion.id).where(
            CollectedDraft.item_id == item_id, CollectedDraft.item_version <= version,
            CollectedDraft.status == "approved", KnowledgeQuestion.kind == "evaluation",
            KnowledgeQuestion.connection_key == database.source_database_key())
        .order_by(CollectedDraft.item_version.desc(), CollectedDraft.created_at.desc()).limit(1))


def dataset_summary(dataset):
    return {key: dataset[key] for key in ("id", "name", "version", "project_id")} if dataset and "id" in dataset else None


def evaluation_context(db, item_id, version, datasets=None):
    origin = evaluation_origin(db, item_id, version)
    if origin is None:
        return None
    if datasets is None:
        from .collector_service import inventory
        datasets = inventory(db)
    current = datasets.get(origin.dataset.get("id"))
    from .evaluation_results import execution_evidence
    from .evaluation_terms import public_terms
    saved = db.get(KnowledgeVersion, (item_id, version))
    return {"dataset": dataset_summary(origin.dataset), "case_context": public_case_context(origin), "question_id": origin.id,
            "execution_evidence": execution_evidence(db, origin),
            "term_links": public_terms(db, saved.content['details'].get('terms', [])) if saved else [],
            "dataset_changed": dataset_changed(origin, datasets),
            "current_dataset_version": current["version"] if current else None}


_GENERIC = {"inline", "epm", "para", "parameter", "operation", "recipe", "value",
            "평가", "사례", "분석", "관련", "현재", "데이터", "데이터셋", "제안", "추가", "공정",
            "변화", "결과", "확인", "비교", "추천", "어떤", "있는", "대한", "알려줘", "찾아줘"}
_GENERIC.update({"해줘", "찾아", "찾기", "찾아주세요", "보여줘", "보여주세요", "제안해줘", "추천해줘",
                 "알려주세요", "최근", "과거", "기존", "비슷한", "선택", "선택한", "이런", "여기", "있나요",
                 "관련된", "평가사례", "사례들", "데이터들"})


def meaningful_terms(text):
    values = set()
    for token in re.findall(r"[a-z_][a-z0-9_.-]*|[가-힣]{2,}", normalized(text)):
        token = token.rstrip(".")
        stem = re.sub(r"(?:에서|으로|들을|들이|들의|들은|을|를|이|가|은|는|의|도|와|과)$", "", token)
        if len(token) > 1 and token not in _GENERIC and stem not in _GENERIC:
            values.add(stem if len(stem) > 1 else token)
    return values


def retrieve_evaluation_cases(db, query, project_id=None, dataset_id=None, card=None, limit=5):
    from .collector_service import inventory, question_visible

    card = card.model_dump(mode="json") if hasattr(card, "model_dump") else (card or {})
    selected_id = dataset_id or card.get("dataset_id")
    datasets = inventory(db)
    selected = datasets.get(selected_id) if selected_id else None
    if selected_id and (selected is None or selected.get("project_id") not in {None, project_id}):
        raise HTTPException(404, "현재 범위에서 평가 사례를 찾을 데이터셋을 확인할 수 없습니다.")
    if selected and card.get("dataset_id") == selected_id and card.get("dataset_version") not in {None, selected["version"]}:
        raise HTTPException(409, "선택 카드의 데이터셋 버전이 변경되었습니다. 현재 화면을 새로 확인하세요.")
    query_text = str(query)[:6000]
    # Only explicit card fields are retrieval hints. A filter never changes a
    # report's historical scope or establishes that its result applies now.
    card_hints = [str(card.get(key) or "") for key in ("title", "x", "y", "color", "trellis")]
    target_filters, required_groups, excluded_targets = [], [], []
    for value in card.get("filters", []):
        if not isinstance(value, dict):
            continue
        if any(word in normalized(str(value.get("column", ""))) for word in ("parameter", "para", "operation", "recipe")):
            values = value.get("value")
            values = [str(v) for v in values] if isinstance(values, list) else [str(values) if values is not None else ""]
            if value.get("op", "eq") in {"eq", "in", "contains"}:
                group = [normalized(v) for v in values if normalized(v)]
                if group:
                    required_groups.append(group)
                    target_filters.extend(values)
            elif value.get("op") in {"ne", "not_in"}:
                excluded_targets.extend(normalized(v) for v in values if normalized(v))
    card_hints.extend(target_filters)
    terms = meaningful_terms(query_text + " " + " ".join(card_hints))
    normalized_query = normalized(query_text)
    # Only explicit identifier-like names narrow generic dataset fallback;
    # normal Korean questions are not treated as exact parameter selections.
    explicit_query_targets = [value.rstrip(".") for value in re.findall(
        r"(?<![a-z0-9_])(?:para(?:meter)?|recipe)[_-]?[a-z]*\d+[a-z0-9_.-]*(?![a-z0-9_])", normalized_query)]
    selected_sources = {source["id"] for source in selected["sources"]} if selected else set()
    snapshot = catalog_snapshot(db)
    ranked = []
    rows = db.scalars(select(KnowledgeItem).where(KnowledgeItem.kind == "evaluation",
        KnowledgeItem.connection_key == database.source_database_key(), KnowledgeItem.published_version.is_not(None)))
    for item in rows:
        version = db.get(KnowledgeVersion, (item.id, item.published_version))
        if version is None or version.status != "approved" or definition_issues(db, item, version, snapshot):
            continue
        origin = evaluation_origin(db, item.id, version.version)
        if origin is None or origin.dataset.get("project_id") not in {None, project_id} or not question_visible(origin, datasets, db=db):
            continue
        current = datasets.get(origin.dataset.get("id"))
        if dataset_changed(origin, datasets):
            continue
        content = version.content
        from .evaluation_results import computed_content_issues, execution_evidence
        if computed_content_issues(db, item.id, content):
            continue
        execution = execution_evidence(db, origin)
        details = content["details"]
        document = normalized(" ".join([content["name"], content["description"], *content.get("aliases", []),
                                         *(str(value) for key, value in details.items() if key != "terms")]))
        matched = sorted(term for term in terms if phrase_matches(document, term))
        operation_match = bool(details.get("operation") and phrase_matches(normalized_query, normalized(details["operation"])))
        exact = bool(selected and current and selected["id"] == current["id"])
        shared = bool(current and selected_sources.intersection(source["id"] for source in current["sources"]))
        from .evaluation_terms import public_terms
        term_links = public_terms(db, details.get("terms", []))
        linked_sources = {ref["source_id"] for term in term_links if term["status"] == "linked" for binding in term["bindings"] for ref in binding["sources"]}
        semantic_match = bool(selected_sources.intersection(linked_sources))
        # A specific PARA/Recipe filter must not silently inherit unrelated
        # reports merely because they were attached to the same broad dataset.
        if any(not any(phrase_matches(document, value) for value in group) for group in required_groups):
            continue
        if any(phrase_matches(document, value) for value in excluded_targets):
            continue
        if explicit_query_targets and not any(phrase_matches(document, value) for value in explicit_query_targets):
            continue
        if not exact and not matched and not operation_match and not ((shared or semantic_match) and not meaningful_terms(query_text)):
            continue
        reasons, score = [], 0
        if exact:
            reasons.append("현재 선택한 데이터셋에 등록된 평가 사례")
            score += 30
        elif shared:
            reasons.append("현재 데이터셋과 원천 데이터를 공유함")
            score += 8
        if semantic_match:
            reasons.append("승인된 용어 지식을 통해 현재 원천 데이터와 연결됨")
            score += 8
        if matched:
            reasons.append("질문·선택 카드와 일치: " + ", ".join(matched[:6]))
            score += min(len(matched), 6) * 4
        if operation_match:
            reasons.append("평가 공정 일치: " + details["operation"])
            score += 5
        limitations = (["표시된 조회 시점과 데이터 범위에서 계산하고 검토한 결과입니다. 이후 데이터에서도 재현되거나 인과관계가 확인됐다는 뜻은 아닙니다."]
                      if execution else ["담당자가 등록하고 검토한 관계 진술입니다. 현재 데이터의 계산 결과나 검증된 인과 효과를 의미하지 않습니다."] if details.get('claims')
                      else ["담당자가 등록하고 검토한 과거 관측이며, 현재 데이터에서 재계산하거나 인과관계를 확인한 결과가 아닙니다."])
        if not details.get("conditions"):
            limitations.append("제품·장비·기간 등 적용 조건이 기록되지 않았습니다. 비교 전에 확인하세요.")
        if not details.get("evidence"):
            limitations.append("별도 평가 자료가 연결되지 않아 등록 원문을 근거로 사용합니다.")
        if not exact and shared and not matched and not operation_match:
            limitations.append("원천 데이터가 같아 제시한 사례입니다. 현재 PARA·필터와 평가 조건이 일치하는지는 확인되지 않았습니다.")
        if details.get("limitations"):
            limitations.append(details["limitations"])
        if any(t["status"] != "linked" for t in term_links):
            limitations.append("일부 용어의 현재 데이터 연결은 미확인입니다. 분석 전에 실제 측정 항목과 조건을 확인하세요.")
        case = {"id": item.id, "version": version.version, "name": content["name"],
                "description": content["description"], "details": details,
                "dataset": dataset_summary(current), "case_context": public_case_context(origin), "term_links": term_links, "question_id": origin.id,
                "match_reasons": reasons, "limitations": limitations}
        if execution:
            case["execution_evidence"] = execution
        ranked.append((score, item.id, case))
    ranked.sort(key=lambda value: (-value[0], value[1]))
    maximum = max(1, min(int(limit), 8))
    warnings = []
    if selected is None:
        warnings.append("선택한 데이터셋이 없어 질문에 명시된 내용과 현재 프로젝트 범위로 찾았습니다.")
    if card.get("filters"):
        warnings.append("선택 카드의 조건은 검색에만 사용했습니다. 과거 평가와 현재 조건의 일치 여부는 추가 확인이 필요합니다.")
    if len(ranked) > maximum:
        warnings.append("관련성이 높은 일부 사례를 표시했습니다. 공정·PARA를 지정하면 범위를 좁힐 수 있습니다.")
    return {"cases": [value[2] for value in ranked[:maximum]], "warnings": warnings,
            "context": {"project_id": project_id, "dataset_id": selected_id}}
