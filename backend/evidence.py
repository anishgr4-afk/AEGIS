"""Review-only index of the supplied, unverified source extracts.

The original catalog contains medication lines and executable instructions. This
endpoint deliberately returns only catalog metadata and a literal heading (or
page header) from each excerpt, never the body of a source page.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import Blueprint, jsonify, request

from .core import require_roles


evidence = Blueprint("evidence", __name__)
SOURCE_DATABASE = Path(__file__).resolve().parent.parent / "reference" / "original" / "medical_database.json"
DEFAULT_LIMIT = 25
MAX_LIMIT = 50
MAX_OFFSET = 100_000

# Every entry in the supplied catalog has this page-header format. Only this
# fixed structure may be used as a fallback; never truncate arbitrary prose.
_PAGE_HEADER = re.compile(r"(?im)^(\d+-\d+[a-z]?[ \t]+MED/ALL/GEN[ \t]+H(?:,\d+)?)[ \t]*$")


class CatalogUnavailable(ValueError):
    """The source was missing, unreadable, malformed, or not review-only."""


def _text(value, max_length):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise CatalogUnavailable()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CatalogUnavailable()
    return value.strip()


def _heading_excerpt(title, raw_excerpt):
    """Return a literal title heading or a non-clinical page header, never prose.

    This is an allowlist, not a regex-based redactor: the surrounding body can
    contain instructions or dosage information, so no other text is emitted.
    """
    if not isinstance(raw_excerpt, str):
        raise CatalogUnavailable()
    for paragraph in re.split(r"\n\s*\n", raw_excerpt):
        heading = " ".join(paragraph.split())
        if heading.casefold() == title.casefold():
            return heading
    match = _PAGE_HEADER.search(raw_excerpt)
    return match.group(1) if match else ""


def _catalog():
    try:
        with SOURCE_DATABASE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("catalog_status") != "extracted_candidates_require_clinical_review":
            raise CatalogUnavailable()
        if not isinstance(data.get("sources"), list) or not isinstance(data.get("protocols"), list):
            raise CatalogUnavailable()
        sources = {}
        for source in data["sources"]:
            if not isinstance(source, dict):
                raise CatalogUnavailable()
            key = _text(source.get("id"), 200)
            if key in sources:
                raise CatalogUnavailable()
            sources[key] = _text(source.get("title"), 200)

        result = []
        for item in data["protocols"]:
            if not isinstance(item, dict) or item.get("verification_status") != "needs_clinical_review":
                raise CatalogUnavailable()
            ident = _text(item.get("id"), 256)
            title = _text(item.get("title"), 120)
            source_title = sources.get(_text(item.get("source_id"), 200))
            region = _text(item.get("region"), 80)
            start, end = item.get("page_start"), item.get("page_end")
            if not source_title or type(start) is not int or type(end) is not int or start < 1 or end < start:
                raise CatalogUnavailable()
            result.append({
                "id": ident,
                "title": title,
                "source_title": source_title,
                "page": start,
                "page_start": start,
                "page_end": end,
                "region": region,
                "verification_status": "needs_clinical_review",
                "excerpt": _heading_excerpt(title, item.get("evidence_excerpt")),
            })
        return result
    except (OSError, UnicodeError, json.JSONDecodeError, CatalogUnavailable) as exc:
        raise CatalogUnavailable() from exc


def _bounded_int(name, default, minimum, maximum):
    value = request.args.get(name)
    if value is None:
        return default
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}.")
    return number


@evidence.get("/api/evidence")
@require_roles("Medical Officer", "Administrator")
def list_evidence():
    """List source headings only. q/query searches titles; region is exact."""
    query = request.args.get("q", request.args.get("query", "")).strip()
    region = request.args.get("region", "").strip()
    try:
        if len(query) > 120 or len(region) > 80:
            raise ValueError("Search query or region is too long.")
        limit = _bounded_int("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        offset = _bounded_int("offset", 0, 0, MAX_OFFSET)
        rows = _catalog()
    except CatalogUnavailable:
        return jsonify(error="Source evidence is unavailable or invalid. Please try again later."), 503
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    needle = query.casefold()
    region_filter = region.casefold()
    matches = [
        row for row in rows
        if (not needle or needle in row["title"].casefold() or needle in row["source_title"].casefold())
        and (not region_filter or region_filter == row["region"].casefold())
    ]
    response = jsonify(matches[offset:offset + limit])
    response.headers["Cache-Control"] = "no-store"
    return response
