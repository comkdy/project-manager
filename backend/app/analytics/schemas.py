from datetime import datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from ..semantic.schemas import DefinitionRef, Strict


class SeriesCriteria(Strict):
    family: Literal["inline", "epm"]
    operation: str = Field(default="", max_length=200)
    parameter: str = Field(min_length=1, max_length=200)
    min_samples: int = Field(ge=1, le=1000000)
    absolute_delta: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def identity(self):
        if (self.family == "inline") != bool(self.operation):
            raise ValueError("Inline 기준에는 operation을 지정하고 EPM 기준에는 비워 두세요.")
        return self


class AnalysisRequest(Strict):
    kind: Literal["statistics", "trend", "split"]
    project_id: int | None = None
    purpose: Literal["analysis", "validation"] = "analysis"
    family: Literal["inline", "epm"] | None = None
    # For split this is the recipe-history operation; for trend the Inline series operation.
    operation: str | None = Field(default=None, min_length=1, max_length=200)
    parameter: str | None = Field(default=None, min_length=1, max_length=200)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    baseline_start: AwareDatetime | None = None
    baseline_end: AwareDatetime | None = None
    current_start: AwareDatetime | None = None
    current_end: AwareDatetime | None = None
    recipe_a: str | None = Field(default=None, min_length=1, max_length=200)
    recipe_b: str | None = Field(default=None, min_length=1, max_length=200)
    lot_ids_a: list[str] | None = Field(default=None, max_length=1000)
    lot_ids_b: list[str] | None = Field(default=None, max_length=1000)
    retest_policy: Literal["latest_per_site", "all_events"] | None = None
    min_samples: int | None = Field(default=None, ge=1, le=1000000)
    absolute_delta: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    series_criteria: list[SeriesCriteria] = Field(default_factory=list, max_length=5000)
    bucket: Literal["day"] = "day"
    knowledge_refs: list[DefinitionRef] = Field(default_factory=list, max_length=20)

    @field_validator("lot_ids_a", "lot_ids_b")
    @classmethod
    def lot_ids(cls, value):
        if value is not None:
            if not value or any(not v.strip() or len(v) > 200 for v in value):
                raise ValueError("Lot 목록은 비어 있지 않은 식별자로 입력하세요.")
            return sorted(set(v.strip() for v in value))
        return value

    @model_validator(mode="after")
    def consistent(self):
        keys = [(c.family, c.operation, c.parameter) for c in self.series_criteria]
        if len(keys) != len(set(keys)):
            raise ValueError("같은 PARA의 판정 기준은 한 번만 지정하세요.")
        self.series_criteria.sort(key=lambda c: (c.family, c.operation, c.parameter))
        if self.kind == "statistics" and self.series_criteria:
            raise ValueError("판정 기준은 변화/추세 또는 split 분석에서 지정하세요.")
        if self.kind != "trend" and any((self.baseline_start, self.baseline_end, self.current_start, self.current_end)):
            raise ValueError("기준·비교 기간은 변화/추세 분석에서만 사용하세요.")
        if self.kind == "trend" and any((self.start, self.end, self.recipe_a, self.recipe_b, self.lot_ids_a, self.lot_ids_b)):
            raise ValueError("변화/추세 분석에는 기준·비교 기간과 PARA 조건을 사용하세요.")
        if self.kind == "statistics" and any((self.recipe_a, self.recipe_b, self.lot_ids_a, self.lot_ids_b)):
            raise ValueError("Recipe 그룹 조건은 split 비교에서 사용하세요.")
        if self.kind == "split" and (self.family or self.parameter):
            raise ValueError("Split 비교는 두 recipe 그룹의 전체 Inline·EPM 계열을 비교합니다.")
        if self.family == "epm" and self.operation:
            raise ValueError("EPM PARA에는 operation 조건을 지정하지 않습니다.")
        for start, end in ((self.start, self.end), (self.baseline_start, self.baseline_end),
                           (self.current_start, self.current_end)):
            if start and end and (end <= start or end - start > timedelta(days=3660)):
                raise ValueError("기간은 시작보다 뒤에 끝나야 하며 10년 이내여야 합니다.")
        if self.kind == "trend" and self.baseline_end and self.current_start and self.baseline_end > self.current_start:
            raise ValueError("기준 기간은 비교 기간 전에 끝나야 합니다. 기간을 겹치지 않게 지정하세요.")
        if self.kind == "split" and self.recipe_a and self.recipe_a == self.recipe_b:
            raise ValueError("서로 다른 두 recipe를 지정하세요.")
        if self.parameter and not self.family:
            raise ValueError("PARA를 선택할 때 Inline 또는 EPM을 지정하세요.")
        if self.kind == "trend" and self.family == "inline" and self.parameter and not self.operation:
            raise ValueError("Inline 단일 PARA 분석에는 operation도 필요합니다.")
        if self.kind == "statistics" and any((self.retest_policy, self.min_samples, self.absolute_delta is not None,
                                               self.baseline_start, self.baseline_end, self.current_start, self.current_end)):
            raise ValueError("전체 기본 통계는 측정 이벤트의 기술 통계입니다. 비교 기준은 변화 탐지에서 지정하세요.")
        return self

    def missing(self):
        if self.kind == "statistics":
            return ["period"] if bool(self.start) != bool(self.end) else []
        required = {"retest_policy": self.retest_policy}
        if self.kind == "trend":
            required["period"] = all((self.baseline_start, self.baseline_end, self.current_start, self.current_end))
        else:
            required.update(period=bool(self.start and self.end), split_groups=bool(self.operation and self.recipe_a and self.recipe_b))
        return [key for key, value in required.items() if not value]

    def missing_criteria(self):
        return (["change_criteria"] if self.kind != "statistics" and not self.series_criteria and
                (self.min_samples is None or self.absolute_delta is None) else [])
