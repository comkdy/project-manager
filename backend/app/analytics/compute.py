"""Deterministic, descriptive calculations for parameter analytics.

These functions neither query a source nor infer business definitions. Time
windows and detection criteria come from the caller. A detected change means an
explicit absolute-delta rule was met; it is not a significance or causal claim.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import statistics
from typing import Any, Iterable, Mapping
from .cancellation import checkpoint


Row = Mapping[str, Any]
SeriesKey = tuple[str, str, str]
WaferKey = tuple[str, str, str]

_DESCRIPTIVE_WARNING = (
    "Descriptive results only. Thresholds use valid measurement counts, not "
    "independent experimental units; results do not establish statistical "
    "significance or causation."
)


def _time(value: Any) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is timezone.utc:
        return value
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Timestamps must be ISO 8601 with an explicit timezone.") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include an explicit timezone.")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _window(start: Any, end: Any) -> tuple[datetime, datetime]:
    start, end = _time(start), _time(end)
    if start >= end:
        raise ValueError("Window start must be before window end.")
    return start, end


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _identity(row: Row) -> tuple[SeriesKey, WaferKey]:
    family = str(row.get("family", ""))
    operation = str(row.get("operation") or "") if family == "inline" else ""
    parameter = str(row.get("parameter") or "")
    if family not in {"inline", "epm"} or not parameter or (family == "inline" and not operation):
        raise ValueError("Measurements require family, parameter and an Inline operation.")
    lot_cd, lot_id, wf_id = row.get("lot_cd"), row.get("lot_id"), row.get("wf_id")
    if lot_cd is None or lot_id is None or wf_id is None:
        raise ValueError("Measurements require lot_cd, lot_id and wf_id.")
    wafer = (str(lot_cd), str(lot_id), str(wf_id))
    if "" in wafer:
        raise ValueError("Measurements require lot_cd, lot_id and wf_id.")
    return (family, operation, parameter), wafer


def _wafer(row: Row) -> WaferKey:
    return tuple(str(row.get(key, "")) for key in ("lot_cd", "lot_id", "wf_id"))


def _record(row: Row) -> int:
    try:
        return int(row["record_id"])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError("Measurements and history require a numeric record_id for timestamp ties.") from exc


@dataclass(slots=True)
class _Measurement:
    series: SeriesKey
    wafer: WaferKey
    site: tuple[Any, Any]
    at: datetime
    record: int
    value: float | None

    @classmethod
    def read(cls, row: Row) -> _Measurement:
        checkpoint()
        series, wafer = _identity(row)
        return cls(series, wafer, (row.get("die_x"), row.get("die_y")),
                   _time(row["measured_at"]), _record(row), _finite(row.get("value")))

    def site_key(self) -> tuple[Any, ...]:
        return self.series, self.wafer, self.site

    def order(self) -> tuple[datetime, int]:
        return self.at, self.record


class _Stats:
    __slots__ = ("values", "event_count", "wafers", "lots", "first", "last")

    def __init__(self) -> None:
        self.values: list[float] = []
        self.event_count = 0
        self.wafers: set[WaferKey] = set()
        self.lots: set[tuple[str, str]] = set()
        self.first: datetime | None = None
        self.last: datetime | None = None

    def add(self, measurement: _Measurement) -> None:
        self.event_count += 1
        self.wafers.add(measurement.wafer)
        self.lots.add(measurement.wafer[:2])
        if measurement.value is not None:
            self.values.append(measurement.value)
        if self.first is None or measurement.at < self.first:
            self.first = measurement.at
        if self.last is None or measurement.at > self.last:
            self.last = measurement.at

    def result(self) -> dict[str, Any]:
        checkpoint()
        values = sorted(self.values)
        count = len(values)
        return {
            "event_count": self.event_count,
            "valid_count": count,
            "missing_count": self.event_count - count,
            "wafer_count": len(self.wafers),
            "lot_count": len(self.lots),
            "min": values[0] if values else None,
            "max": values[-1] if values else None,
            "mean": _finite(statistics.mean(values)) if values else None,
            "median": _quantile(values, 0.5),
            "stddev": _finite(statistics.pstdev(values)) if values else None,
            "q25": _quantile(values, 0.25),
            "q75": _quantile(values, 0.75),
            "first_at": _iso(self.first),
            "last_at": _iso(self.last),
        }


class _Point:
    """Daily display counts do not need duplicate wafer/lot identity sets."""
    __slots__ = ("event_count", "values")

    def __init__(self) -> None:
        self.event_count = 0
        self.values: list[float] = []

    def add(self, measurement: _Measurement) -> None:
        self.event_count += 1
        if measurement.value is not None:
            self.values.append(measurement.value)

    def result(self) -> dict[str, Any]:
        return {"event_count": self.event_count, "valid_count": len(self.values),
                "missing_count": self.event_count - len(self.values),
                "mean": _finite(statistics.mean(self.values)) if self.values else None}


def _quantile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    # Weighted terms avoid overflow in (upper_value - lower_value).
    return _finite(values[lower] * (1 - weight) + values[upper] * weight)


def _series_fields(key: SeriesKey) -> dict[str, str]:
    return dict(zip(("family", "operation", "parameter"), key))


def _policy(retest_policy: str) -> None:
    if retest_policy not in {"latest_per_site", "all_events"}:
        raise ValueError("retest_policy must be latest_per_site or all_events.")


def _criteria(min_samples: int | None, absolute_delta: float | None) -> dict[str, Any]:
    if min_samples is not None and (isinstance(min_samples, bool)
                                   or not isinstance(min_samples, int) or min_samples < 1):
        raise ValueError("min_samples must be a positive valid-measurement count.")
    if absolute_delta is not None:
        number = _finite(absolute_delta)
        if isinstance(absolute_delta, bool) or number is None or number < 0:
            raise ValueError("absolute_delta must be a finite non-negative number.")
        absolute_delta = number
    return {"min_samples": min_samples, "absolute_delta": absolute_delta,
            "sample_count_basis": "valid_measurements", "comparison": "absolute_mean_delta"}


def _comparison(a: dict[str, Any], b: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    delta = _finite(b["mean"] - a["mean"]) if a["mean"] is not None and b["mean"] is not None else None
    percent_change = _finite((delta / a["mean"]) * 100) if delta is not None and a["mean"] else None
    if criteria["min_samples"] is None or criteria["absolute_delta"] is None:
        status = "needs_criteria"
    elif min(a["valid_count"], b["valid_count"]) < criteria["min_samples"] or delta is None:
        status = "insufficient_data"
    else:
        status = "changed" if delta != 0 and abs(delta) >= criteria["absolute_delta"] else "stable"
    return {"delta": delta, "percent_change": percent_change, "status": status}


def _metadata() -> dict[str, Any]:
    return {"analysis_type": "descriptive", "stddev_method": "population",
            "quantile_method": "linear_interpolation", "interval": "[start,end)",
            "timezone": "UTC", "sample_count_basis": "valid_measurements"}


def compute_statistics(rows: Iterable[Row]) -> dict[str, Any]:
    """Summarize every supplied event; NULL and non-finite values remain missing."""
    groups: dict[SeriesKey, _Stats] = defaultdict(_Stats)
    row_count = 0
    for row in rows:
        measurement = _Measurement.read(row)
        groups[measurement.series].add(measurement)
        row_count += 1
    return {"series": [{**_series_fields(key), "statistics": groups[key].result()} for key in sorted(groups)],
            "row_count": row_count, "metadata": _metadata(), "warnings": [_DESCRIPTIVE_WARNING]}


def compute_trend(
    rows: Iterable[Row], *, baseline_start: Any, baseline_end: Any,
    current_start: Any, current_end: Any, bucket: str = "day",
    retest_policy: str = "latest_per_site", min_samples: int | None = None,
    absolute_delta: float | None = None,
) -> dict[str, Any]:
    """Compare explicit windows and describe daily means of selected events.

    latest_per_site selects the last event per series/wafer/site independently
    inside each window, including a last event whose value is NULL. Bucketed
    points use that same selected population, avoiding two contradictory counts.
    """
    baseline = _window(baseline_start, baseline_end)
    current = _window(current_start, current_end)
    if max(baseline[0], current[0]) < min(baseline[1], current[1]):
        raise ValueError("Baseline and current windows must not overlap.")
    if bucket != "day":
        raise ValueError("Only the explicit UTC day bucket is supported.")
    _policy(retest_policy)
    criteria = _criteria(min_samples, absolute_delta)
    groups: dict[SeriesKey, tuple[_Stats, _Stats]] = {}
    points: dict[tuple[SeriesKey, str], _Point] = defaultdict(_Point)
    latest: dict[tuple[Any, ...], tuple[int, _Measurement]] = {}
    row_count = 0
    window_row_count = 0

    def add(index: int, measurement: _Measurement) -> None:
        if measurement.series not in groups:
            groups[measurement.series] = (_Stats(), _Stats())
        groups[measurement.series][index].add(measurement)
        points[(measurement.series, measurement.at.date().isoformat())].add(measurement)

    for row in rows:
        measurement = _Measurement.read(row)
        row_count += 1
        index = 0 if baseline[0] <= measurement.at < baseline[1] else (
            1 if current[0] <= measurement.at < current[1] else None)
        if index is None:
            continue
        window_row_count += 1
        if retest_policy == "all_events":
            add(index, measurement)
        else:
            key = index, measurement.site_key()
            previous = latest.get(key)
            if previous is None or measurement.order() > previous[1].order():
                latest[key] = index, measurement
    for index, measurement in latest.values():
        add(index, measurement)

    series = []
    series_points: dict[SeriesKey, list[dict[str, Any]]] = defaultdict(list)
    for (key, day), accumulator in sorted(points.items()):
        series_points[key].append({"at": day, **accumulator.result()})
    for key in sorted(groups):
        base_stats, current_stats = (group.result() for group in groups[key])
        series.append({**_series_fields(key), "baseline": base_stats, "current": current_stats,
                       **_comparison(base_stats, current_stats, criteria), "points": series_points[key]})
    return {"series": series, "windows": {
                "baseline": {"start": _iso(baseline[0]), "end": _iso(baseline[1])},
                "current": {"start": _iso(current[0]), "end": _iso(current[1])}},
            "bucket": bucket, "retest_policy": retest_policy, "criteria": criteria,
            "row_count": row_count, "window_row_count": window_row_count,
            "selected_row_count": sum(a.event_count + b.event_count for a, b in groups.values()),
            "metadata": _metadata(), "warnings": [_DESCRIPTIVE_WARNING]}


def compute_split(
    measurements: Iterable[Row], histories: Iterable[Row], *, operation: str,
    recipe_a: str, recipe_b: str, start: Any, end: Any,
    retest_policy: str = "latest_per_site", lot_ids_a: Iterable[str] | None = None,
    lot_ids_b: Iterable[str] | None = None, min_samples: int | None = None,
    absolute_delta: float | None = None,
) -> dict[str, Any]:
    """Compare explicitly requested, temporally matched observed recipe cohorts.

    Each measurement uses the latest requested-operation history event at or
    before measurement time. A wafer that revisited the requested operation
    before an included measurement, or has measurements assigned to both requested
    recipes, is excluded and surfaced for a user decision. These are
    observational cohorts, not inferred experimental treatment assignments.
    """
    begin, finish = _window(start, end)
    if not operation or not recipe_a or not recipe_b or recipe_a == recipe_b:
        raise ValueError("An operation and two different recipes are required.")
    _policy(retest_policy)
    criteria = _criteria(min_samples, absolute_delta)
    allowed_a = None if lot_ids_a is None else {str(value) for value in lot_ids_a}
    allowed_b = None if lot_ids_b is None else {str(value) for value in lot_ids_b}
    history: dict[WaferKey, list[tuple[datetime, int, str]]] = defaultdict(list)
    for row in histories:
        checkpoint()
        if str(row.get("operation")) != operation:
            continue
        at = _time(row["measured_at"])
        if at < finish:
            history[_wafer(row)].append((at, _record(row), str(row.get("recipe") or "")))
    timelines = {}
    for wafer, events in history.items():
        events.sort()
        timelines[wafer] = ([event[0] for event in events], events)

    latest: dict[tuple[Any, ...], tuple[_Measurement, str | None]] = {}
    selected: list[tuple[_Measurement, str | None]] = []
    memberships: dict[WaferKey, set[str]] = defaultdict(set)
    revisited: set[WaferKey] = set()
    raw_row_count = 0
    window_row_count = 0
    for row in measurements:
        measurement = _Measurement.read(row)
        raw_row_count += 1
        if not begin <= measurement.at < finish:
            continue
        window_row_count += 1
        assignment = None
        timeline = timelines.get(measurement.wafer)
        if timeline is not None:
            times, events = timeline
            position = bisect_right(times, measurement.at) - 1
            if position >= 0:
                # A second visit may alter exposure even when there was no
                # measurement after the first visit. Identical timestamps use
                # record_id as the declared technical tie breaker; distinct
                # earlier visit times require a business rework rule.
                if times[0] < times[position]:
                    revisited.add(measurement.wafer)
                recipe = events[position][2]
                if recipe in {recipe_a, recipe_b}:
                    # Check crossover before optional cohort filters: a lot filter
                    # must not hide evidence that a wafer received both recipes.
                    memberships[measurement.wafer].add(recipe)
                    allowed = allowed_a if recipe == recipe_a else allowed_b
                    if allowed is None or measurement.wafer[1] in allowed:
                        assignment = "a" if recipe == recipe_a else "b"
        if retest_policy == "all_events":
            selected.append((measurement, assignment))
        else:
            key = measurement.site_key()
            previous = latest.get(key)
            if previous is None or measurement.order() > previous[0].order():
                latest[key] = measurement, assignment
    ambiguous = {wafer for wafer, recipes in memberships.items() if len(recipes) > 1 or wafer in revisited}
    groups: dict[SeriesKey, tuple[_Stats, _Stats]] = {}
    matched = unmatched = excluded = 0
    cohort_wafers: tuple[set[WaferKey], set[WaferKey]] = (set(), set())
    cohort_lots: tuple[set[tuple[str, str]], set[tuple[str, str]]] = (set(), set())
    retained = selected if retest_policy == "all_events" else latest.values()
    for measurement, assignment in retained:
        if measurement.series not in groups:
            groups[measurement.series] = (_Stats(), _Stats())
        if measurement.wafer in ambiguous:
            excluded += 1
        elif assignment is None:
            unmatched += 1
        else:
            index = 0 if assignment == "a" else 1
            groups[measurement.series][index].add(measurement)
            cohort_wafers[index].add(measurement.wafer)
            cohort_lots[index].add(measurement.wafer[:2])
            matched += 1
    series = []
    for key in sorted(groups):
        a, b = (group.result() for group in groups[key])
        series.append({**_series_fields(key), "group_a": a, "group_b": b, **_comparison(a, b, criteria)})
    overlap = cohort_lots[0] & cohort_lots[1]
    warnings = [_DESCRIPTIVE_WARNING, "Recipe assignment is observational and uses the most recent prior operation event."]
    if ambiguous:
        warnings.append("Wafers with prior repeated operation visits or measurements assigned to both recipes were excluded. User confirmation of crossover/rework handling is required.")
    if overlap:
        warnings.append("Some lots contribute different wafers to both cohorts; lot-level dependence must not be treated as independent replication.")
    return {"series": series, "operation": operation, "recipes": {"a": recipe_a, "b": recipe_b},
            "window": {"start": _iso(begin), "end": _iso(finish)},
            "retest_policy": retest_policy, "criteria": criteria,
            "raw_row_count": raw_row_count, "window_row_count": window_row_count,
            "selected_row_count": matched + unmatched + excluded,
            "matched_count": matched, "unmatched_count": unmatched,
            "excluded_ambiguous_wafer_count": len(ambiguous),
            "excluded_ambiguous_measurement_count": excluded,
            "requires_crossover_decision": bool(ambiguous),
            "overlapping_lots": [{"lot_cd": lot[0], "lot_id": lot[1]} for lot in sorted(overlap)],
            "cohorts": {label: {"wafer_count": len(cohort_wafers[index]), "lot_count": len(cohort_lots[index])}
                        for index, label in enumerate(("a", "b"))},
            "metadata": _metadata(), "warnings": warnings}
