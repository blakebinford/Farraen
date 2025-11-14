from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from drive.models import FileNode, FileVersion

from .models import Weld, WeldInspection
from .services import close_repairs_for_weld, mark_repair_for_weld
from .tasks import process_mtr_fileversion, process_mtr_fileversion_sync


logger = logging.getLogger(__name__)


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


@receiver(post_save, sender=FileVersion)
def _handle_mtr_version(sender, instance: FileVersion, created: bool, **kwargs):
    if not created:
        return

    file_node = instance.file_node
    if file_node.doc_type != FileNode.DocType.MTR:
        return

    FileNode.objects.filter(pk=file_node.pk).update(
        mtr_approved=False,
        mtr_approved_by=None,
        mtr_approved_at=None,
        mtr_approved_version=None,
    )

    try:
        process_mtr_fileversion.delay(instance.pk)
    except Exception:
        logger.warning(
            "Failed to enqueue Celery task for MTR parsing; running synchronously.",
            exc_info=True,
            extra={"fileversion_id": instance.pk},
        )
        process_mtr_fileversion_sync(instance.pk)
