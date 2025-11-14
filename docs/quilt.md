# QUILT Query API

The QUILT assistant exposes `/api/quilt/query/` for interactive analytics and `/api/quilt/sources/` for populating the widget selector. Requests must be authenticated and scoped to an organisation or project that the caller can access.

## Scope & RBAC

* Provide either `project_id` or `org_id` in the request body. When both are supplied they must refer to the same organisation.
* Access checks re-use model helpers when available (`project.has_member`, `org.has_member`, `Organization.members`). When no helper is provided the fallback order is: `request.user.is_superuser` → `org.owner` → `project.created_by` → project membership → organisation membership. Failures return `403`.
* Every accepted query is logged in `welds.QuiltQueryLog` with the user, scope, filters, intent, and IP for auditability. Queries are soft rate-limited to 60 requests per minute per user; exceeding the limit returns `429`.

## Request contract

```json
{
  "query": "optional free-text",
  "intent": "repairs_summary | list_welds | kpi | export_csv",
  "org_id": 1,
  "project_id": 2,
  "per_page": 25,
  "page": 1,
  "top_n": 5,
  "filters": {
    "start_date": "2024-01-01",
    "end_date": "2024-02-01",
    "welder_id": 123,
    "wps": "WPS-101",
    "allow_range": false
  }
}
```

* Dates must be ISO formatted (`YYYY-MM-DD`). Invalid formats return `400` with an example.
* The default page size is 25. Requests above 50 are coerced to 50 for interactive intents (`per_page_capped=true` in the response metadata). Export intents allow up to 100 rows.
* `page` must be ≥ 1. `top_n` defaults to 5 and is capped at 20.
* The standard date window limit is 365 days. Staff users or `allow_range=true` may bypass the guard.

## Response contract

```
{
  "answer": "Summary text",
  "kpi": {
    "repair_rate": 0.42,
    "total_welds": 120,
    "repaired_welds": 50,
    "top_welders": [
      {"welder_id": 9, "name": "Alex", "repairs": 11, "profile_url": "/o/farraen/welders/9/"}
    ]
  },
  "welds": [
    {
      "id": 1,
      "identifier": "W-100",
      "status": "REPAIR",
      "repairs_count": 2,
      "last_repair_date": "2024-02-03",
      "welder": {"id": 9, "name": "Alex", "profile_url": "/o/farraen/welders/9/"},
      "weld_url": "/o/farraen/projects/demo/welds/1/",
      "sources": [
        {"type": "weld", "id": 1, "url": "/o/farraen/projects/demo/welds/1/"},
        {"type": "file", "id": 88, "url": "/o/farraen/p/demo/drive/file/88/", "parser_confidence": 0.91}
      ]
    }
  ],
  "meta": {
    "page": 1,
    "per_page": 25,
    "returned": 25,
    "total": 80,
    "total_welds": 320,
    "per_page_capped": true,
    "export_limited": true
  },
  "sources": [ ... ]
}
```

* `meta.total` reports the total number of matches (without pagination). `meta.returned` reflects the current page length.
* Results never include raw MTR content; only URLs plus optional `parser_confidence` from `MaterialHeatDraft` payloads (see `docs/mtr_parsing.md`).
* Large result sets (`total_welds > 50,000`) return a `400` asking the user to tighten filters rather than scan the full dataset.
* Export intents return at most 100 rows. When more are available the response adds `meta.export_limited=true` and the message “Export limited to 100 rows. Use Background Export for larger datasets.”

## Aggregation notes

* Repair detection prefers a dedicated `Repair` model when present; otherwise it falls back to the weld repair count field, reverse relations, or weld status heuristics.
* KPI totals use database aggregation with `select_related`/`prefetch_related` to avoid N+1 queries and to surface top welders by repair volume.

## Front-end widget

* `templates/quilt/widget.html` renders a bottom-right bubble with accessible controls, keyboard navigation, and a graceful no-JavaScript fallback to `/o/<org>/quilt/`.
* `static/js/quilt.js` enforces the same pagination limits as the backend, handles quick actions, and paginates with “Show more” when the dataset is truncated.
