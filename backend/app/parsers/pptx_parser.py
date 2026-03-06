"""PPTX extraction helpers used by scan/fix routes."""

from __future__ import annotations

from typing import Dict, List
from urllib.parse import urlparse

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE


class PPTXParser:
    def parse(self, file_path: str) -> Dict[str, object]:
        prs = Presentation(file_path)
        slide_details: List[Dict[str, object]] = []
        slides_missing_titles: List[int] = []
        total_images = 0
        missing_alt = 0
        total_tables = 0
        tables_missing_headers: List[Dict[str, object]] = []
        table_header_scope_flags: List[Dict[str, object]] = []
        hyperlink_count = 0
        generic_links: List[Dict[str, object]] = []
        invalid_links: List[Dict[str, object]] = []
        reading_order_warnings: List[Dict[str, object]] = []
        generic_link_labels = {
            "click here",
            "here",
            "read more",
            "learn more",
            "more",
            "link",
            "this",
        }
        generic_headers = {"column", "column 1", "column 2", "header", "n/a", "na", "value"}

        for idx, slide in enumerate(prs.slides, start=1):
            title = slide.shapes.title.text.strip() if slide.shapes.title and slide.shapes.title.text else ""
            if not title:
                slides_missing_titles.append(idx)
            slide_images = 0
            slide_tables = 0
            shape_positions: List[tuple[float, float]] = []
            for shape in slide.shapes:
                left = float(getattr(shape, "left", 0) or 0)
                top = float(getattr(shape, "top", 0) or 0)
                shape_positions.append((top, left))
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    slide_images += 1
                    alt_text = (getattr(shape, "alternative_text", None) or "").strip()
                    if not alt_text:
                        missing_alt += 1
                if shape.has_table:
                    slide_tables += 1
                    table = shape.table
                    if len(table.rows) == 0:
                        tables_missing_headers.append({"slide": idx, "table": slide_tables})
                    else:
                        first_row = table.rows[0]
                        header_text = [(cell.text or "").strip() for cell in first_row.cells]
                        if not any(header_text):
                            tables_missing_headers.append({"slide": idx, "table": slide_tables})
                        else:
                            normalized_headers = [text.lower() for text in header_text if text]
                            unique_headers = set(normalized_headers)
                            if len(header_text) > 1 and len(unique_headers) <= 1:
                                table_header_scope_flags.append(
                                    {
                                        "slide": idx,
                                        "table": slide_tables,
                                        "reason": "duplicate_or_single_header_label",
                                        "headers": header_text[:10],
                                    }
                                )
                            elif any(text.lower() in generic_headers for text in header_text if text):
                                table_header_scope_flags.append(
                                    {
                                        "slide": idx,
                                        "table": slide_tables,
                                        "reason": "generic_header_labels",
                                        "headers": header_text[:10],
                                    }
                                )
                link_text = (getattr(shape, "text", None) or "").strip()
                shape_link = None
                try:
                    shape_link = getattr(shape.click_action.hyperlink, "address", None)
                except Exception:
                    shape_link = None
                if shape_link:
                    hyperlink_count += 1
                    if link_text and link_text.lower().strip() in generic_link_labels:
                        generic_links.append({"slide": idx, "text": link_text})
                    invalid_reason = _invalid_link_reason(str(shape_link))
                    if invalid_reason:
                        invalid_links.append(
                            {"slide": idx, "text": link_text or "(shape link)", "target": str(shape_link), "reason": invalid_reason}
                        )
                if hasattr(shape, "text_frame") and shape.text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        for run in paragraph.runs:
                            run_link = getattr(getattr(run, "hyperlink", None), "address", None)
                            if not run_link:
                                continue
                            hyperlink_count += 1
                            run_text = (run.text or "").strip()
                            if run_text and run_text.lower().strip() in generic_link_labels:
                                generic_links.append({"slide": idx, "text": run_text})
                            invalid_reason = _invalid_link_reason(str(run_link))
                            if invalid_reason:
                                invalid_links.append(
                                    {"slide": idx, "text": run_text or "(run link)", "target": str(run_link), "reason": invalid_reason}
                                )
            total_images += slide_images
            total_tables += slide_tables
            if len(shape_positions) > 2:
                disorder = 0
                for i in range(1, len(shape_positions)):
                    if shape_positions[i][0] < shape_positions[i - 1][0]:
                        disorder += 1
                if disorder > 0:
                    reading_order_warnings.append({"slide": idx, "disorderCount": disorder})
            slide_details.append(
                {
                    "slide": idx,
                    "title": title,
                    "images": slide_images,
                    "tables": slide_tables,
                }
            )

        title = (prs.core_properties.title or "").strip()
        language = (getattr(prs.core_properties, "language", None) or "").strip()
        headings = [{"level": 1, "text": s["title"], "slide": s["slide"]} for s in slide_details if s["title"]]
        return {
            "documentType": "pptx",
            "title": title,
            "language": language,
            "slideCount": len(prs.slides),
            "slides": slide_details,
            "slidesMissingTitles": slides_missing_titles,
            "headings": headings,
            "imageCount": total_images,
            "missingAltCount": missing_alt,
            "hyperlinkCount": hyperlink_count,
            "genericLinks": generic_links,
            "invalidLinks": invalid_links,
            "tables": total_tables,
            "tablesMissingHeaders": tables_missing_headers,
            "tableHeaderScopeFlags": table_header_scope_flags,
            "readingOrderWarnings": reading_order_warnings,
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
