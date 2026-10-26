"""Bounded, reviewed semantic references, never model-generated SQL mappings."""
import re

from .models import KnowledgeItem, KnowledgeVersion
from .service import (binding_for, binding_issues, catalog_snapshot, definition_issues,
                      digest, match_score, normalized, phrase_matches, visible_items)


def approved_definitions(db, *, include_setup=False):
    from ..evaluation_recommendation import scoped_definition_refs
    scoped = set() if include_setup else scoped_definition_refs(db)
    catalog = catalog_snapshot(db)
    result = {}
    for item in visible_items(db):
        if item.kind == "evaluation" or not item.published_version or (item.id, item.published_version) in scoped:
            continue
        version = db.get(KnowledgeVersion, (item.id, item.published_version))
        if not version or version.status != "approved" or definition_issues(db, item, version, catalog):
            continue
        head, binding = binding_for(db, item.id, version.version, published=True)
        if binding and (binding.status != "approved" or binding_issues(item, version, binding, catalog)):
            # Do not expose a hidden/stale physical reference as a candidate.
            continue
        result[item.id] = {"id": item.id, "version": version.version, "kind": item.kind,
            "name": version.content["name"], "aliases": version.content.get("aliases", []),
            "description": version.content["description"],
            "details": version.content.get("details", {}),
            "binding_version": binding.version if binding else None,
            "binding_fingerprint": digest([binding.content, binding.schema_snapshot]) if binding else "",
            "binding": binding.content if binding else None}
    return result


def reference_matches(ref, current):
    return bool(current and ref["version"] == current["version"] and
        ref.get("binding_version") == current["binding_version"] and
        ref.get("binding_fingerprint", "") == current["binding_fingerprint"])


def knowledge_candidates(db, question, answers):
    """Lexical shortlist plus aliases confirmed in approved cases of this scope."""
    from .evaluations import evaluation_origin
    from .collector_service import inventory, question_visible
    known = approved_definitions(db)
    datasets = inventory(db)
    for item in visible_items(db):
        if item.kind != "evaluation" or not item.published_version:
            continue
        version = db.get(KnowledgeVersion, (item.id, item.published_version))
        origin = evaluation_origin(db, item.id, item.published_version)
        if (not version or version.status != "approved" or not origin
                or origin.dataset.get("project_id") not in {None, question.dataset.get("project_id")}
                or not question_visible(origin, datasets, db=db)):
            continue
        for term in version.content.get("details", {}).get("terms", []):
            if term["status"] not in {"linked", "defined"} or len(term["references"]) != 1:
                continue
            ref = term["references"][0]
            target = known.get(ref["item_id"])
            if reference_matches(ref, target):
                target["aliases"] = list(dict.fromkeys([*target["aliases"], term["term"]]))[:80]
    text = normalized(" ".join(a["text"] for a in answers))
    ranked = []
    for identity, value in known.items():
        score, _ = match_score({**value, "domain": ""}, text, set(), None)
        if score:
            ranked.append((score, identity, value))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    selected, size = {}, 0
    for _, identity, value in ranked[:30]:
        length = len(str(value))
        if size + length <= 35000:
            selected[identity] = value
            size += length
    return selected


def resolve_mentions(answers, mentions, candidates):
    by_id = {a["id"]: a["text"] for a in answers}
    full_text = " ".join(by_id.values())
    result, seen = [], set()
    for mention in mentions:
        if (mention.term not in full_text or mention.answer_id not in by_id
                or mention.quote not in by_id[mention.answer_id]
                or (mention.explanation and mention.explanation not in mention.quote)):
            raise ValueError("Ungrounded evaluation term")
        key = normalized(mention.term)
        if key in seen:
            continue
        seen.add(key)
        identities = list(dict.fromkeys(mention.knowledge_ids))
        if any(identity not in candidates for identity in identities):
            raise ValueError("Unapproved evaluation term reference")
        # Preserve ambiguity even if the model picked only one exact alias.
        exact = [identity for identity, value in candidates.items() if any(
            normalized(alias) == key for alias in [value["name"], *value["aliases"]])]
        if not mention.explanation and len(exact) > 1:
            identities = list(dict.fromkeys([*identities, *exact]))[:4]
        refs = [{"item_id": identity, **{k: candidates[identity][k] for k in
                 ("version", "name", "binding_version", "binding_fingerprint")}} for identity in identities]
        if mention.deferred and mention.explanation:
            status, refs = "deferred", []
        elif len(refs) > 1:
            status = "ambiguous"
        elif len(refs) == 1:
            status = "linked" if refs[0]["binding_version"] else "defined"
        else:
            status = "explained" if mention.explanation else "unmapped"
        result.append({"term": mention.term, "status": status, "references": refs,
            "explanation": mention.explanation, "answer_id": mention.answer_id, "quote": mention.quote})
    return result


def term_questions(terms):
    questions = []
    recipes = [t["term"] for t in terms if t["status"] == "unmapped" and re.fullmatch(r"(?:recipe|rcp)[\s_-]*\d+[a-z0-9_.-]*", t["term"], re.I)]
    recipe_added = False
    for term in sorted(terms, key=lambda t: t["status"] != "ambiguous"):
        if term["status"] == "ambiguous":
            candidates = " / ".join(ref["name"] for ref in term["references"])
            questions.append(f"‘{term['term']}’는 어떤 의미인가요? 승인 지식에 여러 후보({candidates})가 있습니다. 측정 의미나 사용하던 항목명으로 설명해 주세요.")
        elif term["status"] == "unmapped":
            if term["term"] in recipes and len(recipes) > 1:
                if not recipe_added:
                    questions.append(f"Recipe 항목({', '.join(recipes)})은 공정 이력에서 어떻게 구분하나요? 사용하던 항목명이나 의미로 설명해 주세요. 모르면 모른다고 답해도 됩니다.")
                    recipe_added = True
                continue
            questions.append(f"‘{term['term']}’는 어떤 공정·측정 항목을 뜻하나요? 기존 AI 지식에서 연결을 찾지 못했습니다. 보고서 이름이나 의미로 설명해 주세요. 모르면 모른다고 답해도 됩니다.")
    return questions[:2]


def terms_issues(db, draft):
    if draft.proposal["kind"] != "evaluation":
        return []
    terms = draft.proposal["content"]["details"].get("terms", [])
    if draft.proposal['content']['details'].get('claims', []) != draft.original_proposal['content']['details'].get('claims', []):
        return ['관계·방향·영향량은 자연어 답변을 통해 다시 정리하세요.']
    if terms != draft.original_proposal["content"]["details"].get("terms", []):
        return ["용어 연결은 자연어 답변을 통해 다시 정리하세요."]
    current = approved_definitions(db) if any(t["references"] for t in terms) else {}
    if any(not reference_matches(ref, current.get(ref["item_id"])) for t in terms for ref in t["references"]):
        return ["참고한 AI 지식이나 데이터 연결이 변경됐습니다. 답변을 보완해 최신 지식으로 다시 정리하세요."]
    return []


def public_terms(db, terms):
    """Revalidate references at read time; historical text remains a report."""
    current = approved_definitions(db) if terms else {}
    result = []
    for term in terms:
        available = all(reference_matches(ref, current.get(ref["item_id"])) for ref in term["references"])
        value = {**term, "available": available}
        if not available:
            value.update(status="needs_review", references=[], explanation="", quote="")
        value["bindings"] = [current[ref["item_id"]]["binding"] for ref in term["references"]
                             if available and current[ref["item_id"]]["binding"]]
        result.append(value)
    return result
