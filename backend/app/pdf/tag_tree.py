from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pypdf import PdfReader

MAX_DEPTH = 60
MAX_NODES = 50000


def _clean_text(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value)
    except Exception:
        return None
    text = text.strip()
    return text or None


def _tag_name(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    text = _clean_text(value)
    if not text:
        return None
    return text.lstrip("/")


def extract_tag_tree(reader: PdfReader) -> Dict[str, object]:
    warnings: List[str] = []
    try:
        root = reader.trailer.get("/Root", {})
        struct_root = root.get("/StructTreeRoot")
    except Exception as exc:
        return _safe_payload([f"tag tree: parse failed: {exc.__class__.__name__}"])
    if not struct_root:
        return _safe_payload(warnings, tagged=False)

    try:
        nodes: Dict[str, Dict[str, object]] = {}
        tag_counts: Dict[str, int] = {}
        heading_counts: Dict[str, int] = {}
        table_counts: Dict[str, int] = {}
        figures = 0
        figures_missing_alt = 0

        def add_tag_count(tag: str) -> None:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            if tag in {"H1", "H2", "H3", "H4", "H5", "H6"}:
                heading_counts[tag] = heading_counts.get(tag, 0) + 1
            if tag in {"Table", "TR", "TH", "TD"}:
                table_counts[tag] = table_counts.get(tag, 0) + 1

        def add_node(
            node_id: str,
            role: str,
            tag: Optional[str],
            title: Optional[str],
            alt: Optional[str],
            actual_text: Optional[str],
            lang: Optional[str],
            kids: List[str],
            extra: Optional[Dict[str, object]] = None,
        ) -> None:
            entry = {
                "id": node_id,
                "role": role,
                "tag": tag,
                "title": title,
                "alt": alt,
                "actualText": actual_text,
                "lang": lang,
                "kids": kids,
            }
            if extra:
                entry.update(extra)
            nodes[node_id] = entry

        def resolve_obj(obj: object) -> object:
            try:
                return obj.get_object()  # type: ignore[no-any-return]
            except Exception:
                return obj

        def enqueue_children(kids_value: object, parent_id: str, depth: int) -> List[Tuple[str, object, int]]:
            items: List[Tuple[str, object, int]] = []
            if isinstance(kids_value, list):
                for idx, kid in enumerate(kids_value):
                    items.append((f"{parent_id}.{idx}", kid, depth + 1))
                return items
            items.append((f"{parent_id}.0", kids_value, depth + 1))
            return items

        add_node("0", "StructTreeRoot", None, None, None, None, None, [])

        stack: List[Tuple[str, object, int, Optional[str]]] = []
        node_count = 1
        root_kids = struct_root.get("/K") if isinstance(struct_root, dict) else None
        if root_kids is not None:
            for child_id, child_obj, child_depth in enqueue_children(root_kids, "0", 0):
                stack.append((child_id, child_obj, child_depth, "0"))

        while stack:
            node_id, node_obj, depth, parent_id = stack.pop()
            if node_count >= MAX_NODES:
                warnings.append("Tag tree traversal stopped at max node limit.")
                break
            if depth > MAX_DEPTH:
                warnings.append("Tag tree traversal stopped at max depth.")
                continue

            try:
                obj = resolve_obj(node_obj)

                if isinstance(obj, int):
                    add_node(node_id, "MCID", "MCID", None, None, None, None, [], {"mcid": obj})
                    node_count += 1
                elif isinstance(obj, dict) and obj.get("/Type") == "/MCR":
                    mcid = obj.get("/MCID")
                    pg = obj.get("/Pg")
                    add_node(
                        node_id,
                        "MCR",
                        "MCR",
                        None,
                        None,
                        None,
                        None,
                        [],
                        {"mcid": mcid, "pg": str(pg) if pg else None},
                    )
                    node_count += 1
                else:
                    tag = _tag_name(obj.get("/S") if isinstance(obj, dict) else None)
                    title = _clean_text(obj.get("/T") if isinstance(obj, dict) else None)
                    alt = _clean_text(obj.get("/Alt") if isinstance(obj, dict) else None)
                    actual_text = _clean_text(obj.get("/ActualText") if isinstance(obj, dict) else None)
                    lang = _clean_text(obj.get("/Lang") if isinstance(obj, dict) else None)
                    kids_value = obj.get("/K") if isinstance(obj, dict) else None
                    child_ids: List[str] = []
                    if kids_value is not None:
                        for child_id, child_obj, child_depth in enqueue_children(kids_value, node_id, depth):
                            child_ids.append(child_id)
                            stack.append((child_id, child_obj, child_depth, node_id))
                    role = tag if tag else "StructElem"
                    add_node(node_id, role, tag, title, alt, actual_text, lang, child_ids)
                    node_count += 1
                    if tag:
                        add_tag_count(tag)
                        if tag == "Figure":
                            figures += 1
                            if not alt:
                                figures_missing_alt += 1

                if parent_id:
                    parent = nodes.get(parent_id)
                    if parent and node_id not in parent["kids"]:
                        parent["kids"].append(node_id)
            except Exception as exc:
                warnings.append(f"tag tree: failed to parse node {node_id}: {exc.__class__.__name__}")
                continue

        summary = {
            "nodeCount": node_count,
            "tagCounts": tag_counts,
            "figures": figures,
            "figuresMissingAlt": figures_missing_alt,
            "headings": heading_counts,
            "tables": table_counts,
        }

        return {
            "tagged": True,
            "warnings": warnings,
            "summary": summary,
            "tree": {"rootId": "0", "nodes": nodes},
        }
    except Exception as exc:
        return _safe_payload([f"tag tree: parse failed: {exc.__class__.__name__}"])


def _safe_payload(warnings: List[str], tagged: bool = False) -> Dict[str, object]:
    return {
        "tagged": tagged,
        "warnings": warnings,
        "summary": {
            "nodeCount": 0,
            "tagCounts": {},
            "figures": 0,
            "figuresMissingAlt": 0,
            "headings": {},
            "tables": {},
        },
        "tree": {"rootId": "0", "nodes": {}},
    }
