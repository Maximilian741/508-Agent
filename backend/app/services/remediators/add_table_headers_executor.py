"""Executor that marks an existing header row on data tables that lack one."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


_NEEDS_A_PERSON = "Left for you to mark; nothing was changed and you were not charged for it."


class AddTableHeadersExecutor(RemediationExecutor):
    """Promotes the first row of a header-less table to header cells — only
    when that row really is a row of column names.

    It never invents header text. It used to: a table whose first row did not
    look like labels got a synthetic "Column 1 | Column 2" row inserted into
    the customer's file (visible, bold, repeated on every page) and was
    charged for it — and a first row of DATA ("2023 | 410 | 12%", "North |
    120 | 9%") was promoted to headers, so a screen reader announced "410" as
    a column name for every cell below it. Neither is better than no header.
    When the first row is not clearly a header row the table is left for a
    person with the reason.
    """

    supported_actions = [ActionCode.ADD_TABLE_HEADERS]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "No tree provided.")

        target = _find_table(tree, plan.target_node_id)
        if target is None:
            return _result(action_code, plan, ExecutionStatus.SKIPPED, "Target table not found.")
        if not _has_flag(plan, target, AccessibilityFlagCode.TABLE_MISSING_HEADERS):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "Table is not flagged as missing headers; no changes applied.",
            )

        rows = [child for child in target.children if isinstance(child, TableRowNode)]
        if not rows:
            return _refused(
                action_code,
                plan,
                "This table has no rows we could read, so there is no header row to mark.",
            )

        first_row = rows[0]
        existing_cells = [c for c in first_row.children if isinstance(c, TableCellNode)]
        if existing_cells and all(c.cell_type == TableCellType.HEADER for c in existing_cells):
            return _result(
                action_code,
                plan,
                ExecutionStatus.SKIPPED,
                "First row already consists of header cells.",
            )

        body = [
            [c for c in r.children if isinstance(c, TableCellNode)]
            for r in rows[1:]
        ]
        problem = header_row_problem(existing_cells, body)
        if problem:
            return _refused(
                action_code,
                plan,
                f"We did not mark a header row because {problem}. "
                "A person needs to mark (or add) the row of column names.",
            )

        for cell in existing_cells:
            cell.cell_type = TableCellType.HEADER
            if cell.header_scope == TableHeaderScope.NONE:
                cell.header_scope = TableHeaderScope.COLUMN
        return _result(
            action_code,
            plan,
            ExecutionStatus.SUCCESS,
            f"Promoted {len(existing_cells)} cells in row 1 to TH/scope=col.",
        )


def _find_table(tree: AccessibilityTree, node_id: str) -> Optional[TableNode]:
    for node in iter_reading_order(tree.root):
        if node.id == node_id and isinstance(node, TableNode):
            return node
    return None


def _has_flag(plan: RemediationPlan, target, code: AccessibilityFlagCode) -> bool:
    if plan.flag.code == code:
        return True
    return any(flag.code == code for flag in target.accessibility_flags)


# A number, amount, percentage, measurement or date — DATA, not a column name.
# "12%", "$1,200", "(3.5)", "-4", "1/2/2024", "12:30", "3.2 kg".
_NUMERIC_RE = re.compile(
    r"^[\s(]*[-+−–]?\s*[$€£¥₹]?\s*\d[\d,.\s]*(?:%|[kmb]n?|bn|kg|g|km|m|cm|mm|lb|lbs|hrs?|h|min|x)?[)\s]*$"
    r"|^\d{1,4}[/.\-]\d{1,2}(?:[/.\-]\d{1,4})?$"
    r"|^\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?$",
    re.IGNORECASE,
)
# A year on its own. Years are legitimate COLUMN NAMES ("Region | 2023 | 2024")
# as long as the column under them is not itself a column of years.
_YEAR_RE = re.compile(r"^(?:FY\s?)?(?:19|20|21)\d\d$", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f�]")
# A number followed by a unit or scale word ("10 days", "3.5 hours", "12
# pages", "4 million") is a measurement: data, like "12%".
_NUMBER_THEN_REST_RE = re.compile(r"^[\s(]*[-+−–]?\s*[$€£¥₹]?\s*\d[\d,.]*\s*(.*?)[)\s]*$")
_UNIT_WORDS = frozenset(
    "% day days wk wks week weeks mo mos month months yr yrs year years hr hrs hour hours "
    "min mins minute minutes sec secs second seconds page pages people persons person staff "
    "unit units item items k m bn million millions billion billions thousand thousands "
    "km mi kg g lb lbs ft in cm mm ml l fte usd eur gbp pts points x times".split()
)
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
_URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
_PHONE_RE = re.compile(r"^\+?[\d\s().\-]{7,}$")
# Values that stand in for data ("TBD", "N/A", a tick): a row holding them is
# a data row with gaps, not a row of column names.
_DATA_PLACEHOLDERS = frozenset("n/a na n.a. tbd tba tbc none nil pending varies unknown - — – ✓ ✔ ✗".split())
# "Date RECEIVED", "Name OF applicant": a column name with a qualifier.
_QUALIFIER_WORDS = frozenset(
    "of for per by de del des du da do di von van received requested submitted issued due paid "
    "completed approved started ended created updated sent opened closed signed filed id no number "
    "code type name date amount total range".split()
)
# Words people use to NAME columns. A first row made only of these ("Name |
# Email | Phone", "Term | Definition") is a header row even when nothing
# below it is a number; a first row of other words ("Alice | Engineering |
# Denver", "Monday | Staff meeting | Room 4") may just as well be the first
# row of DATA, and promoting it would make a screen reader announce "Alice"
# as the column name of every cell beneath it. English plus the common
# equivalents in the other languages the offline rules know.
_HEADER_WORDS = frozenset(
    """
    name names first last full surname title role position job department dept division unit office
    team group organization organisation agency company employer vendor supplier provider owner lead
    contact contacts person responsible manager staff email e-mail mail phone telephone tel mobile cell
    fax address street city town state province zip postcode postal country county district region
    area zone location site room building campus facility date dates day days week weeks month months
    year years quarter period time times hour hours duration deadline due start end begin begins
    ends opens closes opening closing schedule frequency status stage phase step steps priority type types
    kind category categories class classification item items product products service services program
    programme programs programmes project projects task tasks activity activities action actions event
    events course courses subject topic topics module modules session sessions description descriptions
    detail details summary notes note comments comment remarks explanation purpose reason reasons
    definition definitions meaning term terms keyword field fields column value values amount amounts
    total totals subtotal sum cost costs price prices fee fees rate rates charge charges budget
    budgets spend spending expense expenses revenue income balance payment payments salary wage
    qty quantity quantities count counts number numbers # id code codes reference ref version
    percent percentage share ratio score scores grade grades level levels rank ranking result results
    outcome outcomes goal goals target targets objective objectives measure measures metric metrics
    indicator indicators kpi baseline actual actuals forecast estimate estimates change difference
    variance growth trend source sources method methods format size weight height length age gender
    population speakers language languages requirement requirements criteria criterion eligibility
    coverage benefit benefits plan plans option options feature features question questions answer
    answers response responses rating ratings feedback file files document documents link links page
    pages section sections chapter version author owner approver approval signature account accounts
    invoice invoices order orders permit permits license licence application applications turnaround
    capacity availability deliverable deliverables milestone milestones risk risks impact likelihood
    mitigation issue issues finding findings recommendation recommendations standard standards
    guideline guidelines rule rules criterion wcag
    nombre apellido fecha hora teléfono correo dirección ciudad estado región departamento cargo
    descripción tipo cantidad importe precio costo coste total categoría servicio programa notas
    nom prénom heure téléphone courriel adresse ville région poste statut quantité montant prix coût
    catégorie programme remarques
    datum uhrzeit telefon adresse stadt abteilung beschreibung typ menge anzahl betrag preis kosten
    summe gesamt kategorie bemerkungen
    nome cognome data ora telefono indirizzo città reparto descrizione tipo quantità importo prezzo
    costo totale categoria servizio note
    endereço cidade descrição quantidade valor preço custo categoria serviço observações
    naam datum tijd adres plaats afdeling omschrijving beschrijving aantal bedrag prijs kosten totaal
    categorie opmerkingen
    """.split()
)
# Abbreviated column names, kept with their dot so "No" (a value) is not one.
_ABBREVIATED_HEADERS = frozenset(
    "no. nr. n° nº # amt. qty. dept. ref. tel. avg. est. pct. approx. yr. mo. wk. hrs. min. max.".split()
)
# Period labels are column names too ("Q1 | Q2", "Jan | Feb", "Mon | Tue").
_PERIOD_RE = re.compile(
    r"^(?:q[1-4]|h[12]|fy\s?\d{2,4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\.?|week\s*\d+|month\s*\d+|day\s*\d+)$",
    re.IGNORECASE,
)


def _cell_text(cell: TableCellNode) -> str:
    return " ".join(((cell.content.text or "") if cell.content else "").split())


def _is_typed_value(text: str) -> bool:
    """A number, amount, measurement, date, time, email, web address or phone
    number — the kind of value a data cell holds, never a column's name."""
    t = text.strip()
    if not t:
        return False
    if _NUMERIC_RE.match(t) or _YEAR_RE.match(t):
        return True
    if _EMAIL_RE.match(t) or _URL_RE.match(t) or (_PHONE_RE.match(t) and sum(ch.isdigit() for ch in t) >= 7):
        return True
    m = _NUMBER_THEN_REST_RE.match(t)
    if m:
        rest = m.group(1).strip(" .").lower()
        if not rest or rest in _UNIT_WORDS:
            return True
    return False


def _is_header_word(text: str) -> bool:
    """True when ``text`` reads as a column NAME ("Due date", "Cost (USD)",
    "Phone number", "Q1") rather than as a value."""
    raw = " ".join((text or "").split()).lower()
    if raw in _ABBREVIATED_HEADERS:
        return True
    t = " ".join(re.sub(r"\([^)]*\)", " ", raw).split()).rstrip(".:")
    if not t:
        return False
    if t in _HEADER_WORDS or _PERIOD_RE.match(t):
        return True
    if re.search(r"\d", t):
        # "Room 4", "Building 7", "Phase 2": a particular one — a value.
        return False
    words = re.findall(r"[^\W\d_]+|#", t)
    if not words or len(words) > 4:
        return False
    # The head noun ("Phone NUMBER", "Annual COST", "Due DATE") ...
    if words[-1] in _HEADER_WORDS:
        return True
    # ... or a leading noun with a qualifier ("DATE received", "NAME of
    # applicant") — but not any phrase that merely starts with one ("Staff
    # meeting" is an event, not a column).
    return len(words) >= 2 and words[0] in _HEADER_WORDS and words[1] in _QUALIFIER_WORDS


def _column_contrast(header: str, header_is_year: bool, below: Sequence[str]) -> bool:
    """The column's name is a word (or a year) and what sits under it is
    mostly typed values: "Q1" over "10, 20", "Fee" over "$240, $60",
    "2023" over "410, 455"."""
    values = [v for v in below if v]
    if not values:
        return False
    if header_is_year:
        typed = [v for v in values if _is_typed_value(v) and not _YEAR_RE.match(v)]
    else:
        if _is_typed_value(header):
            return False
        typed = [v for v in values if _is_typed_value(v)]
    return len(typed) / len(values) >= 0.6


def header_row_problem(cells: Sequence[TableCellNode], body: Sequence[Sequence[TableCellNode]]) -> Optional[str]:
    """Why ``cells`` is NOT a header row, or None when it clearly is.

    A header row names the columns: every cell is a short label, at least one
    of them is a word, and it does not read like the data rows beneath it. A
    row of numbers, amounts or dates is data; a label ending in ':' belongs to
    a label/value form ("Name: | ___"), not to a column; empty cells mean there
    is no complete set of names to promote.

    Not looking like data is not enough: the first row of a table of words
    ("Alice | Engineering | Denver") looks exactly like a row of names. So a
    row is promoted only on POSITIVE evidence — at least one column whose
    name is a word over values that are numbers, amounts, dates, emails or
    phone numbers, or every cell being a word people use to name columns
    ("Name | Email | Phone", "Term | Definition", "Q1 | Q2"). Otherwise the
    table is left for a person: a wrong header row is worse than none.
    """
    if not cells:
        return "the first row has no cells"
    texts = [_cell_text(c) for c in cells]
    if any(_CONTROL_RE.search(t) for t in texts):
        return "the first row's text could not be read reliably"
    if any(not t for t in texts):
        return "the first row has empty cells, so it is not a complete row of column names"
    for t in texts:
        if len(t.split()) > 8:
            return "a cell in the first row is a sentence, not a column name"
        if t.endswith(("!", "?")) or (t.endswith(".") and len(t.split()) >= 2):
            # "No." / "Amt." are column names; "Revenue grew." is not.
            return "a cell in the first row is a sentence, not a column name"
        if t.endswith(":"):
            return "the first row is a label and its value (a form layout), not a row of column names"
    years = [bool(_YEAR_RE.match(t)) for t in texts]
    numeric = [_is_typed_value(t) and not y for t, y in zip(texts, years)]
    if any(numeric):
        return "the first row holds numbers, dates or contact details (data), not column names"
    if any(t.strip().lower() in _DATA_PLACEHOLDERS for t in texts):
        return "the first row holds values such as 'N/A' or 'TBD' (data), not column names"
    if all(years):
        # "2021 | 2022 | 2023" over non-year numbers is a header of years; over
        # more years it is a column of data. Decided per column below.
        pass
    elif not any(re.search(r"[^\W\d_]{2,}", t) for t in texts):
        return "the first row has no words in it"
    # A year in row 1 is a column name only when the column under it is not
    # a column of years too ("2023 | 410 | 12%" over "2024 | 455 | 11%").
    for col, is_year in enumerate(years):
        if not is_year:
            continue
        below = [_cell_text(r[col]) for r in body if col < len(r)]
        if any(_YEAR_RE.match(t) for t in below if t):
            return "the first row is a data row (its years continue in the rows below)"
    if not body:
        return "the table has only one row, so there is no data for a header row to label"

    # Positive evidence, one of:
    # (a) every cell is a column name (or a year heading a column of amounts);
    if all(y or _is_header_word(t) for t, y in zip(texts, years)):
        return None
    # (b) a column whose name is a word sits over typed values — unless the
    #     table's first COLUMN is itself a list of field names ("Name / Phone
    #     / Email" down the side): that is a label/value form, and its first
    #     row is the first label and its value.
    contrast = any(
        _column_contrast(t, y, [_cell_text(r[col]) for r in body if col < len(r)])
        for col, (t, y) in enumerate(zip(texts, years))
    )
    if contrast:
        side = [_cell_text(r[0]) for r in body if r and _cell_text(r[0])]
        if len(side) >= 2 and sum(_is_header_word(s) for s in side) * 2 >= len(side):
            return (
                "the labels run down the first column (a form layout), so the first row is "
                "a label and its value, not a row of column names"
            )
        return None
    return (
        "every row of this table is words, and nothing shows that the first row names the "
        "columns rather than being the first row of data"
    )


def _refused(action_code: ActionCode, plan: RemediationPlan, reason: str) -> ExecutionResult:
    return _result(action_code, plan, ExecutionStatus.SKIPPED, f"{reason.strip()} {_NEEDS_A_PERSON}")


def _looks_like_header_row(cells, body: Optional[List[List[TableCellNode]]] = None) -> bool:
    """Back-compat predicate: True when :func:`header_row_problem` finds none."""
    return header_row_problem(cells, body or [[]]) is None


def _result(
    action_code: ActionCode,
    plan: RemediationPlan,
    status: ExecutionStatus,
    notes: str,
) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=status,
        notes=notes,
    )
