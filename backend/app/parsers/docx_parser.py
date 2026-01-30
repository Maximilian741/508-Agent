"""DOCX parser stub."""

from app.models.accessibility import ParserResult


class DOCXParser:
    def parse(self, file_path: str) -> ParserResult:
        raise NotImplementedError("DOCX parsing is not yet implemented.")