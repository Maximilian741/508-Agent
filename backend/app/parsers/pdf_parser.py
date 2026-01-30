"""PDF parser stub."""

from app.models.accessibility import ParserResult


class PDFParser:
    def parse(self, file_path: str) -> ParserResult:
        raise NotImplementedError("PDF parsing is not yet implemented.")