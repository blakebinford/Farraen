from __future__ import annotations

import re
from decimal import Decimal

from .models import Welder, Weld

_STENCIL_SPLIT_RE = re.compile(r"[;,\s]+")


def _split_stencils(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in _STENCIL_SPLIT_RE.split(value) if part.strip()}


def _percentage(part: int | Decimal, whole: int | Decimal) -> Decimal:
    if not whole:
        return Decimal("0")
    return (Decimal(part) / Decimal(whole)) * Decimal("100")


def _wps_labels_for_weld(weld: Weld) -> set[str]:
    labels: set[str] = set()
    for prefix in ("material1", "material2"):
        heat = getattr(weld, f"{prefix}_heat")
        if not heat:
            continue
        if heat.wps_number:
            labels.add(heat.wps_number)
        elif getattr(heat, "wps_document", None):
            labels.add(heat.wps_document.name)
    if not labels:
        labels.add("Unspecified")
    return labels


def get_project_weld_kpis(project) -> dict:
    weld_qs = (
        Weld.objects.filter(project=project)
        .select_related("material1_heat", "material2_heat", "nde_rig")
        .order_by("id")
    )
    welds = list(weld_qs)

    overall_total_inches = Decimal("0")
    overall_total_welds = len(welds)
    nde_completed = 0
    accepted_count = 0
    repair_count = 0
    cut_out_count = 0

    production_map: dict = {}
    wps_map: dict[str, dict[str, Decimal | int]] = {}
    nde_type_map: dict[str, dict[str, Decimal | int | str]] = {}
    repair_type_map: dict[str, dict[str, int]] = {}
    repair_rig_map: dict[int, dict[str, int | Decimal]] = {}

    records: list[dict] = []
    all_stencils: set[str] = set()

    for weld in welds:
        weld_inches = weld.weld_inches
        overall_total_inches += weld_inches

        is_repair = weld.disposition == Weld.Disposition.REPAIR
        is_accepted = weld.disposition == Weld.Disposition.ACCEPTED

        if weld.nde_type or weld.nde_date:
            nde_completed += 1
        if is_accepted:
            accepted_count += 1
        elif is_repair:
            repair_count += 1
        elif weld.disposition == Weld.Disposition.CUT_OUT:
            cut_out_count += 1

        stencils = {
            "root": _split_stencils(weld.welder_stencil_root_hotpass),
            "fill": _split_stencils(weld.welder_stencil_fill),
            "cap": _split_stencils(weld.welder_stencil_cap),
            "repair": _split_stencils(weld.welder_stencil_repair),
        }
        all_pass_stencils = (
            stencils["root"]
            | stencils["fill"]
            | stencils["cap"]
            | stencils["repair"]
        )
        stencils["all"] = all_pass_stencils
        all_stencils.update(all_pass_stencils)

        if weld.date_welded:
            day_summary = production_map.setdefault(
                weld.date_welded, {"weld_inches": Decimal("0"), "weld_count": 0}
            )
            day_summary["weld_inches"] += weld_inches
            day_summary["weld_count"] += 1

        for label in _wps_labels_for_weld(weld):
            stats = wps_map.setdefault(
                label,
                {"total_welds": 0, "repairs": 0, "accepted": 0},
            )
            stats["total_welds"] += 1
            if is_repair:
                stats["repairs"] += 1
            elif is_accepted:
                stats["accepted"] += 1

        if weld.nde_type:
            nde_stats = nde_type_map.setdefault(
                weld.nde_type,
                {
                    "nde_type": weld.nde_type,
                    "label": weld.get_nde_type_display(),
                    "total": 0,
                    "accepted": 0,
                    "repairs": 0,
                },
            )
            nde_stats["total"] += 1
            if is_repair:
                nde_stats["repairs"] += 1
            elif is_accepted:
                nde_stats["accepted"] += 1

        if is_repair:
            repair_label = (
                weld.get_repair_type_display()
                if weld.repair_type
                else "Unspecified"
            )
            repair_stats = repair_type_map.setdefault(
                repair_label, {"label": repair_label, "repair_count": 0}
            )
            repair_stats["repair_count"] += 1

            if weld.nde_rig_id:
                rig_stats = repair_rig_map.setdefault(
                    weld.nde_rig_id,
                    {
                        "nde_rig_id": weld.nde_rig_id,
                        "nde_rig_name": weld.nde_rig.name,
                        "repair_count": 0,
                        "weld_inches": Decimal("0"),
                    },
                )
                rig_stats["repair_count"] += 1
                rig_stats["weld_inches"] += weld_inches

        records.append(
            {
                "weld": weld,
                "weld_inches": weld_inches,
                "stencils": stencils,
                "is_repair": is_repair,
            }
        )

    if all_stencils:
        welder_lookup = {
            welder.stencil: welder
            for welder in Welder.objects.filter(
                org=project.org, stencil__in=all_stencils
            )
        }
    else:
        welder_lookup = {}

    welder_stats_map: dict[str, dict[str, int | Decimal]] = {}
    for record in records:
        stencils = record["stencils"]
        weld_inches = record["weld_inches"]
        is_repair = record["is_repair"]

        for stencil in stencils["all"]:
            stats = welder_stats_map.setdefault(
                stencil,
                {
                    "welder_stencil": stencil,
                    "weld_count": 0,
                    "repair_pass_count": 0,
                    "total_weld_inches": Decimal("0"),
                },
            )
            stats["weld_count"] += 1
            stats["total_weld_inches"] += weld_inches

        if is_repair:
            for stencil in stencils["repair"]:
                stats = welder_stats_map.setdefault(
                    stencil,
                    {
                        "welder_stencil": stencil,
                        "weld_count": 0,
                        "repair_pass_count": 0,
                        "total_weld_inches": Decimal("0"),
                    },
                )
                stats["repair_pass_count"] += 1

    welder_stats = []
    for stencil, stats in welder_stats_map.items():
        welder = welder_lookup.get(stencil)
        stats["welder_name"] = welder.name if welder else ""
        stats["repair_rate_percent"] = _percentage(
            stats["repair_pass_count"], stats["weld_count"]
        )
        welder_stats.append(stats)

    welder_stats.sort(
        key=lambda item: (
            -item["total_weld_inches"],
            item["welder_stencil"],
        )
    )

    wps_stats = []
    total_repairs_for_share = repair_count
    for label, stats in sorted(wps_map.items()):
        wps_stats.append(
            {
                "wps_label": label,
                "total_welds": stats["total_welds"],
                "repairs": stats["repairs"],
                "accepted": stats["accepted"],
                "repair_share_percent": _percentage(
                    stats["repairs"], total_repairs_for_share
                ),
            }
        )

    nde_stats = []
    for nde_type, stats in sorted(nde_type_map.items()):
        inspected = stats["repairs"] + stats["accepted"]
        nde_stats.append(
            {
                "nde_type": nde_type,
                "nde_label": stats["label"],
                "total_welds": stats["total"],
                "repairs": stats["repairs"],
                "accepted": stats["accepted"],
                "pass_rate_percent": _percentage(stats["accepted"], inspected),
            }
        )

    repair_type_stats = sorted(
        repair_type_map.values(),
        key=lambda x: (-x["repair_count"], x["label"]),
    )

    repair_nde_rig_stats = sorted(
        repair_rig_map.values(),
        key=lambda x: (-x["repair_count"], x["nde_rig_name"]),
    )

    time_series = [
        {
            "date": date,
            "weld_inches": values["weld_inches"],
            "weld_count": values["weld_count"],
        }
        for date, values in sorted(production_map.items())
    ]

    best_day = None
    worst_day = None
    if time_series:
        best_day = max(time_series, key=lambda item: item["weld_inches"])
        nonzero_days = [item for item in time_series if item["weld_inches"] > 0]
        if nonzero_days:
            worst_day = min(nonzero_days, key=lambda item: item["weld_inches"])

    days_with_welds = len(time_series)
    average_weld_inches_per_day = (
        overall_total_inches / Decimal(days_with_welds)
        if days_with_welds
        else Decimal("0")
    )

    overall = {
        "total_welds": overall_total_welds,
        "nde_completed": nde_completed,
        "accepted": accepted_count,
        "repairs": repair_count,
        "cut_out": cut_out_count,
        "project_repair_rate": _percentage(
            repair_count, repair_count + accepted_count
        ),
        "total_weld_inches": overall_total_inches,
        "average_weld_inches_per_weld": (
            overall_total_inches / Decimal(overall_total_welds)
            if overall_total_welds
            else Decimal("0")
        ),
        "average_weld_inches_per_day": average_weld_inches_per_day,
    }

    production = {
        "time_series": time_series,
        "best_day": best_day,
        "worst_day": worst_day,
        "days_with_welds": days_with_welds,
        "total_weld_inches": overall_total_inches,
        "total_weld_count": overall_total_welds,
    }

    return {
        "overall": overall,
        "wps_stats": wps_stats,
        "welder_stats": welder_stats,
        "production": production,
        "nde_stats": nde_stats,
        "repair_type_stats": repair_type_stats,
        "repair_nde_rig_stats": repair_nde_rig_stats,
    }


def build_weld_dashboard_chart_payload(kpis: dict) -> dict:
    def _to_float(value):
        return float(value) if isinstance(value, Decimal) else value

    wps_stats = kpis.get("wps_stats", [])
    welder_stats = kpis.get("welder_stats", [])
    production = kpis.get("production", {})
    time_series = production.get("time_series", [])

    total_repairs = kpis.get("overall", {}).get("repairs", 0)

    return {
        "repair_rate_by_wps": {
            "labels": [item["wps_label"] for item in wps_stats],
            "repair_rates": [
                _to_float(item["repair_share_percent"]) for item in wps_stats
            ],
            "repairs": [item["repairs"] for item in wps_stats],
            "total_repairs": total_repairs,
        },
        "repair_rate_by_welder": {
            "labels": [item["welder_stencil"] for item in welder_stats],
            "repair_rates": [
                _to_float(item["repair_rate_percent"]) for item in welder_stats
            ],
            "weld_counts": [item["weld_count"] for item in welder_stats],
            "repair_counts": [item["repair_pass_count"] for item in welder_stats],
        },
        "weld_inches_by_day": {
            "labels": [entry["date"].isoformat() for entry in time_series],
            "weld_inches": [_to_float(entry["weld_inches"]) for entry in time_series],
            "weld_counts": [entry["weld_count"] for entry in time_series],
        },
    }
