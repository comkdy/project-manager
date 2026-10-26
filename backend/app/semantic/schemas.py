from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Kind = Literal["term", "metric", "data", "relation", "query", "evaluation"]


class DefinitionRef(Strict):
    item_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)


class MetricDetails(Strict):
    metric_type: Literal["ratio", "count", "sum", "other"] | None = None
    formula: str = Field(default="", max_length=4000)
    numerator: str = Field(default="", max_length=2000)
    denominator: str = Field(default="", max_length=2000)
    grain: str = Field(default="", max_length=1000)
    unit: str = Field(default="", max_length=200)
    time_basis: str = Field(default="", max_length=1000)
    time_window: str = Field(default="", max_length=1000)
    timezone: str = Field(default="", max_length=200)
    exclusions: str = Field(default="", max_length=2000)
    retest_policy: str = Field(default="", max_length=2000)
    deduplication: str = Field(default="", max_length=2000)
    zero_policy: str = Field(default="", max_length=1000)
    aggregation: str = Field(default="", max_length=2000)


class DataDetails(Strict):
    grain: str = Field(default="", max_length=2000)
    column_notes: str = Field(default="", max_length=4000)


class RelationDetails(Strict):
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"] | None = None
    evidence: str = Field(default="", max_length=2000)
    duplication_notes: str = Field(default="", max_length=2000)
    join_notes: str = Field(default="", max_length=2000)


class QueryDetails(Strict):
    question: str = Field(default="", max_length=2000)
    sql: str = Field(default="", max_length=20000)
    assumptions: str = Field(default="", max_length=4000)
    expected_result: str = Field(default="", max_length=4000)


class EvaluationTermReference(Strict):
    item_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    binding_version: int | None = Field(default=None, ge=1)
    binding_fingerprint: str = Field(default="", max_length=64)


class EvaluationTerm(Strict):
    term: str = Field(min_length=1, max_length=200)
    status: Literal["linked", "defined", "ambiguous", "unmapped", "explained", "deferred"]
    explanation: str = Field(default="", max_length=1000)
    answer_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=1000)
    references: list[EvaluationTermReference] = Field(default_factory=list, max_length=4)


class ReportedValue(Strict):
    value: str = Field(min_length=1, max_length=300)
    answer_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=1000)


class EvaluationClaim(Strict):
    """A person's grounded statement, never an inferred causal estimate."""
    source_term: str = Field(min_length=1, max_length=200)
    target_term: str = Field(min_length=1, max_length=200)
    relationship: Literal['reported_influence', 'co_movement', 'hypothesis']
    answer_id: str = Field(min_length=1, max_length=36)
    quote: str = Field(min_length=1, max_length=1000)
    direction: ReportedValue | None = None
    magnitude: ReportedValue | None = None

    @model_validator(mode='after')
    def grounded(self):
        if self.source_term not in self.quote or self.target_term not in self.quote:
            raise ValueError('관계의 두 항목은 같은 원문 구절에서 확인해야 합니다.')
        for fact in (self.direction, self.magnitude):
            if fact and fact.value not in fact.quote:
                raise ValueError('방향·영향량은 사용자가 명시한 구절이어야 합니다.')
        return self


class EvaluationDetails(Strict):
    """Reported observations; approval does not establish a causal effect."""
    operation: str = Field(default="", max_length=1000)
    recipe_changes: str = Field(default="", max_length=2000)
    inline_observations: str = Field(default="", max_length=2000)
    epm_observations: str = Field(default="", max_length=2000)
    conditions: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=2000)
    limitations: str = Field(default="", max_length=2000)
    analysis_result: str = Field(default="", max_length=6000)
    terms: list[EvaluationTerm] = Field(default_factory=list, max_length=12)
    claims: list[EvaluationClaim] = Field(default_factory=list, max_length=12)


DETAIL_MODELS = {"term": Strict, "metric": MetricDetails, "data": DataDetails,
                 "relation": RelationDetails, "query": QueryDetails, "evaluation": EvaluationDetails}


class Content(Strict):
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=6000)
    aliases: list[str] = Field(default_factory=list, max_length=80)
    unresolved: list[str] = Field(default_factory=list, max_length=40)
    references: list[DefinitionRef] = Field(default_factory=list, max_length=30)
    details: dict = Field(default_factory=dict)
    change_note: str = Field(default="", max_length=2000)

    @field_validator("aliases", "unresolved")
    @classmethod
    def clean_lines(cls, values):
        if any(not v.strip() or len(v) > 300 for v in values):
            raise ValueError("항목은 1~300자여야 합니다.")
        return list(dict.fromkeys(v.strip() for v in values))

    def for_kind(self, kind):
        value = self.model_dump()
        value["details"] = DETAIL_MODELS[kind].model_validate(self.details).model_dump()
        # Keep historical computed evidence byte-for-byte comparable. This is
        # an additive JSON field, not a migration or a reinterpretation of old runs.
        if kind == 'evaluation' and not value['details']['claims']:
            value['details'].pop('claims')
        return value


class CreateItem(Strict):
    kind: Kind
    scope: Literal["common", "database"] = "common"
    content: Content

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind in {"data", "relation", "query", "evaluation"} and self.scope != "database":
            raise ValueError("데이터 설명·관계·검증된 질의는 현재 DB 범위에 저장하세요.")
        self.content.for_kind(self.kind)
        return self


class UpdateItem(Strict):
    expected_revision: int = Field(ge=1)
    content: Content


class Review(Strict):
    expected_revision: int = Field(ge=1)
    version: int = Field(ge=1)
    decision: Literal["approve", "retire"]
    note: str = Field(min_length=1, max_length=2000)


class SourceRef(Strict):
    source_id: str = Field(min_length=1, max_length=80)
    role: str = Field(default="", max_length=100)
    columns: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("columns")
    @classmethod
    def valid_columns(cls, values):
        if any(not v or len(v) > 200 for v in values) or len(set(values)) != len(values):
            raise ValueError("컬럼 이름은 비어 있거나 중복될 수 없습니다.")
        return values


Scalar = str | StrictBool | StrictInt | StrictFloat | None


class Predicate(Strict):
    source_id: str = Field(min_length=1, max_length=80)
    column: str = Field(min_length=1, max_length=200)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "is_not_null"]
    value: Scalar | list[Scalar] = None

    @model_validator(mode="after")
    def valid_value(self):
        if self.op == "in" and (not isinstance(self.value, list) or not self.value or len(self.value) > 100):
            raise ValueError("IN 조건에는 1~100개의 값이 필요합니다.")
        if self.op != "in" and isinstance(self.value, list):
            raise ValueError("목록 값은 IN 조건에서만 사용하세요.")
        if self.op in {"is_null", "is_not_null"} and self.value is not None:
            raise ValueError("NULL 조건에는 값을 입력하지 마세요.")
        if self.op not in {"in", "is_null", "is_not_null"} and self.value is None:
            raise ValueError("NULL 비교에는 IS NULL 또는 IS NOT NULL을 사용하세요.")
        values = self.value if isinstance(self.value, list) else [self.value]
        if any(isinstance(v, str) and len(v) > 2000 for v in values):
            raise ValueError("조건값은 2,000자 이하여야 합니다.")
        return self


class BindingContent(Strict):
    sources: list[SourceRef] = Field(default_factory=list, max_length=30)
    predicates: list[Predicate] = Field(default_factory=list, max_length=40)
    expression: str = Field(default="", max_length=6000)
    notes: str = Field(default="", max_length=4000)


class SaveBinding(Strict):
    definition_version: int = Field(ge=1)
    expected_revision: int = Field(ge=0)
    content: BindingContent


class BindingReview(Review):
    definition_version: int = Field(ge=1)


class ValidateRequest(Strict):
    version: int | None = Field(default=None, ge=1)
    live_schema: bool = True


class SchemaHint(Strict):
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    dataset_id: str | None = Field(default=None, max_length=80)
    dataset_version: int | None = Field(default=None, ge=1)


class ContextRequest(Strict):
    query: str = Field(min_length=1, max_length=6000)
    connection_key: str = Field(min_length=1, max_length=64)
    workspace_token: str = Field(min_length=1, max_length=100)
    project_id: int | None = None
    domain: str = Field(default="", max_length=200)
    schema_hint: SchemaHint = Field(default_factory=SchemaHint)
    previous_context_id: str | None = Field(default=None, max_length=36)
    selected_definitions: list[DefinitionRef] = Field(default_factory=list, max_length=20)
    confirmed_conditions: dict[str, str] = Field(default_factory=dict, max_length=20)
    max_items: int = Field(default=12, ge=1, le=24)

    @field_validator("confirmed_conditions")
    @classmethod
    def bounded_conditions(cls, values):
        if any(not k.strip() or len(k) > 100 or not v.strip() or len(v) > 2000 for k, v in values.items()):
            raise ValueError("확정 조건의 이름과 값을 확인하세요.")
        return values
