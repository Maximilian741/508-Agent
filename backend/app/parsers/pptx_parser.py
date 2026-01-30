"""PPTX parser stub."""

from app.models.accessibility import ParserResult


class PPTXParser:
    def parse(self, file_path: str) -> ParserResult:
        raise NotImplementedError("PPTX parsing is not yet implemented.")