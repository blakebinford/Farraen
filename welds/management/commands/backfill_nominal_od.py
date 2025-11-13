import logging
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.db import transaction

from welds.models import NominalPipeOD, Weld

logger = logging.getLogger(__name__)

THREE_DECIMAL = Decimal("0.001")


def _normalize_wall(value):
    if value is None:
        return None
    try:
        dec = Decimal(value)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return dec.quantize(THREE_DECIMAL, rounding=ROUND_HALF_UP)


def _determine_wall_thickness(weld):
    candidates = [
        weld.wall_thickness_norm,
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


def _select_actual_od(weld):
    if weld.od is not None:
        return Decimal(weld.od)
    value = weld._select_outer_diameter()
    return Decimal(value) if value is not None else None


def _build_nominal_lookup(org_id):
    configs = list(
        NominalPipeOD.objects.filter(org_id=org_id).order_by("actual_od")
    )
    return configs


def _match_nominal(actual_od, configs):
    if actual_od is None:
        return None
    actual = Decimal(actual_od)
    best = None
    for config in configs:
        diff = abs(actual - config.actual_od)
        if diff <= config.tolerance:
            if best is None or diff < best[0]:
                best = (diff, config)
            elif best and diff == best[0]:
                # Prefer config with closer label ordering if tied on diff
                if config.actual_od < best[1].actual_od:
                    best = (diff, config)
    if best:
        return best[1].label
    return None


class Command(BaseCommand):
    help = "Populate nominal_od and wall_thickness_norm for existing welds"

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=int,
            dest="org_id",
            help="Limit updates to a specific organization ID",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Number of welds to update per batch",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Calculate updates without writing to the database",
        )

    def handle(self, *args, **options):
        org_id = options.get("org_id")
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        qs = Weld.objects.all().select_related(
            "project__org", "material1_heat", "material2_heat"
        )
        if org_id:
            qs = qs.filter(project__org_id=org_id)

        total = qs.count()
        updated = 0
        unmapped = Counter()
        logger.info(
            "Starting weld nominal OD backfill", extra={"total": total, "org_id": org_id}
        )

        org_cache = {}
        for start in range(0, total, batch_size):
            batch = list(qs.order_by("pk")[start : start + batch_size])
            updates = []
            for weld in batch:
                org = weld.project.org
                configs = org_cache.get(org.id)
                if configs is None:
                    configs = _build_nominal_lookup(org.id)
                    org_cache[org.id] = configs
                actual_od = _select_actual_od(weld)
                nominal_label = _match_nominal(actual_od, configs)
                if nominal_label is None and actual_od is not None:
                    unmapped[str(actual_od)] += 1
                    nominal_label = "Unspecified"
                wall_value = _determine_wall_thickness(weld)
                current_nominal = weld.nominal_od or None
                current_wall = weld.wall_thickness_norm or None
                if nominal_label == "Unspecified" and current_nominal:
                    # Keep existing specific label
                    nominal_label = current_nominal
                if (
                    nominal_label == current_nominal
                    and (wall_value == current_wall or (wall_value is None and current_wall is None))
                ):
                    continue
                weld.nominal_od = nominal_label
                weld.wall_thickness_norm = wall_value
                updates.append(weld)
            if updates and not dry_run:
                with transaction.atomic():
                    Weld.objects.bulk_update(
                        updates, ["nominal_od", "wall_thickness_norm"], batch_size=batch_size
                    )
                updated += len(updates)
            elif updates:
                updated += len(updates)

        if unmapped:
            logger.warning(
                "Some outer diameters did not match any nominal mapping",
                extra={"counts": dict(unmapped)},
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {total} welds. {'Would update' if dry_run else 'Updated'} {updated}."
            )
        )
*** End of File
