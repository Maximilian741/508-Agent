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
        # The catalog and /StructTreeRoot are normally indirect references;
        # resolve them or `isinstance(..., dict)` checks below silently fail and
        # we under-read every genuinely tagged PDF as "0 figures/headings/tables".
        if hasattr(root, "get_object"):
            root = root.get_object()
        struct_root = root.get("/StructTreeRoot")
        if struct_root is not None and hasattr(struct_root, "get_object"):
            struct_root = struct_root.get_object()
    except Exception as exc:
        return _safe_payload([f"tag tree: parse failed: {exc.__class__.__name__}"])
    if struct_root is None:
        return _safe_payload(warnings, tagged=False)

    try:
        page_ref_map: Dict[tuple, int] = {}
        for idx, page in enumerate(reader.pages, start=1):
            try:
                ref = page.indirect_reference
                if ref is not None:
                    page_ref_map[(ref.idnum, ref.generation)] = idx
                try:
                    page_obj = page.get_object()
                    page_ref_map[("obj", id(page_obj))] = idx
                except Exception:
                    page_ref_map[("obj", id(page))] = idx
            except Exception:
                continue

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
                    page_num = None
                    try:
                        if pg is not None and hasattr(pg, "idnum"):
                            page_num = page_ref_map.get((pg.idnum, pg.generation))
                        elif pg is not None:
                            page_num = page_ref_map.get(("obj", id(pg))) or page_ref_map.get(("obj", id(pg.get_object())))
                    except Exception:
                        page_num = None
                    add_node(
                        node_id,
                        "MCR",
                        "MCR",
                        None,
                        None,
                        None,
                        None,
                        [],
                        {"mcid": mcid, "pg": str(pg) if pg else None, "page": page_num},
                    )
                    node_count += 1
                else:
                    tag = _tag_name(obj.get("/S") if isinstance(obj, dict) else None)
                    title = _clean_text(obj.get("/T") if isinstance(obj, dict) else None)
                    alt = _clean_text(obj.get("/Alt") if isinstance(obj, dict) else None)
                    actual_text = _clean_text(obj.get("/ActualText") if isinstance(obj, dict) else None)
                    lang = _clean_text(obj.get("/Lang") if isinstance(obj, dict) else None)
                    page_num = None
                    if isinstance(obj, dict):
                        pg = obj.get("/Pg")
                        try:
                            if pg is not None and hasattr(pg, "idnum"):
                                page_num = page_ref_map.get((pg.idnum, pg.generation))
                            elif pg is not None:
                                page_num = page_ref_map.get(("obj", id(pg))) or page_ref_map.get(("obj", id(pg.get_object())))
                        except Exception:
                            page_num = None
                    kids_value = obj.get("/K") if isinstance(obj, dict) else None
                    child_ids: List[str] = []
                    if kids_value is not None:
                        if isinstance(kids_value, list):
                            for idx, kid in enumerate(kids_value):
                                if isinstance(kid, int):
                                    mcid_id = f"{node_id}.mcid.{idx}"
                                    add_node(mcid_id, "MCID", "MCID", None, None, None, None, [], {"mcid": kid})
                                    child_ids.append(mcid_id)
                                    node_count += 1
                                else:
                                    for child_id, child_obj, child_depth in enqueue_children(kid, node_id, depth):
                                        child_ids.append(child_id)
                                        stack.append((child_id, child_obj, child_depth, node_id))
                        elif isinstance(kids_value, int):
                            mcid_id = f"{node_id}.mcid.0"
                            add_node(mcid_id, "MCID", "MCID", None, None, None, None, [], {"mcid": kids_value})
                            child_ids.append(mcid_id)
                            node_count += 1
                        else:
                            for child_id, child_obj, child_depth in enqueue_children(kids_value, node_id, depth):
                                child_ids.append(child_id)
                                stack.append((child_id, child_obj, child_depth, node_id))
                    role = tag if tag else "StructElem"
                    add_node(node_id, role, tag, title, alt, actual_text, lang, child_ids, {"page": page_num})
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

        def resolve_page(node_id: str, memo: Dict[str, Optional[int]]) -> Optional[int]:
            if node_id in memo:
                return memo[node_id]
            node = nodes.get(node_id)
            if not node:
                memo[node_id] = None
                return None
            page_value = node.get("page")
            if isinstance(page_value, int):
                memo[node_id] = page_value
                return page_value
            for kid in node.get("kids", []):
                found = resolve_page(kid, memo)
                if found:
                    memo[node_id] = found
                    node["page"] = found
                    return found
            memo[node_id] = None
            return None

        memo: Dict[str, Optional[int]] = {}
        resolve_page("0", memo)

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
