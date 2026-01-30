"""Accessibility tree data models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AccessibilityNode(BaseModel):
    node_id: str
    role: str
    name: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    children: List["AccessibilityNode"] = Field(default_factory=list)


class AccessibilityTree(BaseModel):
    root: AccessibilityNode
    metadata: Dict[str, Any] = Field(default_factory=dict)


class NodeLocation(BaseModel):
    node_id: str
    path: List[str] = Field(default_factory=list)


class Violation(BaseModel):
    violation_id: str
    rule_id: str
    severity: str
    description: str
    location: NodeLocation
    evidence: Dict[str, Any] = Field(default_factory=dict)


class RemediationAction(BaseModel):
    action_id: str
    action_type: str
    target_node_id: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rationale: str
    deterministic: bool = True


class RemediationLogEntry(BaseModel):
    timestamp: datetime
    action_id: str
    status: str
    details: Dict[str, Any] = Field(default_factory=dict)


class RemediationReport(BaseModel):
    document_id: str
    violations: List[Violation] = Field(default_factory=list)
    actions: List[RemediationAction] = Field(default_factory=list)
    logs: List[RemediationLogEntry] = Field(default_factory=list)


class ParserResult(BaseModel):
    document_id: str
    format: str
    tree: AccessibilityTree
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)