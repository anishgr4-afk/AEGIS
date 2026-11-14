"""Extract page text from local medical PDFs for human-reviewed curation.

This script does not infer protocols or convert extracted text into medical
instructions. Place source PDFs under medical_sources/ and review every page
before adding a protocol to data/medical_protocols.json.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDF_ROOT = ROOT / "medical_sources"
OUTPUT = ROOT / "data" / "pdf_text_index.json"


def main() -> int:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit("Install the optional PDF parser with: pip install -r requirements-pdf.txt")

    pdfs = sorted(PDF_ROOT.rglob("*.pdf")) if PDF_ROOT.exists() else []
    if not pdfs:
        print(f"No PDFs found under {PDF_ROOT}. No source text was generated.")
        return 1

    pages = []
    for pdf in pdfs:
        reader = PdfReader(str(pdf))
        for page_number, page in enumerate(reader.pages, start=1):
            pages.append({
                "document": pdf.relative_to(ROOT).as_posix(),
                "page": page_number,
                "text": page.extract_text() or "",
            })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"pages": pages}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Indexed {len(pages)} pages from {len(pdfs)} PDF files into {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
