"""Devtools-only fixtures for deterministic smoke tests."""

from __future__ import annotations

from typing import Tuple

from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    HeadingNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    ImageNode,
    NodeType,
    NodeContent,
    NodeMetadata,
)


def decorative_image_with_alt_flag_tree() -> Tuple[AccessibilityTree, ImageNode]:
    image_node = ImageNode.model_construct(
        id="img-1",
        node_type=NodeType.IMAGE,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[],
        accessibility_flags=[AccessibilityFlag.from_code(AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT)],
        is_decorative=True,
        alt_text="decorative flourish",
    )
    root = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(language="en", source_format="pdf"),
        children=[],
        accessibility_flags=[],
    )
    root.children.append(image_node)
    tree = AccessibilityTree(root=root)
    return tree, image_node


def heading_level_jump_tree() -> Tuple[AccessibilityTree, HeadingNode, HeadingNode]:
    heading_one = HeadingNode(
        id="h1",
        content=NodeContent(kind=ContentKind.TEXT, text="Heading 1"),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[],
        accessibility_flags=[],
        level=1,
    )
    heading_jump = HeadingNode(
        id="h2",
        content=NodeContent(kind=ContentKind.TEXT, text="Heading 2"),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[],
        accessibility_flags=[AccessibilityFlag.from_code(AccessibilityFlagCode.HEADING_LEVEL_JUMP)],
        level=4,
    )
    tree = AccessibilityTree(
        root=DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(language="en", source_format="pdf"),
            children=[heading_one, heading_jump],
            accessibility_flags=[],
        )
    )
    return tree, heading_one, heading_jump


def invalid_list_structure_tree() -> Tuple[AccessibilityTree, ListNode]:
    paragraph = ParagraphNode(
        id="p-1",
        content=NodeContent(kind=ContentKind.TEXT, text="Item text"),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[],
        accessibility_flags=[],
    )
    list_item = ListItemNode(
        id="li-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[],
        accessibility_flags=[],
    )
    list_node = ListNode(
        id="list-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(page=1, source_format="pdf"),
        children=[paragraph, list_item],
        accessibility_flags=[AccessibilityFlag.from_code(AccessibilityFlagCode.LIST_STRUCTURE_INVALID)],
        ordered=False,
        marker=None,
    )
    tree = AccessibilityTree(
        root=DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(language="en", source_format="pdf"),
            children=[list_node],
            accessibility_flags=[],
        )
    )
    return tree, list_node
