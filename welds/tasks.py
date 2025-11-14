"""Celery tasks for welds application."""
from __future__ import annotations

import io
import logging
from decimal import Decimal, InvalidOperation
from typing import Iterable, List

try:  # pragma: no cover - fallback for environments without Celery
    from pipeledger.celery import shared_task
except ImportError:  # pragma: no cover
    def shared_task(*decorator_args, **decorator_kwargs):
        def decorator(func):
            def delay(*args, **kwargs):
                return func(*args, **kwargs)

            def apply(args=None, kwargs=None):
                call_args = args or ()
                call_kwargs = kwargs or {}
                result = func(*call_args, **call_kwargs)

                class SimpleResult:
                    def get(self, *a, **k):
                        return result

                return SimpleResult()

            func.delay = delay
            func.apply = apply
            func.run = func
            return func

        if decorator_args and callable(decorator_args[0]):
            return decorator(decorator_args[0])
        return decorator
from django.db import transaction

from drive.models import FileNode, FileVersion

from .models import MaterialHeatDraft
from .openai_client import MTRParsingError, parse_mtr_text_with_openai

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 10


def _decimal_or_none(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        logger.debug("Failed to coerce decimal value", extra={"value": value})
        return None


def _get_pdf_document(fileversion: FileVersion):
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - dependency missing path
        raise RuntimeError("PyMuPDF (fitz) is required for MTR parsing") from exc

    return fitz.open(fileversion.blob.path)


def _ocr_pixmap(pixmap) -> str:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:  # pragma: no cover - dependency missing path
        raise RuntimeError("pytesseract and pillow are required for OCR fallback") from exc

    mode = "RGBA" if pixmap.alpha else "RGB"
    image = Image.frombytes(mode, [pixmap.width, pixmap.height], pixmap.samples)
    if mode == "RGBA":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return pytesseract.image_to_string(Image.open(buffer))


def _extract_page_texts(fileversion: FileVersion) -> List[tuple[int, str]]:
    texts: List[tuple[int, str]] = []
    with _get_pdf_document(fileversion) as doc:
        for index in range(doc.page_count):
            page = doc.load_page(index)
            text = page.get_text("text") or ""
            if not text.strip():
                try:
                    text = _ocr_pixmap(page.get_pixmap())
                except Exception:
                    logger.exception(
                        "OCR fallback failed for MTR page",
                        extra={"fileversion_id": fileversion.id, "page_index": index},
                    )
                    text = ""
            texts.append((index + 1, text))
    return texts


def _chunk_pages(pages: List[tuple[int, str]], chunk_size: int = _CHUNK_SIZE) -> Iterable[List[tuple[int, str]]]:
    chunk: List[tuple[int, str]] = []
    for page in pages:
        chunk.append(page)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _merge_materials(chunks: Iterable[dict]) -> List[dict]:
    merged: List[dict] = []
    for chunk in chunks:
        materials = chunk.get("materials", [])
        if not isinstance(materials, list):
            continue
        for material in materials:
            if not isinstance(material, dict):
                continue
            merged.append(material)
    return merged


def _serialize_pages(pages: List[tuple[int, str]]) -> str:
    parts = []
    for page_no, text in pages:
        parts.append(f"[Page {page_no}]\n{text}")
    return "\n\n".join(parts)


_suppressed_dependency_errors: set[str] = set()


def _material_payloads_from_text(fileversion: FileVersion) -> List[dict]:
    try:
        pages = _extract_page_texts(fileversion)
    except RuntimeError as exc:
        message = str(exc)
        extra = {"fileversion_id": fileversion.id}
        if message not in _suppressed_dependency_errors:
            logger.exception(
                "Unable to extract text from MTR for parsing",
                extra=extra,
            )
            _suppressed_dependency_errors.add(message)
        else:
            logger.debug(
                "Unable to extract text from MTR for parsing (suppressed repeat): %s",
                message,
                extra=extra,
            )
        return []
    if not pages:
        return []

    parsed_chunks = []
    for chunk in _chunk_pages(pages):
        combined_text = _serialize_pages(chunk)
        if not combined_text.strip():
            continue
        try:
            parsed = parse_mtr_text_with_openai(combined_text)
        except MTRParsingError:
            logger.warning(
                "OpenAI returned unparseable response for chunk",
                extra={"fileversion_id": fileversion.id},
            )
            continue
        parsed_chunks.append(parsed)

    return _merge_materials(parsed_chunks)


def _clear_existing_drafts(file_node_id: int):
    MaterialHeatDraft.objects.filter(file_node_id=file_node_id).delete()


def _create_draft(fileversion: FileVersion, material: dict):
    file_node = fileversion.file_node
    page_numbers = material.get("page_numbers") or []
    if isinstance(page_numbers, list):
        page_numbers_value = ",".join(str(p) for p in page_numbers)
    elif page_numbers is None:
        page_numbers_value = ""
    else:
        page_numbers_value = str(page_numbers)

    confidence_raw = material.get("confidence")
    try:
        confidence_value = float(confidence_raw) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence_value = None

    draft = MaterialHeatDraft(
        file_node=file_node,
        org=file_node.org,
        heat_number=(material.get("heat_number") or "").strip(),
        material_description=(material.get("material_description") or "").strip(),
        material_type=(material.get("material_type") or "").strip(),
        material_grade=(material.get("material_grade") or "").strip(),
        outer_diameter_in=_decimal_or_none(material.get("outer_diameter_in")),
        wall_thickness_in=_decimal_or_none(material.get("wall_thickness_in")),
        wps_number=(material.get("wps_number") or "").strip(),
        page_numbers=page_numbers_value,
        confidence=confidence_value,
        notes=(material.get("notes") or "").strip(),
        raw_payload=material,
    )
    draft.full_clean()
    draft.save()


@shared_task(
    name="welds.process_mtr_fileversion",
    autoretry_for=(RuntimeError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def process_mtr_fileversion(fileversion_id: int):
    try:
        fileversion = FileVersion.objects.select_related("file_node", "file_node__org").get(pk=fileversion_id)
    except FileVersion.DoesNotExist:
        logger.warning("FileVersion for MTR parsing not found", extra={"fileversion_id": fileversion_id})
        return

    file_node = fileversion.file_node
    if file_node.doc_type != FileNode.DocType.MTR:
        logger.info("Skipping non-MTR file for parsing", extra={"fileversion_id": fileversion_id})
        return

    logger.info(
        "Starting MTR parsing", extra={"fileversion_id": fileversion_id, "file_node_id": file_node.id}
    )

    materials = _material_payloads_from_text(fileversion)

    with transaction.atomic():
        _clear_existing_drafts(file_node.id)
        for material in materials:
            _create_draft(fileversion, material)
        file_node.material_heat_drafts.filter(verified=True).update(
            verified=False,
            verified_by=None,
            verified_at=None,
            resolved_material_heat=None,
        )
        file_node.mtr_approved = False
        file_node.mtr_approved_by = None
        file_node.mtr_approved_at = None
        file_node.mtr_approved_version = None
        file_node.save(
            update_fields=[
                "mtr_approved",
                "mtr_approved_by",
                "mtr_approved_at",
                "mtr_approved_version",
            ]
        )

    logger.info(
        "Completed MTR parsing", extra={"fileversion_id": fileversion_id, "materials_count": len(materials)}
    )
    return len(materials)


def process_mtr_fileversion_sync(fileversion_id: int):
    """Synchronous helper used for development when Celery is unavailable."""

    try:
        return process_mtr_fileversion.apply(args=(fileversion_id,)).get()
    except Exception:
        logger.exception("Synchronous MTR parsing failed")
        return None
