from datetime import datetime
from types import SimpleNamespace

import pytest

from app.analytics.chat_contracts import AnalysisCall
from app.analytics.chat_tools import prepare_request
from app.analytics.condition_evidence import date_supported, policy_supported


@pytest.mark.parametrize("text,value", [
    ("2026-10-01T03:04:05.123456Z", "2026-10-01T03:04:05.123456+00:00"),
    ("2026-10-01T12:04:05+09:00", "2026-10-01T03:04:05+00:00"),
    ("KST 2026-10-01 13:30", "2026-10-01T13:30:00+09:00"),
    ("UTC 2026/10/01", "2026-10-01T00:00:00+00:00"),
    ("GMT+09:00 2026.10.01", "2026-10-01T00:00:00+09:00"),
    ("한국 시간 2026년 10월 1일 13시 30분", "2026-10-01T13:30:00+09:00"),
    ("서울 시간 2026년 10월 1일~3일", "2026-10-03T00:00:00+09:00"),
    ("UTC 2026년 9월 1일부터 10월 1일", "2026-10-01T00:00:00+00:00"),
    ("October 1, 2026 UTC", "2026-10-01T00:00:00+00:00"),
    ("Oct 1st 2026 KST", "2026-10-01T00:00:00+09:00"),
])
def test_explicit_dates_and_equivalent_timezones_remain_supported(text, value):
    assert date_supported(datetime.fromisoformat(value), text, text)


@pytest.mark.parametrize("text,value", [
    ("KST 2026-10-01", "2025-10-01T00:00:00+09:00"),
    ("KST 2026-10-01", "2026-10-01T12:00:00+09:00"),
    ("KST 2026-10-01", "2026-10-01T00:00:00+00:00"),
    ("2026-10-01", "2026-10-01T00:00:00+00:00"),
    ("KST 10월 1일", "2026-10-01T00:00:00+09:00"),
    ("KST 최근 일주일", "2026-10-01T00:00:00+09:00"),
    ("KST 2026-02-30", "2026-03-02T00:00:00+09:00"),
    ("UTC 또는 KST 2026-10-01", "2026-10-01T00:00:00+09:00"),
])
def test_unstated_or_conflicting_dates_are_not_grounded(text, value):
    assert not date_supported(datetime.fromisoformat(value), text, text)


def test_short_date_quote_cannot_discard_the_users_explicit_clock():
    assert not date_supported(datetime.fromisoformat("2026-10-01T00:00:00+09:00"),
                              "2026-10-01", "KST 2026-10-01 13:30")


@pytest.mark.parametrize("text,policy", [
    ("마지막 측정만 사용. 마지막 NULL도 유지.", "latest_per_site"),
    ("최종 값으로 비교", "latest_per_site"),
    ("Use the latest measurement per site", "latest_per_site"),
    ("latest_per_site", "latest_per_site"),
    ("모든 측정을 포함", "all_events"),
    ("Include all measurement events", "all_events"),
    ("all_events", "all_events"),
    ("모든 측정은 사용하지 않고 마지막 측정만 사용", "latest_per_site"),
    ("Do not use the last measurement. Include all events.", "all_events"),
])
def test_explicit_policies(text, policy):
    assert policy_supported(policy, text)
    assert not policy_supported("all_events" if policy == "latest_per_site" else "latest_per_site", text)


@pytest.mark.parametrize("text", ["재측정 방식은 아직 미정", "마지막 측정 또는 모든 측정", "마지막 측정만 사용하지 마"])
def test_unspecified_or_ambiguous_policy_cannot_be_chosen(text):
    assert not policy_supported("latest_per_site", text)
    assert not policy_supported("all_events", text)


def test_model_cannot_replace_user_period_and_policy_with_other_values():
    text = "Compare oper A RCP1 versus RCP2. KST 2026-10-01 through 2026-10-03. Include all measurement events."
    call = AnalysisCall(kind="analysis", analysis_kind="split", operation="oper A", recipe_a="RCP1", recipe_b="RCP2",
        start="2025-01-01T00:00:00+09:00", end="2025-02-01T00:00:00+09:00", retest_policy="latest_per_site",
        evidence={field: text for field in ("start", "end", "retest_policy")})
    catalog = {"series": [], "recipe_operations": [{"operation": "oper A", "recipes": ["RCP1", "RCP2"]}]}
    request, blockers, _ = prepare_request(call, SimpleNamespace(message=text, history=[], project_id=None), catalog)
    assert request.start is request.end is request.retest_policy is None
    assert "period" in blockers and "retest_policy" in request.missing()


def test_short_policy_quote_cannot_discard_negation_in_user_message():
    call = AnalysisCall(kind="analysis", analysis_kind="trend", retest_policy="latest_per_site",
                        evidence={"retest_policy": "last measurement"})
    request, _, _ = prepare_request(call, SimpleNamespace(
        message="Do not use the last measurement. Include all events.", history=[], project_id=None),
        {"series": [], "recipe_operations": []})
    assert request.retest_policy is None


@pytest.mark.parametrize("text,policy", [
    ("I do not want to use the last measurement", "latest_per_site"),
    ("I do not intend to use the last measurement", "latest_per_site"),
    ("I do not wish to include all events", "all_events"),
    ("Avoid using the last measurement", "latest_per_site"),
    ("Skip the last measurement", "latest_per_site"),
    ("Exclude the last measurement", "latest_per_site"),
    ("Omit the last measurement", "latest_per_site"),
    ("마지막 측정값은 빼고", "latest_per_site"),
    ("마지막 측정값은 배제", "latest_per_site"),
    ("I do not want to include all events", "all_events"),
])
def test_explicit_rejection_is_not_a_positive_policy_selection(text, policy):
    assert not policy_supported(policy, text)
    call = AnalysisCall(kind="analysis", analysis_kind="trend", retest_policy=policy,
                        evidence={"retest_policy": text})
    request, _, _ = prepare_request(call, SimpleNamespace(message=text, history=[], project_id=None),
                                   {"series": [], "recipe_operations": []})
    assert request.retest_policy is None


@pytest.mark.parametrize("text,policy", [
    ("마지막 측정만 사용하고 마지막 NULL은 버리지 마", "latest_per_site"),
    ("Do not drop NULLs. Use the last measurement", "latest_per_site"),
    ("Do not replace NULLs. Include all events", "all_events"),
])
def test_null_preservation_does_not_negate_the_selected_policy(text, policy):
    assert policy_supported(policy, text)


@pytest.mark.parametrize("text,value", [
    ("KST 2026년 10월 1일 오후 3시", "2026-10-01T00:00:00+09:00"),
    ("KST 2026년 10월 1일 오전 3시", "2026-10-01T00:00:00+09:00"),
    ("KST 2026-10-01 at 3pm", "2026-10-01T00:00:00+09:00"),
    ("KST 2026-10-01 3am", "2026-10-01T00:00:00+09:00"),
    ("KST 2026-10-01 3:00 PM", "2026-10-01T03:00:00+09:00"),
    ("KST 2026-10-01 3:00 a.m.", "2026-10-01T03:00:00+09:00"),
    ("2026-10-01 KST 오후 3시", "2026-10-01T00:00:00+09:00"),
    ("KST 2026-10-01 at 15:00", "2026-10-01T00:00:00+09:00"),
])
def test_unsupported_explicit_clocks_never_become_midnight_or_24_hour_values(text, value):
    assert not date_supported(datetime.fromisoformat(value), text, text)


def test_short_date_quote_cannot_hide_unsupported_clock_in_original_message():
    assert not date_supported(datetime.fromisoformat("2026-10-01T00:00:00+09:00"),
                              "2026-10-01", "KST 2026-10-01 at 3pm")
