"""Analytics helpers powering the weld dashboard."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import ceil
from statistics import mean, median, pstdev
from typing import Iterable, List, Tuple

from django.db.models import Prefetch, Q

from .models import NominalPipeOD, Weld, WeldRepair


DecimalZero = Decimal("0")
DecimalOneThousand = Decimal("1000")
DecimalOne = Decimal("1")
TWO_PLACE = Decimal("0.01")
THREE_DECIMAL = Decimal("0.001")

CLUSTER_MIN_COUNT = 2


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeldLengthInfo:
    weld_id: int
    length: Decimal
    estimated: bool
    source: str
    basis: str


def _extract_primary_stencil(weld: Weld) -> str | None:
    if weld.primary_stencil:
        return weld.primary_stencil
    for field in (
        "welder_stencil_root_hotpass",
        "welder_stencil_fill",
        "welder_stencil_cap",
        "welder_stencil_repair",
    ):
        value = getattr(weld, field, "") or ""
        if value:
            parts = [part.strip() for part in value.split(",") if part.strip()]
            if parts:
                return parts[0]
    if weld.primary_welder_id:
        return weld.primary_welder.stencil
    return None


def _safe_decimal(value) -> Decimal:
    if value is None:
        return DecimalZero
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _average(values: Iterable[Decimal]) -> Decimal:
    vals = [value for value in values if value]
    if not vals:
        return DecimalZero
    return sum(vals, DecimalZero) / Decimal(len(vals))


def _collect_length_statistics(welds: Iterable[Weld]) -> tuple[Decimal, int, dict[str, tuple[Decimal, int]]]:
    measured_lengths: list[Decimal] = []
    stencil_lengths: dict[str, list[Decimal]] = defaultdict(list)
    for weld in welds:
        if weld.weld_length_inches is not None:
            length = _safe_decimal(weld.weld_length_inches)
            measured_lengths.append(length)
            stencil = _extract_primary_stencil(weld)
            if stencil:
                stencil_lengths[stencil].append(length)
    project_average = _average(measured_lengths)
    project_count = len(measured_lengths)
    stencil_stats: dict[str, tuple[Decimal, int]] = {}
    for stencil, values in stencil_lengths.items():
        stencil_stats[stencil] = (_average(values), len(values))
    return project_average, project_count, stencil_stats


def resolve_weld_lengths(welds: Iterable[Weld]) -> dict[int, WeldLengthInfo]:
    weld_list = list(welds)
    project_average, project_count, stencil_stats = _collect_length_statistics(weld_list)
    results: dict[int, WeldLengthInfo] = {}
    for weld in weld_list:
        if weld.weld_length_inches is not None:
            length = _safe_decimal(weld.weld_length_inches)
            source = weld.weld_length_source or Weld.WeldLengthSource.MEASURED
            basis = weld.weld_length_basis or "Recorded measurement"
            results[weld.pk] = WeldLengthInfo(
                weld_id=weld.pk,
                length=length,
                estimated=False,
                source=source,
                basis=basis,
            )
            continue

        # Outer diameter based calculation
        if weld.od is not None:
            length = _safe_decimal(weld.od) * Decimal("3.14")
            results[weld.pk] = WeldLengthInfo(
                weld_id=weld.pk,
                length=length,
                estimated=True,
                source=Weld.WeldLengthSource.OUTER_DIAMETER,
                basis="Derived from recorded outer diameter (OD × π)",
            )
            continue

        stencil = _extract_primary_stencil(weld)
        if stencil and stencil in stencil_stats and stencil_stats[stencil][0]:
            avg, count = stencil_stats[stencil]
            results[weld.pk] = WeldLengthInfo(
                weld_id=weld.pk,
                length=avg,
                estimated=True,
                source=Weld.WeldLengthSource.STENCIL_AVERAGE,
                basis=f"Stencil average based on {count} measured welds",
            )
            continue

        if project_average:
            results[weld.pk] = WeldLengthInfo(
                weld_id=weld.pk,
                length=project_average,
                estimated=True,
                source=Weld.WeldLengthSource.PROJECT_AVERAGE,
                basis=(
                    f"Project average based on {project_count} measured welds"
                    if project_count
                    else "Project average from historical data"
                ),
            )
            continue

        results[weld.pk] = WeldLengthInfo(
            weld_id=weld.pk,
            length=DecimalZero,
            estimated=True,
            source=Weld.WeldLengthSource.PROJECT_AVERAGE,
            basis="No weld length data available",
        )

    return results


def _normalize_wall(value) -> Decimal | None:
    if value is None:
        return None
    try:
        dec = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return dec.quantize(THREE_DECIMAL, rounding=ROUND_HALF_UP)


def _wall_thickness_for_weld(weld: Weld) -> Decimal | None:
    if weld.wall_thickness_norm is not None:
        return weld.wall_thickness_norm
    candidates = [
        weld.material1_wall_thickness_in,
        getattr(weld.material1_heat, "wall_thickness_in", None)
        if getattr(weld, "material1_heat", None)
        else None,
        weld.material2_wall_thickness_in,
        getattr(weld.material2_heat, "wall_thickness_in", None)
        if getattr(weld, "material2_heat", None)
        else None,
    ]
    for value in candidates:
        normalized = _normalize_wall(value)
        if normalized is not None:
            return normalized
    return None


def _nominal_label_for_weld(weld: Weld, configs: list[NominalPipeOD]) -> str:
    if weld.nominal_od:
        return weld.nominal_od
    actual = weld.od if weld.od is not None else weld._select_outer_diameter()
    if actual is None:
        return "Unspecified"
    try:
        actual_dec = Decimal(actual)
    except (TypeError, ValueError, InvalidOperation):
        return "Unspecified"
    best = None
    for config in configs:
        diff = abs(actual_dec - config.actual_od)
        if diff <= config.tolerance:
            if best is None or diff < best[0]:
                best = (diff, config)
    if best:
        return best[1].label
    return "Unspecified"


def _default_cluster_stats():
    return {"count": 0, "weld_inches": DecimalZero, "weld_ids": set()}


def _increment_cluster(container, key, weld_id: int, length: Decimal):
    stats = container[key]
    stats["count"] += 1
    seen = stats["weld_ids"]
    if weld_id not in seen:
        stats["weld_inches"] += length
        seen.add(weld_id)


def _group_daily(values: Iterable[Tuple[date, Decimal, Weld]]) -> dict[date, dict[str, Decimal]]:
    series: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"weld_inches": DecimalZero, "weld_count": 0}
    )
    for weld_date, length, weld in values:
        if weld_date is None:
            continue
        entry = series[weld_date]
        entry["weld_inches"] += length
        entry["weld_count"] += 1
    return series


def _daterange(start: date, end: date) -> Iterable[date]:
    if start > end:
        return []
    delta = end - start
    for i in range(delta.days + 1):
        yield start + timedelta(days=i)


def _sorted_dates(series: dict[date, dict]) -> list[date]:
    return sorted([d for d in series.keys() if d is not None])


def _running_sum(values: List[Tuple[date, Decimal]]) -> List[Tuple[date, Decimal]]:
    running = DecimalZero
    results: List[Tuple[date, Decimal]] = []
    for weld_date, amount in values:
        running += amount
        results.append((weld_date, running))
    return results


def _mean_and_stddev(data: List[Decimal]) -> Tuple[Decimal, Decimal]:
    if not data:
        return DecimalZero, DecimalZero
    if len(data) == 1:
        return data[0], DecimalZero
    float_values = [float(value) for value in data]
    mu = Decimal(str(mean(float_values)))
    sigma = Decimal(str(pstdev(float_values)))
    return mu, sigma


def _to_calendar_days(workdays: int, workdays_per_week: int | None) -> int:
    if not workdays_per_week:
        return workdays
    return ceil((workdays / workdays_per_week) * 7)


def _planned_schedule(
    start: date,
    daily_inches: Decimal,
    total_inches: Decimal,
    workdays_per_week: int | None,
) -> list[tuple[date, Decimal]]:
    if not daily_inches or daily_inches <= DecimalZero:
        return []
    current_date = start
    remaining = total_inches
    cumulative = DecimalZero
    results: list[tuple[date, Decimal]] = []
    if workdays_per_week and workdays_per_week > 0:
        workday_position = 1
    else:
        workday_position = 0
    while remaining > DecimalZero:
        cumulative += daily_inches
        remaining = max(total_inches - cumulative, DecimalZero)
        results.append((current_date, min(cumulative, total_inches)))
        if workdays_per_week and workdays_per_week > 0:
            if workday_position >= workdays_per_week:
                days_to_skip = max(7 - workdays_per_week, 0)
                current_date += timedelta(days=days_to_skip + 1)
                workday_position = 1
            else:
                current_date += timedelta(days=1)
                workday_position += 1
        else:
            current_date += timedelta(days=1)
    return results


def build_dashboard_analytics(project, filters: dict) -> dict:
    """Assemble the analytics payload for the weld dashboard.

    The returned dictionary exposes production series (daily, cumulative,
    per-welder), planner metadata, repair rate calculations (daily and rolling),
    clustering summaries, and helper collections for the front-end filters.
    """
    weld_qs = (
        Weld.objects.filter(project=project)
        .select_related(
            "primary_welder",
            "material1_heat",
            "material2_heat",
            "wps_document",
        )
        .prefetch_related(
            Prefetch(
                "repairs",
                queryset=WeldRepair.objects.order_by("flagged_at", "id"),
            )
        )
        .order_by("weld_date", "pk")
    )

    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        weld_qs = weld_qs.filter(
            Q(weld_date__gte=start_date)
            | (Q(weld_date__isnull=True) & Q(date_welded__gte=start_date))
        )
    if end_date:
        weld_qs = weld_qs.filter(
            Q(weld_date__lte=end_date)
            | (Q(weld_date__isnull=True) & Q(date_welded__lte=end_date))
        )

    welder_filter = filters.get("welder_ids") or []
    if isinstance(welder_filter, (str, int)):
        welder_ids = []
        try:
            welder_ids.append(int(welder_filter))
        except (TypeError, ValueError):
            welder_ids = []
    else:
        welder_ids = []
        for value in welder_filter:
            try:
                welder_ids.append(int(value))
            except (TypeError, ValueError):
                continue
    if welder_ids:
        weld_qs = weld_qs.filter(primary_welder_id__in=welder_ids)

    stencil_id = filters.get("stencil_id")
    if stencil_id:
        weld_qs = weld_qs.filter(
            Q(primary_stencil=stencil_id)
            | Q(welder_stencil_root_hotpass__icontains=stencil_id)
            | Q(welder_stencil_fill__icontains=stencil_id)
            | Q(welder_stencil_cap__icontains=stencil_id)
            | Q(welder_stencil_repair__icontains=stencil_id)
        )

    heat_number = filters.get("heat_number")
    if heat_number:
        weld_qs = weld_qs.filter(heat_number=heat_number)

    pipe_size = filters.get("pipe_size")
    if pipe_size:
        weld_qs = weld_qs.filter(pipe_size=pipe_size)

    od = filters.get("od")
    if od is not None:
        weld_qs = weld_qs.filter(od=od)

    wps_id = filters.get("wps_id")
    if wps_id:
        weld_qs = weld_qs.filter(wps_document_id=wps_id)

    welds = list(weld_qs)
    length_info = resolve_weld_lengths(welds)

    daily_entries: list[tuple[date, Decimal, Weld]] = []
    welder_daily: dict[int, dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

    for weld in welds:
        info = length_info.get(weld.pk)
        length = info.length if info else DecimalZero
        weld_date = weld.weld_date or weld.date_welded
        daily_entries.append((weld_date, length, weld))
        if weld.primary_welder_id:
            welder_daily[weld.primary_welder_id][weld_date] += length

    daily_map = _group_daily(daily_entries)
    sorted_daily_dates = _sorted_dates(daily_map)
    daily_series = []
    for day in sorted_daily_dates:
        entry = daily_map[day]
        daily_series.append(
            {
                "date": day,
                "weld_inches": entry["weld_inches"],
                "weld_count": entry["weld_count"],
            }
        )

    cumulative_series = _running_sum(
        [(day, daily_map[day]["weld_inches"]) for day in sorted_daily_dates]
    )
    cumulative_payload = [
        {"date": day, "weld_inches": total} for day, total in cumulative_series
    ]

    welder_series = {}
    for welder_id, series in welder_daily.items():
        sorted_dates = sorted([d for d in series.keys() if d is not None])
        values = []
        running = DecimalZero
        for day in sorted_dates:
            running += series[day]
            values.append(
                {
                    "date": day,
                    "daily_inches": series[day],
                    "cumulative_inches": running,
                }
            )
        welder_series[welder_id] = values

    all_welders_daily = []
    for day in sorted_daily_dates:
        total_for_day = daily_map[day]["weld_inches"]
        all_welders_daily.append(
            {
                "date": day,
                "weld_inches_total": total_for_day.quantize(
                    TWO_PLACE, rounding=ROUND_HALF_UP
                ),
            }
        )

    welder_median_daily = []
    for day in sorted_daily_dates:
        day_values = [
            series.get(day)
            for series in welder_daily.values()
            if day in series and series.get(day) is not None
        ]
        if day_values:
            float_values = [float(value) for value in day_values]
            median_value = Decimal(str(median(float_values)))
        else:
            median_value = DecimalZero
        welder_median_daily.append(
            {
                "date": day,
                "median_daily_inches": median_value.quantize(
                    TWO_PLACE, rounding=ROUND_HALF_UP
                ),
            }
        )

    total_weld_inches = sum((entry["weld_inches"] for entry in daily_series), DecimalZero)
    project_total_scope = _safe_decimal(project.project_total_weld_inches) or total_weld_inches

    # Planner inputs & planned series
    project_average, project_count, _ = _collect_length_statistics(welds)
    planner_average_basis = (
        f"Computed from {project_count} measured welds"
        if project_count
        else "No recorded weld length measurements"
    )
    planned_daily_inches = DecimalZero
    planner_basis = None
    recorded_planned_inches = _safe_decimal(project.planned_weld_inches_per_workday)

    # Prefer the explicit planned weld inches field when available.
    if recorded_planned_inches and recorded_planned_inches > DecimalZero:
        planned_daily_inches = recorded_planned_inches
        planner_basis = "User-defined planned weld inches per workday"
    elif project.planned_welds_per_workday:
        legacy_planned = _safe_decimal(project.planned_welds_per_workday)
        if project_average and project_average > DecimalZero:
            planned_daily_inches = legacy_planned * project_average
            planner_basis = (
                "Converted from planned weld count × project average weld length"
            )
        else:
            planner_basis = (
                "Legacy planned weld count available but no measured weld lengths to convert."
            )
            planned_daily_inches = DecimalZero
    else:
        planner_basis = "No planner input configured"

    if planned_daily_inches and planned_daily_inches > DecimalZero:
        planned_daily_inches = planned_daily_inches.quantize(TWO_PLACE, rounding=ROUND_HALF_UP)
    else:
        planned_daily_inches = DecimalZero

    planned_start = (
        project.planned_start_date
        or (sorted_daily_dates[0] if sorted_daily_dates else date.today())
    )
    planned_series = _planned_schedule(
        planned_start,
        planned_daily_inches,
        project_total_scope,
        project.workdays_per_week,
    )
    planned_payload = [
        {"date": day, "weld_inches": cumulative} for day, cumulative in planned_series
    ]

    # Forecast
    recent_days = daily_series[-14:] if len(daily_series) > 14 else daily_series
    recent_daily_inches = [entry["weld_inches"] for entry in recent_days if entry["weld_inches"]]
    recent_mean, recent_stddev = _mean_and_stddev(recent_daily_inches)
    remaining_inches = max(project_total_scope - (cumulative_payload[-1]["weld_inches"] if cumulative_payload else DecimalZero), DecimalZero)
    if recent_mean:
        expected_workdays = ceil(remaining_inches / recent_mean) if remaining_inches else 0
    else:
        expected_workdays = 0
    calendar_days = _to_calendar_days(expected_workdays, project.workdays_per_week)
    forecast_start = sorted_daily_dates[-1] if sorted_daily_dates else planned_start
    projected_completion_date = forecast_start + timedelta(days=calendar_days) if calendar_days else forecast_start

    optimistic_days = max(expected_workdays - int(recent_stddev), 0)
    pessimistic_days = expected_workdays + int(recent_stddev)
    optimistic_date = forecast_start + timedelta(days=_to_calendar_days(optimistic_days, project.workdays_per_week)) if optimistic_days else forecast_start
    pessimistic_date = forecast_start + timedelta(days=_to_calendar_days(pessimistic_days, project.workdays_per_week)) if pessimistic_days else forecast_start

    forecast_series = []
    if recent_mean and cumulative_payload:
        running = cumulative_payload[-1]["weld_inches"]
        current_date = forecast_start
        workdays_remaining = remaining_inches
        while workdays_remaining > DecimalZero:
            running += recent_mean
            workdays_remaining = max(project_total_scope - running, DecimalZero)
            current_date += timedelta(days=1)
            forecast_series.append({"date": current_date, "weld_inches": min(running, project_total_scope)})

    # Repairs
    repair_qs = WeldRepair.objects.filter(weld__in=[weld.pk for weld in welds])
    repair_qs = repair_qs.filter(flagged_at__isnull=False)
    if start_date:
        repair_qs = repair_qs.filter(flagged_at__gte=start_date)
    if end_date:
        repair_qs = repair_qs.filter(flagged_at__lte=end_date)
    repairs = list(
        repair_qs.select_related(
            "weld",
            "weld__primary_welder",
            "weld__wps_document",
            "weld__nde_rig",
        )
    )

    repairs_by_day: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"repairs": 0, "weld_inches": DecimalZero}
    )
    weld_length_counted: set[tuple[date, int]] = set()
    for repair in repairs:
        weld = repair.weld
        info = length_info.get(weld.pk)
        length = info.length if info else DecimalZero
        event_date = repair.flagged_at
        if event_date is None:
            continue
        entry = repairs_by_day[event_date]
        entry["repairs"] += 1
        key = (event_date, weld.pk)
        if key not in weld_length_counted:
            entry["weld_inches"] += length
            weld_length_counted.add(key)

    repair_rate_series = []
    rolling_rates: list[Decimal] = []
    sorted_repair_dates = sorted(repairs_by_day.keys())
    for idx, day in enumerate(sorted_repair_dates):
        entry = repairs_by_day[day]
        inches = entry["weld_inches"]
        repairs_count = entry["repairs"]
        denominator = inches if inches else DecimalOne
        rate = (
            (Decimal(repairs_count) / denominator) * DecimalOneThousand
            if denominator
            else DecimalZero
        )
        rate = rate.quantize(TWO_PLACE, rounding=ROUND_HALF_UP)
        rolling_rates.append(rate)
        window = rolling_rates[max(0, idx - 6) : idx + 1]
        rolling_average = DecimalZero
        if window:
            rolling_average = sum(window, DecimalZero) / Decimal(len(window))
        repair_rate_series.append(
            {
                "date": day,
                "repairs": repairs_count,
                "weld_inches": inches.quantize(TWO_PLACE, rounding=ROUND_HALF_UP),
                "rate_per_1000_inches": rate,
                "rolling_rate_7d": rolling_average.quantize(
                    TWO_PLACE, rounding=ROUND_HALF_UP
                ),
                "weld_inches_zero": inches == DecimalZero,
            }
        )

    total_repairs = len(repairs)
    normalized_repair_rate = (
        (Decimal(total_repairs) / total_weld_inches) * DecimalOneThousand
        if total_weld_inches
        else DecimalZero
    )
    normalized_repair_rate = normalized_repair_rate.quantize(
        TWO_PLACE, rounding=ROUND_HALF_UP
    )

    if repair_rate_series:
        current_rate = repair_rate_series[-1]["rate_per_1000_inches"]
        previous_rate = (
            repair_rate_series[-2]["rate_per_1000_inches"]
            if len(repair_rate_series) > 1
            else None
        )
        if previous_rate is None:
            direction_symbol = "—"
        elif current_rate > previous_rate:
            direction_symbol = "▲"
        elif current_rate < previous_rate:
            direction_symbol = "▼"
        else:
            direction_symbol = "➖"
        repair_rate_summary = {
            "current_rate_per_1000_inches": current_rate,
            "previous_rate_per_1000_inches": previous_rate,
            "direction": direction_symbol,
        }
    else:
        repair_rate_summary = {
            "current_rate_per_1000_inches": None,
            "previous_rate_per_1000_inches": None,
            "direction": "—",
        }

    # Clustering
    nominal_configs = NominalPipeOD.configs_for_org(project.org_id)
    single_clusters = {
        "heat_number": defaultdict(_default_cluster_stats),
        "welder": defaultdict(_default_cluster_stats),
        "wps": defaultdict(_default_cluster_stats),
        "nominal_od_wall": defaultdict(_default_cluster_stats),
    }
    pair_clusters = {
        "welder_wps": defaultdict(_default_cluster_stats),
        "nde_welder": defaultdict(_default_cluster_stats),
        "nde_nominal": defaultdict(_default_cluster_stats),
    }

    for repair in repairs:
        weld = repair.weld
        info = length_info.get(weld.pk)
        length = info.length if info else DecimalZero
        heat_label = weld.heat_number or "Unspecified"
        welder_label = getattr(weld.primary_welder, "stencil", None) or "Unspecified"
        wps_label = getattr(weld.wps_document, "name", None) or "Unspecified"
        nde_label = (
            repair.original_nderig
            or getattr(getattr(weld, "nde_rig", None), "name", None)
            or "Unspecified"
        )
        wall_value = _wall_thickness_for_weld(weld)
        nominal_label = _nominal_label_for_weld(weld, nominal_configs) or "Unspecified"
        nominal_key = (nominal_label, wall_value)

        _increment_cluster(single_clusters["heat_number"], heat_label, weld.pk, length)
        _increment_cluster(single_clusters["welder"], welder_label, weld.pk, length)
        _increment_cluster(single_clusters["wps"], wps_label, weld.pk, length)
        _increment_cluster(single_clusters["nominal_od_wall"], nominal_key, weld.pk, length)

        pair_key_welder_wps = (welder_label, wps_label)
        pair_key_nde_welder = (nde_label, welder_label)
        pair_key_nde_nominal = (nde_label, nominal_label, wall_value)
        _increment_cluster(pair_clusters["welder_wps"], pair_key_welder_wps, weld.pk, length)
        _increment_cluster(pair_clusters["nde_welder"], pair_key_nde_welder, weld.pk, length)
        _increment_cluster(pair_clusters["nde_nominal"], pair_key_nde_nominal, weld.pk, length)

    def _serialize_cluster_items(items, *, formatter):
        data = []
        for key, stats in items.items():
            count = stats["count"]
            if count < CLUSTER_MIN_COUNT:
                continue
            inches = stats["weld_inches"]
            denominator = inches if inches else DecimalOne
            rate = (
                (Decimal(count) / denominator) * DecimalOneThousand
                if denominator
                else DecimalZero
            )
            payload = formatter(key)
            payload.update(
                {
                    "count": count,
                    "weld_inches": inches.quantize(TWO_PLACE, rounding=ROUND_HALF_UP),
                    "repair_rate_per_1000_inches": rate.quantize(
                        TWO_PLACE, rounding=ROUND_HALF_UP
                    ),
                }
            )
            data.append(payload)
        data.sort(key=lambda item: (-item["repair_rate_per_1000_inches"], -item["count"]))
        return data[:10]

    clustering_payload = {
        "heat_number": _serialize_cluster_items(
            single_clusters["heat_number"], formatter=lambda key: {"key": key, "label": key}
        ),
        "welder": _serialize_cluster_items(
            single_clusters["welder"], formatter=lambda key: {"key": key, "label": key}
        ),
        "wps": _serialize_cluster_items(
            single_clusters["wps"], formatter=lambda key: {"key": key, "label": key}
        ),
        "nominal_od_wall": _serialize_cluster_items(
            single_clusters["nominal_od_wall"],
            formatter=lambda key: {
                "key": json.dumps(
                    {
                        "nominal_od": key[0],
                        "wall_thickness_norm": str(key[1]) if key[1] is not None else None,
                    }
                ),
                "label": f"{key[0]} × {format(key[1], '.3f') if key[1] is not None else '—'}",
                "nominal_od": key[0],
                "wall_thickness_norm": str(key[1]) if key[1] is not None else None,
            },
        ),
    }

    pair_clustering_payload = {
        "welder_wps": _serialize_cluster_items(
            pair_clusters["welder_wps"],
            formatter=lambda key: {
                "key": json.dumps({"welder": key[0], "wps": key[1]}),
                "label": f"Welder {key[0]} × WPS {key[1]}",
                "welder": key[0],
                "wps": key[1],
            },
        ),
        "nde_welder": _serialize_cluster_items(
            pair_clusters["nde_welder"],
            formatter=lambda key: {
                "key": json.dumps({"nde_rig": key[0], "welder": key[1]}),
                "label": f"NDE {key[0]} × Welder {key[1]}",
                "nde_rig": key[0],
                "welder": key[1],
            },
        ),
        "nde_nominal": _serialize_cluster_items(
            pair_clusters["nde_nominal"],
            formatter=lambda key: {
                "key": json.dumps(
                    {
                        "nde_rig": key[0],
                        "nominal_od": key[1],
                        "wall_thickness_norm": str(key[2]) if key[2] is not None else None,
                    }
                ),
                "label": (
                    f"NDE {key[0]} × {key[1]} × {format(key[2], '.3f') if key[2] is not None else '—'}"
                ),
                "nde_rig": key[0],
                "nominal_od": key[1],
                "wall_thickness_norm": str(key[2]) if key[2] is not None else None,
            },
        ),
    }

    heatmap_payload = []
    for (welder_label, wps_label), stats in pair_clusters["welder_wps"].items():
        count = stats["count"]
        if count < CLUSTER_MIN_COUNT:
            continue
        inches = stats["weld_inches"]
        denominator = inches if inches else DecimalOne
        rate = (
            (Decimal(count) / denominator) * DecimalOneThousand
            if denominator
            else DecimalZero
        )
        heatmap_payload.append(
            {
                "key": json.dumps({"welder": welder_label, "wps": wps_label}),
                "welder": welder_label,
                "wps": wps_label,
                "count": count,
                "weld_inches": inches.quantize(TWO_PLACE, rounding=ROUND_HALF_UP),
                "repair_rate_per_1000_inches": rate.quantize(
                    TWO_PLACE, rounding=ROUND_HALF_UP
                ),
            }
        )

    welder_options = []
    welder_ids_seen = set()
    for weld in welds:
        if weld.primary_welder_id and weld.primary_welder_id not in welder_ids_seen:
            welder_ids_seen.add(weld.primary_welder_id)
            welder_options.append(
                {
                    "id": weld.primary_welder_id,
                    "stencil": weld.primary_welder.stencil,
                    "name": weld.primary_welder.name,
                }
            )
    welder_options.sort(key=lambda item: (item["stencil"], item["name"]))

    stencil_options = sorted(
        {
            weld.primary_stencil
            for weld in welds
            if getattr(weld, "primary_stencil", None)
        }
    )

    planner_inputs = {
        "planned_weld_inches_per_workday": project.planned_weld_inches_per_workday,
        "planned_welds_per_workday": project.planned_welds_per_workday,
        "workdays_per_week": project.workdays_per_week,
        "project_total_weld_inches": project.project_total_weld_inches,
        "planned_start_date": planned_start,
        "project_average_weld_length": project_average,
        "project_average_basis": planner_average_basis,
        "planned_daily_weld_inches": planned_daily_inches,
        "basis": planner_basis,
        "scope_is_estimated": project.project_total_weld_inches is None,
        "total_weld_inches_logged": total_weld_inches,
        "project_average_sample_size": project_count,
    }

    percent_complete = (
        (cumulative_payload[-1]["weld_inches"] / project_total_scope) * Decimal("100")
        if cumulative_payload and project_total_scope
        else DecimalZero
    )

    forecast_summary = {
        "projected_completion_date": projected_completion_date,
        "optimistic_date": optimistic_date,
        "pessimistic_date": pessimistic_date,
        "recent_mean_daily_inches": recent_mean,
        "recent_stddev": recent_stddev,
        "remaining_weld_inches": remaining_inches,
    }

    return {
        "daily_production": daily_series,
        "cumulative_production": cumulative_payload,
        "welder_series": welder_series,
        "all_welders_daily": all_welders_daily,
        "welder_median_daily": welder_median_daily,
        "planned_series": planned_payload,
        "forecast_series": forecast_series,
        "forecast_summary": forecast_summary,
        "normalized_repair_rate": normalized_repair_rate,
        "repair_rate_series": repair_rate_series,
        "repair_rate_summary": repair_rate_summary,
        "clustering": clustering_payload,
        "pair_clustering": pair_clustering_payload,
        "heatmap": heatmap_payload,
        "planner_inputs": planner_inputs,
        "percent_complete": percent_complete,
        "total_weld_inches": total_weld_inches,
        "project_total_scope": project_total_scope,
        "length_info": {weld_id: info for weld_id, info in length_info.items()},
        "welder_options": welder_options,
        "stencil_options": stencil_options,
    }


def build_drilldown(
    project,
    filters: dict,
    cluster_type: str,
    cluster_filters: dict | None = None,
    *,
    page: int = 1,
    page_size: int | None = 50,
    sort: str | None = None,
) -> dict:
    cluster_filters = cluster_filters or {}
    cluster_type = cluster_type or ""

    qs = WeldRepair.objects.filter(weld__project=project, flagged_at__isnull=False)
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        qs = qs.filter(flagged_at__gte=start_date)
    if end_date:
        qs = qs.filter(flagged_at__lte=end_date)

    def _filter_heat(qs, value):
        if not value or value == "Unspecified":
            return qs.filter(Q(weld__heat_number__isnull=True) | Q(weld__heat_number__exact=""))
        return qs.filter(weld__heat_number=value)

    def _filter_welder(qs, value):
        if not value or value == "Unspecified":
            return qs.filter(
                Q(weld__primary_welder__isnull=True)
                & (Q(weld__primary_stencil__isnull=True) | Q(weld__primary_stencil__exact=""))
            )
        return qs.filter(
            Q(weld__primary_welder__stencil=value)
            | Q(weld__primary_stencil=value)
            | Q(repair_stencil=value)
        )

    def _filter_wps(qs, value):
        if not value or value == "Unspecified":
            return qs.filter(
                Q(weld__wps_document__isnull=True)
                | Q(weld__wps_document__name__isnull=True)
                | Q(weld__wps_document__name__exact="")
            )
        return qs.filter(weld__wps_document__name=value)

    def _filter_nominal(qs, nominal, wall):
        if not nominal or nominal == "Unspecified":
            qs = qs.filter(
                Q(weld__nominal_od__isnull=True) | Q(weld__nominal_od="Unspecified")
            )
        else:
            qs = qs.filter(weld__nominal_od=nominal)
        if wall in (None, "", "Unspecified"):
            qs = qs.filter(Q(weld__wall_thickness_norm__isnull=True))
        else:
            try:
                wall_value = Decimal(wall)
            except (InvalidOperation, TypeError, ValueError):
                pass
            else:
                qs = qs.filter(weld__wall_thickness_norm=wall_value)
        return qs

    def _filter_nde(qs, value):
        if not value or value == "Unspecified":
            return qs.filter(
                (Q(original_nderig__isnull=True) | Q(original_nderig__exact=""))
                & Q(weld__nde_rig__isnull=True)
            )
        return qs.filter(
            Q(original_nderig=value)
            | (
                (Q(original_nderig__isnull=True) | Q(original_nderig__exact=""))
                & Q(weld__nde_rig__name=value)
            )
        )

    def _get_value(*keys):
        for key in keys:
            if key in cluster_filters and cluster_filters[key] not in (None, ""):
                return cluster_filters[key]
        return None

    if cluster_type == "heat_number":
        qs = _filter_heat(qs, _get_value("heat_number", "key"))
    elif cluster_type == "welder":
        qs = _filter_welder(qs, _get_value("welder", "stencil", "key"))
    elif cluster_type == "wps":
        qs = _filter_wps(qs, _get_value("wps", "key"))
    elif cluster_type == "nominal_od_wall":
        qs = _filter_nominal(
            qs,
            _get_value("nominal_od", "key"),
            _get_value("wall_thickness_norm"),
        )
    elif cluster_type == "welder_wps":
        qs = _filter_welder(qs, _get_value("welder", "stencil"))
        qs = _filter_wps(qs, _get_value("wps"))
    elif cluster_type == "nde_welder":
        qs = _filter_nde(qs, _get_value("nde_rig", "nde"))
        qs = _filter_welder(qs, _get_value("welder", "stencil"))
    elif cluster_type == "nde_nominal":
        qs = _filter_nde(qs, _get_value("nde_rig", "nde"))
        qs = _filter_nominal(
            qs,
            _get_value("nominal_od"),
            _get_value("wall_thickness_norm"),
        )
    elif cluster_type == "heat_number_wps":
        qs = _filter_heat(qs, _get_value("heat_number"))
        qs = _filter_wps(qs, _get_value("wps"))

    sort_mapping = {
        "flagged_at": "flagged_at",
        "-flagged_at": "-flagged_at",
        "repair_date": "repair_date",
        "-repair_date": "-repair_date",
        "weld_id": "weld__weld_id",
        "-weld_id": "-weld__weld_id",
    }
    ordering = sort_mapping.get(sort or "", "-flagged_at")

    total_count = qs.count()
    if page_size:
        safe_page = max(page, 1)
        page_size = max(page_size, 1)
        offset = (safe_page - 1) * page_size
        page_qs = qs.order_by(ordering)[offset : offset + page_size]
    else:
        page_qs = qs.order_by(ordering)
        page_size = total_count or 1
        page = 1

    page_repairs = list(
        page_qs.select_related(
            "weld",
            "weld__primary_welder",
            "weld__wps_document",
            "weld__nde_rig",
        )
    )
    welds_for_lengths = [repair.weld for repair in page_repairs]
    length_info = resolve_weld_lengths(welds_for_lengths)

    rows: list[dict] = []
    for repair in page_repairs:
        weld = repair.weld
        info = length_info.get(weld.pk)
        length = info.length if info else DecimalZero
        rows.append(
            {
                "id": repair.pk,
                "weld_pk": weld.pk,
                "weld_id": weld.weld_id,
                "flagged_at": repair.flagged_at,
                "repair_date": repair.repair_date,
                "repair_stencil": repair.repair_stencil,
                "welder_name": getattr(weld.primary_welder, "name", ""),
                "welder_stencil": getattr(weld.primary_welder, "stencil", ""),
                "nominal_od": weld.nominal_od,
                "wall_thickness_norm": weld.wall_thickness_norm,
                "weld_inches": length,
                "length_estimated": bool(info.estimated) if info else True,
                "length_basis": info.basis if info else "",
                "nde_rig": repair.original_nderig
                or getattr(getattr(weld, "nde_rig", None), "name", ""),
                "defect_code_snapshot": repair.defect_code_snapshot,
                "attempt_count": repair.attempt_count,
                "comments": repair.comments,
                "status": repair.status,
            }
        )

    return {
        "results": rows,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
    }

