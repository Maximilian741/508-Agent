"""Analyzer base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.accessibility import AccessibilityTree


class Analyzer(ABC):
    name: str = "unnamed"

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def run(self, tree: AccessibilityTree) -> None:
        if not self.enabled:
            return
        self.analyze(tree)

    @abstractmethod
    def analyze(self, tree: AccessibilityTree) -> None:
        raise NotImplementedError
