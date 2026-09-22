"""What the offline (no-AI) path is allowed to write into a customer's file.

The launch state has no AI key. Before this module, the heuristic provider
"fixed" things by pattern-filling: ``Read more about click here`` for a link,
``Image html-img-1 — Home About Contact Login`` for a picture that sat under a
nav bar, ``Table: v00, v01, v02`` for a table, ``fr`` for a Spanish page, and
``Doc 20240912 Wa0003`` for a WhatsApp export's title. Every one of those was
written into the output bytes, counted as a fix, and charged.

The rule here is the product's rule: a change that is not clearly better than
the original is REFUSED. Each function below either returns text it can stand
behind or ``None`` plus a plain-English reason a customer can read (the
executors put it in the "needs you" list). Refusing is always allowed; guessing
never is.

These are pure functions with no I/O so the catalog of good and bad cases can
be pinned directly (see ``smoke_semantic_rules_catalog``) and the same gates
can vet an AI provider's answer, not just the heuristic's.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class Verdict:
    """``text`` is what may be written, or ``None`` with ``reason`` saying why not."""

    text: Optional[str]
    reason: str

    @property
    def ok(self) -> bool:
        return bool(self.text)


def _refuse(reason: str) -> Verdict:
    return Verdict(None, reason)


def _collapse(text: Optional[str]) -> str:
    return " ".join(str(text or "").split())


def has_control_chars(text: str) -> bool:
    """True for C0 controls (other than whitespace) or U+FFFD.

    A PDF whose font maps glyphs through a 2-byte CMap yields strings like
    ``\\x00:\\x00L\\x00Q`` when decoded naively; nothing derived from bytes we
    could not decode is ever written as a title, caption or alt.
    """
    for ch in text or "":
        if ch == "\N{REPLACEMENT CHARACTER}":
            return True
        if ord(ch) < 0x20 and ch not in "\t\n\r":
            return True
    return False


# Unicode letters only (no digits/underscore), 3+ in a row: "a real word".
_REAL_WORD_RE = re.compile(r"[^\W\d_]{3,}")


def _real_words(text: str) -> List[str]:
    return _REAL_WORD_RE.findall(text or "")


# ---------------------------------------------------------------------------
# Shared text-shape predicates
# ---------------------------------------------------------------------------

# Words a site menu is made of. Two or more of them in one short line is a
# nav bar, not a description of anything.
_NAV_WORDS = {
    "home", "about", "about us", "contact", "contact us", "login", "log in",
    "logout", "log out", "sign in", "sign up", "signin", "signup", "register",
    "search", "menu", "services", "products", "blog", "news", "careers",
    "jobs", "faq", "faqs", "help", "support", "shop", "store", "cart",
    "account", "my account", "privacy", "terms", "sitemap", "donate",
    "events", "resources", "team", "pricing", "portfolio", "gallery",
    "inicio", "contacto", "accueil", "startseite", "kontakt", "impressum",
}
_NAV_SEPARATORS_RE = re.compile(r"\s[|·•»›/]\s")

_BYLINE_RE = re.compile(
    r"^(posted|published|updated|written|last\s+(updated|modified|edited)|submitted|filed)\b"
    r"|\bby\s+(admin|administrator|staff|webmaster|editor|the\s+editor)\b",
    re.IGNORECASE,
)
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_DATE_ONLY_RE = re.compile(
    rf"^(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?"
    rf"(?:(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{2,4}}"
    rf"|\d{{1,2}}\s+(?:{_MONTHS})\.?,?\s+\d{{2,4}}"
    rf"|\d{{1,4}}[/.\-]\d{{1,2}}[/.\-]\d{{1,4}})$",
    re.IGNORECASE,
)
_CREDIT_RE = re.compile(
    r"^(?:(?:photo|image|picture|illustration)\s*(?:credit|courtesy|by|source)\b|©|\(c\)\s|copyright\b|source:)",
    re.IGNORECASE,
)
# Interface instructions, not descriptions ("Click to enlarge").
_UI_PHRASE_RE = re.compile(
    r"^(?:click|tap|hover|press|select|zoom|enlarge|open|download|watch|play)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URLISH_RE = re.compile(r"(?:https?://|www\.|mailto:|tel:|javascript:|file:|ftp://)", re.IGNORECASE)
_PHONEISH_RE = re.compile(r"^\+?[\d\s().\-]{7,}$")


def is_nav_like(text: str) -> bool:
    t = _collapse(text).lower()
    if not t:
        return False
    if _NAV_SEPARATORS_RE.search(f" {t} ") and len(t.split()) <= 12:
        return True
    tokens = re.split(r"[\s|·•»›/]+", t)
    hits = 0
    i = 0
    while i < len(tokens):
        pair = " ".join(tokens[i:i + 2])
        if pair in _NAV_WORDS and " " in pair:
            hits += 1
            i += 2
            continue
        if tokens[i] in _NAV_WORDS:
            hits += 1
        i += 1
    return hits >= 2 and len(tokens) <= 10


def is_byline_or_date(text: str) -> bool:
    t = _collapse(text)
    return bool(_BYLINE_RE.search(t) or _DATE_ONLY_RE.match(t))


# ---------------------------------------------------------------------------
# Alt text
# ---------------------------------------------------------------------------

# Where a parser found the text it attached as properties['caption'].
# Authored = a person wrote it FOR this picture. Everything else is merely
# near the picture: a nav bar, a byline, the next body paragraph.
AUTHORED_CAPTION_SOURCES = frozenset({"figcaption", "title", "caption_style", "figure_label"})
UNAUTHORED_CAPTION_SOURCES = frozenset({"preceding_text", "own_paragraph", "nearby_text"})

_FIGURE_WORDS = (
    r"figure|fig\.?|chart|graph|photo(?:graph)?|image|picture|illustration|diagram"
    r"|map|exhibit|plate|infographic|graphic|abbildung|abb\.|figura|imagen|gráfico|grafico"
    r"|table|tabla|tableau|tabelle|tabella"
)
# Captions that are page furniture, not descriptions.
_NOT_A_DESCRIPTION_RE = re.compile(
    r"^\(?(?:continued|cont(?:'d|\.)?|continued (?:on|from) (?:next|previous) page|see (?:below|above)"
    r"|see (?:figure|fig\.?|chart|table) \S+(?: (?:below|above))?)\)?\.?$",
    re.IGNORECASE,
)
# "Figure 2: …", "Fig. 3 – …", "Chart 1) …", "Figure 2-1. …", "Figure IV: …"
_FIGURE_LABEL_SEP_RE = re.compile(
    rf"^\s*(?:{_FIGURE_WORDS})\s*(?:no\.?\s*)?(?:\d+(?:[.\-]\d+)*[a-z]?|[ivxlc]+)"
    r"\s*(?::|\)|[\-–—]|\.(?=\s))\s*",
    re.IGNORECASE,
)
# "Figure 2.1 Quarterly revenue" (no separator, next word capitalized).
_FIGURE_LABEL_BARE_RE = re.compile(
    rf"^\s*(?:{_FIGURE_WORDS})\s*(?:\d+(?:[.\-]\d+)*[a-z]?)\s+(?=(?-i:[A-ZÀ-Þ]))",
    re.IGNORECASE,
)
# Nothing but the label: "Figure 1.", "Fig. 3", "Chart 2:", "Figure IV".
_FIGURE_LABEL_ONLY_RE = re.compile(
    rf"^\s*(?:{_FIGURE_WORDS})\s*(?:no\.?\s*)?(?:\d+(?:[.\-]\d+)*[a-z]?|[ivxlc]+)\s*[.:)\-–—]?\s*$",
    re.IGNORECASE,
)
_ALT_MAX = 200


def split_figure_label(text: str) -> Tuple[bool, str]:
    """``(had_label, description)`` — strip a leading "Figure N:" label."""
    t = _collapse(text)
    if _FIGURE_LABEL_ONLY_RE.match(t):
        return True, ""
    m = _FIGURE_LABEL_SEP_RE.match(t) or _FIGURE_LABEL_BARE_RE.match(t)
    if not m:
        return False, t
    return True, t[m.end():].strip()


def _trim_alt(text: str) -> str:
    t = text.strip().rstrip(":").strip()
    if len(t) <= _ALT_MAX:
        return t
    cut = t[:_ALT_MAX]
    # Prefer ending on a sentence; else on a word boundary.
    for stop in (". ", "; "):
        idx = cut.rfind(stop)
        if idx >= 60:
            return cut[: idx + 1].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _description_problem(desc: str) -> Optional[str]:
    """Why ``desc`` is not a description of a picture, or None if it is."""
    from app.analyzers.image_analyzer import is_nondescriptive_alt

    d = _collapse(desc)
    if not d:
        return "the caption only numbers the figure and says nothing about what it shows"
    if has_control_chars(d):
        return "the text near it could not be read reliably"
    if is_nondescriptive_alt(d):
        return "the text near it is a file name or placeholder, not a description"
    if _URLISH_RE.search(d) or _EMAIL_RE.search(d):
        return "the text near it is a web or email address, not a description"
    if is_byline_or_date(d):
        return "the text near it is a date or byline, not a description"
    if _CREDIT_RE.match(d):
        return "the text near it is a photo credit, not a description"
    if is_nav_like(d):
        return "the text near it is a website menu, not a description"
    if _UI_PHRASE_RE.match(d):
        return "the text near it is an instruction ('click to enlarge'), not a description"
    if _NOT_A_DESCRIPTION_RE.match(d):
        return "the text near it is a page note ('continued', 'see below'), not a description"
    if len(_real_words(d)) < 2 and len(d) < 6:
        return "the caption is too short to describe anything"
    if len(_REAL_WORD_RE.findall(d)) == 0:
        return "the caption has no words in it"
    return None


def alt_from_caption(caption: Optional[str], source: Optional[str]) -> Verdict:
    """Alt text derived ONLY from a caption a person wrote for this picture.

    ``source`` is the parser's ``caption_source``. A caption with no recorded
    source (older parsers, PDF) is accepted only when it carries its own
    figure label ("Figure 2: …") — that is the author saying "this text is
    this picture's caption". Text that merely sat nearby is never used: the
    heuristic has no eyes, and the paragraph above a picture is as likely to
    be a menu or a byline as a description.
    """
    c = _collapse(caption)
    if not c:
        return _refuse(
            "This picture has no caption, so there is nothing to describe it from. "
            "It needs a person to write one sentence saying what it shows."
        )
    src = (source or "").strip().lower() or None
    if src in UNAUTHORED_CAPTION_SOURCES:
        return _refuse(
            "The only text near this picture is ordinary page text, not a caption written for it, "
            "so we did not use it. It needs a person to write one sentence saying what the picture shows."
        )
    had_label, desc = split_figure_label(c)
    if src not in AUTHORED_CAPTION_SOURCES and not had_label:
        return _refuse(
            "The text near this picture is not marked as its caption, so we did not use it. "
            "It needs a person to write one sentence saying what the picture shows."
        )
    problem = _description_problem(desc)
    if problem:
        return _refuse(
            f"We did not describe this picture because {problem}. "
            "It needs a person to write one sentence saying what it shows."
        )
    return Verdict(_trim_alt(desc), "from the picture's own caption")


# Our own pre-fix output shape: "Image html-img-1 — …", "Image page-3-img2 — …".
_NODE_ID_ALT_RE = re.compile(
    r"^(?:image|picture|figure|graphic|photo)\s+[a-z0-9]+(?:-[a-z0-9]+)*\s+[—–-]\s+",
    re.IGNORECASE,
)


def vet_alt_text(text: Optional[str], *, node_id: Optional[str] = None) -> Optional[str]:
    """Final gate for ANY provider's alt text. Returns a refusal reason or None."""
    from app.analyzers.image_analyzer import is_nondescriptive_alt

    t = _collapse(text)
    if not t:
        return "no description was produced"
    if has_control_chars(t):
        return "the description contained unreadable characters"
    if node_id and node_id.lower() in t.lower():
        return "the description contained an internal id instead of words"
    if _NODE_ID_ALT_RE.match(t):
        return "the description was a label, not a description"
    if is_nondescriptive_alt(t):
        return "the description was a file name or placeholder"
    if _URLISH_RE.search(t) or _EMAIL_RE.search(t):
        return "the description was a web or email address"
    if is_nav_like(t) or is_byline_or_date(t):
        return "the description was a menu, date or byline"
    return None


# ---------------------------------------------------------------------------
# Link text
# ---------------------------------------------------------------------------

_FILE_KINDS = {
    "pdf": "PDF", "doc": "Word document", "docx": "Word document", "rtf": "Word document",
    "xls": "Excel workbook", "xlsx": "Excel workbook", "csv": "CSV file",
    "ppt": "PowerPoint", "pptx": "PowerPoint", "zip": "ZIP file",
    "mp4": "video", "mov": "video", "mp3": "audio", "wav": "audio",
    "txt": "text file", "odt": "document", "epub": "e-book",
}
_WEB_PAGE_EXTS = {"html", "htm", "php", "asp", "aspx", "jsp", "cfm", "shtml", "xhtml"}
# Tokens that name the container, not the content ("index", "view", "IMG").
_JUNK_SLUG_TOKENS = {
    "index", "default", "home", "page", "pages", "file", "files", "download", "downloads",
    "view", "show", "get", "item", "items", "doc", "docs", "document", "img", "image",
    "images", "content", "uploads", "upload", "wp", "attachment", "attachments", "watch",
    "article", "post", "node", "detail", "details", "dsc", "dscn", "pic", "photo", "scan",
    "untitled", "copy", "temp", "tmp", "asset", "assets", "media", "static", "cdn",
    "redirect", "link", "url", "main",
}
_SLUG_NOISE_RE = re.compile(r"^(?:v\d+|rev\d*|final|draft|copy|new|old|tmp|temp)$", re.IGNORECASE)
_HASHLIKE_RE = re.compile(r"^(?=.*\d)(?=.*[a-z])[a-z0-9]{12,}$|^[0-9a-f]{8,}$", re.IGNORECASE)
_HOST_OK_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _is_generic_link_text(text: str) -> bool:
    from app.analyzers.link_analyzer import NON_DESCRIPTIVE_LINK_TEXT, _looks_like_url, _normalize_text

    normalized = _normalize_text(text or "")
    return (not normalized) or normalized in NON_DESCRIPTIVE_LINK_TEXT or _looks_like_url(text or "")


def link_text_from_target(original: Optional[str], target: Optional[str]) -> Verdict:
    """Link text derived from the destination's own words, or a refusal.

    Only the last path segment of a web address can supply words, and only
    when it has at least two real words ("annual-report-2025.pdf" -> "Annual
    report 2025 (PDF)"). An in-page anchor, an email or phone link, a site's
    home page, or a slug like "f1040" or "watch?v=…" says nothing a person
    could act on — those are left for a person to name.
    """
    t = (target or "").strip()
    if not t:
        return _refuse(
            "This link has no destination address, so there is nothing to name it after. "
            "A person needs to write what it links to."
        )
    if t.startswith("#"):
        return _refuse(
            "This link jumps to another place in the same document, and the address doesn't say where. "
            "A person needs to name it after the section it goes to."
        )
    parsed = urlparse(t)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("mailto", "tel", "sms"):
        return _refuse(
            "This link starts an email or a phone call. Its text should say who it contacts "
            "(for example 'Email the benefits office'), which a person has to write."
        )
    if scheme and scheme not in ("http", "https"):
        return _refuse(
            "This link runs a script or opens something other than a web page, so its address "
            "can't be turned into a name. A person needs to write what it does."
        )
    if not scheme and not parsed.netloc and "/" not in t and "." not in t:
        # A bare bookmark name such as Word's "_Toc12345".
        return _refuse(
            "This link jumps to a bookmark inside the document, and the bookmark name isn't words. "
            "A person needs to name it after the section it goes to."
        )
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    if host and (host == "localhost" or _IP_RE.match(host) or not _HOST_OK_RE.match(host)):
        return _refuse(
            "This link points at a server address rather than a named page, so its address "
            "can't be turned into a name. A person needs to write what it links to."
        )
    path = parsed.path or ""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return _refuse(
            "This link goes to a website's home page, and the address alone doesn't say what the "
            "reader will find there. A person needs to write what it links to."
        )
    last = unquote(segments[-1])
    stem, ext = os.path.splitext(last)
    ext = ext.lower().lstrip(".")
    if ext and ext not in _FILE_KINDS and ext not in _WEB_PAGE_EXTS:
        # "setup-v2.3.1" — the "extension" is part of the name.
        stem, ext = last, ""
    tokens = [tok for tok in re.split(r"[\-_+.\s]+", stem) if tok]
    tokens = [tok for tok in tokens if not _SLUG_NOISE_RE.match(tok)]
    if not tokens or len(tokens) > 10 or any(_HASHLIKE_RE.match(tok) for tok in tokens):
        return _refuse(
            "The link's address is a code, not words, so it can't be turned into a name. "
            "A person needs to write what it links to."
        )
    words = [
        tok for tok in tokens
        if re.fullmatch(r"[^\W\d_]{3,}", tok) and tok.lower() not in _JUNK_SLUG_TOKENS
    ]
    if len(words) < 2 or len(words) * 2 < len(tokens):
        return _refuse(
            "The link's address doesn't contain enough real words to say where it goes "
            f"({last!r}). A person needs to write what it links to."
        )
    phrase = " ".join(tokens)
    phrase = phrase[:1].upper() + phrase[1:]
    kind = _FILE_KINDS.get(ext)
    text = f"{phrase} ({kind})" if kind else phrase
    if _is_generic_link_text(phrase) or _is_generic_link_text(text) or len(text) > 100:
        return _refuse(
            "The link's address is itself a generic phrase, so it can't be turned into a better name. "
            "A person needs to write what it links to."
        )
    return Verdict(text, "from the words in the link's address")


_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:please\s+)?(?:click|tap)\s+(?:here\s+)?(?:to\s+)?(?:read|view|see|visit|open|go\s+to|for)?\s*"
    r"|(?:read|learn|find\s+out|see|view)\s+more(?:\s+(?:about|on|at|of))?\s*"
    r"|(?:visit|go\s+to|open|see|view|more\s+(?:about|on))\s+)",
    re.IGNORECASE,
)


def vet_link_text(suggestion: Optional[str], original: Optional[str], target: Optional[str]) -> Optional[str]:
    """Final gate for ANY provider's link text. Returns a refusal reason or None."""
    from app.analyzers.link_analyzer import _normalize_text

    s = _collapse(suggestion)
    if not s:
        return "no link name was produced"
    if _is_generic_link_text(s):
        return "the suggested name was itself generic"
    if _normalize_text(s) == _normalize_text(original or ""):
        return "the suggested name was the same as the old one"
    if _URLISH_RE.search(s):
        return "the suggested name was a raw address"
    rest = _LEADING_FILLER_RE.sub("", s, count=1).strip(" .:;,-")
    if rest != s:
        # "Read more about <x>": only acceptable if <x> is real words that are
        # not the old text, an address, a phone number or an anchor.
        if (
            not rest
            or _is_generic_link_text(rest)
            or _normalize_text(rest) == _normalize_text(original or "")
            or _EMAIL_RE.search(rest)
            or _PHONEISH_RE.match(rest)
            or rest.startswith("#")
            or not _real_words(rest)
            # One token of code or address: "void(0)", "_Toc12345", "a/b".
            or (" " not in rest and re.search(r"[()#@/:=?&_]", rest))
        ):
            return "the suggested name only wrapped the old text or an address in filler"
        from app.analyzers.link_analyzer import _looks_like_url

        if _looks_like_url(rest):
            return "the suggested name only wrapped an address in filler"
    if _EMAIL_RE.search(s) and len(_real_words(_EMAIL_RE.sub("", s))) < 2:
        return "the suggested name was an email address"
    if s.startswith("#") or has_control_chars(s):
        return "the suggested name was not words"
    return None


# ---------------------------------------------------------------------------
# Document language (Latin-script languages)
# ---------------------------------------------------------------------------

# The most frequent function words of each language. Words that appear in MORE
# THAN ONE list are removed below, so only distinctive words vote: "de" and
# "la" are Spanish, French, Italian and Portuguese at once and used to tip a
# Spanish page to French by dictionary order.
_FUNCTION_WORDS: Dict[str, str] = {
    "en": (
        "the of and to in is that for it as with was on be by at this are from or have an they "
        "which you were her his all their has will would there been not but can if more one we "
        "our your these those than other into about should must may also such each any who what "
        "when how please only its a i"
    ),
    "fr": (
        "le la les de des du un une et en est que qui dans pour pas par sur au aux avec ce cette "
        "ces il elle ils elles nous vous votre vos notre nos leur leurs son sa ses sont ont été "
        "être avoir fait mais ou où plus tout tous toutes très sans sous entre aussi comme lors "
        "afin dont chaque peut doit ainsi depuis avant après on se ne y"
    ),
    "es": (
        "el la los las de del y en un una unos unas que es por para con no se su sus al lo como "
        "más pero este esta estos estas ese esa son está están ha han fue ser muy también sobre "
        "entre cuando hasta desde nuestro nuestra nuestros usted ustedes hay ya porque todos todas "
        "puede sin según durante cada le les otro otra donde año años o"
    ),
    "de": (
        "der die das den dem des und ist nicht mit für auf ein eine einen einem einer zu von im "
        "sie ihre ihr wir sind es auch als bei nach aus wie oder aber wird werden wurde hat haben "
        "sich dass noch nur über unter bis vor zum zur kann können muss diese dieser dieses alle "
        "mehr sehr jedoch sowie bitte an was am um"
    ),
    "it": (
        "il lo la i gli le di del della dei delle degli dello un una uno e è che per non con da "
        "dal dalla in nel nella nei nelle su sul sulla al alla ai alle si sono come anche più "
        "questo questa questi queste essere ha hanno ma se suo sua loro tra fra dopo prima ogni "
        "molto perché quando può così ci"
    ),
    "pt": (
        "o a os as de do da dos das e é em no na nos nas um uma uns umas que para com não por "
        "pelo pela se seu sua seus suas ao aos à às mais como mas foi são está estão tem têm "
        "ser também sobre entre quando até desde este esta estes estas isso isto pode cada muito "
        "já ou nosso nossa você vocês será após durante"
    ),
    "nl": (
        "de het een en van in is dat die niet op te voor met zijn er aan om ook als bij of maar "
        "dan nog wordt worden werd naar uit door over tot hun deze dit wij we u uw jullie hebben "
        "heeft kan kunnen moet moeten zal zullen geen meer zo al wel nu onze ons hier waar "
        "wanneer omdat alle"
    ),
}


def _distinctive_words() -> Dict[str, frozenset]:
    sets = {code: set(words.split()) for code, words in _FUNCTION_WORDS.items()}
    counts: Dict[str, int] = {}
    for words in sets.values():
        for w in words:
            counts[w] = counts.get(w, 0) + 1
    return {code: frozenset(w for w in words if counts[w] == 1) for code, words in sets.items()}


_ALL_WORDS = {code: frozenset(words.split()) for code, words in _FUNCTION_WORDS.items()}
_DISTINCTIVE = _distinctive_words()
_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+")

# Spelling that only one of these languages uses. Counted as distinctive
# evidence alongside the distinctive function words ("revisión" is Spanish,
# "revisão" Portuguese, "revisione" Italian, even though the stems match).
_SPELLING_FEATURES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("es", re.compile(r"ñ|ción$|ciones$|¿|¡")),
    ("pt", re.compile(r"[ãõ]|ção$|ções$|ões$")),
    ("it", re.compile(r"zione$|zioni$")),
    ("de", re.compile(r"ß|ung$|ungen$|keit$|heit$|lich$|isch$")),
    ("nl", re.compile(r"ij|heid$|lijk$")),
    ("fr", re.compile(r"eaux?$|œ|[ûî]")),
)
# French elision ("qu'il", "n'est", "j'ai"); Italian articulated elision
# ("dell'anno", "all'ingresso", "nell'ambito").
_ELISION_FEATURES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("fr", re.compile(r"\b(?:qu|n|j)['’][a-zà-ÿ]", re.IGNORECASE)),
    ("it", re.compile(r"\b(?:dell|all|nell|dall|sull|quest|un)['’][a-zà-ÿ]", re.IGNORECASE)),
)
_SINGLE_LETTER_WORDS = frozenset({"é", "è", "à", "y", "e", "o", "a", "u", "i"})

# Evidence thresholds, measured on realistic notices, reports and letters in
# each language (pinned in smoke_semantic_rules_catalog). A real page clears
# them easily; a two-line snippet, a half-and-half page, or a language we have
# no word list for (Swedish, Tagalog, lorem ipsum) does not, and ABSTAINS —
# a wrong language tag is worse than none, because a screen reader picks its
# pronunciation from it.
_MIN_FUNCTION_WORDS = 5   # the winner's function words, shared or not
_MIN_DENSITY = 0.15       # ...per word of the sample (real prose runs 0.3-0.5)
_MIN_DISTINCT = 2         # evidence only the winner has
_MIN_MARGIN = 2.0         # the winner's distinct evidence vs the runner-up's


def detect_latin_language(sample: str) -> Tuple[Optional[str], float, str]:
    """``(code, confidence, detail)`` for en/fr/es/de/it/pt/nl; ``code`` is
    None when the evidence does not clearly point at one language."""
    text = unicodedata.normalize("NFC", sample or "")
    tokens = [t.lower() for t in _WORD_TOKEN_RE.findall(text)]
    tokens = [t for t in tokens if len(t) >= 2 or t in _SINGLE_LETTER_WORDS]
    if not tokens:
        return None, 0.0, "no words"
    total = {code: 0 for code in _ALL_WORDS}
    distinct = {code: 0 for code in _ALL_WORDS}
    for tok in tokens:
        for code, words in _ALL_WORDS.items():
            if tok in words:
                total[code] += 1
                if tok in _DISTINCTIVE[code]:
                    distinct[code] += 1
        for code, pattern in _SPELLING_FEATURES:
            if pattern.search(tok):
                distinct[code] += 1
    for code, pattern in _ELISION_FEATURES:
        distinct[code] += len(pattern.findall(text))
    ranked = sorted(distinct.items(), key=lambda kv: kv[1], reverse=True)
    (best, d1), (second, d2) = ranked[0], ranked[1]
    n = total[best]
    detail = f"{best}: {d1} distinct/{n} function words vs {second}: {d2}, over {len(tokens)} words"
    if d1 < _MIN_DISTINCT:
        return None, 0.0, f"too little evidence ({detail})"
    if d2 * _MIN_MARGIN > d1:
        return None, 0.0, f"no clear winner ({detail})"
    if n < _MIN_FUNCTION_WORDS or n / len(tokens) < _MIN_DENSITY:
        return None, 0.0, f"too few function words for a language we know ({detail})"
    margin = 1.0 - (d2 / d1)
    confidence = min(0.85, 0.5 + 0.03 * d1 + 0.1 * margin)
    return best, round(confidence, 3), detail


# ---------------------------------------------------------------------------
# Document title
# ---------------------------------------------------------------------------

_GENERIC_HEADINGS = {
    "introduction", "intro", "contents", "table of contents", "toc", "abstract", "overview",
    "summary", "executive summary", "background", "preface", "foreword", "welcome",
    "notes", "agenda", "appendix", "references", "bibliography", "index", "glossary",
    "acknowledgements", "acknowledgments", "purpose", "scope", "untitled", "title",
    "heading", "document", "draft", "memo", "memorandum", "page", "cover", "cover page",
}
_NUMBERED_SECTION_RE = re.compile(
    r"^(?!section\s+508\b)(?:chapter|section|part|appendix|article|unit|lesson|module|page|step|annex)\s+"
    r"(?:\d+|[ivxlc]+|[a-z])\.?$",
    re.IGNORECASE,
)
# "Chapter 1: Getting started", "Topic 1 - Accessibility programme", "Part II.
# Methods": the heading of ONE part, whatever follows the number.
_NUMBERED_PART_PREFIX_RE = re.compile(
    r"^(?!section\s+508\b)(?:chapter|section|part|appendix|article|unit|lesson|module|page|step|annex|topic|slide|week|day"
    r"|session|phase|stage)\s+(?:\d+(?:\.\d+)*|[ivxlc]+|[a-z])\s*[:.)\-–—]\s*\S",
    re.IGNORECASE,
)
_PRINTED_FROM_RE = re.compile(r"^microsoft\s+(?:word|powerpoint|excel)\s*[-–]\s*", re.IGNORECASE)
_FILENAME_NOISE = {
    "final", "draft", "copy", "rev", "version", "new", "old", "latest", "updated", "edit",
    "edited", "fixed", "tmp", "temp", "backup", "bak", "wa",
}
_COPY_OF_RE = re.compile(r"^(?:copy\s+of\s+)+", re.IGNORECASE)
_FORMAT_WORDS = {"html", "htm", "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "rtf", "odt"}
_SMALL_TITLE_WORDS = {
    "of", "the", "and", "or", "for", "to", "in", "on", "at", "by", "a", "an", "with",
    "de", "la", "el", "los", "las", "y", "et", "du", "des", "le", "les", "und", "der", "die", "das",
    "da", "do", "e", "di", "del", "van", "het", "en",
}
_FILENAME_JUNK_WORDS = {
    "img", "image", "dsc", "dscn", "dscf", "imgp", "pic", "photo", "scan", "scanned", "doc",
    "docx", "pdf", "pptx", "xlsx", "html", "file", "screenshot", "screen", "shot", "capture",
    "untitled", "document", "download", "attachment", "export", "print", "output", "test",
    "microsoft", "word", "powerpoint", "excel",
}
_WHATSAPP_RE = re.compile(r"^(?:doc|img|vid|aud|ptt)-\d{8}-wa\d+", re.IGNORECASE)
_DATE_STEM_RE = re.compile(r"^\d{2,4}[-_.]\d{1,2}[-_.]\d{1,4}$|^\d{6,8}$")


def title_from_heading(text: Optional[str]) -> Verdict:
    t = _collapse(text)
    if not t:
        return _refuse("the heading is empty")
    if has_control_chars(t):
        return _refuse("the heading text could not be read reliably")
    if not _real_words(t):
        return _refuse(f"the first heading ({t!r}) is a number or label, not a title")
    low = t.lower().strip(" .:!")
    if low in _GENERIC_HEADINGS or _NUMBERED_SECTION_RE.match(low) or _NUMBERED_PART_PREFIX_RE.match(t):
        return _refuse(f"the first heading ({t!r}) names a section, not the document")
    return Verdict(t[:200], "from the first heading")


def title_from_filename(filename: Optional[str]) -> Verdict:
    name = PurePosixPath(str(filename or "").replace("\\", "/")).name
    stem = name
    # Strip up to two extensions ("memo.docx.pdf").
    for _ in range(2):
        s, ext = os.path.splitext(stem)
        if ext and len(ext) <= 6 and re.fullmatch(r"\.[A-Za-z0-9]+", ext):
            stem = s
    stem = _PRINTED_FROM_RE.sub("", stem.strip())
    if not stem:
        return _refuse("there is no file name")
    if _WHATSAPP_RE.match(stem) or _DATE_STEM_RE.match(stem):
        return _refuse(f"the file name ({name!r}) is a camera, scanner or chat-app export name")
    # "Copy of Copy of budget" -> "budget" ("of" elsewhere is part of the name).
    stem = _COPY_OF_RE.sub("", stem)
    tokens = [tok for tok in re.split(r"[\s_\-.()\[\]]+", stem) if tok]
    kept = [
        tok for tok in tokens
        if tok.lower() not in _FILENAME_NOISE and not re.fullmatch(r"v\d+|\d{5,}", tok, re.IGNORECASE)
    ]
    words = [tok for tok in kept if re.fullmatch(r"[^\W\d_]{3,}", tok) and tok.lower() not in _FILENAME_JUNK_WORDS]
    if any(tok.lower() in _FILENAME_JUNK_WORDS for tok in kept) and len(words) < 2:
        return _refuse(f"the file name ({name!r}) is a camera, scanner or export name")
    if len(words) < 2:
        return _refuse(f"the file name ({name!r}) doesn't contain enough real words to be a title")
    if len(words) * 2 < len(kept):
        return _refuse(f"the file name ({name!r}) is mostly numbers and codes")
    # A format typed into the name ("html_deep_nesting", "report pdf") is not
    # part of the title.
    kept = [tok for tok in kept if tok.lower() not in _FORMAT_WORDS]
    title = " ".join(
        tok if (i and tok.lower() in _SMALL_TITLE_WORDS) else tok[:1].upper() + tok[1:]
        for i, tok in enumerate(kept)
    )
    return Verdict(title[:200], "from the file name")


# ---------------------------------------------------------------------------
# Table caption
# ---------------------------------------------------------------------------

_PLACEHOLDER_CAPTIONS = {"table", "data table", "data", "table 1", "untitled table", "caption"}


def vet_table_caption(
    text: Optional[str], headers: Iterable[str], table_text: Optional[str] = None
) -> Optional[str]:
    """Final gate for ANY provider's table caption. Returns a refusal reason or None.

    ``table_text`` is every word of the table; when given, a caption that
    states a number the table does not contain ("Revenue grew 12% in 2025"
    over a table with neither) is refused as an invented fact.
    """
    t = _collapse(text)
    if table_text is not None:
        have = set(re.findall(r"\d+(?:[.,]\d+)*", table_text))
        invented = [n for n in re.findall(r"\d+(?:[.,]\d+)*", t) if n not in have]
        if invented:
            return f"the caption stated a number the table does not contain ({invented[0]})"
    if not t:
        return "no caption was produced"
    low = t.lower().strip(" .:")
    if low in _PLACEHOLDER_CAPTIONS or re.fullmatch(r"table\s*\d*", low):
        return "the caption was a placeholder"
    if has_control_chars(t):
        return "the caption contained unreadable characters"
    body = re.sub(r"^table\s*\d*\s*[:.\-–—]\s*", "", t, flags=re.IGNORECASE)
    header_list = [_collapse(h).lower() for h in headers if _collapse(h)]
    if header_list:
        parts = [p.strip().lower() for p in re.split(r"\s*[,;|/]\s*|\s+and\s+", body) if p.strip()]
        if parts and all(p in header_list for p in parts):
            return "the caption only repeated the column names"
    if len(_real_words(body)) < 2:
        return "the caption did not say what the table is about"
    return None


__all__ = [
    "AUTHORED_CAPTION_SOURCES",
    "UNAUTHORED_CAPTION_SOURCES",
    "Verdict",
    "alt_from_caption",
    "detect_latin_language",
    "has_control_chars",
    "is_byline_or_date",
    "is_nav_like",
    "link_text_from_target",
    "split_figure_label",
    "title_from_filename",
    "title_from_heading",
    "vet_alt_text",
    "vet_link_text",
    "vet_table_caption",
]
