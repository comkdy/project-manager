"""Tagged case context in the existing JSON column; legacy snapshots are untouched.

The public API exposes this as case_context with dataset=null. No placeholder
dataset or schema/data migration is needed, and project scope stays DB-local.
"""
from fastapi import HTTPException


def is_case_context(question):
    return ((question.kind == "evaluation" and question.dataset.get("context_kind") == "evaluation_case")
            or (question.kind == 'term' and question.dataset.get('context_kind') == 'evaluation_dataset'))


def project_ids(db):
    from ..catalog import projects_list
    if "evaluation_project_ids" not in db.info:
        db.info["evaluation_project_ids"] = {p["id"] for p in projects_list() if p.get("active", True)}
    return db.info["evaluation_project_ids"]


def make_case_context(db, project_id):
    if project_id is not None and project_id not in project_ids(db):
        raise HTTPException(422, "현재 DB의 적용 프로젝트를 선택하세요.")
    return {"context_kind": "evaluation_case", "project_id": project_id}


def case_context_visible(db, question):
    if question.dataset.get('context_kind') == 'evaluation_dataset':
        from ..evaluation_datasets import question_scope_visible
        return question_scope_visible(db, question)
    owner = question.dataset.get("project_id")
    return owner is None or (db is not None and owner in project_ids(db))


def dataset_changed(question, datasets):
    if is_case_context(question):
        return False
    from ..source_compatibility import dataset_compatible
    current = datasets.get(question.dataset["id"])
    return not dataset_compatible(question.dataset, current)


def public_case_context(question):
    return {"project_id": question.dataset.get("project_id")} if is_case_context(question) else None
