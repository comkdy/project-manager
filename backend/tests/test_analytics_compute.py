"""Descriptive analytics do not infer business rules or temporal assignments."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from app.analytics.compute import compute_split, compute_statistics, compute_trend


def measurement(value=10, at="2026-10-02T00:00:00Z", **overrides):
    return {"family": "epm", "operation": "", "parameter": "PARA1", "value": value,
            "measured_at": at, "lot_cd": "XXXA", "lot_id": "XXXA001", "wf_id": "01",
            "die_x": 1, "die_y": 2, "record_id": 1, **overrides}


def history(recipe="RCP1", at="2026-10-01T00:00:00Z", **overrides):
    return {"lot_cd": "XXXA", "lot_id": "XXXA001", "wf_id": "01", "operation": "oper1",
            "recipe": recipe, "measured_at": at, "record_id": 1, **overrides}


def trend(rows, **overrides):
    return compute_trend(rows, **{"baseline_start": "2026-10-01T00:00:00Z",
        "baseline_end": "2026-10-03T00:00:00Z", "current_start": "2026-10-03T00:00:00Z",
        "current_end": "2026-10-05T00:00:00Z", **overrides})


def split(rows, histories, **overrides):
    return compute_split(rows, histories, **{"operation": "oper1", "recipe_a": "RCP1",
        "recipe_b": "RCP2", "start": "2026-10-01T00:00:00Z", "end": "2026-10-05T00:00:00Z",
        **overrides})


def test_statistics_counts_exact_values_and_missing_nonfinite():
    rows = [measurement(value, record_id=index) for index, value in enumerate([1, 2, 3, 4, None, float("nan"), float("inf")])]
    result = compute_statistics(rows)
    stats = result["series"][0]["statistics"]
    assert stats == {"event_count": 7, "valid_count": 4, "missing_count": 3,
                     "wafer_count": 1, "lot_count": 1, "min": 1, "max": 4,
                     "mean": 2.5, "median": 2.5, "stddev": pytest.approx(1.11803398875),
                     "q25": 1.75, "q75": 3.25, "first_at": "2026-10-02T00:00:00Z",
                     "last_at": "2026-10-02T00:00:00Z"}
    json.dumps(result, allow_nan=False)


def test_inline_series_separates_operation_and_parameter_and_wafer_identity():
    rows = [measurement(1, family="inline", operation="oper1", parameter="MAIN"),
            measurement(100, family="inline", operation="oper2", parameter="MAIN"),
            measurement(200, family="inline", operation="oper1", parameter="SUB1"),
            measurement(3, family="inline", operation="oper1", parameter="MAIN", lot_id="XXXA002")]
    series = compute_statistics(rows)["series"]
    assert [(item["operation"], item["parameter"]) for item in series] == [("oper1", "MAIN"), ("oper1", "SUB1"), ("oper2", "MAIN")]
    assert series[0]["statistics"]["mean"] == 2
    assert series[0]["statistics"]["wafer_count"] == 2
    assert series[0]["statistics"]["lot_count"] == 2


def test_latest_null_is_not_backfilled_and_timestamp_tie_uses_record_id():
    rows = [measurement(20, record_id=1), measurement(None, record_id=2),
            measurement(30, at="2026-10-03T00:00:00Z", record_id=3)]
    item = trend(rows)["series"][0]
    assert item["baseline"]["event_count"] == 1
    assert item["baseline"]["valid_count"] == 0
    assert item["baseline"]["missing_count"] == 1
    assert item["baseline"]["mean"] is None
    assert item["delta"] is None
    assert item["current"]["mean"] == 30
    assert trend(rows, retest_policy="all_events")["series"][0]["baseline"]["event_count"] == 2


def test_trend_explicit_half_open_windows_and_utc_buckets():
    rows = [measurement(1, at="2026-10-01T09:00:00+09:00"),
            measurement(5, at="2026-10-03T00:00:00Z", record_id=2),
            measurement(1000, at="2026-10-05T00:00:00Z", record_id=3)]
    result = trend(rows)
    assert result["window_row_count"] == 2
    assert result["series"][0]["delta"] == 4
    assert [point["at"] for point in result["series"][0]["points"]] == ["2026-10-01", "2026-10-03"]


def test_thresholds_are_explicit_and_percent_change_handles_zero_baseline():
    rows = [measurement(0), measurement(5, at="2026-10-03T00:00:00Z", record_id=2)]
    assert trend(rows)["series"][0]["status"] == "needs_criteria"
    assert trend(rows, min_samples=1)["series"][0]["status"] == "needs_criteria"
    assert trend(rows, min_samples=2, absolute_delta=1)["series"][0]["status"] == "insufficient_data"
    result = trend(rows, min_samples=1, absolute_delta=5)
    assert result["series"][0]["status"] == "changed"
    assert result["series"][0]["percent_change"] is None
    assert result["criteria"]["sample_count_basis"] == "valid_measurements"
    assert trend(rows, min_samples=1, absolute_delta=6)["series"][0]["status"] == "stable"


def test_trend_selects_latest_independently_in_each_window_and_does_not_mutate():
    rows = [measurement(10), measurement(12, at="2026-10-02T12:00:00Z", record_id=2),
            measurement(20, at="2026-10-03T00:00:00Z", record_id=3),
            measurement(22, at="2026-10-04T00:00:00Z", record_id=4)]
    original = deepcopy(rows)
    result = trend(reversed(rows))["series"][0]
    assert result["baseline"]["mean"] == 12
    assert result["current"]["mean"] == 22
    assert sum(point["event_count"] for point in result["points"]) == 2
    assert rows == original


def test_zero_delta_is_stable_even_with_zero_detection_threshold():
    rows = [measurement(10), measurement(10, at="2026-10-03T00:00:00Z")]
    assert trend(rows, min_samples=1, absolute_delta=0)["series"][0]["status"] == "stable"


def test_split_uses_prior_event_and_never_future_history_or_many_to_many_join():
    rows = [measurement(10), measurement(20, wf_id="02"), measurement(99, wf_id="03")]
    histories = [history(), history(record_id=2), history("RCP2", wf_id="02"),
                 history("RCP2", at="2026-10-03T00:00:00Z", record_id=3),
                 history("RCP1", at="2026-10-03T00:00:00Z", wf_id="03")]
    result = split(rows, histories, min_samples=1, absolute_delta=2)
    assert result["matched_count"] == 2
    assert result["unmatched_count"] == 1
    assert result["series"][0]["group_a"]["event_count"] == 1
    assert result["series"][0]["group_b"]["event_count"] == 1
    assert result["series"][0]["delta"] == 10
    assert result["overlapping_lots"] == [{"lot_cd": "XXXA", "lot_id": "XXXA001"}]


def test_split_unknown_latest_recipe_does_not_backfill_older_requested_recipe():
    histories = [history(), history("OTHER", at="2026-10-01T12:00:00Z", record_id=2)]
    result = split([measurement()], histories)
    assert result["matched_count"] == 0
    assert result["unmatched_count"] == 1


def test_split_crossover_excludes_wafer_before_latest_reduction():
    rows = [measurement(10), measurement(20, at="2026-10-04T00:00:00Z", record_id=2),
            measurement(30, wf_id="02"), measurement(40, lot_id="XXXA002")]
    histories = [history(), history("RCP2", at="2026-10-03T00:00:00Z", record_id=2),
                 history(wf_id="02"), history("RCP2", lot_id="XXXA002")]
    result = split(rows, histories)
    assert result["excluded_ambiguous_wafer_count"] == 1
    assert result["excluded_ambiguous_measurement_count"] == 1
    assert result["requires_crossover_decision"] is True
    assert result["matched_count"] == 2
    assert result["selected_row_count"] == 3
    assert result["series"][0]["group_a"]["mean"] == 30
    assert result["series"][0]["group_b"]["mean"] == 40
    all_events = split(rows, histories, retest_policy="all_events")
    assert all_events["excluded_ambiguous_measurement_count"] == 2


@pytest.mark.parametrize("later_recipe", ["RCP1", "RCP2"])
def test_prior_operation_revisit_requires_decision_even_without_earlier_measurement(later_recipe):
    histories = [history("RCP1", at="2026-10-01T00:00:00Z"),
                 history(later_recipe, at="2026-10-01T12:00:00Z", record_id=2)]
    result = split([measurement(10)], histories)
    assert result["excluded_ambiguous_wafer_count"] == 1
    assert result["excluded_ambiguous_measurement_count"] == 1
    assert result["requires_crossover_decision"] is True
    assert result["matched_count"] == 0
    assert result["series"][0]["group_a"]["event_count"] == 0
    assert result["series"][0]["group_b"]["event_count"] == 0


def test_split_latest_null_lot_filters_and_history_key_separation():
    rows = [measurement(10), measurement(None, record_id=2),
            measurement(20, lot_id="XXXA002"), measurement(30, lot_cd="XXXB")]
    histories = [history(), history("RCP2", lot_id="XXXA002")]
    result = split(rows, histories, lot_ids_a=["XXXA001"], lot_ids_b=["XXXA002"])
    assert result["series"][0]["group_a"]["missing_count"] == 1
    assert result["series"][0]["group_a"]["mean"] is None
    assert result["unmatched_count"] == 1  # Different lot_cd cannot borrow a history.
    assert split(rows, histories, lot_ids_a=[], lot_ids_b=[])["matched_count"] == 0
    json.dumps(result, allow_nan=False)


def test_history_timestamp_tie_and_equal_measurement_timestamp():
    result = split([measurement()], [history("RCP1", at="2026-10-02T00:00:00Z", record_id=1),
                                    history("RCP2", at="2026-10-02T00:00:00Z", record_id=2)])
    assert result["series"][0]["group_b"]["mean"] == 10


@pytest.mark.parametrize("options", [{"min_samples": 0}, {"min_samples": True}, {"absolute_delta": -1},
                                    {"absolute_delta": float("nan")}, {"retest_policy": "first"},
                                    {"bucket": "week"}, {"baseline_start": "2026-10-01"},
                                    {"current_start": "2026-10-02T00:00:00Z"}])
def test_invalid_analysis_settings_are_rejected(options):
    with pytest.raises(ValueError):
        trend([], **options)


def test_empty_data_and_datetime_inputs_are_json_safe():
    assert compute_statistics([])["series"] == []
    assert trend([])["series"] == []
    assert split([], [])["series"] == []
    result = compute_statistics([measurement(at=datetime(2026, 10, 1, tzinfo=timezone.utc))])
    json.dumps(result, allow_nan=False)


def test_naive_measurement_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone"):
        compute_statistics([measurement(at=datetime(2026, 10, 1))])
