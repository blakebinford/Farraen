"""Utilities for interacting with OpenAI to parse MTR documents."""
from __future__ import annotations

import json
import json
import logging
import os
import re
from typing import Any, Dict

from django.conf import settings

logger = logging.getLogger(__name__)

_JSON_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class MTRParsingError(RuntimeError):
    """Raised when the OpenAI response cannot be parsed."""


def _get_openai_client():
    try:
        from openai import AzureOpenAI, OpenAI
    except ImportError as exc:  # pragma: no cover - dependency missing at import time
        raise RuntimeError("openai package is required for MTR parsing") from exc

    api_base = getattr(settings, "OPENAI_API_BASE", None) or os.getenv("OPENAI_API_BASE")
    api_key = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    if api_base:
        deployment = (
            getattr(settings, "OPENAI_DEPLOYMENT", None)
            or os.getenv("OPENAI_DEPLOYMENT")
            or getattr(settings, "OPENAI_MODEL", None)
            or os.getenv("OPENAI_MODEL")
        )
        if not deployment:
            raise RuntimeError("OPENAI_DEPLOYMENT (Azure) is not configured")
        api_version = (
            getattr(settings, "OPENAI_API_VERSION", None)
            or os.getenv("OPENAI_API_VERSION")
            or "2024-02-15-preview"
        )
        return AzureOpenAI(
            api_key=api_key,
            api_base=api_base,
            api_version=api_version,
            azure_deployment=deployment,
        )

    return OpenAI(api_key=api_key)


def _extract_json_block(text: str) -> Dict[str, Any]:
    match = _JSON_PATTERN.search(text)
    if not match:
        raise MTRParsingError("Unable to find JSON payload in OpenAI response")
    payload = match.group(0)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode OpenAI JSON payload", exc_info=exc)
        raise MTRParsingError("OpenAI response did not return valid JSON") from exc


def parse_mtr_text_with_openai(mtr_text: str) -> Dict[str, Any]:
    """Parse MTR text and return a structured dictionary."""

    if not mtr_text.strip():
        return {"materials": []}

    client = _get_openai_client()
    model = (
        getattr(settings, "OPENAI_MODEL", None)
        or os.getenv("OPENAI_MODEL")
        or getattr(settings, "OPENAI_DEPLOYMENT", None)
        or os.getenv("OPENAI_DEPLOYMENT")
    )
    if not model:
        raise RuntimeError("OPENAI_MODEL is not configured")

    system_prompt = (
        "You are an expert materials traceability assistant. Extract material heat data from "
        "the supplied MTR text. Return ONLY valid JSON matching the schema: {\n"
        '  "materials": [\n'
        "    {\"heat_number\": null|string, \"material_description\": null|string, \"material_type\": \"PIPE|FLANGE|OLET|ELBOW|FITTING|PLATE|OTHER\",\n"
        "     \"material_grade\": null|string, \"outer_diameter_in\": null|number, \"wall_thickness_in\": null|number,\n"
        "     \"wps_number\": null|string, \"page_numbers\": array<int>, \"confidence\": null|number between 0 and 1, \"notes\": null|string }\n"
        "  ]\n"
        "}. Use inches as units. Convert from millimeters if necessary. Return null when data is missing."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Parse the following Material Test Report (MTR). Include page numbers and set confidence "
                        "between 0 and 1. Text:\n" + mtr_text
                    ),
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - network failure path
        logger.exception("OpenAI request for MTR parsing failed")
        raise RuntimeError("OpenAI request failed") from exc

    if not response or not getattr(response, "choices", None):
        raise MTRParsingError("OpenAI returned no choices")

    content = response.choices[0].message.content  # type: ignore[attr-defined]
    if not content:
        raise MTRParsingError("OpenAI returned empty content")

    data = _extract_json_block(content)
    materials = data.get("materials")
    if materials is None:
        raise MTRParsingError("OpenAI response missing 'materials'")
    if not isinstance(materials, list):
        raise MTRParsingError("OpenAI response has invalid 'materials' value")
    return {"materials": materials}


__all__ = ["parse_mtr_text_with_openai", "MTRParsingError"]
