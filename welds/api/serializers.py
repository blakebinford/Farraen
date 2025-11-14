from __future__ import annotations

from datetime import timedelta
from typing import Any

from rest_framework import serializers

DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 50
MAX_TOP_N = 20
DEFAULT_TOP_N = 5
MAX_EXPORT_ROWS = 100
DATE_WINDOW_LIMIT_DAYS = 365


class QuiltQueryFiltersSerializer(serializers.Serializer):
    start_date = serializers.DateField(
        required=False,
        error_messages={"invalid": "Invalid date format. Use YYYY-MM-DD."},
    )
    end_date = serializers.DateField(
        required=False,
        error_messages={"invalid": "Invalid date format. Use YYYY-MM-DD."},
    )
    welder_id = serializers.IntegerField(required=False)
    wps = serializers.CharField(required=False, allow_blank=True)
    allow_range = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs.get("start_date")
        end = attrs.get("end_date")
        if start and end and start > end:
            raise serializers.ValidationError(
                "start_date must be before or equal to end_date"
            )

        request = self.context.get("request")
        allow_range = attrs.get("allow_range", False)
        if start and end:
            span = end - start
            if span > timedelta(days=DATE_WINDOW_LIMIT_DAYS):
                if not allow_range and not getattr(request.user, "is_staff", False):
                    raise serializers.ValidationError(
                        {
                            "non_field_errors": [
                                "Date range cannot exceed 365 days. Pass allow_range=true or contact an admin.",
                            ]
                        }
                    )
        return attrs


class QuiltQuerySerializer(serializers.Serializer):
    query = serializers.CharField(required=False, allow_blank=True)
    intent = serializers.CharField(required=False, allow_blank=True)
    org_id = serializers.IntegerField(required=False)
    project_id = serializers.IntegerField(required=False)
    per_page = serializers.IntegerField(required=False)
    page = serializers.IntegerField(required=False)
    top_n = serializers.IntegerField(required=False)
    filters = QuiltQueryFiltersSerializer(required=False)
    allow_range = serializers.BooleanField(required=False)  # passthrough for convenience

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if "filters" in self.fields:
            self.fields["filters"].context.update(self.context)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("org_id") and not attrs.get("project_id"):
            raise serializers.ValidationError(
                {"org_id": "An org_id or project_id is required."}
            )

        # Normalise pagination options.
        per_page = attrs.get("per_page")
        per_page_coerced = False
        if per_page is None:
            per_page = DEFAULT_PER_PAGE
        else:
            if per_page <= 0:
                raise serializers.ValidationError(
                    {"per_page": "per_page must be a positive integer."}
                )
            if per_page > MAX_PER_PAGE:
                per_page = MAX_PER_PAGE
                per_page_coerced = True
        attrs["per_page"] = per_page
        attrs["per_page_coerced"] = per_page_coerced

        page = attrs.get("page", 1)
        if page <= 0:
            raise serializers.ValidationError({"page": "page must be >= 1."})
        attrs["page"] = page

        top_n = attrs.get("top_n", DEFAULT_TOP_N)
        if top_n <= 0:
            top_n = DEFAULT_TOP_N
        if top_n > MAX_TOP_N:
            top_n = MAX_TOP_N
        attrs["top_n"] = top_n

        filters = attrs.get("filters") or {}
        if not isinstance(filters, dict):
            raise serializers.ValidationError({"filters": "Invalid filters payload."})
        attrs["filters"] = filters

        # Carry allow_range down into filters for convenience.
        if attrs.get("allow_range") is True and "allow_range" not in filters:
            filters["allow_range"] = True

        return attrs


__all__ = [
    "QuiltQueryFiltersSerializer",
    "QuiltQuerySerializer",
    "DEFAULT_PER_PAGE",
    "MAX_PER_PAGE",
    "MAX_TOP_N",
    "DEFAULT_TOP_N",
    "MAX_EXPORT_ROWS",
]
