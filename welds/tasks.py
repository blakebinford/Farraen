"""Celery tasks for welds application."""
from __future__ import annotations

import io
import logging
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Iterable, List, Optional

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


_HEAT_SUPPORT_THRESHOLD = 0.6
_FIELD_SUPPORT_THRESHOLD = 0.5
_AUTO_APPROVE_CONFIDENCE = 0.65
_MIN_ACCEPT_WEIGHT = 0.3
_CONFIDENCE_SMOOTHING = 0.5
_NUMERIC_VARIANCE_THRESHOLD = 0.02  # 2% coefficient of variation

_TYPE_SYNONYMS = {
    "PIPE": "PIPE",
    "TUBE": "PIPE",
    "TUBING": "PIPE",
    "PIPING": "PIPE",
    "FLANGE": "FLANGE",
    "ELBOW": "ELBOW",
    "OLET": "OLET",
    "TEE": "FITTING",
    "COUPLING": "FITTING",
    "REDUCER": "FITTING",
    "FITTING": "FITTING",
    "CAP": "FITTING",
    "PLUG": "FITTING",
    "PLATE": "PLATE",
}


def _clean_string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_material_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = _clean_string(value).upper()
    for candidate, mapped in _TYPE_SYNONYMS.items():
        if candidate in text:
            return mapped
    if text in _TYPE_SYNONYMS.values():
        return text
    if text:
        return "OTHER"
    return None


def _normalize_heat_number(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = _clean_string(value).upper()
    if not text:
        return None
    text = text.replace("—", "-")
    text = re.sub(r"(?i)\bHEAT(?:\s*(?:NO|NUMBER|#))?[:\s-]*", "", text)
    text = re.sub(r"[^A-Z0-9\-]+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = text.strip("-")
    if not text:
        return None
    return text


def _canonical_heat(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return re.sub(r"[^A-Z0-9]", "", value)


def _normalize_grade(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = _clean_string(value).upper()
    if not text:
        return None
    match = re.search(r"\bX\d+\b", text)
    if match:
        return match.group(0)
    return text


def _normalize_description(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = _clean_string(value)
    if not text:
        return None
    text = re.sub(r"(?i)^(?:material|description)[:\s-]+", "", text)
    return text or None


def _normalize_wps(value: Optional[str]) -> Optional[str]:
    text = _clean_string(value)
    return text or None


def _normalize_numeric(value, field_name: str, notes: List[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    original_text = value
    if isinstance(value, (int, float)):
        number = float(value)
        text = ""
    else:
        text = str(value).strip().lower()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            try:
                number = float(value)
            except (TypeError, ValueError):
                logger.debug("Unable to parse numeric value", extra={"field": field_name, "value": value})
                return None
        else:
            number = float(match.group(0))

    is_mm = "mm" in text and "in" not in text
    if not text and number > 50:
        is_mm = True
    if number > 50 and "in" not in text:
        is_mm = True

    if is_mm:
        inches = number / 25.4
        notes.append(f"Converted {field_name} from mm to inches using value {original_text!r}")
        return round(inches, 3)

    return round(number, 3)


def _ensure_page_numbers(value) -> List[int]:
    if not value:
        return []
    pages: List[int] = []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    for item in items:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page not in pages:
            pages.append(page)
    return pages


def _normalized_confidence(value) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.4
    if math.isnan(confidence):  # pragma: no cover - guard against NaNs
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return confidence


def _values_close(a: Optional[float], b: Optional[float], tolerance: float = 0.02) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == 0 or b == 0:
        return False
    return abs(a - b) / max(a, b) <= tolerance


def _pages_close(pages_a: List[int], pages_b: List[int]) -> bool:
    if not pages_a or not pages_b:
        return True
    return min(abs(a - b) for a in pages_a for b in pages_b) <= 1


def _cluster_candidates(candidates):
    groups = []
    heat_groups: dict[str, dict] = {}
    null_candidates = []

    for candidate in candidates:
        if candidate["canonical_heat"]:
            key = candidate["canonical_heat"]
            if key not in heat_groups:
                heat_groups[key] = {"candidates": []}
            heat_groups[key]["candidates"].append(candidate)
        else:
            null_candidates.append(candidate)

    groups.extend(heat_groups.values())

    clustered_nulls: List[dict] = []
    for candidate in null_candidates:
        matched_group = None
        for group in clustered_nulls:
            representative = group["representative"]
            if candidate["material_type"] != representative["material_type"]:
                continue
            if candidate["material_grade"] != representative["material_grade"]:
                continue
            if not _pages_close(candidate["page_numbers"], group["pages"]):
                continue
            if not _values_close(candidate["outer_diameter_in"], representative["outer_diameter_in"]):
                continue
            matched_group = group
            break
        if matched_group is None:
            matched_group = {
                "candidates": [],
                "representative": candidate,
                "pages": list(candidate["page_numbers"]),
            }
            clustered_nulls.append(matched_group)
        matched_group["candidates"].append(candidate)
        for page in candidate["page_numbers"]:
            if page not in matched_group["pages"]:
                matched_group["pages"].append(page)

    groups.extend(clustered_nulls)
    return groups


def _aggregate_string_field(candidates, field: str, total_weight: float):
    values = {}
    for candidate in candidates:
        value = candidate[field]
        if not value:
            continue
        values.setdefault(value, 0.0)
        values[value] += candidate["weight"]
    if not values:
        return None, False
    best_value, best_weight = max(values.items(), key=lambda item: item[1])
    support = best_weight / total_weight if total_weight else 0.0
    return best_value, support < _FIELD_SUPPORT_THRESHOLD


def _aggregate_numeric_field(candidates, field: str):
    weighted_values = [
        (candidate[field], candidate["weight"])
        for candidate in candidates
        if candidate[field] is not None and candidate["weight"] > 0
    ]
    if not weighted_values:
        return None, False
    total_weight = sum(weight for _, weight in weighted_values)
    if total_weight == 0:
        return None, True
    mean = sum(value * weight for value, weight in weighted_values) / total_weight
    variance = sum(weight * (value - mean) ** 2 for value, weight in weighted_values) / total_weight
    if mean:
        coefficient = math.sqrt(variance) / abs(mean)
    else:
        coefficient = 0.0
    return round(mean, 3), coefficient > _NUMERIC_VARIANCE_THRESHOLD


def _summarize_heat(candidates, total_weight: float):
    heat_support = {}
    for candidate in candidates:
        canonical = candidate["canonical_heat"]
        heat_value = candidate["heat_number"]
        if not canonical or not heat_value:
            continue
        entry = heat_support.setdefault(canonical, {"weight": 0.0, "values": {}})
        entry["weight"] += candidate["weight"]
        entry["values"].setdefault(heat_value, 0.0)
        entry["values"][heat_value] += candidate["weight"]
    if not heat_support:
        return None, 0.0
    canonical, entry = max(heat_support.items(), key=lambda item: item[1]["weight"])
    support_fraction = entry["weight"] / total_weight if total_weight else 0.0
    value, _ = max(entry["values"].items(), key=lambda item: item[1])
    return value, support_fraction


def _build_group_result(group):
    candidates = group["candidates"]
    total_weight = sum(candidate["weight"] for candidate in candidates)
    pages = sorted({page for candidate in candidates for page in candidate["page_numbers"]})

    notes: List[str] = []

    heat_number, heat_support = _summarize_heat(candidates, total_weight)
    needs_manual_review = False
    if heat_number is None and total_weight < _MIN_ACCEPT_WEIGHT:
        needs_manual_review = True
        notes.append("Heat number missing with low total confidence")
    elif heat_number is None:
        needs_manual_review = True
        notes.append("Heat number unavailable")
    elif heat_support < _HEAT_SUPPORT_THRESHOLD:
        needs_manual_review = True
        notes.append(
            f"Heat number support below threshold ({heat_support:.2f})"
        )

    material_type, type_low_support = _aggregate_string_field(candidates, "material_type", total_weight)
    if type_low_support:
        needs_manual_review = True
        notes.append("Material type disagreement detected")

    material_grade, grade_low_support = _aggregate_string_field(candidates, "material_grade", total_weight)
    if grade_low_support:
        notes.append("Material grade has weak support")

    material_description, description_low_support = _aggregate_string_field(
        candidates, "material_description", total_weight
    )
    if description_low_support:
        notes.append("Material description varies across candidates")

    wps_number, wps_low_support = _aggregate_string_field(candidates, "wps_number", total_weight)
    if wps_low_support:
        notes.append("WPS number has weak support")

    outer_diameter_in, od_variance = _aggregate_numeric_field(candidates, "outer_diameter_in")
    wall_thickness_in, wt_variance = _aggregate_numeric_field(candidates, "wall_thickness_in")

    if od_variance:
        needs_manual_review = True
        notes.append("Outer diameter values conflict across candidates")
    if wt_variance:
        needs_manual_review = True
        notes.append("Wall thickness values conflict across candidates")

    total_weight = max(total_weight, 0.0)
    aggregated_confidence = (
        0.0
        if total_weight == 0
        else min(0.99, total_weight / (total_weight + _CONFIDENCE_SMOOTHING))
    )

    if aggregated_confidence < _AUTO_APPROVE_CONFIDENCE:
        needs_manual_review = True
        notes.append("Aggregated confidence below auto-approval threshold")

    conversion_notes = []
    for candidate in candidates:
        conversion_notes.extend(candidate["conversion_notes"])
    if conversion_notes:
        notes.extend(sorted(set(conversion_notes)))

    source_candidates = []
    for candidate in candidates:
        source_candidates.append(
            {
                "chunk_index": candidate["chunk_index"],
                "page_numbers": candidate["page_numbers"],
                "normalized_confidence": candidate["weight"],
                "normalized_heat_number": candidate["heat_number"],
                "raw_candidate": candidate["raw"],
            }
        )

    result = {
        "heat_number": heat_number,
        "material_description": material_description,
        "material_type": material_type,
        "material_grade": material_grade,
        "outer_diameter_in": outer_diameter_in,
        "wall_thickness_in": wall_thickness_in,
        "wps_number": wps_number,
        "page_numbers": pages,
        "confidence": aggregated_confidence,
        "notes": "; ".join(notes) if notes else None,
        "aggregated_confidence": aggregated_confidence,
        "needs_manual_review": needs_manual_review,
        "source_candidates": source_candidates,
    }

    return result


def _merge_materials(chunks: Iterable[dict]) -> List[dict]:
    normalized_candidates = []
    for chunk_index, chunk in enumerate(chunks):
        materials = chunk.get("materials", []) if isinstance(chunk, dict) else []
        if not isinstance(materials, list):
            continue
        for material in materials:
            if not isinstance(material, dict):
                continue
            raw_candidate = dict(material)
            conversion_notes: List[str] = []
            heat_number = _normalize_heat_number(material.get("heat_number"))
            canonical_heat = _canonical_heat(heat_number)
            material_type = _normalize_material_type(material.get("material_type"))
            material_grade = _normalize_grade(material.get("material_grade"))
            material_description = _normalize_description(material.get("material_description"))
            wps_number = _normalize_wps(material.get("wps_number"))
            outer_diameter_in = _normalize_numeric(
                material.get("outer_diameter_in"), "outer_diameter_in", conversion_notes
            )
            wall_thickness_in = _normalize_numeric(
                material.get("wall_thickness_in"), "wall_thickness_in", conversion_notes
            )
            page_numbers = _ensure_page_numbers(material.get("page_numbers"))
            confidence = _normalized_confidence(material.get("confidence"))

            normalized_candidates.append(
                {
                    "raw": raw_candidate,
                    "heat_number": heat_number,
                    "canonical_heat": canonical_heat,
                    "material_type": material_type,
                    "material_grade": material_grade,
                    "material_description": material_description,
                    "wps_number": wps_number,
                    "outer_diameter_in": outer_diameter_in,
                    "wall_thickness_in": wall_thickness_in,
                    "page_numbers": page_numbers,
                    "confidence": confidence,
                    "weight": confidence,
                    "chunk_index": chunk_index,
                    "conversion_notes": conversion_notes,
                }
            )

    if not normalized_candidates:
        return []

    groups = _cluster_candidates(normalized_candidates)
    merged: List[dict] = []
    for group in groups:
        if not group["candidates"]:
            continue
        merged.append(_build_group_result(group))
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
