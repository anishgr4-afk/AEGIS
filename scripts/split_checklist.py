"""Split the Medical Checklist into one PDF per procedure, with highlights.

Reads data/checklist_sections.json (hand-built index) and writes
data/checklist/<section id>.pdf plus data/checklist/manifest.json.

Each split PDF shows NASA's original pages, unmodified underneath, with:
  - BLUE box: where this procedure starts (pages are shared between
    procedures, so the crew needs to know where to begin).
  - ORANGE box: medications the checklist marks with * = "Contact Surgeon
    before giving". Under Mars comms delay the Crew Medical Officer decides
    while the surgeon's reply is in transit.
  - A banner and legend saying this is a simulated demo.

Fails (exit 1) if any procedure's heading cannot be found on its start page,
which is how page ranges in the index are verified.

Run from the repo root (needs requirements-pdf.txt):
    .venv/Scripts/python scripts/split_checklist.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber
from pypdf import PageObject, PdfReader, PdfWriter
from pypdf.generic import ContentStream, DictionaryObject, FloatObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Medical Checklist Rev H.pdf"
INDEX = ROOT / "data" / "checklist_sections.json"
OUT_DIR = ROOT / "data" / "checklist"

# The PDF's font maps some glyphs to unusable codes on text extraction.
GLYPH_FIXES = {"(cid:1)": "I", "(cid:2)": "O", "(cid:3)": "/", "(cid:4)": "i", "(cid:6)": "(",
               "�": "-", "–": "-", "—": "-"}
LABEL = re.compile(r"(?:^|\s)(\d{1,2})\W{1,2}(\d{1,2}[ab]?)\s+M", re.M)
TEMP_PAGE = re.compile(r"TEMP\s+\d\W\d{1,2}")
SURGEON_MED = re.compile(r"^\*[A-Za-z(]")  # "*Proventil" - not a bare "*" box border

ORANGE = (1.0, 0.55, 0.0)
BLUE = (0.15, 0.45, 1.0)


def normalize(text: str) -> str:
    for bad, good in GLYPH_FIXES.items():
        text = text.replace(bad, good)
    return re.sub(r"\s+", " ", text).strip().upper()


def label_map(pdf: pdfplumber.PDF) -> dict[str, int]:
    """Printed page label (e.g. '1-36') -> 0-based PDF page index."""
    labels: dict[str, int] = {}
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        if text.count(". . .") > 5:  # table of contents / effective-pages list
            continue
        tail = "\n".join(text.splitlines()[-3:])
        if TEMP_PAGE.search(tail):  # superseded TEMP inserts
            continue
        found = LABEL.findall(tail)
        if found:
            labels[f"{found[-1][0]}-{found[-1][1]}"] = i
    return labels


def page_lines(page) -> list[dict]:
    """Group words into lines (pdfplumber coordinates: top-left origin)."""
    lines: list[dict] = []
    for w in sorted(page.extract_words(use_text_flow=True), key=lambda w: (round(w["top"]), w["x0"])):
        for line in lines:
            if abs(line["top"] - w["top"]) < 3:
                line["words"].append(w)
                line["x0"] = min(line["x0"], w["x0"])
                line["x1"] = max(line["x1"], w["x1"])
                line["bottom"] = max(line["bottom"], w["bottom"])
                break
        else:
            lines.append({"top": w["top"], "bottom": w["bottom"], "x0": w["x0"], "x1": w["x1"], "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        line["text"] = normalize(" ".join(w["text"] for w in line["words"]))
    return sorted(lines, key=lambda l: l["top"])


def surgeon_med_blocks(lines: list[dict]) -> list[tuple[str, dict]]:
    """Each *-marked medication plus its Dose lines, as (name, bbox)."""
    blocks = []
    # Rows framed by "* ... *" boxed notes. The border asterisks sit a few
    # points off the text baseline, so match on nearby height, not same line.
    border_tops = [l["top"] for l in lines if any(w["text"] == "*" for w in l["words"])]
    for i, line in enumerate(lines):
        star = next((w for w in line["words"] if SURGEON_MED.match(w["text"])), None)
        if not star:
            continue
        name = normalize(star["text"].lstrip("*")).strip("(),").title()
        box = {"x0": star["x0"], "x1": line["x1"], "top": line["top"], "bottom": line["bottom"]}
        dose_x0 = None
        prev_bottom = line["bottom"]
        for nxt in lines[i + 1:i + 7]:
            if nxt["top"] - prev_bottom > 6:
                break  # blank gap: the block has ended
            if any(abs(nxt["top"] - t) < 7 for t in border_tops):
                break  # a "* ... *" boxed note
            if any(star["x0"] - 45 <= w["x0"] < star["x0"] - 5 for w in nxt["words"]):
                break  # next numbered step starts just left of the column
            # Only the medication's own column: far-left margin labels such as
            # "Med Locker" share rows with dose lines.
            col = [w for w in nxt["words"] if w["x0"] >= star["x0"] - 5]
            if not col:
                break
            prev_bottom = nxt["bottom"]
            t = normalize(" ".join(w["text"] for w in col))
            x0, x1 = min(w["x0"] for w in col), max(w["x1"] for w in col)
            if t.startswith("DOSE") or t.startswith("(SEE"):
                dose_x0 = dose_x0 if dose_x0 is not None else x0
            elif not (dose_x0 is not None and x0 > dose_x0 + 10):
                break
            box = {"x0": min(box["x0"], x0), "x1": max(box["x1"], x1),
                   "top": box["top"], "bottom": max(w["bottom"] for w in col)}
        blocks.append((name, box))
    return blocks


def pdf_escape(text: str) -> str:
    return text.encode("ascii", "replace").decode().replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def overlay_for(width: float, height: float, rects: list[tuple[tuple, dict]], banner: str | None,
                legend: str | None) -> PageObject:
    """A transparent page carrying highlights and text, merged over the original."""
    ops: list[str] = []
    for (r, g, b), box in rects:
        pad = 2.5
        x, y = box["x0"] - pad, height - box["bottom"] - pad
        w, h = box["x1"] - box["x0"] + 2 * pad, box["bottom"] - box["top"] + 2 * pad
        ops.append(f"q /GSh gs {r} {g} {b} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f Q")
        if (r, g, b) == BLUE:  # solid start-here bar in the left margin
            ops.append(f"q {r} {g} {b} rg {max(4.0, x - 12):.1f} {y:.1f} 5 {h:.1f} re f Q")
    if banner:
        ops.append(f"q 0.08 0.1 0.16 rg 0 {height - 18:.1f} {width:.1f} 18 re f Q")
        ops.append(f"q 1 1 1 rg BT /F1 8 Tf 10 {height - 12:.1f} Td ({pdf_escape(banner)}) Tj ET Q")
    if legend:
        ops.append(f"q 0.25 0.25 0.25 rg BT /F1 7 Tf 10 14 Td ({pdf_escape(legend)}) Tj ET Q")

    page = PageObject.create_blank_page(None, width, height)
    resources = DictionaryObject()
    resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/GSh"): DictionaryObject({
        NameObject("/Type"): NameObject("/ExtGState"),
        NameObject("/ca"): FloatObject(0.33), NameObject("/CA"): FloatObject(0.33)})})
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica-Bold")})})
    page[NameObject("/Resources")] = resources
    stream = ContentStream(None, None)
    stream.set_data("\n".join(ops).encode("latin-1"))
    page.replace_contents(stream)
    return page


def main() -> int:
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    sections = index["sections"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(SOURCE))
    problems: list[str] = []
    manifest_sections = []

    with pdfplumber.open(str(SOURCE)) as pdf:
        labels = label_map(pdf)
        line_cache: dict[int, list[dict]] = {}

        def lines_of(i: int) -> list[dict]:
            if i not in line_cache:
                line_cache[i] = page_lines(pdf.pages[i])
            return line_cache[i]

        for sec in sections:
            sid = sec["id"]
            if sec["start"] not in labels or sec["end"] not in labels:
                problems.append(f"{sid}: page label {sec['start']} or {sec['end']} not found in PDF")
                continue
            first, last = labels[sec["start"]], labels[sec["end"]]
            if last < first:
                problems.append(f"{sid}: end page {sec['end']} is before start {sec['start']}")
                continue

            heading = normalize(sec["heading"])
            heading_line = next((l for l in lines_of(first) if heading in l["text"]), None)
            if heading_line is None:
                problems.append(f"{sid}: heading '{sec['heading']}' not found on printed page {sec['start']}")
                continue

            writer = PdfWriter()
            meds: list[dict] = []
            pages_out = list(range(first, last + 1))
            for n, i in enumerate(pages_out):
                writer.add_page(reader.pages[i])
                page = writer.pages[-1]
                W, H = float(page.mediabox.width), float(page.mediabox.height)
                rects = []
                if n == 0:
                    rects.append((BLUE, heading_line))
                printed = next((k for k, v in labels.items() if v == i), "?")
                for name, box in surgeon_med_blocks(lines_of(i)):
                    rects.append((ORANGE, box))
                    meds.append({"name": name, "printed_page": printed})
                banner = (f"SIMULATED DEMO - NOT FOR OPERATIONAL USE | {sec['title']} | "
                          f"JSC-48031 Medical Checklist Rev H (2001), printed pp {sec['start']} to {sec['end']}")
                legend = ("BLUE = this procedure starts here.   ORANGE = medication marked * in the checklist: "
                          "Contact Surgeon first - under comms delay the Crew Medical Officer decides.")
                page.merge_page(overlay_for(W, H, rects, banner if n == 0 else None, legend))

            out = OUT_DIR / f"{sid}.pdf"
            with out.open("wb") as fh:
                writer.write(fh)
            manifest_sections.append({
                **{k: v for k, v in sec.items() if k != "heading"},
                "file": out.name,
                "pdf_pages": [i + 1 for i in pages_out],
                "surgeon_approval_meds": meds,
                "heading_verified": True,
            })

    if problems:
        print("FAILED - fix data/checklist_sections.json:", *problems, sep="\n  ")
        return 1

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_pdf": SOURCE.name,
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "sections": manifest_sections,
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    meds_total = sum(len(s["surgeon_approval_meds"]) for s in manifest_sections)
    size_kb = sum((OUT_DIR / s["file"]).stat().st_size for s in manifest_sections) // 1024
    print(f"Wrote {len(manifest_sections)} procedure PDFs ({size_kb} KB) to {OUT_DIR.relative_to(ROOT)}; "
          f"{meds_total} surgeon-approval medication marks highlighted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
