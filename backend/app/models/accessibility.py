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
    HEADING_TEXT_EMPTY = "HEADING_TEXT_EMPTY"
    TABLE_CAPTION_MISSING = "TABLE_CAPTION_MISSING"
    LINK_TARGET_BROKEN = "LINK_TARGET_BROKEN"
    LOW_CONTRAST_TEXT = "LOW_CONTRAST_TEXT"
    FORM_FIELD_UNLABELED = "FORM_FIELD_UNLABELED"
    SLIDE_TITLE_MISSING = "SLIDE_TITLE_MISSING"
    ALT_TEXT_NOT_DESCRIPTIVE = "ALT_TEXT_NOT_DESCRIPTIVE"
    DOCUMENT_NO_HEADINGS = "DOCUMENT_NO_HEADINGS"
    SCANNED_DOCUMENT_NO_TEXT = "SCANNED_DOCUMENT_NO_TEXT"
    PDF_UNTAGGED = "PDF_UNTAGGED"
    TEXT_STYLED_AS_HEADING = "TEXT_STYLED_AS_HEADING"


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
    AccessibilityFlagCode.HEADING_TEXT_EMPTY: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.HEADING_TEXT_EMPTY,
        severity=Severity.ERROR,
        message="Heading has no text content.",
        standards=StandardReference(
            wcag_2_1=["2.4.6"],
            section_508=["E207.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
    AccessibilityFlagCode.TABLE_CAPTION_MISSING: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.TABLE_CAPTION_MISSING,
        severity=Severity.WARNING,
        message="Table is missing a caption or descriptive label.",
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E205.2"],
            pdf_ua=["7.3-5"],
        ),
    ),
    AccessibilityFlagCode.LINK_TARGET_BROKEN: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.LINK_TARGET_BROKEN,
        severity=Severity.WARNING,
        message="Link target is missing, empty, or unsafe.",
        standards=StandardReference(
            wcag_2_1=["2.4.4"],
            section_508=["E205.4"],
            pdf_ua=["7.6-6"],
        ),
    ),
    AccessibilityFlagCode.LOW_CONTRAST_TEXT: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.LOW_CONTRAST_TEXT,
        severity=Severity.WARNING,
        message="Text colour may not meet the WCAG AA minimum contrast ratio.",
        standards=StandardReference(
            wcag_2_1=["1.4.3"],
            section_508=["E205.4"],
            pdf_ua=[],
        ),
    ),
    AccessibilityFlagCode.FORM_FIELD_UNLABELED: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.FORM_FIELD_UNLABELED,
        severity=Severity.ERROR,
        message="One or more form fields are missing an accessible label.",
        standards=StandardReference(
            wcag_2_1=["3.3.2", "4.1.2"],
            section_508=["E205.4"],
            pdf_ua=["7.18-1"],
        ),
    ),
    AccessibilityFlagCode.SLIDE_TITLE_MISSING: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.SLIDE_TITLE_MISSING,
        severity=Severity.ERROR,
        message="One or more slides are missing a title.",
        standards=StandardReference(
            wcag_2_1=["2.4.2", "1.3.1"],
            section_508=["E205.4"],
            pdf_ua=[],
        ),
    ),
    AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE,
        severity=Severity.WARNING,
        message="Image alternative text is a filename or generic placeholder, not a description.",
        standards=StandardReference(
            wcag_2_1=["1.1.1"],
            section_508=["E205.1"],
            pdf_ua=["7.1-4"],
        ),
    ),
    AccessibilityFlagCode.DOCUMENT_NO_HEADINGS: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.DOCUMENT_NO_HEADINGS,
        severity=Severity.WARNING,
        message="Document has substantial text but no headings to organize it.",
        standards=StandardReference(
            wcag_2_1=["2.4.6", "1.3.1"],
            section_508=["E207.2"],
            pdf_ua=["7.3-1"],
        ),
    ),
    AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT,
        severity=Severity.ERROR,
        message=(
            "Document appears to be scanned image(s) with little or no extractable text. "
            "OCR is required before any accessibility remediation can apply."
        ),
        standards=StandardReference(
            wcag_2_1=["1.1.1", "1.4.5"],
            section_508=["E205.1"],
            pdf_ua=["7.1-2"],
        ),
    ),
    AccessibilityFlagCode.PDF_UNTAGGED: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.PDF_UNTAGGED,
        severity=Severity.ERROR,
        message=(
            "PDF has no structure tags — screen readers see undifferentiated text "
            "with no headings, lists, or tables."
        ),
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E205.4"],
            pdf_ua=["7.1-2"],
        ),
    ),
    AccessibilityFlagCode.TEXT_STYLED_AS_HEADING: AccessibilityFlagDefinition(
        code=AccessibilityFlagCode.TEXT_STYLED_AS_HEADING,
        severity=Severity.WARNING,
        message=(
            "Text is visually styled as a heading (large/bold or Title style) "
            "but is not a real heading, so it is missing from the navigation outline."
        ),
        standards=StandardReference(
            wcag_2_1=["1.3.1"],
            section_508=["E207.2"],
            pdf_ua=[],
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

    def recommended_actions(self) -> List["RemediationAction"]:
        return list(REMEDIATION_ACTIONS_BY_FLAG.get(self.code, []))


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
    model_config = ConfigDict(extra="forbid")

    action_code: "ActionCode"
    description: str
    requires_ai: bool
    requires_human_review: bool
    is_auto_applicable: bool
    supported_node_types: List[NodeType]
    related_flag_code: AccessibilityFlagCode


class ActionCode(str, Enum):
    GENERATE_ALT_TEXT = "GENERATE_ALT_TEXT"
    REMOVE_DECORATIVE_ALT_TEXT = "REMOVE_DECORATIVE_ALT_TEXT"
    NORMALIZE_HEADING_LEVEL = "NORMALIZE_HEADING_LEVEL"
    PROMOTE_HEADING = "PROMOTE_HEADING"
    ADD_TABLE_HEADERS = "ADD_TABLE_HEADERS"
    SET_TABLE_HEADER_SCOPE = "SET_TABLE_HEADER_SCOPE"
    FIX_LIST_STRUCTURE = "FIX_LIST_STRUCTURE"
    IMPROVE_LINK_TEXT = "IMPROVE_LINK_TEXT"
    SET_DOCUMENT_LANGUAGE = "SET_DOCUMENT_LANGUAGE"
    SET_DOCUMENT_TITLE = "SET_DOCUMENT_TITLE"
    RESOLVE_READING_ORDER = "RESOLVE_READING_ORDER"
    TAG_PDF_STRUCTURE = "TAG_PDF_STRUCTURE"
    ADD_OCR_TEXT_LAYER = "ADD_OCR_TEXT_LAYER"
    FLAG_FOR_MANUAL_REVIEW = "FLAG_FOR_MANUAL_REVIEW"


REMEDIATION_ACTIONS_BY_FLAG: Dict[AccessibilityFlagCode, List[RemediationAction]] = {
    AccessibilityFlagCode.PDF_UNTAGGED: [
        RemediationAction(
            action_code=ActionCode.TAG_PDF_STRUCTURE,
            description="Reconstruct a PDF/UA structure tree (headings, lists, tables, figures, artifacts).",
            requires_ai=False,
            requires_human_review=False,
            is_auto_applicable=True,
            supported_node_types=[NodeType.DOCUMENT],
            related_flag_code=AccessibilityFlagCode.PDF_UNTAGGED,
        )
    ],
    AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT: [
        RemediationAction(
            action_code=ActionCode.ADD_OCR_TEXT_LAYER,
            description=(
                "Recognize the scanned pages with OCR and add an invisible, "
                "position-matched text layer (requires OCR enabled on the deployment)."
            ),
            requires_ai=False,
            requires_human_review=False,
            is_auto_applicable=True,
            supported_node_types=[NodeType.DOCUMENT],
            related_flag_code=AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.DOCUMENT],
            related_flag_code=AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT,
        ),
    ],
    AccessibilityFlagCode.MISSING_ALT_TEXT: [
        RemediationAction(
        action_code=ActionCode.GENERATE_ALT_TEXT,
        description="Generate alternative text for meaningful images.",
        requires_ai=True,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.IMAGE],
        related_flag_code=AccessibilityFlagCode.MISSING_ALT_TEXT,
        )
    ],
    AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE: [
        RemediationAction(
        action_code=ActionCode.GENERATE_ALT_TEXT,
        description="Replace filename/placeholder alt text with a real description.",
        requires_ai=True,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.IMAGE],
        related_flag_code=AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE,
        )
    ],
    AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT: [
        RemediationAction(
        action_code=ActionCode.REMOVE_DECORATIVE_ALT_TEXT,
        description="Remove alternative text from decorative images.",
        requires_ai=False,
        requires_human_review=False,
        is_auto_applicable=True,
        supported_node_types=[NodeType.IMAGE],
        related_flag_code=AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT,
        )
    ],
    AccessibilityFlagCode.HEADING_LEVEL_JUMP: [
        RemediationAction(
        action_code=ActionCode.NORMALIZE_HEADING_LEVEL,
        description="Normalize heading levels to preserve hierarchy.",
        requires_ai=False,
        requires_human_review=False,
        is_auto_applicable=True,
        supported_node_types=[NodeType.HEADING],
        related_flag_code=AccessibilityFlagCode.HEADING_LEVEL_JUMP,
        )
    ],
    AccessibilityFlagCode.SKIPPED_HEADING_LEVEL: [
        RemediationAction(
            action_code=ActionCode.NORMALIZE_HEADING_LEVEL,
            description="Normalize heading levels to preserve hierarchy.",
            requires_ai=False,
            requires_human_review=False,
            is_auto_applicable=True,
            supported_node_types=[NodeType.HEADING],
            related_flag_code=AccessibilityFlagCode.SKIPPED_HEADING_LEVEL,
        )
    ],
    AccessibilityFlagCode.TEXT_STYLED_AS_HEADING: [
        RemediationAction(
            action_code=ActionCode.PROMOTE_HEADING,
            description=(
                "Promote text that only looks like a heading into a real heading "
                "so screen-reader users can navigate to it."
            ),
            requires_ai=False,
            requires_human_review=False,
            is_auto_applicable=True,
            supported_node_types=[NodeType.PARAGRAPH],
            related_flag_code=AccessibilityFlagCode.TEXT_STYLED_AS_HEADING,
        )
    ],
    AccessibilityFlagCode.TABLE_MISSING_HEADERS: [
        RemediationAction(
        action_code=ActionCode.ADD_TABLE_HEADERS,
        description="Add table header cells for data tables.",
        requires_ai=False,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.TABLE],
        related_flag_code=AccessibilityFlagCode.TABLE_MISSING_HEADERS,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.TABLE],
            related_flag_code=AccessibilityFlagCode.TABLE_MISSING_HEADERS,
        ),
    ],
    AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID: [
        RemediationAction(
        action_code=ActionCode.SET_TABLE_HEADER_SCOPE,
        description="Set table header scope for header cells.",
        requires_ai=False,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.TABLE_CELL],
        related_flag_code=AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.TABLE_CELL],
            related_flag_code=AccessibilityFlagCode.TABLE_HEADER_SCOPE_INVALID,
        ),
    ],
    AccessibilityFlagCode.LIST_STRUCTURE_INVALID: [
        RemediationAction(
        action_code=ActionCode.FIX_LIST_STRUCTURE,
        description="Normalize list structure to valid list/list_item hierarchy.",
        requires_ai=False,
        requires_human_review=False,
        is_auto_applicable=True,
        # LIST = malformed ListNode tree; PARAGRAPH = a typed fake-list run
        # ("- item" paragraphs) being converted into a real Word list.
        supported_node_types=[NodeType.LIST, NodeType.PARAGRAPH],
        related_flag_code=AccessibilityFlagCode.LIST_STRUCTURE_INVALID,
        )
    ],
    AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE: [
        RemediationAction(
        action_code=ActionCode.IMPROVE_LINK_TEXT,
        description="Provide descriptive link text.",
        requires_ai=False,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.LINK],
        related_flag_code=AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.LINK],
            related_flag_code=AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE,
        ),
    ],
    AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING: [
        RemediationAction(
        action_code=ActionCode.SET_DOCUMENT_LANGUAGE,
        description="Set the document language metadata.",
        requires_ai=False,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.DOCUMENT],
        related_flag_code=AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.DOCUMENT],
            related_flag_code=AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING,
        ),
    ],
    AccessibilityFlagCode.DOCUMENT_TITLE_MISSING: [
        RemediationAction(
        action_code=ActionCode.SET_DOCUMENT_TITLE,
        description="Set the document title metadata.",
        requires_ai=False,
        requires_human_review=True,
        is_auto_applicable=False,
        supported_node_types=[NodeType.DOCUMENT],
        related_flag_code=AccessibilityFlagCode.DOCUMENT_TITLE_MISSING,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.DOCUMENT],
            related_flag_code=AccessibilityFlagCode.DOCUMENT_TITLE_MISSING,
        ),
    ],
    AccessibilityFlagCode.READING_ORDER_AMBIGUOUS: [
        RemediationAction(
        action_code=ActionCode.RESOLVE_READING_ORDER,
        description="Resolve ambiguous reading order.",
        requires_ai=False,
        requires_human_review=True,
        is_auto_applicable=False,
        # SECTION = a slide whose text shapes are stacked bottom-before-top.
        supported_node_types=[NodeType.DOCUMENT, NodeType.SECTION],
        related_flag_code=AccessibilityFlagCode.READING_ORDER_AMBIGUOUS,
        ),
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.DOCUMENT],
            related_flag_code=AccessibilityFlagCode.READING_ORDER_AMBIGUOUS,
        ),
    ],
    AccessibilityFlagCode.HEADING_TEXT_EMPTY: [
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.HEADING],
            related_flag_code=AccessibilityFlagCode.HEADING_TEXT_EMPTY,
        ),
    ],
    AccessibilityFlagCode.TABLE_CAPTION_MISSING: [
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.TABLE],
            related_flag_code=AccessibilityFlagCode.TABLE_CAPTION_MISSING,
        ),
    ],
    AccessibilityFlagCode.LINK_TARGET_BROKEN: [
        RemediationAction(
            action_code=ActionCode.FLAG_FOR_MANUAL_REVIEW,
            description="Flag issue for manual review.",
            requires_ai=False,
            requires_human_review=True,
            is_auto_applicable=False,
            supported_node_types=[NodeType.LINK],
            related_flag_code=AccessibilityFlagCode.LINK_TARGET_BROKEN,
        ),
    ],
}


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
