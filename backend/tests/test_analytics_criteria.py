import pytest
from pydantic import ValidationError

from app.analytics.schemas import AnalysisRequest
from app.analytics.service import compute_request


def request(**extra):
    return AnalysisRequest(kind="trend", baseline_start="2026-01-01T00:00:00Z", baseline_end="2026-01-02T00:00:00Z",
                           current_start="2026-01-02T00:00:00Z", current_end="2026-01-03T00:00:00Z",
                           retest_policy="all_events", **extra)


def measurements():
    return [{"family": "epm", "operation": "", "parameter": parameter, "value": value,
             "measured_at": f"2026-01-0{day}T12:00:00Z", "record_id": day, "lot_cd": "A",
             "lot_id": "A1", "wf_id": 1, "die_x": 0, "die_y": 0}
            for parameter in ("small_scale", "large_scale") for day, value in ((1, 10), (2, 12))]


def test_each_series_uses_its_explicit_threshold():
    req = request(series_criteria=[
        {"family": "epm", "parameter": "small_scale", "min_samples": 1, "absolute_delta": 1},
        {"family": "epm", "parameter": "large_scale", "min_samples": 1, "absolute_delta": 5}])
    result = {s["parameter"]: s for s in compute_request(req, measurements(), [])["series"]}
    assert result["small_scale"]["status"] == "changed"
    assert result["large_scale"]["status"] == "stable"
    assert result["small_scale"]["criteria"]["absolute_delta"] == 1


def test_undefined_series_criterion_is_not_inferred_from_other_series():
    req = request(series_criteria=[{"family": "epm", "parameter": "small_scale", "min_samples": 1, "absolute_delta": 1}])
    result = {s["parameter"]: s for s in compute_request(req, measurements(), [])["series"]}
    assert result["large_scale"]["status"] == "needs_criteria"
    assert result["large_scale"]["criteria"]["absolute_delta"] is None


def test_unused_series_criteria_are_reported():
    req = request(series_criteria=[{"family": "epm", "parameter": "not_measured", "min_samples": 1, "absolute_delta": 1}])
    assert compute_request(req, measurements(), [])["unused_criteria"][0]["parameter"] == "not_measured"


def test_duplicate_and_wrong_identity_criteria_rejected():
    rule = {"family": "epm", "parameter": "small_scale", "min_samples": 1, "absolute_delta": 1}
    with pytest.raises(ValidationError):
        request(series_criteria=[rule, rule])
    with pytest.raises(ValidationError):
        request(series_criteria=[{**rule, "operation": "oper1"}])
    with pytest.raises(ValidationError):
        request(series_criteria=[{**rule, "family": "inline"}])
