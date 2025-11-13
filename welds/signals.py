from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Weld, WeldInspection
from .services import close_repairs_for_weld, mark_repair_for_weld


@receiver(pre_save, sender=Weld)
def _store_previous_disposition(sender, instance: Weld, **kwargs):
    if not instance.pk:
        instance._previous_disposition = None
        return
    try:
        previous = sender.objects.only("disposition").get(pk=instance.pk)
        instance._previous_disposition = previous.disposition
    except sender.DoesNotExist:
        instance._previous_disposition = None


@receiver(post_save, sender=Weld)
def _handle_weld_disposition(sender, instance: Weld, created: bool, **kwargs):
    previous = getattr(instance, "_previous_disposition", None)
    current = instance.disposition
    if current == Weld.Disposition.REPAIR and previous != Weld.Disposition.REPAIR:
        mark_repair_for_weld(instance, manual_flag=True)
    elif (
        previous == Weld.Disposition.REPAIR
        and current != Weld.Disposition.REPAIR
        and previous is not None
    ):
        reason = f"Closed via weld disposition change to {current}"
        close_repairs_for_weld(instance, comment=reason)


def _inspection_requires_repair(inspection: WeldInspection) -> bool:
    if inspection.requires_repair:
        return True
    return inspection.result in {
        WeldInspection.Result.REPAIR,
        WeldInspection.Result.REJECTED,
    }


@receiver(post_save, sender=WeldInspection)
def _handle_inspection(sender, instance: WeldInspection, created: bool, **kwargs):
    if not _inspection_requires_repair(instance):
        return
    mark_repair_for_weld(
        instance.weld,
        flagged_at=instance.inspection_date,
        original_ndereport_ref=instance.report_reference or str(instance.pk),
        original_nderig=instance.nde_rig,
        defect_code=instance.defect_code,
        nde_type=instance.nde_type,
        manual_flag=False,
    )
