"""Index source pages and produce review-only candidates from local NASA PDFs.

This deterministic extractor does not diagnose or prescribe. Every candidate
must be checked against the cited PDF page and clinically approved before use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDFS = (
    "Medical Checklist Rev H.pdf",
    "nasa-std-3001-vol-1-rev-c-with-signature.pdf",
)
PAGE_HEADER = re.compile(r"^\s*(?P<printed>\d{1,2}-\d{1,2}[a-z]?)\s+MED/ALL/GEN\s+H\s*$", re.I)
SECTION_HEADER = re.compile(r"^\s*SECTION\s+(?P<section>\d+)\s*(?P<title>[A-Z][A-Z0-9 /&(),'-]{2,100})?\s*$", re.I)
STD_SECTION = re.compile(r"^\s*(?P<section>\d{1,2})\s+(?P<title>[A-Z][A-Z0-9 /&(),'-]{2,100})\s*$")
DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mcg|mg|g|ml|mL|L|units?)\b", re.I)
ROUTE = re.compile(r"\b(oral(?:ly)?|PO|IM|IV|intramuscular|intravenous|subcutaneous|SC|SQ|topical|inhal(?:ed|ation))\b", re.I)
WARNING = re.compile(r"\b(WARNING|CAUTION|CONTRAINDICATED|DO NOT|CONTACT SURGEON|SURGEON APPROVAL)\b", re.I)
STOWAGE = re.compile(r"\b(?:Drug|Airway|Trauma|EENT|Med(?:ical)? Locker)\s+Subpack(?:[- ]\d+[A-Za-z]?)?\b", re.I)


def normalize_text(value: str) -> str:
    """Repair only known PDF encoding artifacts and retain a normalization note."""
    # Checklist Rev H's embedded font emits this glyph code for capital I.
    value = value.replace("/C0073", "I").replace(chr(1), "I")
    # PDFium can return UTF-8 punctuation interpreted as CP1252 (for example
    # the three-character sequence commonly displayed as "â€“"). Retain the
    # final punctuation glyph and remove the two mojibake prefix characters.
    value = value.replace("\u00e2\u20ac", "").replace("\u2013", "-").replace("\u2014", "-")
    value = value.replace("\ufffd", "[unmapped]")
    return value.replace("\r", "\n")


def locate_pdf(name_or_path: str) -> Path:
    candidate = Path(name_or_path).expanduser()
    choices = [candidate] if candidate.is_absolute() else [ROOT / candidate, ROOT.parent / candidate]
    for path in choices:
        if path.is_file() and path.suffix.lower() == ".pdf":
            return path.resolve()
    raise FileNotFoundError(f"PDF not found: {name_or_path}; searched project root and its parent")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pdf(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        from pypdf import PdfReader
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("Install extraction tools with: pip install -r requirements-pdf.txt") from exc
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None

    reader = PdfReader(str(path), strict=False)
    table_pdf = pdfplumber.open(path) if pdfplumber is not None else None
    pdf = pdfium.PdfDocument(str(path))
    pages: list[dict[str, Any]] = []
    try:
        for page_idx in range(len(pdf)):
            page = pdf[page_idx]
            text_page = page.get_textpage()
            text = normalize_text(text_page.get_text_range() or "")
            tables: list[list[list[str | None]]] = []
            if table_pdf is not None and page_idx < len(table_pdf.pages):
                for table in table_pdf.pages[page_idx].extract_tables() or []:
                    tables.append([[normalize_text(cell or "") for cell in row] for row in table])
            pages.append({"page": page_idx + 1, "text": text, "tables": tables})
            text_page.close()
            page.close()
    finally:
        pdf.close()
        if table_pdf is not None:
            table_pdf.close()

    meta = reader.metadata
    source = {
        "id": re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-"),
        "file_name": path.name,
        "source_path": path.name if path.parent == ROOT else f"../{path.name}",
        "sha256": digest_file(path),
        "page_count": len(reader.pages),
        "title": str(meta.title) if meta and meta.title else path.stem,
        "creator": str(meta.creator) if meta and meta.creator else None,
        "extraction": "PDFium page text; pdfplumber table candidates when available",
        "normalization": "Known embedded-font capital-I glyphs and observed mojibake punctuation are normalized; source PDF hash and page provenance are retained.",
        "text_pages": sum(bool(p["text"].strip()) for p in pages),
        "empty_text_pages": [p["page"] for p in pages if not p["text"].strip()],
    }
    return source, pages


def candidate(source_id: str, page: dict[str, Any], section: str, title: str, excerpt: str) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return {
        "id": f"{source_id}-pdf{page['page']}-{section}-{slug}",
        "title": title.title(),
        "section": section,
        "source_id": source_id,
        "page_start": page["page"],
        "page_end": page["page"],
        "region": "unclassified",
        "symptoms": [],
        "required_vitals": [],
        "urgency": "unreviewed",
        "diagnostic_checks": [],
        "steps": [],
        "warnings": [],
        "med_kit_locations": [],
        "evidence_excerpt": excerpt[:1800],
        "verification_status": "needs_clinical_review",
    }


def protocol_candidates(source_id: str, pages: list[dict[str, Any]], checklist: bool) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for page in pages:
        lines = page["text"].splitlines()
        if checklist:
            match = next((PAGE_HEADER.match(line) for line in lines if PAGE_HEADER.match(line)), None)
            if not match:
                continue
            printed = match.group("printed")
            titles = []
            header_index = next(i for i, line in enumerate(lines) if PAGE_HEADER.match(line))
            content_lines = [" ".join(line.split()).strip(" .:-") for line in lines[header_index + 1:] if line.strip()]
            first_content = content_lines[0].upper() if content_lines else ""
            # The PDF contains several contents/index pages with the same
            # running header; don't mislabel section headings as protocols.
            if first_content.startswith(("SECTION ", "FIGURES", "CONTENTS", "TABLE OF")):
                continue
            for title in content_lines:
                if title.upper() in {"STEP ACTIVITY", "THIS PAGE INTENTIONALLY BLANK"}:
                    break
                if re.fullmatch(r"[A-Z0-9 /&(),'-]{3,100}", title) and len(re.findall(r"[A-Z]", title)) >= 3:
                    titles.append(title)
                    if len(titles) >= 3:
                        break
                elif titles:
                    break
            for title in dict.fromkeys(titles):
                output.append(candidate(source_id, page, printed, title, page["text"]))
            continue

        for line in lines:
            match = STD_SECTION.match(line)
            if not match:
                continue
            section, title = match.group("section"), " ".join(match.group("title").split())
            output.append(candidate(source_id, page, section, title, page["text"]))
    return output


def evidence_lines(source_id: str, pages: list[dict[str, Any]], pattern: re.Pattern, kind: str) -> list[dict[str, Any]]:
    out = []
    for page in pages:
        for line in page["text"].splitlines():
            excerpt = " ".join(line.split())
            if excerpt and pattern.search(excerpt):
                out.append({"source_id": source_id, "page": page["page"], "excerpt": excerpt[:700], "verification_status": f"raw_{kind}_evidence_requires_clinical_review"})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", help="PDF paths or names; defaults to the two NASA source PDFs")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    paths = [locate_pdf(item) for item in (args.pdfs or DEFAULT_PDFS)]

    sources, documents = [], {}
    for path in paths:
        source, pages = extract_pdf(path)
        sources.append(source)
        documents[source["id"]] = pages

    protocols, medication, warnings, stowage = [], [], [], []
    for source in sources:
        pages = documents[source["id"]]
        is_checklist = "checklist" in source["file_name"].lower()
        protocols.extend(protocol_candidates(source["id"], pages, is_checklist))
        if is_checklist:
            medication.extend(evidence_lines(source["id"], pages, re.compile(r"\b\d+(?:\.\d+)?\s*(?:mcg|mg|g|mL|ml|L|units?)\b", re.I), "medication"))
            warnings.extend(evidence_lines(source["id"], pages, WARNING, "warning"))
            stowage.extend(evidence_lines(source["id"], pages, STOWAGE, "stowage"))

    def unique_evidence(items):
        seen = set()
        output = []
        for item in items:
            key = (item["source_id"], item["page"], item["excerpt"])
            if key not in seen:
                seen.add(key)
                output.append(item)
        return output

    medication, warnings, stowage = map(unique_evidence, (medication, warnings, stowage))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text_index = {"schema_version": 1, "generated_at_utc": generated, "sources": sources, "documents": documents}
    database = {
        "schema_version": 1,
        "generated_at_utc": generated,
        "catalog_status": "extracted_candidates_require_clinical_review",
        "notice": "Extracted text, headings, medication lines, warnings and stowage mentions are evidence candidates, not validated clinical instructions. No candidate is executable until clinician review approves structured fields and source pages.",
        "sources": sources,
        "protocols": protocols,
        "medication_evidence": medication,
        "thresholds": [],
        "warnings_and_contraindications": warnings,
        "stowage_evidence": stowage,
        "field_schema": {
            "protocol": ["id", "title", "section", "source_id", "page_start", "page_end", "symptoms", "required_vitals", "diagnostic_checks", "steps", "warnings", "med_kit_locations", "verification_status"],
            "evidence": ["source_id", "page", "excerpt", "verification_status"],
        },
    }
    (args.output_dir / "pdf_text_index.json").write_text(json.dumps(text_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "medical_database.json").write_text(json.dumps(database, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Indexed {sum(s['page_count'] for s in sources)} pages from {len(sources)} PDFs; candidates: protocols={len(protocols)}, medication={len(medication)}, warnings={len(warnings)}, stowage={len(stowage)}.")
    print(f"Wrote {args.output_dir / 'medical_database.json'} and {args.output_dir / 'pdf_text_index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
