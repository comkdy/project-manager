"""Small model-to-tool contract. The model selects conditions, never computes values."""
from typing import Literal

from pydantic import AwareDatetime, Field

from ..schemas import StrictModel


class AnalysisCall(StrictModel):
    kind: Literal["analysis"]
    analysis_kind: Literal["statistics", "trend", "split"]
    goal: Literal["describe", "cause", "outlier"] = "describe"
    family: Literal["inline", "epm"] | None = None
    operation: str | None = Field(default=None, max_length=200)
    parameter: str | None = Field(default=None, max_length=200)
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    baseline_start: AwareDatetime | None = None
    baseline_end: AwareDatetime | None = None
    current_start: AwareDatetime | None = None
    current_end: AwareDatetime | None = None
    recipe_a: str | None = Field(default=None, max_length=200)
    recipe_b: str | None = Field(default=None, max_length=200)
    retest_policy: Literal["latest_per_site", "all_events"] | None = None
    min_samples: int | None = Field(default=None, ge=1, le=1000000)
    absolute_delta: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Verbatim user evidence for conditions that must never become defaults.
    evidence: dict[str, str] = Field(default_factory=dict, max_length=12)
    unresolved: list[Literal["analysis_target", "analysis_scope", "analysis_method", "period"]] = Field(default_factory=list, max_length=4)


ROUTING_INSTRUCTIONS = """
Use analysis_grounding as the authoritative entity catalog. Its projects are project scopes,
series.parameter values are measurement names, and recipe_operations are process-history operations.
Project names (e.g. XXXA) MUST NOT be put into parameter. Match whitespace/case variants only
when they uniquely resolve to an actual registered name: operA may be oper A, never oper1.
For 'XXXA에서 operA에 따른 특성 변화가 관측돼?' interpret XXXA as project and oper A as the
history operation, analysis_kind=split, goal=describe, family/parameter=null. No time windows or
pair of recipes was specified; do not invent them. The app resolves the explicit project scope.
Changing characteristics across process conditions does not by itself request a temporal trend or causal proof.
If a name has multiple actual matches or no reliable mapping, ask one business clarification.
analysis: answer measured Inline/EPM statistics, temporal trends, recipe split comparisons,
or questions about their causes/outliers. Return an analysis tool call directly in this routing
step (no SQL and no numeric answer). Explicit SQL/dataset/card creation still uses route.
Use analysis_kind=trend for changes over time, split for recipe groups, statistics for counts/distribution.
Two time windows ALWAYS use trend, even when only means/counts/delta are requested and no
change decision is wanted. Statistics accepts only zero or one time window, never two.
Copy only conditions explicitly provided by the user, including prior user turns for a follow-up.
Never infer 'recent', year, timezone, baseline, recipe, operation, retest policy or change threshold.
Omit missing fields; the app records the required questions in the knowledge inbox.
For every non-null date, retest_policy, min_samples and absolute_delta, provide evidence[field]
as a short EXACT quote from a user message supporting that condition. Dates need an explicit
timezone in the user message; never use UI/dashboard periods as analysis dates.
Dates are inclusive start, exclusive end. latest_per_site retains the latest NULL and uses
record_id to break timestamp ties; all_events includes repeated measurements.
For split, operation is the recipe-history operation; family/parameter select only the answer's
displayed series (the underlying tool compares all series). Do not equate oper1 with oper A.
Built-in supported behavior (do NOT flag as unresolved): group by lot_cd, lot_id, wf_id,
die_x, die_y, parameter (and inline operation); choose the latest timestamp, break ties by
largest record_id, retain the latest NULL; split uses the last operation history at or before
each measurement, and asks about revisited/crossover wafers only if any are actually found.
These are EXACTLY the tool's built-in semantics, not unsupported custom filters.
Set goal=cause/outlier if requested, even if the period or method is missing. No causal or
outlier inference is supported. Use unresolved=analysis_scope for a custom dataset filter,
lot subset, selected-card conditions or scope that this contract cannot express; analysis_method
for unsupported correlation/normalization/retest rules; analysis_target for ambiguous targets.
Do not silently drop requested filters. Missing analysis criteria must use analysis, not clarification.
Examples: '최근 EPM PARA1 감소 원인?' -> analysis/trend/cause, family epm, parameter PARA1,
no dates/policy/threshold. 'PARA1 두 기간 평균만 비교, 변화 판정은 하지 마' -> analysis/trend/describe,
not statistics. 'oper A RCP1 RCP2 비교, 재방문 wafer는 확인 필요' -> analysis/split/describe;
the tool detects revisits, so unresolved is empty if no other unsupported condition exists.
"""
