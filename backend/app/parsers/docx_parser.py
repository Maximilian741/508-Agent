"""DOCX extraction helpers used by scan/fix routes."""

from __future__ import annotations

from typing import Dict, List, Optional

from docx import Document


class DOCXParser:
    def parse(self, file_path: str) -> Dict[str, object]:
        doc = Document(file_path)
        core = doc.core_properties

        headings: List[Dict[str, object]] = []
        prev_level: Optional[int] = None
        skipped_jumps: List[Dict[str, object]] = []
        for idx, para in enumerate(doc.paragraphs, start=1):
            style_name = (para.style.name or "") if para.style else ""
            if not style_name.lower().startswith("heading"):
                continue
            level = 1
            try:
                tail = style_name.split(" ", 1)[1]
                level = max(1, min(6, int(tail)))
            except Exception:
                level = 1
            text = (para.text or "").strip()
            if not text:
                text = f"Heading {idx}"
            headings.append({"level": level, "text": text, "section": idx})
            if prev_level is not None and level > prev_level + 1:
                skipped_jumps.append({"from": prev_level, "to": level, "section": idx, "text": text})
            prev_level = level

        image_count = 0
        alt_missing = 0
        for rel in doc.part.rels.values():
            reltype = str(rel.reltype)
            if "image" in reltype:
                image_count += 1
                alt_missing += 1

        tables = len(doc.tables)
        return {
            "documentType": "docx",
            "title": (core.title or "").strip(),
            "language": (getattr(core, "language", None) or "").strip(),
            "headings": headings,
            "headingJumps": skipped_jumps,
            "imageCount": image_count,
            "missingAltCount": alt_missing,
            "tables": tables,
            "outlineCount": len(headings),
        }
