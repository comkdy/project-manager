from typing import Annotated, Any, Literal
from datetime import date, datetime
import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .expressions import quote_output


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(StrictModel):
    column: str = Field(min_length=1, max_length=160)
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains"]
    value: Any


class ISOWeek(StrictModel):
    year: int = Field(ge=1900, le=9998)
    week: int = Field(ge=1, le=53)

    @model_validator(mode="after")
    def valid_week(self):
        date.fromisocalendar(self.year, self.week, 1)
        return self


class CardPeriod(StrictModel):
    use_dashboard: bool = True
    source_id: str | None = Field(default=None, max_length=80)
    column: str | None = Field(default=None, max_length=160)

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_period_options(cls, value):
        # Previous saved cards may contain a separate week range. Turning off
        # the common period now means no automatic date predicate, always.
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key not in {"start", "enabled"}}
        return value

    @model_validator(mode="after")
    def complete_selection(self):
        if bool(self.source_id) != bool(self.column):
            raise ValueError("기간 기준 테이블과 컬럼을 함께 선택하세요.")
        return self


class QueryContext(StrictModel):
    project_id: int | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    limit: int = Field(default=200, ge=1, le=20000)
    cursor: str | None = Field(default=None, max_length=4096)
    sort: str | None = None
    direction: Literal["asc", "desc"] = "asc"
    refresh: bool = False
    period_start: ISOWeek | None = None
    gantt_page: int = Field(default=0, ge=0, le=10000)
    gantt_search: str = Field(default='', max_length=200)
    gantt_lot_cd: str | None = Field(default=None, max_length=200)
    gantt_detail_lot: str = Field(default='', max_length=200)


class DatasetColumn(StrictModel):
    name: str = Field(min_length=1, max_length=63)
    expression: str = Field(min_length=1, max_length=4000)

    @field_validator('name')
    @classmethod
    def valid_output_name(cls, value):
        quote_output(value)
        return value


class GroupCompletionRequest(StrictModel):
    expressions: list[Annotated[str, Field(max_length=4000)]] = Field(max_length=100)
    input_columns: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(max_length=2000)
    having_sql: str | None = Field(default=None, max_length=4000)


class PivotOutput(StrictModel):
    value: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=63)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        quote_output(value)
        if value.startswith('__'):
            raise ValueError('__로 시작하는 이름은 사용할 수 없습니다.')
        return value


class PivotSpec(StrictModel):
    keys: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=1, max_length=20)
    operation_column: str = Field(min_length=1, max_length=160)
    value_column: str = Field(min_length=1, max_length=160)
    time_column: str = Field(min_length=1, max_length=160)
    tie_breaker: str = Field(min_length=1, max_length=160)
    outputs: list[PivotOutput] = Field(min_length=1, max_length=30)
    require_all: bool = True
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    where_sql: str | None = Field(default=None, max_length=4000)

    @model_validator(mode='after')
    def distinct_fields(self):
        roles = [self.operation_column, self.value_column, self.time_column]
        if len(set(self.keys)) != len(self.keys) or len(set(roles)) != 3 or set(self.keys).intersection(roles):
            raise ValueError('행 기준·열 기준·값·시간 기준에는 서로 다른 컬럼을 선택하세요.')
        if self.tie_breaker in [*self.keys, *roles]:
            raise ValueError('동일 시각 기준에는 별도의 순번·고유 번호 컬럼을 선택하세요.')
        if len({item.value for item in self.outputs}) != len(self.outputs):
            raise ValueError('열 항목이 중복됩니다.')
        names = [*self.keys, *(item.name for item in self.outputs)]
        if len(set(names)) != len(names):
            raise ValueError('출력 이름은 행 기준 및 다른 출력 이름과 달라야 합니다.')
        return self


class PivotValuesRequest(StrictModel):
    column: str = Field(min_length=1, max_length=160)


class DatasetSpec(StrictModel):
    source_id: str = Field(min_length=1, max_length=80)
    columns: list[DatasetColumn] = Field(default_factory=list, max_length=100)
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    joins: list[str] = Field(default_factory=list, max_length=8)
    join_types: dict[str, Literal["left", "inner"]] = Field(default_factory=dict)
    where_sql: str | None = Field(default=None, max_length=4000)
    group_by: list[str] = Field(default_factory=list, max_length=30)
    having_sql: str | None = Field(default=None, max_length=4000)
    raw_sql: str | None = Field(default=None, min_length=1, max_length=20000)
    pivot: PivotSpec | None = None

    @model_validator(mode="after")
    def one_editing_mode(self):
        if self.pivot and (self.columns or self.filters or self.joins or self.join_types or self.where_sql or self.group_by or self.having_sql):
            raise ValueError('피벗은 원천 테이블에서 구성합니다. 피벗의 필터 설정을 사용하세요.')
        if self.raw_sql is not None and (self.columns or self.filters or self.joins or self.join_types or self.where_sql or self.group_by or self.having_sql):
            raise ValueError("Raw SQL과 컬럼·필터·관계 폼은 동시에 사용할 수 없습니다")
        if set(self.join_types) - set(self.joins):
            raise ValueError("JOIN 유형은 선택한 관계에만 지정할 수 있습니다")
        return self

    @field_validator("columns")
    @classmethod
    def distinct_columns(cls, value):
        if len({c.name for c in value}) != len(value):
            raise ValueError("Output column names must be unique")
        if any(c.name.startswith("__") for c in value):
            raise ValueError("Names beginning with __ are reserved")
        return value


class CardOptions(StrictModel):
    palette: str = "teal"
    bins: int = Field(default=30, ge=5, le=100)
    sort: Literal["asc", "desc"] = "asc"
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    x_font_size: int = Field(default=10, ge=8, le=32)
    y_font_size: int = Field(default=10, ge=8, le=32)
    x_scale: Literal["linear", "log"] = "linear"
    y_scale: Literal["linear", "log"] = "linear"
    x_label: str | None = Field(default=None, max_length=80)
    y_label: str | None = Field(default=None, max_length=80)
    value_format: Literal["number", "percent"] = "number"
    trellis_columns: int = Field(default=0, ge=0, le=4)

    @model_validator(mode="after")
    def positive_log_ranges(self):
        for axis in ("x", "y"):
            if getattr(self, f"{axis}_scale") == "log":
                for bound in ("min", "max"):
                    value = getattr(self, f"{axis}_{bound}")
                    if value is not None and (not math.isfinite(value) or value <= 0):
                        raise ValueError(f"{axis.upper()}축 로그 범위에는 0보다 큰 값을 입력하세요.")
        return self

    @field_validator("x_label", "y_label")
    @classmethod
    def trim_axis_label(cls, value):
        return (value.strip() or None) if value is not None else None


class ExpressionColumn(StrictModel):
    kind: Literal["column"]
    name: str = Field(min_length=1, max_length=160)


class ExpressionNumber(StrictModel):
    kind: Literal["number"]
    value: str = Field(max_length=32, pattern=r"^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")


class ExpressionOperator(StrictModel):
    kind: Literal["operator"]
    value: Literal["+", "-", "*", "/", "(", ")"]


ValueExpressionToken = Annotated[ExpressionColumn | ExpressionNumber | ExpressionOperator, Field(discriminator="kind")]


class GanttSettings(StrictModel):
    data_mode: Literal['tables', 'dataset'] = 'tables'
    status_source_id: str | None = Field(default=None, min_length=1, max_length=80)
    tat_source_id: str | None = Field(default=None, min_length=1, max_length=80)
    history_source_id: str | None = Field(default=None, min_length=1, max_length=80)
    line_filters: list[Filter] = Field(default_factory=list, max_length=30)
    key_filters: list[Filter] = Field(default_factory=list, max_length=30)
    started_column: str | None = Field(default=None, max_length=160)
    as_of: str | None = Field(default=None, max_length=50)

    @field_validator('as_of')
    @classmethod
    def timezone_required(cls, value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if value is not None else None
        if parsed is not None and parsed.tzinfo is None:
            raise ValueError('기준 시각에는 시간대가 필요합니다.')
        return parsed.isoformat() if parsed else None


class CardSpec(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    dataset_id: str = Field(min_length=1, max_length=80)
    dataset_version: int = Field(ge=1)
    kind: Literal["kpi", "scatter", "bar", "line", "histogram", "box", "wafer", "gantt"]
    gantt: GanttSettings | None = None
    renderer: Literal["plotly", "seaborn"] = "plotly"
    x: str | None = None
    y: str | None = None
    color: str | None = None
    trellis: str | None = Field(default=None, min_length=1, max_length=160)
    aggregate: Literal["count", "sum", "avg", "min", "max", "yield", "none"] = "none"
    value_expression: list[ValueExpressionToken] | None = Field(default=None, max_length=100)
    value_expression_name: str | None = Field(default=None, max_length=80)
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    options: CardOptions = Field(default_factory=CardOptions)
    period: CardPeriod = Field(default_factory=CardPeriod)
    editor_mode: Literal['simple', 'python'] = 'simple'
    python_script: str | None = Field(default=None, min_length=1, max_length=20000)
    script_input: Literal['dataset', 'chart'] = 'dataset'

    @model_validator(mode='after')
    def require_python_script(self):
        if self.editor_mode == 'python' and not self.python_script:
            raise ValueError('Python 스크립트를 입력하세요.')
        if self.editor_mode == 'python' and self.kind == 'gantt':
            raise ValueError('간트 전용 편집기는 Python 변환을 지원하지 않습니다. 일정 데이터셋으로 새 스크립트 카드를 만드세요.')
        return self

    @field_validator("renderer")
    @classmethod
    def interactive_renderer(cls, value):
        # Accept legacy saved cards; all live cards now use the same renderer.
        return "plotly"

    @field_validator("value_expression_name")
    @classmethod
    def trim_expression_name(cls, value):
        return (value.strip() or None) if value is not None else None


class Layout(StrictModel):
    i: str
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0, le=10000)
    w: int = Field(ge=2, le=12)
    h: int = Field(ge=2, le=30)


class DashboardUpdate(StrictModel):
    version: int = Field(ge=1)
    cards: list[CardSpec] = Field(max_length=40)
    layout: list[Layout] = Field(max_length=40)


class QueryRequest(StrictModel):
    dataset_id: str
    dataset_version: int = Field(ge=1)
    context: QueryContext = Field(default_factory=QueryContext)


class ChartRequest(StrictModel):
    card: CardSpec
    context: QueryContext = Field(default_factory=QueryContext)


class DatasetPreviewRequest(StrictModel):
    spec: DatasetSpec
    context: QueryContext = Field(default_factory=QueryContext)
    editor_mode: Literal["simple", "sql"] | None = None


class SimpleEditorRequest(StrictModel):
    source_id: str = Field(min_length=1, max_length=80)
    sql: str = Field(max_length=20000)
    pivot: PivotSpec | None = None


class DatasetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    spec: DatasetSpec
    project_id: int | None = None


class DatasetUpdate(DatasetCreate):
    expected_version: int = Field(ge=1)


class SourceCreate(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
    name: str = Field(min_length=1, max_length=200)
    schema_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
    table_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
    project_column: str | None = None
    primary_key: str
    columns: list[str] | None = None


class RelationCreate(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
    left_source_id: str
    right_source_id: str
    left_column: str
    right_column: str
    join_type: Literal["left", "inner"] = "left"
