"""DOCX extraction helpers used by scan/fix routes."""

from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import urlparse

from docx import Document


class DOCXParser:
    def parse(self, file_path: str) -> Dict[str, object]:
        doc = Document(file_path)
        core = doc.core_properties
        w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        generic_link_labels = {
            "click here",
            "here",
            "read more",
            "learn more",
            "more",
            "link",
            "this",
        }

        headings: List[Dict[str, object]] = []
        empty_heading_sections: List[int] = []
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
                empty_heading_sections.append(idx)
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

        hyperlink_count = 0
        generic_links: List[Dict[str, object]] = []
        invalid_links: List[Dict[str, object]] = []
        for idx, para in enumerate(doc.paragraphs, start=1):
            hyperlink_nodes = list(para._p.iterfind(f".//{w_ns}hyperlink"))
            if not hyperlink_nodes:
                continue
            for hyperlink in hyperlink_nodes:
                text_bits = [node.text or "" for node in hyperlink.iterfind(f".//{w_ns}t")]
                link_text = "".join(text_bits).strip()
                hyperlink_count += 1
                rid = hyperlink.get(f"{rel_ns}id")
                anchor = hyperlink.get(f"{w_ns}anchor")
                target = ""
                if rid and rid in doc.part.rels:
                    rel = doc.part.rels[rid]
                    target = str(getattr(rel, "target_ref", "") or "")
                elif anchor:
                    target = f"#{anchor}"
                if not link_text:
                    continue
                normalized = link_text.lower().strip()
                if normalized in generic_link_labels:
                    generic_links.append({"section": idx, "text": link_text})
                invalid_reason = _invalid_link_reason(target)
                if invalid_reason:
                    invalid_links.append({"section": idx, "text": link_text, "target": target, "reason": invalid_reason})

        tables = len(doc.tables)
        tables_missing_headers: List[int] = []
        table_header_scope_flags: List[Dict[str, object]] = []
        generic_headers = {"column", "column 1", "column 2", "header", "n/a", "na", "value"}
        for table_index, table in enumerate(doc.tables, start=1):
            if not table.rows:
                tables_missing_headers.append(table_index)
                continue
            header_cells = table.rows[0].cells
            header_text = [(cell.text or "").strip() for cell in header_cells]
            if not any(header_text):
                tables_missing_headers.append(table_index)
                continue
            normalized_headers = [text.lower() for text in header_text if text]
            unique_headers = set(normalized_headers)
            if len(header_text) > 1 and len(unique_headers) <= 1:
                table_header_scope_flags.append(
                    {"table": table_index, "reason": "duplicate_or_single_header_label", "headers": header_text[:10]}
                )
            elif any(text.lower() in generic_headers for text in header_text if text):
                table_header_scope_flags.append(
                    {"table": table_index, "reason": "generic_header_labels", "headers": header_text[:10]}
                )
        return {
            "documentType": "docx",
            "title": (core.title or "").strip(),
            "language": (getattr(core, "language", None) or "").strip(),
            "headings": headings,
            "emptyHeadingSections": empty_heading_sections,
            "headingJumps": skipped_jumps,
            "imageCount": image_count,
            "missingAltCount": alt_missing,
            "hyperlinkCount": hyperlink_count,
            "genericLinks": generic_links,
            "invalidLinks": invalid_links,
            "tables": tables,
            "tablesMissingHeaders": tables_missing_headers,
            "tableHeaderScopeFlags": table_header_scope_flags,
            "outlineCount": len(headings),
        }


def _invalid_link_reason(target: str) -> str:
    value = (target or "").strip()
    if not value:
        return "missing_target"
    if value.startswith("#"):
        return ""
    lowered = value.lower()
    if lowered in {"http://", "https://", "www.", "mailto:"}:
        return "placeholder_target"
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "mailto"}:
        if parsed.scheme in {"http", "https"} and not parsed.netloc:
            return "missing_host"
        return ""
    if parsed.scheme == "" and parsed.path:
        return ""
    return "unsupported_scheme"
