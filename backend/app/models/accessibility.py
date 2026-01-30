"""Accessibility tree data models and validation helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NodeType(str, Enum):
    DOCUMENT = "document"
    SECTION = "section"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    IMAGE = "image"
    LINK = "link"
    ARTIFACT = "artifact"


class ContentKind(str, Enum):
    TEXT = "text"
    REFERENCE = "reference"
    NONE = "none"


class NodeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: Optional[int] = None
    language: Optional[str] = None
    source_format: Optional[str] = None
    source_reference: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)


class NodeContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContentKind
    text: Optional[str] = None
    reference: Optional[str] = None

    @model_validator(mode="after")
    def _validate_content(self) -> "NodeContent":
        if self.kind == ContentKind.TEXT and not self.text:
            raise ValueError("text content requires non-empty text")
        if self.kind == ContentKind.REFERENCE and not self.reference:
            raise ValueError("reference content requires a reference id")
        if self.kind == ContentKind.NONE and (self.text or self.reference):
            raise ValueError("none content cannot include text or reference")
        return self


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class AccessibilityFlagCode(str, Enum):
    MISSING_ALT_TEXT = "MISSING_ALT_TEXT"
    DECORATIVE_IMAGE_WITH_ALT = "DECORATIVE_IMAGE_WITH_ALT"
    HEADING_LEVEL_JUMP = "HEADING_LEVEL_JUMP"
    SKIPPED_HEADING_LEVEL = "SKIPPED_HEADING_LEVEL"
    TABLE_MISSING_HEADERS = "TABLE_MISSING_HEADERS"
    TABLE_HEADER_SCOPE_INVALID = "TABLE_HEADER_SCOPE_INVALID"
    LIST_STRUCTURE_INVALID = "LIST_STRUCTURE_INVALID"
    LINK_TEXT_NON_DESCRIPTIVE = "LINK_TEXT_NON_DESCRIPTIVE"
    DOCUMENT_LANGUAGE_MISSING = "DOCUMENT_LANGUAGE_MISSING"
    DOCUMENT_TITLE_MISSING = "DOCUMENT_TITLE_MISSING"
    READING_ORDER_AMBIGUOUS = "READING_ORDER_AMBIGUOUS"


class StandardReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wcag_2_1: List[str] = Field(default_factory=list)
    section_508: List[str] = Field(default_factory=list)
    pdf_ua: List[str] = Field(default_factory=list)


class AccessibilityFlagDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AccessibilityFlagCode
    severity: Severity
    message: str
    standards: StandardReference


FLAG_DEFINITIONS: Dict[AccessibilityFlagCode, AccessibilityFlagDefinition] = {
    AccessibilityFlagCode.MISSING_ALT_TEXT: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.MISSING_ALT_TEXT,
        severity=Severity.ERROR,
        message="Image is missing alternative text.",
        standards=StandardReference(
            wcag_2_1=["1.1.1"],
            section_508=["E205.1"],
            pdf_ua=["7.1-4"],
        ),
    ),
    AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT,
        severity=Severity.WARNING,
        message="Decorative image includes alternative text.",
        standards=StandardReference(
            wcag_2_1=["1.1.1"],
            section_508=["E205.1"],
            pdf_ua=["7.1-4"],
        ),
    ),
    AccessibilityFlagCode.HEADING_LEVEL_JUMP: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.HEADING_LEVEL_JUMP,
        severity=Severity.WARNING,
        message="Heading levels jump by more than one.",
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E207.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
    AccessibilityFlagCode.SKIPPED_HEADING_LEVEL: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.SKIPPED_HEADING_LEVEL,
        severity=Severity.WARNING,
        message="Heading level is skipped in the document outline.",
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E207.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
    AccessibilityFlagCode.TABLE_MISSING_HEADERS: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.TABLE_MISSING_HEADERS,
        severity=Severity.ERROR,
        message="Table is missing header cells.",
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E205.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
    AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID,
        severity=Severity.WARNING,
        message="Table header cell has invalid or missing scope.",
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E205.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
    AccessibilityFlagCode.LIST_STRUCTURE_INVALID: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.LIST_STRUCTURE_INVALID,
        severity=Severity.WARNING,
        message="List structure is invalid or contains non-list children.",
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E207.2"],
            pdf_ua=["7.3-3"],
        ),
    ),
    AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE,
        severity=Severity.WARNING,
        message="Link text is not descriptive.",
        standards=StandardReference(
            wcag_2_1=["2.4.4"],
            section_508=["E205.4"],
            pdf_ua=["7.6-6"],
        ),
    ),
    AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING,
        severity=Severity.ERROR,
        message="Document language is missing.",
        standards=StandardReference(
            wcag_2_1=["3.1.1"],
            section_508=["E207.1"],
            pdf_ua=["7.2-1"],
        ),
    ),
    AccessibilityFlagCode.DOCUMENT_TITLE_MISSING: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.DOCUMENT_TITLE_MISSING,
        severity=Severity.WARNING,
        message="Document title is missing.",
        standards=StandardReference(
            wcag_2_1=["2.4.2"],
            section_508=["E207.4"],
            pdf_ua=["7.1-2"],
        ),
    ),
    AccessibilityFlagCode.READING_ORDER_AMBIGUOUS: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.READING_ORDER_AMBIGUOUS,
        severity=Severity.WARNING,
        message="Reading order is ambiguous or inconsistent.",
        standards=StandardReference(
            wcag_2_1=["1.3.2"],
            section_508=["E207.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
}


class AccessibilityFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AccessibilityFlagCode
    severity: Severity
    message: str
    standards: StandardReference

    @model_validator(mode="after")
    def _validate_definition(self) -> "AccessibilityFlag":
        definition = FLAG_DEFINITIONS.get(self.code)
        if not definition:
            raise ValueError("accessibility flag code is not registered")
        if self.severity != definition.severity:
            raise ValueError("accessibility flag severity does not match registry")
        if self.message != definition.message:
            raise ValueError("accessibility flag message does not match registry")
        if self.standards != definition.standards:
            raise ValueError("accessibility flag standards do not match registry")
        return self

    @classmethod
    def from_code(cls, code: AccessibilityFlagCode) -> "AccessibilityFlag":
        definition = FLAG_DEFINITIONS[code]
        return cls(
            code=definition.code,
            severity=definition.severity,
            message=definition.message,
            standards=definition.standards,
        )


class BaseNode(BaseModel, ABC):
    model_config = ConfigDict(extra="forbid")

    id: str
    node_type: NodeType
    content: NodeContent
    metadata: NodeMetadata
    children: List["Node"] = Field(default_factory=list)
    accessibility_flags: List[AccessibilityFlag] = Field(default_factory=list)

    @classmethod
    @abstractmethod
    def type_value(cls) -> NodeType:
        raise NotImplementedError


class DocumentNode(BaseNode):
    node_type: Literal[NodeType.DOCUMENT] = NodeType.DOCUMENT

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.DOCUMENT


class SectionNode(BaseNode):
    node_type: Literal[NodeType.SECTION] = NodeType.SECTION

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.SECTION


class HeadingNode(BaseNode):
    node_type: Literal[NodeType.HEADING] = NodeType.HEADING
    level: int = Field(ge=1, le=6)

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.HEADING


class ParagraphNode(BaseNode):
    node_type: Literal[NodeType.PARAGRAPH] = NodeType.PARAGRAPH

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.PARAGRAPH


class ListNode(BaseNode):
    node_type: Literal[NodeType.LIST] = NodeType.LIST
    ordered: bool = False
    marker: Optional[str] = None

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.LIST


class ListItemNode(BaseNode):
    node_type: Literal[NodeType.LIST_ITEM] = NodeType.LIST_ITEM

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.LIST_ITEM


class TableNode(BaseNode):
    node_type: Literal[NodeType.TABLE] = NodeType.TABLE

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.TABLE


class TableRowNode(BaseNode):
    node_type: Literal[NodeType.TABLE_ROW] = NodeType.TABLE_ROW

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.TABLE_ROW


class TableCellType(str, Enum):
    HEADER = "header"
    DATA = "data"


class TableHeaderScope(str, Enum):
    ROW = "row"
    COLUMN = "column"
    BOTH = "both"
    NONE = "none"


class TableCellNode(BaseNode):
    node_type: Literal[NodeType.TABLE_CELL] = NodeType.TABLE_CELL
    cell_type: TableCellType
    header_scope: TableHeaderScope = TableHeaderScope.NONE
    row_span: int = Field(default=1, ge=1)
    col_span: int = Field(default=1, ge=1)

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.TABLE_CELL


class ImageNode(BaseNode):
    node_type: Literal[NodeType.IMAGE] = NodeType.IMAGE
    is_decorative: bool = False
    alt_text: Optional[str] = None

    @model_validator(mode="after")
    def _validate_decorative(self) -> "ImageNode":
        if self.is_decorative and self.alt_text:
            raise ValueError("decorative images must not include alt text")
        return self

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.IMAGE


class LinkNode(BaseNode):
    node_type: Literal[NodeType.LINK] = NodeType.LINK
    target: Optional[str] = None

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.LINK


class ArtifactNode(BaseNode):
    node_type: Literal[NodeType.ARTIFACT] = NodeType.ARTIFACT

    @classmethod
    def type_value(cls) -> NodeType:
        return NodeType.ARTIFACT


Node = Annotated[
    Union[
        DocumentNode,
        SectionNode,
        HeadingNode,
        ParagraphNode,
        ListNode,
        ListItemNode,
        TableNode,
        TableRowNode,
        TableCellNode,
        ImageNode,
        LinkNode,
        ArtifactNode,
    ],
    Field(discriminator="node_type"),
]


class AccessibilityTree(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: DocumentNode
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


class HeadingHierarchyIssue(BaseModel):
    previous_heading_id: Optional[str]
    previous_level: Optional[int]
    current_heading_id: str
    current_level: int
    message: str


class TableStructureIssue(BaseModel):
    table_id: str
    message: str
    path: List[str] = Field(default_factory=list)


def iter_reading_order(root: Node) -> Iterable[Node]:
    stack: List[Node] = [root]
    while stack:
        node = stack.pop(0)
        yield node
        if node.children:
            stack[0:0] = node.children


def validate_heading_hierarchy(tree: AccessibilityTree) -> List[HeadingHierarchyIssue]:
    issues: List[HeadingHierarchyIssue] = []
    previous_heading: Optional[HeadingNode] = None
    for node in iter_reading_order(tree.root):
        if isinstance(node, HeadingNode):
            if previous_heading is not None:
                if node.level > previous_heading.level + 1:
                    issues.append(
                        HeadingHierarchyIssue(
                            previous_heading_id=previous_heading.id,
                            previous_level=previous_heading.level,
                            current_heading_id=node.id,
                            current_level=node.level,
                            message="Heading level jumps by more than one.",
                        )
                    )
            previous_heading = node
    return issues


def validate_table_structure(tree: AccessibilityTree) -> List[TableStructureIssue]:
    issues: List[TableStructureIssue] = []
    for node in iter_reading_order(tree.root):
        if isinstance(node, TableNode):
            for row in node.children:
                if not isinstance(row, TableRowNode):
                    issues.append(
                        TableStructureIssue(
                            table_id=node.id,
                            message="Table contains a non-row child.",
                            path=[node.id, row.id],
                        )
                    )
                    continue
                for cell in row.children:
                    if not isinstance(cell, TableCellNode):
                        issues.append(
                            TableStructureIssue(
                                table_id=node.id,
                                message="Table row contains a non-cell child.",
                                path=[node.id, row.id, cell.id],
                            )
                        )
            if not node.children:
                issues.append(
                    TableStructureIssue(
                        table_id=node.id,
                        message="Table has no rows.",
                        path=[node.id],
                    )
                )
    return issues


def export_tree_schema() -> Dict[str, Any]:
    return AccessibilityTree.model_json_schema()
