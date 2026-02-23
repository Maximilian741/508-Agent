from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class Repository(ABC):
    @abstractmethod
    def save_document(self, doc: Dict[str, object]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def update_document(self, doc_id: str, updates: Dict[str, object]) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_documents(self) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def save_job(self, job: Dict[str, object]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def update_job(self, job_id: str, updates: Dict[str, object]) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_issues(self, doc_id: str, phase: str, issues: List[Dict[str, object]], keys: List[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_issues(self, doc_id: str, phase: str) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def get_latest_issues(self, doc_id: str) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def save_fix_report(self, doc_id: str, report: Dict[str, object]) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_fix_report(self, doc_id: str) -> Optional[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def add_manual_review_items(self, doc_id: str, items: List[Dict[str, object]]) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_manual_review_items(self) -> List[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def clear_manual_review_items(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_manual_review_item(self, item_id: str) -> Optional[Dict[str, object]]:
        raise NotImplementedError

    @abstractmethod
    def update_manual_review_item(self, item_id: str, item: Dict[str, object], resolved: bool = False) -> bool:
        raise NotImplementedError
