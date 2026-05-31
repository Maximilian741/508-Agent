"""Scan API routes."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.analyzers.registry import get_default_analyzers, run_analyzers
from app.api import state
from app.api.deps import require_user_id
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ContentKind,
    DocumentNode,
    HeadingNode,
    ImageNode,
    NodeContent,
    NodeMetadata,
    RemediationAction,
    SectionNode,
    Severity,
    iter_reading_order,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documentId: str
    sourceFormat: str
    content: str


class ApiRemediationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionCode: str
    description: str
    requiresAi: bool
    requiresHumanReview: bool
    isAutoApplicable: bool
    supportedNodeTypes: List[str]
    relatedFlagCode: str


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    ruleId: str
    severity: str
    description: str
    nodeId: str
    nodePath: Optional[List[str]] = None
    evidence: Optional[Dict[str, Any]] = None
    recommendedActions: List[ApiRemediationAction]


class ScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scanId: str
    documentId: str
    issues: List[Issue]


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", value.strip())
    return cleaned or "doc"


def _parse_content(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _node_metadata(source_format: str, properties: Optional[Dict[str, Any]] = None) -> NodeMetadata:
    return NodeMetadata(source_format=source_format, properties=properties or {})


def _build_tree(request: ScanRequest) -> AccessibilityTree:
    payload = _parse_content(request.content)
    doc_id = "doc-1"
    title = payload.get("title")
    properties: Dict[str, Any] = {}
    if isinstance(title, str):
        properties["title"] = title

    root = DocumentNode(
        id=doc_id,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=_node_metadata(request.sourceFormat, properties),
        children=[],
        accessibility_flags=[],
    )

    images_payload = payload.get("images")
    if isinstance(images_payload, list):
        images_section = SectionNode(
            id=f"{doc_id}-images",
            content=NodeContent(kind=ContentKind.TEXT, text="Images"),
            metadata=_node_metadata(request.sourceFormat),
            children=[],
            accessibility_flags=[],
        )
        for index, image in enumerate(images_payload, start=1):
            if not isinstance(image, dict):
                continue
            image_id = f"img-{index}"
            alt_text = image.get("alt_text") or image.get("altText") or ""
            if not isinstance(alt_text, str):
                alt_text = ""
            decorative_flag = bool(image.get("decorative"))
            decorative_hint = "decorative" in alt_text.lower()
            is_decorative = decorative_flag or decorative_hint
            metadata = _node_metadata(request.sourceFormat)
            if is_decorative and alt_text:
                node = ImageNode.model_construct(
                    id=image_id,
                    node_type=ImageNode.type_value(),
                    content=NodeContent(kind=ContentKind.NONE),
                    metadata=metadata,
                    children=[],
                    accessibility_flags=[],
                    is_decorative=True,
                    alt_text=alt_text,
                )
            else:
                node = ImageNode(
                    id=image_id,
                    content=NodeContent(kind=ContentKind.NONE),
                    metadata=metadata,
                    children=[],
                    accessibility_flags=[],
                    is_decorative=is_decorative,
                    alt_text=alt_text or None,
                )
            images_section.children.append(node)
        root.children.append(images_section)

    headings_payload = payload.get("headings")
    if isinstance(headings_payload, list):
        headings_section = SectionNode(
            id=f"{doc_id}-headings",
            content=NodeContent(kind=ContentKind.TEXT, text="Headings"),
            metadata=_node_metadata(request.sourceFormat),
            children=[],
            accessibility_flags=[],
        )
        for index, heading in enumerate(headings_payload, start=1):
            if not isinstance(heading, dict):
                continue
            heading_id = str(heading.get("id") or f"h-{index}")
            level = heading.get("level", 1)
            try:
                level_int = int(level)
            except (TypeError, ValueError):
                level_int = 1
            level_int = max(1, min(level_int, 6))
            text = heading.get("text")
            if not isinstance(text, str) or not text.strip():
                text = "Heading"
            node = HeadingNode(
                id=heading_id,
                level=level_int,
                content=NodeContent(kind=ContentKind.TEXT, text=text.strip()),
                metadata=_node_metadata(request.sourceFormat),
                children=[],
                accessibility_flags=[],
            )
            headings_section.children.append(node)
        root.children.append(headings_section)

    return AccessibilityTree(root=root, metadata={})


def _find_path(root: DocumentNode, target_id: str) -> Optional[List[str]]:
    stack: List[tuple[Any, List[str]]] = [(root, [root.id])]
    while stack:
        node, path = stack.pop(0)
        if node.id == target_id:
            return path
        for child in getattr(node, "children", []) or []:
            stack.append((child, path + [child.id]))
    return None


def _action_to_api(action: RemediationAction) -> ApiRemediationAction:
    return ApiRemediationAction(
        actionCode=action.action_code.value,
        description=action.description,
        requiresAi=action.requires_ai,
        requiresHumanReview=action.requires_human_review,
        isAutoApplicable=action.is_auto_applicable,
        supportedNodeTypes=[node.value for node in action.supported_node_types],
        relatedFlagCode=action.related_flag_code.value,
    )


def _issue_description(flag_code: AccessibilityFlagCode) -> str:
    if flag_code == AccessibilityFlagCode.DOCUMENT_TITLE_MISSING:
        return "Document title is missing."
    if flag_code == AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT:
        return "Decorative image has alternative text."
    return flag_code.value.replace("_", " ").title() + "."


def _issue_severity(flag_code: AccessibilityFlagCode, default: Severity) -> str:
    if flag_code == AccessibilityFlagCode.DOCUMENT_TITLE_MISSING:
        return Severity.ERROR.value
    if flag_code == AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT:
        return Severity.WARNING.value
    return default.value


def _build_issues(tree: AccessibilityTree) -> List[Issue]:
    issues: List[Issue] = []
    for node in iter_reading_order(tree.root):
        if not node.accessibility_flags:
            continue
        for flag in node.accessibility_flags:
            path = _find_path(tree.root, node.id)
            evidence: Dict[str, Any] = {}
            if isinstance(node, DocumentNode):
                evidence["title"] = node.metadata.properties.get("title")
            if isinstance(node, ImageNode):
                evidence["alt_text"] = node.alt_text
                evidence["decorative"] = node.is_decorative
            issues.append(
                Issue(
                    id=f"issue-{node.id}-{flag.code.value.lower()}",
                    ruleId=flag.code.value,
                    severity=_issue_severity(flag.code, flag.severity),
                    description=_issue_description(flag.code),
                    nodeId=node.id,
                    nodePath=path,
                    evidence=evidence or None,
                    recommendedActions=[_action_to_api(a) for a in flag.recommended_actions()],
                )
            )
    return issues


@router.post("/scan", response_model=ScanResponse)
async def scan(
    request: ScanRequest,
    user_id: str = Depends(require_user_id),
) -> ScanResponse:
    logger.info("POST /scan documentId=%s sourceFormat=%s", request.documentId, request.sourceFormat)
    tree = _build_tree(request)
    run_analyzers(tree, get_default_analyzers())
    issues = _build_issues(tree)
    scan_id = f"scan-{uuid.uuid4().hex}"

    st = state.for_user(user_id)
    st.last_tree = tree
    st.last_scan_id = scan_id
    st.last_document_id = request.documentId
    st.last_issues = [issue.model_dump() for issue in issues]

    return ScanResponse(scanId=scan_id, documentId=request.documentId, issues=issues)
