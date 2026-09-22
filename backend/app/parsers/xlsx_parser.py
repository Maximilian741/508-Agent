"""XLSX (Excel workbook) parser.

Reads the OOXML package DIRECTLY (zipfile + lxml) rather than through
openpyxl, for two reasons that both come down to the honesty rule:

* openpyxl's reader keeps pictures and charts but forgets their alt text,
  drops shapes and text boxes, and its writer stamps ``descr="Picture"`` on
  every image it saves. A parser built on it would report the wrong alt text,
  and a writer built on it would *destroy* the alt text a customer already
  wrote. The XLSX writer (``app.writers.xlsx_writer``) therefore edits the few
  XML parts a fix touches and copies every other part through untouched;
  openpyxl is only used afterwards to prove the output still opens.
* a real workbook can hold a million cells. We stream each sheet with
  ``iterparse`` and keep a bounded window of it, so a large export costs
  seconds, not gigabytes.

What the tree carries
---------------------
* The document root: core-properties title and language (``dc:title`` /
  ``dc:language``), the filename, and a ``title_candidate`` when the first
  sheet opens with a clear title line.
* One :class:`SectionNode` per VISIBLE sheet, with its tab name and whether
  that name is still an application default ("Sheet1", "Feuil1", "Chart1").
* One :class:`TableNode` per block of data. A block is found by cutting the
  sheet at blank rows and columns; an existing Excel table is taken as
  declared. Each block records ``header_detection``:

    - ``declared``  an Excel table with its header row on (cells are HEADER);
    - ``candidate`` a plain range whose first row is unambiguously a row of
      column headings (all text, distinct, and set apart from the data by
      type or formatting) and which can safely become an Excel table;
    - ``unclear``   anything else, with ``header_reason`` saying why in plain
      English;
    - ``off``       an Excel table whose header row is switched off.

  Only ``candidate`` blocks are offered the automatic fix. The table's rows
  are a SAMPLE (the first row and a few data rows, capped in width); the true
  size is in ``rows`` / ``cols`` and the address in ``cell_range``. Spreadsheet
  data is supposed to be large, so the document-oriented "big grid needs a
  summary" rule is not applied to it by feeding it every row.
* One :class:`ParagraphNode` for each titled/note text cell outside the data
  (bounded per sheet): they are what the language detector samples and they
  act as the label of the block that follows them.
* One :class:`ImageNode` per picture and chart, with its existing alt text
  (``descr``), the decorative flag, the cell it is anchored to, and — for a
  chart — a description built from the chart's OWN title, type and series
  names. That is text the author wrote for the chart, so it is offered to the
  alt-text generator as a caption; a picture with no such text gets none and
  is left for a person (or a vision model) instead of receiving a placeholder.
"""

from __future__ import annotations

import base64
import os
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from lxml import etree

from app.models.accessibility import (
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    ImageNode,
    NodeContent,
    NodeMetadata,
    ParagraphNode,
    ParserResult,
    SectionNode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
)
from app.parsers.document_id import derive_document_id

try:
    from app.ai.semantic_inference import vision_provider_configured as _vpc

    _WANT_IMAGE_BYTES = bool(_vpc())
except Exception:  # pragma: no cover - never let the AI module break parsing
    _WANT_IMAGE_BYTES = True


# ---------------------------------------------------------------------------
# Namespaces and relationship types
# ---------------------------------------------------------------------------

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS_CX = "http://schemas.microsoft.com/office/drawing/2014/chartex"
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
NS_ADEC = "http://schemas.microsoft.com/office/drawing/2017/decorative"
# ISO/IEC 29500 "Strict" spreadsheets use a different main namespace.
NS_MAIN_STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"

_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
REL_OFFICE_DOCUMENT = _R + "officeDocument"
REL_WORKSHEET = _R + "worksheet"
REL_CHARTSHEET = _R + "chartsheet"
REL_SHARED_STRINGS = _R + "sharedStrings"
REL_STYLES = _R + "styles"
REL_DRAWING = _R + "drawing"
REL_TABLE = _R + "table"
REL_PIVOT = _R + "pivotTable"
REL_CHART = _R + "chart"
REL_IMAGE = _R + "image"
REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"

CT_TABLE = "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"
CT_CORE = "application/vnd.openxmlformats-package.core-properties+xml"

_DECORATIVE_EXT_URI = "{C183D7F6-B498-43B3-948B-1728B52AA6E4}"
_TABLE_ALT_EXT_URI = "{504A1905-F514-4f6f-8877-14C23A59335A}"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# Limits. Every one of them exists so a hostile or merely enormous workbook
# costs bounded time and memory; each one that trips is DISCLOSED (an
# ``unclear`` block or ANALYSIS_TRUNCATED), never silently ignored.
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


SCAN_ROWS = _env_int("XLSX_SCAN_ROWS", 5000, 50, 200000)
SCAN_COLS = 200
SAMPLE_BODY_ROWS = 5
SAMPLE_COLS = 12
EVAL_BODY_ROWS = 25
MAX_PARAGRAPHS_PER_SHEET = 40
MAX_BLOCKS_PER_SHEET = 100
MAX_SHARED_STRINGS = 2_000_000
# The writer parses a whole sheet part with lxml to add a table to it; above
# this size it refuses, so the parser does not offer the fix either.
MAX_SHEET_XML_FOR_TABLE_FIX = 60 * 1024 * 1024
MAX_IMAGE_BYTES_FOR_VISION = 4 * 1024 * 1024


# ---------------------------------------------------------------------------
# Cell-reference helpers
# ---------------------------------------------------------------------------

_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")


def col_to_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def index_to_col(n: int) -> str:
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def parse_cell_ref(ref: str) -> Optional[Tuple[int, int]]:
    m = _REF_RE.match((ref or "").strip())
    if not m:
        return None
    return int(m.group(2)), col_to_index(m.group(1))


def parse_range(ref: str) -> Optional[Tuple[int, int, int, int]]:
    """``"B3:E40"`` -> (r1, c1, r2, c2); a single cell is a 1x1 range."""
    text = (ref or "").strip()
    if not text:
        return None
    # A sqref can list several ranges; the first is what callers want here.
    text = text.split()[0]
    parts = text.split(":")
    a = parse_cell_ref(parts[0])
    b = parse_cell_ref(parts[-1])
    if a is None or b is None:
        return None
    r1, c1 = a
    r2, c2 = b
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


def format_range(r1: int, c1: int, r2: int, c2: int) -> str:
    a = f"{index_to_col(c1)}{r1}"
    b = f"{index_to_col(c2)}{r2}"
    return a if a == b else f"{a}:{b}"


def ranges_intersect(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# ---------------------------------------------------------------------------
# Package access
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rel:
    rid: str
    type: str
    target: str          # resolved part name, or the raw target when external
    external: bool


def rels_part_for(part: str) -> str:
    if not part:
        return "_rels/.rels"
    d, base = posixpath.split(part)
    return posixpath.join(d, "_rels", base + ".rels")


def resolve_target(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = posixpath.dirname(source_part) if source_part else ""
    return posixpath.normpath(posixpath.join(base, target)).lstrip("/")


# No entity expansion, no network, and libxml2's default depth / text-node
# limits kept on (no huge_tree): every part comes from the customer's file.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)


def parse_xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, parser=_PARSER)


class Package:
    """A case-insensitive view over the workbook zip (OPC part names are
    case-insensitive; zip member names are not)."""

    def __init__(self, zf: zipfile.ZipFile) -> None:
        self.zf = zf
        self._names = {n.lower(): n for n in zf.namelist()}

    def name(self, part: str) -> Optional[str]:
        return self._names.get((part or "").lstrip("/").lower())

    def has(self, part: str) -> bool:
        return self.name(part) is not None

    def read(self, part: str) -> bytes:
        name = self.name(part)
        if name is None:
            raise KeyError(part)
        return self.zf.read(name)

    def open(self, part: str):
        name = self.name(part)
        if name is None:
            raise KeyError(part)
        return self.zf.open(name)

    def size(self, part: str) -> int:
        name = self.name(part)
        if name is None:
            return 0
        return int(self.zf.getinfo(name).file_size)

    def rels(self, part: str) -> Dict[str, Rel]:
        rp = rels_part_for(part)
        if not self.has(rp):
            return {}
        try:
            root = parse_xml(self.read(rp))
        except Exception:
            return {}
        out: Dict[str, Rel] = {}
        for el in root:
            if _local(el.tag) != "Relationship":
                continue
            rid = el.get("Id") or ""
            target = el.get("Target") or ""
            external = (el.get("TargetMode") or "").lower() == "external"
            out[rid] = Rel(
                rid=rid,
                type=el.get("Type") or "",
                target=target if external else resolve_target(part, target),
                external=external,
            )
        return out


# ---------------------------------------------------------------------------
# Scan model
# ---------------------------------------------------------------------------


@dataclass
class CellInfo:
    text: str
    kind: str            # "text" | "number" | "bool" | "error" | "formula"
    styled: bool         # bold, filled, or bottom-bordered: how headings are usually set apart
    formula: bool = False


@dataclass
class TableDef:
    part: str
    rid: str
    id: int
    name: str
    display_name: str
    ref: Tuple[int, int, int, int]
    header_rows: int
    columns: List[str]
    alt_text: Optional[str] = None


@dataclass
class DrawingObject:
    kind: str                     # "picture" | "chart"
    index: int                    # ordinal among pic/graphicFrame elements in the drawing part
    cnvpr_id: str
    cnvpr_name: str
    descr: Optional[str]
    title: Optional[str]
    decorative: bool
    anchor_cell: Optional[str]
    media_part: Optional[str] = None
    chart_part: Optional[str] = None
    caption: Optional[str] = None
    caption_source: Optional[str] = None


@dataclass
class DataBlock:
    r1: int
    c1: int
    r2: int
    c2: int
    state: str = "unclear"        # declared | candidate | unclear | off
    reason: Optional[str] = None
    table: Optional[TableDef] = None
    label: Optional[str] = None
    label_cell: Optional[str] = None
    open_bottom: bool = False     # reached the end of the scan window
    shape_changed: bool = False   # widened below the scan window
    merges: List[Tuple[int, int, int, int]] = field(default_factory=list)

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.r1, self.c1, self.r2, self.c2)

    @property
    def ref(self) -> str:
        return format_range(self.r1, self.c1, self.r2, self.c2)


@dataclass
class SheetScan:
    index: int                    # 1-based position in the workbook's sheet list
    name: str
    state: str
    kind: str                     # "worksheet" | "chartsheet"
    part: str
    rels: Dict[str, Rel] = field(default_factory=dict)
    grid: Dict[Tuple[int, int], CellInfo] = field(default_factory=dict)
    blocks: List[DataBlock] = field(default_factory=list)
    notes: List[Tuple[int, int, str]] = field(default_factory=list)
    drawing_part: Optional[str] = None
    drawings: List[DrawingObject] = field(default_factory=list)
    tables: List[TableDef] = field(default_factory=list)
    pivots: List[Tuple[int, int, int, int]] = field(default_factory=list)
    merges: List[Tuple[int, int, int, int]] = field(default_factory=list)
    array_ranges: List[Tuple[int, int, int, int]] = field(default_factory=list)
    autofilter: Optional[Tuple[int, int, int, int]] = None
    protected: bool = False
    truncated: bool = False
    part_size: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.grid and not self.drawings and not self.tables


@dataclass
class WorkbookScan:
    workbook_part: str
    core_part: Optional[str]
    title: Optional[str]
    language: Optional[str]
    sheets: List[SheetScan]
    defined_names: Set[str]
    table_names: Set[str]
    max_table_id: int
    table_parts: Set[str]
    hidden_sheets: int = 0
    truncated: bool = False


# ---------------------------------------------------------------------------
# Default names
# ---------------------------------------------------------------------------

# Application default tab names in the languages Excel, LibreOffice and
# Google Sheets ship with. Matched as the WHOLE name plus digits.
_DEFAULT_SHEET_WORDS = (
    "sheet", "feuil", "feuille", "hoja", "tabelle", "blad", "foglio", "planilha",
    "folha", "arkusz", "лист", "munkalap", "list", "ark", "taulukko", "sayfa",
    "φύλλο", "工作表", "シート", "시트", "foaie", "hárok", "lapas", "leht",
    "tabela", "worksheet", "tab",
)
_DEFAULT_CHART_WORDS = ("chart", "graph", "diagramm", "gráfico", "grafico", "graphique", "wykres", "grafiek")
_DEFAULT_SHEET_RE = re.compile(
    r"^(?:" + "|".join(re.escape(w) for w in _DEFAULT_SHEET_WORDS + _DEFAULT_CHART_WORDS) + r")\s*\d+$"
    # openpyxl (and so most Python-generated reports) names the first sheet
    # plain "Sheet" and a chart sheet plain "Chart": defaults with no number.
    r"|^(?:sheet|worksheet|chart)$",
    re.IGNORECASE,
)


def is_default_sheet_name(name: str) -> bool:
    return bool(_DEFAULT_SHEET_RE.match((name or "").strip()))


# ---------------------------------------------------------------------------
# Header-row judgement
# ---------------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Excel stores some characters in table column names as _xHHHH_ escapes; a
# heading that already contains one would not round-trip exactly.
_XESCAPE_RE = re.compile(r"_x[0-9A-Fa-f]{4}_")


def _header_text_problem(cells: List[Optional[CellInfo]]) -> Optional[str]:
    if not cells:
        return "the data range is empty"
    if any(c is None or not c.text.strip() for c in cells):
        return "some cells in its first row are empty"
    if any(c.formula for c in cells):
        return "its first row is calculated by formulas"
    if any(c.kind != "text" for c in cells):
        return "its first row holds numbers or dates, which reads as data rather than headings"
    seen: Dict[str, str] = {}
    for c in cells:
        key = c.text.strip().casefold()
        if key in seen:
            return f"two columns in its first row have the same text ({c.text.strip()!r})"
        seen[key] = c.text
    for c in cells:
        t = c.text
        if len(t) > 255 or _CONTROL_RE.search(t) or _XESCAPE_RE.search(t):
            return "its first row holds long or multi-line text, which reads as data rather than headings"
        if len(t.split()) > 8 or t.rstrip().endswith((".", "!", "?", ":")):
            return "its first row reads like sentences or labels, not column headings"
    return None


def judge_header(block: DataBlock, grid: Dict[Tuple[int, int], CellInfo]) -> Tuple[bool, Optional[str]]:
    """Is the block's first row, unambiguously, a row of column headings?

    Returns (clear, reason_if_not). "Clear" needs every heading to be distinct
    text AND the row to be set apart from the data below it — by type (a
    column of numbers under a text heading) or by formatting (bold/filled/
    bordered headings over plain data). Two rows of names look alike, so a
    row of names over more names is NOT clear, however likely it seems.
    """
    header = [grid.get((block.r1, c)) for c in range(block.c1, block.c2 + 1)]
    problem = _header_text_problem(header)
    if problem:
        return False, problem
    if block.r2 <= block.r1:
        return False, "there is no data below its first row"
    body_last = min(block.r2, block.r1 + EVAL_BODY_ROWS)
    body_rows = range(block.r1 + 1, body_last + 1)

    type_contrast = False
    for c in range(block.c1, block.c2 + 1):
        vals = [grid.get((r, c)) for r in body_rows]
        vals = [v for v in vals if v is not None and (v.text.strip() or v.formula)]
        if len(vals) < 1:
            continue
        numeric = sum(1 for v in vals if v.kind in ("number", "formula", "bool"))
        if numeric >= max(1, int(0.8 * len(vals) + 0.999)) and (numeric >= 2 or len(vals) == 1):
            type_contrast = True
            break

    style_contrast = False
    if all(h is not None and h.styled for h in header):
        body = [grid.get((r, c)) for r in body_rows for c in range(block.c1, block.c2 + 1)]
        body = [v for v in body if v is not None]
        styled = sum(1 for v in body if v.styled)
        style_contrast = bool(body) and styled < 0.25 * len(body)

    if not (type_contrast or style_contrast):
        return False, (
            "its first row looks just like the data below it (same kind of values, same "
            "formatting), so we cannot tell it is a header row"
        )
    return True, None


# ---------------------------------------------------------------------------
# Shared strings and styles
# ---------------------------------------------------------------------------


def _si_text(si: etree._Element) -> str:
    parts: List[str] = []
    for el in si:
        name = _local(el.tag)
        if name == "t":
            parts.append(el.text or "")
        elif name == "r":
            for t in el:
                if _local(t.tag) == "t":
                    parts.append(t.text or "")
        # rPh (phonetic guide) runs are not part of the displayed text.
    return "".join(parts)


def _read_shared_strings(pkg: Package, part: Optional[str]) -> List[str]:
    if not part or not pkg.has(part):
        return []
    out: List[str] = []
    with pkg.open(part) as fh:
        for _event, el in etree.iterparse(fh, events=("end",), tag=_q(NS_MAIN, "si"),
                                          resolve_entities=False, no_network=True):
            out.append(_si_text(el))
            el.clear()
            if len(out) >= MAX_SHARED_STRINGS:
                break
    return out


def _read_style_flags(pkg: Package, part: Optional[str]) -> List[bool]:
    """cellXfs index -> "is this cell visibly set apart" (bold, fill, or a
    bottom border): the formatting authors use to mark a heading row."""
    if not part or not pkg.has(part):
        return []
    try:
        root = parse_xml(pkg.read(part))
    except Exception:
        return []

    def _children(parent_name: str) -> List[etree._Element]:
        parent = root.find(_q(NS_MAIN, parent_name))
        return list(parent) if parent is not None else []

    bold_fonts: List[bool] = []
    for font in _children("fonts"):
        b = font.find(_q(NS_MAIN, "b"))
        bold_fonts.append(b is not None and (b.get("val") or "1").lower() not in ("0", "false"))
    real_fills: List[bool] = []
    for fill in _children("fills"):
        pf = fill.find(_q(NS_MAIN, "patternFill"))
        pt = (pf.get("patternType") if pf is not None else None) or ""
        grad = fill.find(_q(NS_MAIN, "gradientFill")) is not None
        real_fills.append(grad or pt not in ("", "none", "gray125"))
    bottom_borders: List[bool] = []
    for border in _children("borders"):
        bottom = border.find(_q(NS_MAIN, "bottom"))
        bottom_borders.append(bottom is not None and bool(bottom.get("style")) and bottom.get("style") != "none")

    flags: List[bool] = []
    for xf in _children("cellXfs"):
        def _idx(attr: str) -> int:
            try:
                return int(xf.get(attr) or 0)
            except ValueError:
                return 0

        fi, fl, bo = _idx("fontId"), _idx("fillId"), _idx("borderId")
        flags.append(
            (fi < len(bold_fonts) and bold_fonts[fi])
            or (fl < len(real_fills) and real_fills[fl])
            or (bo < len(bottom_borders) and bottom_borders[bo])
        )
    return flags


# ---------------------------------------------------------------------------
# Tables, pivots, charts, drawings
# ---------------------------------------------------------------------------


def _read_table(pkg: Package, part: str, rid: str) -> Optional[TableDef]:
    try:
        root = parse_xml(pkg.read(part))
    except Exception:
        return None
    ref = parse_range(root.get("ref") or "")
    if ref is None:
        return None
    try:
        tid = int(root.get("id") or 0)
    except ValueError:
        tid = 0
    try:
        header_rows = int(root.get("headerRowCount") if root.get("headerRowCount") is not None else 1)
    except ValueError:
        header_rows = 1
    cols: List[str] = []
    tc = root.find(_q(NS_MAIN, "tableColumns"))
    if tc is not None:
        for col in tc:
            if _local(col.tag) == "tableColumn":
                cols.append(col.get("name") or "")
    alt = None
    for ext in root.iter(_q(NS_MAIN, "ext")):
        if (ext.get("uri") or "").upper() == _TABLE_ALT_EXT_URI.upper():
            for el in ext:
                if _local(el.tag) == "table":
                    alt = (el.get("altText") or "").strip() or None
    return TableDef(
        part=part,
        rid=rid,
        id=tid,
        name=root.get("name") or "",
        display_name=root.get("displayName") or root.get("name") or "",
        ref=ref,
        header_rows=header_rows,
        columns=cols,
        alt_text=alt,
    )


def _read_pivot_location(pkg: Package, part: str) -> Optional[Tuple[int, int, int, int]]:
    try:
        root = parse_xml(pkg.read(part))
    except Exception:
        return None
    loc = root.find(_q(NS_MAIN, "location"))
    return parse_range(loc.get("ref") or "") if loc is not None else None


_CHART_TYPE_NAMES = {
    "lineChart": "Line chart",
    "line3DChart": "3-D line chart",
    "pieChart": "Pie chart",
    "pie3DChart": "3-D pie chart",
    "ofPieChart": "Pie-of-pie chart",
    "doughnutChart": "Doughnut chart",
    "areaChart": "Area chart",
    "area3DChart": "3-D area chart",
    "scatterChart": "Scatter chart",
    "bubbleChart": "Bubble chart",
    "radarChart": "Radar chart",
    "stockChart": "Stock chart",
    "surfaceChart": "Surface chart",
    "surface3DChart": "3-D surface chart",
}
_DEFAULT_SERIES_RE = re.compile(r"^(series|serie|reihe|série)\s*\d+$", re.IGNORECASE)


def _rich_text(el: Optional[etree._Element]) -> str:
    if el is None:
        return ""
    return " ".join(" ".join((t.text or "").split()) for t in el.iter(_q(NS_A, "t")) if (t.text or "").strip()).strip()


def _cached_str(el: Optional[etree._Element]) -> str:
    """Text of a c:tx that points at a cell: strRef/strCache/pt/v, or a literal c:v."""
    if el is None:
        return ""
    vals = [" ".join((v.text or "").split()) for v in el.iter(_q(NS_C, "v")) if (v.text or "").strip()]
    return " ".join(vals).strip()


# Where a picture/chart caption came from, in the vocabulary the alt-text
# rules use: "title" is text the author wrote FOR this object (the chart's own
# title, a picture's Title field) and may become its alt text; "chart_series"
# (only the series names, no title) is grounded but says little about what
# the chart shows, so it is offered as context and not as authored alt text.
CAPTION_FROM_TITLE = "title"
CAPTION_FROM_SERIES = "chart_series"


def describe_chart(pkg: Package, part: str) -> Optional[str]:
    found = chart_caption(pkg, part)
    return found[0] if found else None


def chart_caption(pkg: Package, part: str) -> Optional[Tuple[str, str]]:
    """``(description, caption_source)`` for a chart, from its OWN parts: its
    type, its title, and its (non-default) series names. None when the chart
    names nothing — "Column chart" alone describes no chart in particular."""
    try:
        root = parse_xml(pkg.read(part))
    except Exception:
        return None
    if root.tag == _q(NS_CX, "chartSpace"):
        title = _rich_text(root.find(f".//{_q(NS_CX, 'title')}"))
        return (f"Chart: {title}", CAPTION_FROM_TITLE) if title else None
    chart = root.find(_q(NS_C, "chart"))
    if chart is None:
        return None
    title = ""
    t = chart.find(_q(NS_C, "title"))
    if t is not None:
        title = _rich_text(t.find(_q(NS_C, "tx"))) or _cached_str(t.find(_q(NS_C, "tx")))
    plot = chart.find(_q(NS_C, "plotArea"))
    kinds: List[str] = []
    series: List[str] = []
    if plot is not None:
        for el in plot:
            name = _local(el.tag)
            if not name.endswith("Chart"):
                continue
            if name in ("barChart", "bar3DChart"):
                bd = el.find(_q(NS_C, "barDir"))
                horizontal = bd is not None and bd.get("val") == "bar"
                label = "Bar chart" if horizontal else "Column chart"
                kinds.append(("3-D " + label.lower()) if name == "bar3DChart" else label)
            else:
                kinds.append(_CHART_TYPE_NAMES.get(name, "Chart"))
            for ser in el.findall(_q(NS_C, "ser")):
                s = _cached_str(ser.find(_q(NS_C, "tx")))
                if s and not _DEFAULT_SERIES_RE.match(s) and s not in series:
                    series.append(s)
    kind = kinds[0] if len(set(kinds)) == 1 else ("Combination chart" if kinds else "Chart")
    if title:
        desc = f"{kind}: {title}"
        if len(series) >= 2:
            desc += " (" + ", ".join(series[:3]) + (", …" if len(series) > 3 else "") + ")"
        return desc, CAPTION_FROM_TITLE
    if series:
        return f"{kind} of " + ", ".join(series[:3]) + (", …" if len(series) > 3 else ""), CAPTION_FROM_SERIES
    return None


def iter_drawing_objects(root: etree._Element) -> Iterator[Tuple[etree._Element, etree._Element]]:
    """Yield (object element, its anchor) for every picture and graphic frame
    in a drawing part, in document order, descending into groups. The writer
    walks with this SAME function, so an ordinal from the parser addresses
    the same element in the writer."""
    for anchor in root:
        if _local(anchor.tag) not in ("twoCellAnchor", "oneCellAnchor", "absoluteAnchor"):
            continue
        stack = list(anchor)
        while stack:
            el = stack.pop(0)
            name = _local(el.tag)
            if name in ("pic", "graphicFrame"):
                yield el, anchor
            elif name == "grpSp":
                stack[0:0] = list(el)
            elif name == "AlternateContent":
                # mc:AlternateContent wraps newer objects (e.g. chartEx
                # charts); the first mc:Choice is what Excel renders.
                choice = next((c for c in el if _local(c.tag) == "Choice"), None)
                if choice is not None:
                    stack[0:0] = list(choice)


def cnvpr_of(obj: etree._Element) -> Optional[etree._Element]:
    for el in obj.iter(_q(NS_XDR, "cNvPr")):
        return el
    return None


def _is_decorative(cnvpr: etree._Element) -> bool:
    for ext in cnvpr.iter(_q(NS_A, "ext")):
        if (ext.get("uri") or "").upper() == _DECORATIVE_EXT_URI:
            for el in ext:
                if _local(el.tag) == "decorative" and (el.get("val") or "0") in ("1", "true"):
                    return True
    return False


def _anchor_cell(anchor: etree._Element) -> Optional[str]:
    frm = anchor.find(_q(NS_XDR, "from"))
    if frm is None:
        return None
    try:
        col = int(frm.findtext(_q(NS_XDR, "col")) or 0) + 1
        row = int(frm.findtext(_q(NS_XDR, "row")) or 0) + 1
    except ValueError:
        return None
    return f"{index_to_col(col)}{row}"


def _read_drawing(pkg: Package, part: str) -> List[DrawingObject]:
    try:
        root = parse_xml(pkg.read(part))
    except Exception:
        return []
    rels = pkg.rels(part)
    out: List[DrawingObject] = []
    for index, (obj, anchor) in enumerate(iter_drawing_objects(root)):
        cnvpr = cnvpr_of(obj)
        if cnvpr is None:
            continue
        if (cnvpr.get("hidden") or "0") in ("1", "true"):
            continue
        kind = "picture" if _local(obj.tag) == "pic" else ""
        media_part = chart_part = None
        if kind == "picture":
            blip = obj.find(f".//{_q(NS_A, 'blip')}")
            rid = blip.get(_q(NS_R, "embed")) if blip is not None else None
            rel = rels.get(rid or "")
            if rel is not None and not rel.external:
                media_part = rel.target
        else:
            gd = obj.find(f".//{_q(NS_A, 'graphicData')}")
            if gd is None:
                continue
            for el in gd:
                rid = el.get(_q(NS_R, "id"))
                rel = rels.get(rid or "")
                if rel is not None and not rel.external and rel.type.endswith("/chart"):
                    chart_part = rel.target
                elif rel is not None and not rel.external and "chartEx" in rel.type:
                    chart_part = rel.target
            if chart_part is None:
                # SmartArt / OLE frames: not charts; out of scope here.
                continue
            kind = "chart"
        descr = cnvpr.get("descr")
        title = (cnvpr.get("title") or "").strip() or None
        obj_rec = DrawingObject(
            kind=kind,
            index=index,
            cnvpr_id=cnvpr.get("id") or "",
            cnvpr_name=cnvpr.get("name") or "",
            descr=(descr.strip() if isinstance(descr, str) and descr.strip() else None),
            title=title,
            decorative=_is_decorative(cnvpr),
            anchor_cell=_anchor_cell(anchor),
            media_part=media_part,
            chart_part=chart_part,
        )
        if kind == "chart" and chart_part and pkg.has(chart_part):
            found = chart_caption(pkg, chart_part)
            if found:
                obj_rec.caption, obj_rec.caption_source = found
        elif kind == "picture" and title:
            from app.analyzers.image_analyzer import is_nondescriptive_alt

            if not is_nondescriptive_alt(title):
                obj_rec.caption, obj_rec.caption_source = title, CAPTION_FROM_TITLE
        out.append(obj_rec)
    return out


# ---------------------------------------------------------------------------
# Worksheet streaming
# ---------------------------------------------------------------------------


_COL_CACHE: Dict[str, int] = {}


def _col_of(ref: str) -> int:
    letters = ref.rstrip("0123456789")
    col = _COL_CACHE.get(letters)
    if col is None:
        col = col_to_index(letters.lstrip("$")) if letters else 0
        if len(_COL_CACHE) < 20000:
            _COL_CACHE[letters] = col
    return col


def _cell_text(c: etree._Element, t: str, shared: List[str]) -> Tuple[str, bool]:
    """(displayed text, has_formula) for a <c> element."""
    f = c.find(_q(NS_MAIN, "f"))
    has_formula = f is not None
    if t == "inlineStr":
        is_ = c.find(_q(NS_MAIN, "is"))
        return (_si_text(is_) if is_ is not None else ""), has_formula
    v = c.find(_q(NS_MAIN, "v"))
    raw = v.text if v is not None and v.text is not None else ""
    if t == "s":
        try:
            return shared[int(raw)], has_formula
        except (ValueError, IndexError):
            return "", has_formula
    if t == "b":
        return ("TRUE" if raw.strip() == "1" else "FALSE") if raw else "", has_formula
    return raw, has_formula


def _scan_worksheet(pkg: Package, sheet: SheetScan, shared: List[str], style_flags: List[bool]) -> None:
    sheet.part_size = pkg.size(sheet.part)
    # Tables and pivots come from the sheet's relationships, which are known
    # before the (possibly huge) sheet XML is streamed.
    for rel in sheet.rels.values():
        if rel.external:
            continue
        if rel.type == REL_TABLE and pkg.has(rel.target):
            tdef = _read_table(pkg, rel.target, rel.rid)
            if tdef is not None:
                sheet.tables.append(tdef)
        elif rel.type == REL_PIVOT and pkg.has(rel.target):
            loc = _read_pivot_location(pkg, rel.target)
            if loc is not None:
                sheet.pivots.append(loc)
        elif rel.type == REL_DRAWING and pkg.has(rel.target) and sheet.drawing_part is None:
            sheet.drawing_part = rel.target
    consumed = [t.ref for t in sheet.tables] + list(sheet.pivots)

    def _is_consumed(r: int, c: int) -> bool:
        for (r1, c1, r2, c2) in consumed:
            if r1 <= r <= r2 and c1 <= c <= c2:
                return True
        return False

    row_tag = _q(NS_MAIN, "row")
    c_tag = _q(NS_MAIN, "c")
    f_tag = _q(NS_MAIN, "f")
    tags = (
        row_tag,
        _q(NS_MAIN, "mergeCell"),
        _q(NS_MAIN, "autoFilter"),
        _q(NS_MAIN, "sheetProtection"),
    )
    window_closed = False
    open_blocks: List[DataBlock] = []
    last_row = 0
    with pkg.open(sheet.part) as fh:
        for _event, el in etree.iterparse(fh, events=("end",), tag=tags,
                                          resolve_entities=False, no_network=True):
            name = _local(el.tag)
            if name == "row":
                try:
                    r = int(el.get("r") or (last_row + 1))
                except ValueError:
                    r = last_row + 1
                last_row = r
                col = 0
                occupied_cols: List[int] = []
                for c in el.iterchildren(c_tag):
                    # Hot loop (a million cells in a big export): no regex,
                    # no per-cell tag building. Column letters are cached.
                    ref = c.get("r")
                    col = _col_of(ref) if ref else col + 1
                    if not len(c):
                        continue  # a styled but empty cell: nothing here
                    f = c[0] if c[0].tag == f_tag else None  # <f> is first when present
                    if f is not None and f.get("t") in ("array", "dataTable") and f.get("ref"):
                        rng = parse_range(f.get("ref") or "")
                        if rng:
                            sheet.array_ranges.append(rng)
                    t = c.get("t") or "n"
                    if r > SCAN_ROWS or col > SCAN_COLS:
                        # Outside the window: only "is anything here" matters.
                        if col > SCAN_COLS:
                            sheet.truncated = True
                        else:
                            occupied_cols.append(col)
                        continue
                    text, has_formula = _cell_text(c, t, shared)
                    if not text.strip() and not has_formula:
                        continue
                    # Cells inside an existing table/pivot are recorded too
                    # (a declared table's sample rows are read from here);
                    # _segment leaves them out of the new blocks.
                    try:
                        s = int(c.get("s") or 0)
                    except ValueError:
                        s = 0
                    kind = "formula" if has_formula else {
                        "s": "text", "inlineStr": "text", "str": "text",
                        "b": "bool", "e": "error",
                    }.get(t, "number")
                    sheet.grid[(r, col)] = CellInfo(
                        text=text,
                        kind=kind,
                        styled=bool(style_flags[s]) if 0 <= s < len(style_flags) else False,
                        formula=has_formula,
                    )
                if r > SCAN_ROWS and occupied_cols:
                    if not window_closed:
                        window_closed = True
                        sheet.blocks, sheet.notes = _segment(sheet, consumed)
                        open_blocks = [b for b in sheet.blocks if b.r2 == SCAN_ROWS]
                        for b in open_blocks:
                            b.open_bottom = True
                    claimed: Set[int] = set()
                    for b in list(open_blocks):
                        in_span = [c for c in occupied_cols if b.c1 <= c <= b.c2]
                        if r == b.r2 + 1 and in_span:
                            b.r2 = r
                            claimed.update(in_span)
                            if any(c == b.c1 - 1 or c == b.c2 + 1 for c in occupied_cols):
                                b.shape_changed = True
                        else:
                            open_blocks.remove(b)
                    # Content below the window that no open block continues
                    # is content we did not analyze: disclose it.
                    if any(c not in claimed and not _is_consumed(r, c) for c in occupied_cols):
                        sheet.truncated = True
                el.clear()
                while el.getprevious() is not None:
                    parent = el.getparent()
                    if parent is None:
                        break
                    del parent[0]
            elif name == "mergeCell":
                rng = parse_range(el.get("ref") or "")
                if rng:
                    sheet.merges.append(rng)
            elif name == "autoFilter":
                parent = el.getparent()
                if parent is not None and _local(parent.tag) == "worksheet":
                    sheet.autofilter = parse_range(el.get("ref") or "")
            elif name == "sheetProtection":
                if (el.get("sheet") or "0").lower() in ("1", "true"):
                    sheet.protected = True
    if not window_closed:
        sheet.blocks, sheet.notes = _segment(sheet, consumed)
    # Existing Excel tables are declared blocks of their own.
    for tdef in sheet.tables:
        r1, c1, r2, c2 = tdef.ref
        blk = DataBlock(r1=r1, c1=c1, r2=r2, c2=c2, table=tdef)
        if tdef.header_rows >= 1:
            blk.state = "declared"
        else:
            blk.state = "off"
            blk.reason = "it is an Excel table with its header row switched off"
        sheet.blocks.append(blk)
    sheet.blocks.sort(key=lambda b: (b.r1, b.c1))
    for blk in sheet.blocks:
        blk.merges = [m for m in sheet.merges if ranges_intersect(m, blk.bbox)]
        if blk.table is not None:
            continue
        clear, reason = judge_header(blk, sheet.grid)
        blocker = _blocker(sheet, blk)
        if clear and blocker is None:
            blk.state = "candidate"
        else:
            blk.state = "unclear"
            blk.reason = reason or blocker


def _blocker(sheet: SheetScan, blk: DataBlock) -> Optional[str]:
    """A reason we could not safely turn this block into an Excel table even
    if its header row is clear. Each one is a real Excel constraint or a
    place where we would be guessing."""
    if blk.merges:
        return "it contains merged cells, which an Excel table cannot hold"
    if blk.shape_changed:
        return "its shape changes further down the sheet than we read"
    if sheet.truncated and blk.c2 >= SCAN_COLS:
        return "it is wider than the part of the sheet we read"
    if any(ranges_intersect(a, blk.bbox) for a in sheet.array_ranges):
        return "it contains array formulas, which an Excel table cannot hold"
    if sheet.autofilter is not None and ranges_intersect(sheet.autofilter, blk.bbox):
        return "it has a filter applied, and turning it into a table would change how that filter works"
    if any(ranges_intersect(p, blk.bbox) for p in sheet.pivots):
        return "it overlaps a PivotTable"
    if any(ranges_intersect(t.ref, blk.bbox) for t in sheet.tables):
        return "it overlaps an existing Excel table"
    if sheet.protected:
        return "the sheet is protected, so we will not restructure it"
    if sheet.part_size > MAX_SHEET_XML_FOR_TABLE_FIX:
        return "the sheet is too large for us to restructure safely"
    return None


def _segment(sheet: SheetScan, consumed: List[Tuple[int, int, int, int]]) -> Tuple[List[DataBlock], List[Tuple[int, int, str]]]:
    """Cut the window's occupied cells into rectangular blocks at blank rows
    and blank columns (a recursive XY-cut). Rectangles of at least 2x2 are
    data; everything else is text for paragraphs."""

    def _in_consumed(rc: Tuple[int, int]) -> bool:
        r, c = rc
        return any(r1 <= r <= r2 and c1 <= c <= c2 for (r1, c1, r2, c2) in consumed)

    cells = [rc for rc in sheet.grid if not _in_consumed(rc)]
    leaves: List[List[Tuple[int, int]]] = []
    stack = [cells] if cells else []
    while stack:
        group = stack.pop()
        split = _split_on_gaps(group, axis=0)
        if len(split) == 1:
            split = _split_on_gaps(group, axis=1)
        if len(split) == 1:
            leaves.append(group)
        else:
            stack.extend(split)

    blocks: List[DataBlock] = []
    notes: List[Tuple[int, int, str]] = []
    for group in sorted(leaves, key=lambda g: (min(r for r, _ in g), min(c for _, c in g))):
        rows = sorted({r for r, _ in group})
        r1, r2 = rows[0], rows[-1]
        c1, c2 = min(c for _, c in group), max(c for _, c in group)
        # A lone text cell on the first row of a block, over a wider row, is
        # the block's title ("Q3 sales by region" above the data).
        first_row = [rc for rc in group if rc[0] == r1]
        label: Optional[Tuple[int, int]] = None
        if len(rows) >= 3 and len(first_row) == 1:
            second = [rc for rc in group if rc[0] == rows[1]]
            cell = sheet.grid.get(first_row[0])
            if len(second) >= 2 and cell is not None and cell.kind == "text":
                label = first_row[0]
        if label is not None:
            notes.append((label[0], label[1], sheet.grid[label].text))
            group = [rc for rc in group if rc != label]
            rows = sorted({r for r, _ in group})
            r1, r2 = rows[0], rows[-1]
            c1, c2 = min(c for _, c in group), max(c for _, c in group)
        if (r2 - r1 + 1) >= 2 and (c2 - c1 + 1) >= 2 and len(group) >= 4:
            if len(blocks) >= MAX_BLOCKS_PER_SHEET:
                sheet.truncated = True
                continue
            blk = DataBlock(r1=r1, c1=c1, r2=r2, c2=c2)
            if label is not None:
                blk.label = sheet.grid[label].text
                blk.label_cell = f"{index_to_col(label[1])}{label[0]}"
            blocks.append(blk)
        else:
            for rc in sorted(group):
                cell = sheet.grid.get(rc)
                if cell is not None and cell.kind == "text" and cell.text.strip():
                    notes.append((rc[0], rc[1], cell.text))
    notes.sort()
    return blocks, notes


def _split_on_gaps(group: List[Tuple[int, int]], axis: int) -> List[List[Tuple[int, int]]]:
    values = sorted({rc[axis] for rc in group})
    if len(values) <= 1:
        return [group]
    cuts: List[Tuple[int, int]] = []
    start = values[0]
    prev = values[0]
    for v in values[1:]:
        if v > prev + 1:
            cuts.append((start, prev))
            start = v
        prev = v
    cuts.append((start, prev))
    if len(cuts) == 1:
        return [group]
    out: List[List[Tuple[int, int]]] = []
    for lo, hi in cuts:
        out.append([rc for rc in group if lo <= rc[axis] <= hi])
    return out


# ---------------------------------------------------------------------------
# Workbook scan
# ---------------------------------------------------------------------------


def _core_properties(pkg: Package, part: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not part or not pkg.has(part):
        return None, None
    try:
        root = parse_xml(pkg.read(part))
    except Exception:
        return None, None
    title = root.findtext(_q(NS_DC, "title"))
    language = root.findtext(_q(NS_DC, "language"))
    return ((title or "").strip() or None), ((language or "").strip() or None)


def scan_workbook(path: str | Path) -> WorkbookScan:
    """Read everything the XLSX tree and the XLSX writer need from ``path``."""
    with zipfile.ZipFile(str(path)) as zf:
        pkg = Package(zf)
        root_rels = pkg.rels("")
        wb_part = next((r.target for r in root_rels.values() if r.type == REL_OFFICE_DOCUMENT), "xl/workbook.xml")
        core_part = next((r.target for r in root_rels.values() if r.type == REL_CORE), None)
        if not pkg.has(wb_part):
            raise ValueError("not an Excel workbook: no workbook part")
        wb_root = parse_xml(pkg.read(wb_part))
        if wb_root.tag == _q(NS_MAIN_STRICT, "workbook"):
            raise ValueError("strict OOXML spreadsheets are not supported")
        if wb_root.tag != _q(NS_MAIN, "workbook"):
            raise ValueError("not an Excel workbook")
        wb_rels = pkg.rels(wb_part)
        shared = _read_shared_strings(
            pkg, next((r.target for r in wb_rels.values() if r.type == REL_SHARED_STRINGS), None)
        )
        style_flags = _read_style_flags(
            pkg, next((r.target for r in wb_rels.values() if r.type == REL_STYLES), None)
        )
        defined: Set[str] = set()
        dn = wb_root.find(_q(NS_MAIN, "definedNames"))
        if dn is not None:
            for el in dn:
                if el.get("name"):
                    defined.add(el.get("name").casefold())
        title, language = _core_properties(pkg, core_part)

        sheets: List[SheetScan] = []
        hidden = 0
        sheets_el = wb_root.find(_q(NS_MAIN, "sheets"))
        for i, sh in enumerate(list(sheets_el) if sheets_el is not None else [], start=1):
            rel = wb_rels.get(sh.get(_q(NS_R, "id")) or "")
            if rel is None or rel.external or not pkg.has(rel.target):
                continue
            kind = "worksheet" if rel.type == REL_WORKSHEET else ("chartsheet" if rel.type == REL_CHARTSHEET else "")
            if not kind:
                continue  # dialog / macro sheets carry no document content here
            state = sh.get("state") or "visible"
            scan = SheetScan(index=i, name=sh.get("name") or f"Sheet{i}", state=state, kind=kind, part=rel.target)
            scan.rels = pkg.rels(rel.target)
            if state != "visible":
                hidden += 1
            if kind == "worksheet":
                _scan_worksheet(pkg, scan, shared, style_flags)
            else:
                for r in scan.rels.values():
                    if r.type == REL_DRAWING and not r.external and pkg.has(r.target):
                        scan.drawing_part = r.target
                        break
            if scan.drawing_part:
                scan.drawings = _read_drawing(pkg, scan.drawing_part)
            sheets.append(scan)

        table_names: Set[str] = set()
        table_parts: Set[str] = set()
        max_id = 0
        for n in zf.namelist():
            low = n.lower()
            if low.startswith("xl/tables/") and low.endswith(".xml"):
                table_parts.add(low)
                try:
                    troot = parse_xml(zf.read(n))
                except Exception:
                    continue
                for attr in ("name", "displayName"):
                    if troot.get(attr):
                        table_names.add(troot.get(attr).casefold())
                try:
                    max_id = max(max_id, int(troot.get("id") or 0))
                except ValueError:
                    pass
        return WorkbookScan(
            workbook_part=wb_part,
            core_part=core_part if core_part and pkg.has(core_part) else None,
            title=title,
            language=language,
            sheets=sheets,
            defined_names=defined,
            table_names=table_names,
            max_table_id=max_id,
            table_parts=table_parts,
            hidden_sheets=hidden,
            truncated=any(s.truncated for s in sheets if s.state == "visible"),
        )


def read_media(path: str | Path, part: str) -> Optional[bytes]:
    try:
        with zipfile.ZipFile(str(path)) as zf:
            pkg = Package(zf)
            if pkg.size(part) > MAX_IMAGE_BYTES_FOR_VISION:
                return None
            return pkg.read(part)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tree building
# ---------------------------------------------------------------------------

_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _meta(sheet: SheetScan, **props: Any) -> NodeMetadata:
    base = {"sheet_name": sheet.name, "sheet_index": sheet.index}
    base.update({k: v for k, v in props.items() if v is not None})
    return NodeMetadata(source_format="xlsx", properties=base)


def _text_content(text: str) -> NodeContent:
    t = " ".join((text or "").split())
    return NodeContent(kind=ContentKind.TEXT, text=t[:500]) if t else NodeContent(kind=ContentKind.NONE)


def _table_node(sheet: SheetScan, blk: DataBlock, table_no: int) -> TableNode:
    tid = f"xlsx-s{sheet.index}-t{table_no}"
    header = blk.state == "declared"
    last_col = min(blk.c2, blk.c1 + SAMPLE_COLS - 1)
    last_row = min(blk.r2, blk.r1 + SAMPLE_BODY_ROWS)
    covered: Dict[Tuple[int, int], Tuple[int, int]] = {}
    spans: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for (m1, n1, m2, n2) in blk.merges:
        spans[(m1, n1)] = (min(m2, last_row) - m1 + 1, min(n2, last_col) - n1 + 1)
        for r in range(m1, m2 + 1):
            for c in range(n1, n2 + 1):
                if (r, c) != (m1, n1):
                    covered[(r, c)] = (m1, n1)
    rows: List[TableRowNode] = []
    for ri, r in enumerate(range(blk.r1, last_row + 1), start=1):
        cells: List[TableCellNode] = []
        for ci, c in enumerate(range(blk.c1, last_col + 1), start=1):
            if (r, c) in covered:
                continue
            info = sheet.grid.get((r, c))
            is_header = header and r == blk.r1
            rs, cs = spans.get((r, c), (1, 1))
            cells.append(
                TableCellNode(
                    id=f"{tid}-r{ri}-c{ci}",
                    cell_type=TableCellType.HEADER if is_header else TableCellType.DATA,
                    header_scope=TableHeaderScope.COLUMN if is_header else TableHeaderScope.NONE,
                    row_span=max(1, rs),
                    col_span=max(1, cs),
                    content=_text_content(info.text if info else ""),
                    metadata=_meta(sheet, cell=f"{index_to_col(c)}{r}"),
                    children=[],
                    accessibility_flags=[],
                )
            )
        rows.append(
            TableRowNode(
                id=f"{tid}-r{ri}",
                content=NodeContent(kind=ContentKind.NONE),
                metadata=_meta(sheet),
                children=cells,
                accessibility_flags=[],
            )
        )
    props: Dict[str, Any] = {
        "sheet_part": sheet.part,
        "cell_range": blk.ref,
        "header_detection": blk.state,
        "header_reason": blk.reason,
        "excel_table": blk.table.display_name if blk.table else None,
        "rows": blk.r2 - blk.r1 + 1,
        "cols": blk.c2 - blk.c1 + 1,
        "sampled": (last_row < blk.r2) or (last_col < blk.c2),
        "label": blk.label,
    }
    if blk.table is not None and blk.table.alt_text:
        # An Excel table's Alt Text title is its programmatic caption.
        props["caption"] = blk.table.alt_text
    return TableNode(
        id=tid,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=_meta(sheet, **props),
        children=rows,
        accessibility_flags=[],
    )


def _image_node(sheet: SheetScan, obj: DrawingObject, number: int, source: str) -> ImageNode:
    prefix = "chart" if obj.kind == "chart" else "img"
    nid = f"xlsx-s{sheet.index}-{prefix}{number}"
    props: Dict[str, Any] = {
        "drawing_part": sheet.drawing_part,
        "object_index": obj.index,
        "object_kind": obj.kind,
        "cnvpr_id": obj.cnvpr_id,
        "cnvpr_name": obj.cnvpr_name,
        "anchor_cell": obj.anchor_cell,
        "chart_part": obj.chart_part,
        # The picture's bytes inside the package (xl/media/...), so a finding
        # can show a thumbnail read straight from the zip without inlining
        # base64 into the tree.
        "media_part": obj.media_part,
        "caption": obj.caption,
        "caption_source": obj.caption_source,
    }
    if _WANT_IMAGE_BYTES and obj.kind == "picture" and obj.media_part:
        ext = posixpath.splitext(obj.media_part)[1].lower()
        mime = _MIME_BY_EXT.get(ext)
        if mime:
            data = read_media(source, obj.media_part)
            if data:
                props["image_b64"] = base64.b64encode(data).decode("ascii")
                props["image_mime"] = mime
    meta = _meta(sheet, **props)
    if obj.decorative and obj.descr:
        # Decorative AND described: the validator forbids the combination, so
        # build it unvalidated — the contradiction is itself the finding.
        return ImageNode.model_construct(
            id=nid,
            node_type=ImageNode.type_value(),
            is_decorative=True,
            alt_text=obj.descr,
            content=NodeContent(kind=ContentKind.NONE),
            metadata=meta,
            children=[],
            accessibility_flags=[],
        )
    return ImageNode(
        id=nid,
        is_decorative=obj.decorative,
        alt_text=None if obj.decorative else obj.descr,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=meta,
        children=[],
        accessibility_flags=[],
    )


def _title_candidate(scan: WorkbookScan) -> Optional[str]:
    """The first visible sheet's opening title line, when it has one: a lone
    text cell above the data, 3-120 characters, reading like a title."""
    for sheet in scan.sheets:
        if sheet.state != "visible" or sheet.kind != "worksheet":
            continue
        if not sheet.notes:
            return None
        r, c, text = sheet.notes[0]
        first = min(sheet.blocks, key=lambda b: (b.r1, b.c1), default=None)
        first_row = first.r1 if first is not None else 10 ** 9
        first_col = first.c1 if first is not None else 1
        t = " ".join(text.split())
        if r > first_row or c > max(3, first_col + 1):
            return None  # not the sheet's opening line: a side note, a footer
        # A title stands on its own: a blank row under it, or the data block
        # it labels starts right under it. Text directly below it means it is
        # the first entry of a list ("Name" over a column of names is a
        # column heading, and writing it as the workbook title is worse than
        # the filename).
        labels_first_block = first is not None and first.r1 == r + 1 and first.c1 <= c <= first.c2
        if not labels_first_block and any(rr == r + 1 for (rr, _cc) in sheet.grid):
            return None
        if not (3 <= len(t) <= 120) or len(t.split()) > 15:
            return None
        if t.endswith((":", ".")) or not re.search(r"[^\W\d_]", t):
            return None
        return t
    return None


class XLSXParser:
    """``parse_to_tree`` for .xlsx workbooks (see the module docstring)."""

    def parse_to_tree(self, file_path: str) -> ParserResult:
        path = Path(file_path)
        scan = scan_workbook(path)
        return build_tree(path, scan)


def build_tree(path: Path, scan: WorkbookScan) -> ParserResult:
    properties: Dict[str, Any] = {"filename": path.name}
    if scan.title:
        properties["title"] = scan.title
    visible = [s for s in scan.sheets if s.state == "visible"]
    properties["sheet_count"] = len(visible)
    if scan.hidden_sheets:
        properties["hidden_sheet_count"] = scan.hidden_sheets
    candidate = _title_candidate(scan)
    if candidate:
        properties["title_candidate"] = candidate
    if scan.truncated:
        # Read by AnalysisTruncatedAnalyzer: part of at least one sheet lies
        # outside the window we read, and the report must say so.
        properties["pages_truncated"] = True
    root = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(language=scan.language, source_format="xlsx", properties=properties),
        children=[],
        accessibility_flags=[],
    )
    for sheet in visible:
        section = SectionNode(
            id=f"xlsx-s{sheet.index}",
            content=_text_content(sheet.name),
            metadata=_meta(
                sheet,
                sheet_part=sheet.part,
                sheet_kind=sheet.kind,
                default_sheet_name=is_default_sheet_name(sheet.name) or None,
                empty_sheet=sheet.is_empty or None,
            ),
            children=[],
            accessibility_flags=[],
        )
        root.children.append(section)
        items: List[Tuple[int, int, int, Any]] = []
        for blk in sheet.blocks:
            items.append((blk.r1, blk.c1, 1, blk))
        for r, c, text in sheet.notes[:MAX_PARAGRAPHS_PER_SHEET]:
            items.append((r, c, 0, text))
        items.sort(key=lambda it: (it[0], it[1], it[2]))
        p_no = t_no = 0
        for r, c, kind, payload in items:
            if kind == 1:
                t_no += 1
                if payload.label:
                    p_no += 1
                    section.children.append(
                        ParagraphNode(
                            id=f"xlsx-s{sheet.index}-p{p_no}",
                            content=_text_content(payload.label),
                            metadata=_meta(sheet, cell=payload.label_cell, role="table_label"),
                            children=[],
                            accessibility_flags=[],
                        )
                    )
                section.children.append(_table_node(sheet, payload, t_no))
            else:
                if payload.strip() and not any(
                    b.label_cell == f"{index_to_col(c)}{r}" for b in sheet.blocks
                ):
                    p_no += 1
                    section.children.append(
                        ParagraphNode(
                            id=f"xlsx-s{sheet.index}-p{p_no}",
                            content=_text_content(payload),
                            metadata=_meta(sheet, cell=f"{index_to_col(c)}{r}"),
                            children=[],
                            accessibility_flags=[],
                        )
                    )
        pics = charts = 0
        for obj in sheet.drawings:
            if obj.kind == "chart":
                charts += 1
                section.children.append(_image_node(sheet, obj, charts, str(path)))
            else:
                pics += 1
                section.children.append(_image_node(sheet, obj, pics, str(path)))

    raw_metadata: Dict[str, Any] = {
        "sheet_count": len(visible),
        "title": scan.title,
        "language": scan.language,
    }
    return ParserResult(
        document_id=derive_document_id(path),
        format="xlsx",
        tree=AccessibilityTree(root=root, metadata=raw_metadata),
        raw_metadata=raw_metadata,
    )


__all__ = [
    "XLSXParser",
    "scan_workbook",
    "build_tree",
    "iter_drawing_objects",
    "cnvpr_of",
    "is_default_sheet_name",
    "judge_header",
    "parse_range",
    "format_range",
    "index_to_col",
    "Package",
    "parse_xml",
    "rels_part_for",
    "resolve_target",
]
