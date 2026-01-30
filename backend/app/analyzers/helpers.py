"""Traversal and flag helpers."""

from __future__ import annotations

from typing import Iterable

from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    Node,
    iter_reading_order,
)


def iter_nodes(tree: AccessibilityTree) -> Iterable[Node]:
    return iter_reading_order(tree.root)


def attach_flag(node: Node, code: AccessibilityFlagCode) -> bool:
    for flag in node.accessibility_flags:
        if flag.code == code:
            return False
    node.accessibility_flags.append(AccessibilityFlag.from_code(code))
    return True
