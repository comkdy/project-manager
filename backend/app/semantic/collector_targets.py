"""Question presentation grounded in the saved schema, never inferred business meaning."""
import re

import sqlglot
from sqlglot import exp

from .collector_schemas import QuestionTarget


def outside_quotes(value, transform):
    parts = re.split(r"('[^']*'|\"[^\"]*\"|‘[^’]*’|“[^”]*”)", value)
    return "".join(part if i % 2 else transform(part) for i, part in enumerate(parts))


def column_options(dataset):
    result = [{"column": c["name"], "source_id": "", "source_name": ""} for c in dataset["columns"]]
    for source in dataset["sources"]:
        result.extend({"column": name, "source_id": source["id"], "source_name": source.get("name", source["table_name"])}
                      for name in source["columns"])
    return result


def output_literals(node):
    """Only literal outputs, not thresholds or constants inside arithmetic."""
    if isinstance(node, exp.Literal):
        return {str(node.this)}
    if isinstance(node, exp.Boolean):
        return {str(node.this).lower()}
    if isinstance(node, exp.Null):
        return {"NULL"}
    if isinstance(node, exp.Case):
        values = output_literals(node.args.get("default"))
        for condition in node.args.get("ifs", []):
            values.update(output_literals(condition.args.get("true")))
        return values
    return set()


def defined_values(dataset, target):
    """Values explicitly defined for this output column or a structured filter."""
    values = set()
    spec = dataset["spec"]
    if not target.source_id:
        try:
            if spec.get("raw_sql"):
                tree = sqlglot.parse_one(spec["raw_sql"], read="postgres")
                for node in tree.selects:
                    if node.alias_or_name == target.column:
                        values.update(output_literals(node.this if isinstance(node, exp.Alias) else node))
            for column in spec.get("columns", []):
                if column.get("name") == target.column:
                    values.update(output_literals(sqlglot.parse_one(column["expression"], read="postgres")))
        except (sqlglot.errors.SqlglotError, KeyError, AttributeError):
            pass
    if target.source_id == spec.get("source_id"):
        for f in spec.get("filters", []):
            if f.get("column") == target.column and f.get("op") in {"eq", "ne", "in", "not_in"}:
                entries = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
                values.update("NULL" if v is None else str(v).lower() if isinstance(v, bool) else str(v) for v in entries)
    return values


def infer_targets(dataset, title, subject="", topic=""):
    """Legacy compatibility: only schema identifiers; quoted values are not columns."""
    options = column_options(dataset)
    # Quoted text can be a value. Do not guess its meaning from spelling alone.
    visible = re.sub(r"'[^']*'|\"[^\"]*\"|‘[^’]*’|“[^”]*”", " ", title)
    result, seen = [], set()
    for option in options:
        column = option["column"]
        if column in seen:
            continue
        matches = [o for o in options if o["column"] == column]
        if option["source_id"] and len(matches) > 1:
            continue  # An unqualified name alone cannot choose a joined source.
        if re.search(r"(?<![\w.])" + re.escape(column) + r"(?![A-Za-z0-9_])", visible) or (
            topic == "column_meaning" and subject == column
        ):
            result.append(QuestionTarget(kind="column", column=column, source_id=option["source_id"]))
            seen.add(column)
    # Qualified names can be resolved against the actual source table.
    for source in dataset["sources"]:
        for column in source["columns"]:
            if column in seen:
                continue
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(source["table_name"] + "." + column) + r"(?![A-Za-z0-9_])", visible):
                output = next((o for o in options if o["column"] == column and not o["source_id"]), None)
                result.append(QuestionTarget(kind="column", column=column, source_id="" if output else source["id"]))
                seen.add(column)
    if not result and topic == "grain":
        result.append(QuestionTarget(kind="dataset"))
    return result[:8]


def validated_targets(dataset, targets, *, title="", subject="", topic="", generated=False):
    values = [QuestionTarget.model_validate(v) for v in targets] if targets else infer_targets(dataset, title, subject, topic)
    options = {(o["source_id"], o["column"]) for o in column_options(dataset)}
    for target in values:
        if target.kind != "dataset" and (target.source_id, target.column) not in options:
            raise ValueError("질문 대상 컬럼이 데이터셋에 없습니다.")
        if generated and target.kind == "column_value" and target.value not in defined_values(dataset, target):
            raise ValueError("질문 대상 값의 메타데이터 근거가 없습니다. 값은 추정하지 않습니다.")
    return [v.model_dump() for v in values]


def display_targets(dataset, targets):
    enriched = []
    for target in targets:
        source = next((s for s in dataset.get("sources", []) if s["id"] == target.get("source_id")), None)
        enriched.append({**target, "source_name": source.get("name", source["table_name"]) if source else ""})
    return enriched


def question_presentation(question):
    dataset = question.dataset
    if dataset.get("context_kind") in {"evaluation_case", "evaluation_dataset"}:
        return {"title": question.title, "reason": question.reason, "targets": []}
    targets = question.targets or validated_targets(dataset, [], title=question.title, subject=question.subject, topic=question.topic)
    title, reason = question.title, question.reason
    for source in dataset["sources"]:
        # Match only registered identifiers, including optional schema prefixes.
        for column in sorted(source["columns"], key=len, reverse=True):
            qualified = r"(?<![A-Za-z0-9_])(?:" + re.escape(source["schema_name"]) + r"\.)?" + re.escape(source["table_name"] + "." + column) + r"(?![A-Za-z0-9_])"
            title = outside_quotes(title, lambda text: re.sub(qualified, lambda _: column, text))
            reason = outside_quotes(reason, lambda text: re.sub(qualified, lambda _: column, text))
        title = outside_quotes(title, lambda text: re.sub(r"(?<![A-Za-z0-9_])" + re.escape(source["table_name"]) + r"(?![A-Za-z0-9_])", "이 데이터셋", text))
    return {"title": title, "reason": reason, "targets": display_targets(dataset, targets)}
