"""Error responses a person can read AND a program can branch on.

Every error from the routers that use :class:`CodedErrorRoute` (``/pipeline``
and ``/auth``) is returned as::

    {"detail": <unchanged>, "code": "empty_upload", "message": "That file is empty. ..."}

* ``detail`` is exactly what it was before (a machine string such as
  ``"empty_upload"``, or an already-human sentence for 413/422), so no existing
  caller or test that reads it changes behaviour.
* ``code`` is a STABLE snake_case identifier. Branch on this, never on the
  wording of ``message``.
* ``message`` is one or two plain-English sentences safe to show a customer
  verbatim: no config keys, no exception text, no internal jargon.

The rate limiter builds the same shape for its 429 (see
``app.security.rate_limit``), plus ``retryAfter`` in seconds.

Why a route class and not an app-level exception handler: the handlers live
on the FastAPI app (``app/main.py``), which other work owns; a route class is
local to the routers that opt in, catches errors raised by dependencies too
(the 401 from ``require_user_id``), and leaves every other router's error
bodies byte-identical.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from fastapi import HTTPException


class ApiError(HTTPException):
    """An HTTPException that names its own stable ``code`` and human ``message``.

    ``detail`` defaults to the message, which is what the current frontend
    shows verbatim; pass ``detail=`` to keep a legacy machine string there.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        detail: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail=message if detail is None else detail,
            headers=headers,
        )
        self.code = code
        self.message = message


# Stable code -> the sentence a customer reads. Keep these short, concrete and
# free of jargon; say what to do next and, where money is involved, whether
# they were charged.
MESSAGES: Dict[str, str] = {
    # --- uploads ------------------------------------------------------------
    "empty_upload": "That file is empty. Choose it again, or re-save it and upload the new copy.",
    "file_content_mismatch": (
        "That file's contents don't match its name (for example, a PDF renamed to .docx). "
        "Save it in its real format and upload it again."
    ),
    "invalid_ooxml": (
        "That Word or PowerPoint file is damaged or isn't really an Office file. "
        "Open it in Word or PowerPoint, save a new copy and upload that."
    ),
    "too_large": "That file is too big for us to check. Try compressing it, or split it into smaller parts.",
    "unsupported_type": "We can't open that type of file. Save it as a PDF or Word document and try again.",
    "upload_failed": "Something went wrong while receiving your file. Please try again.",
    # --- documents ----------------------------------------------------------
    "password_protected": (
        "This PDF is password-protected, so we can't read it. Remove the password "
        "(in Acrobat or Preview: save a copy without security) and upload it again. "
        "You were not charged."
    ),
    "invalid_pdf": (
        "We couldn't open this PDF. It may be damaged or only partly downloaded. "
        "Try re-saving or re-exporting it and upload it again. You were not charged."
    ),
    "invalid_document": (
        "We couldn't open this file. It may be damaged, or not really the type its name says. "
        "You were not charged."
    ),
    "unreadable_document": (
        "We couldn't fix this file. It may be encrypted or damaged. You were not charged."
    ),
    "write_failed": "We couldn't write the fixed file. You were not charged. Please try again.",
    "save_failed": "We couldn't save the fixed file, so there is nothing to give you. You were not charged. Please try again.",
    "content_loss_refused": (
        "We stopped before writing this file: the fixed version came out with less text than "
        "the original, and we won't hand you a file that loses content. You were not charged."
    ),
    "invalid_request": "Some of the information sent with that request was missing or in the wrong format.",
    "approved_violations_invalid": "The list of fixes to apply was not in the expected format.",
    # --- money --------------------------------------------------------------
    "insufficient_credits": "You don't have enough credits for this file. You were not charged.",
    # --- identity -----------------------------------------------------------
    "authentication_required": "Please sign in to do that.",
    "unknown_account": "We couldn't find your account. Please sign in again.",
    "missing_account": "Please sign in to do that.",
    "invalid_credentials": "That password doesn't match this email address.",
    "password_required": "Choose a password with at least 8 characters.",
    "too_many_attempts": "Too many tries. Wait a minute, then try again.",
    "verify_email_first": "Confirm your email address first. We sent you a link; open it, then try again.",
    "current_password_required": "Enter your current password to make this change.",
    "invalid_current_password": "Your current password isn't right.",
    "email_in_use": "That email address is already in use.",
    "token_expired": "That link has expired or was already used. Request a new one.",
    "team_has_members": "Remove the other members of your team (or delete the team) before deleting your account.",
    "admin_only": "Only an administrator can do that.",
    # --- files / jobs -------------------------------------------------------
    "file_not_found": "That file is no longer available.",
    "no_files_available": "None of those files are available any more.",
    "invalid_job_or_filename": "That download link isn't valid.",
    "url_expired": "That download link has expired. Open your recent files to get a fresh one.",
    "url_revoked": "That download link stopped working when you signed out or changed your password. Open your recent files to get a fresh one.",
    "missing_signature": "That download link isn't valid.",
    "bad_signature": "That download link isn't valid.",
    "bad_expiry": "That download link isn't valid.",
    "not_owner": "That file belongs to a different account.",
    "no_jobs": "Choose at least one file to download.",
    "too_many_jobs": "That's too many files for one download. Choose 200 or fewer.",
    # --- URL scans ----------------------------------------------------------
    "url_not_allowed": "We can only check public web pages. That address isn't one we can reach.",
    "url_unreachable": "We couldn't load that web page. Check the address and try again.",
    # --- limits -------------------------------------------------------------
    "rate_limited": "Too many requests. Please wait a moment and try again.",
    "internal_error": "Something went wrong on our side. Please try again.",
}

# A status code's fallback when a detail maps to nothing more specific.
_STATUS_CODES: Dict[int, str] = {
    400: "bad_request",
    401: "authentication_required",
    402: "insufficient_credits",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "too_large",
    415: "unsupported_type",
    422: "unprocessable",
    429: "rate_limited",
}
_STATUS_MESSAGES: Dict[int, str] = {
    400: "We couldn't use that request.",
    403: "You don't have access to that.",
    404: "We couldn't find that.",
    405: "That action isn't available here.",
    409: "That conflicts with something that already exists.",
    410: "That is no longer available.",
    422: "We couldn't process that.",
}

# Sentences the routes already raise, mapped to their stable code. Matched by
# PREFIX so a trailing clause can change without changing the code.
_SENTENCE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("file_content_mismatch", "file_content_mismatch"),
    ("invalid_ooxml", "invalid_ooxml"),
    ("upload_rejected", "too_large"),
    ("upload_io_failure", "upload_failed"),
    ("Unsupported file type", "unsupported_type"),
    ("Insufficient credits", "insufficient_credits"),
    ("Failed to parse document", "invalid_document"),
    ("Failed to write the remediated file", "write_failed"),
    ("Could not remediate this file", "unreadable_document"),
    ("We stopped before writing this file", "content_loss_refused"),
    ("We could not save the remediated file", "save_failed"),
    ("approved_violations must be", "approved_violations_invalid"),
    ("rejected_violations must be", "approved_violations_invalid"),
)

_MACHINE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _looks_human(text: str) -> bool:
    """A sentence (spaces, capital or punctuation), not a machine token."""
    return " " in text and not text.startswith(("{", "[")) and len(text) <= 600


def describe(exc: Any, path: str = "") -> Tuple[str, str]:
    """Return ``(code, message)`` for an HTTPException-like error.

    Never raises. Resolution order: an :class:`ApiError`'s own fields; a known
    machine ``detail``; a known sentence prefix; the URL-scan routes' SSRF /
    fetch errors; then the status code's generic code and sentence.
    """
    status = int(getattr(exc, "status_code", 500) or 500)
    detail = getattr(exc, "detail", None)
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if isinstance(code, str) and code and isinstance(message, str) and message:
        return code, message

    text = detail if isinstance(detail, str) else ""
    resolved: Optional[str] = None
    # Is ``detail`` itself fit to show a person? Not when it is a machine token
    # ("empty_upload") or starts with one ("invalid_ooxml: missing [...]").
    detail_is_human = bool(text) and _looks_human(text)
    if text and _MACHINE_RE.match(text):
        resolved = text
        detail_is_human = False
    if resolved is None and text:
        for prefix, mapped in _SENTENCE_PREFIXES:
            if text.startswith(prefix):
                resolved = mapped
                if "_" in prefix.split(" ", 1)[0]:
                    detail_is_human = False
                break
    if resolved is None and path.startswith(("/pipeline/analyze-url", "/pipeline/scan-site")):
        if status == 400:
            resolved = "url_not_allowed"
        elif status == 422:
            resolved = "url_unreachable"
    if resolved is None:
        resolved = _STATUS_CODES.get(status, "internal_error" if status >= 500 else "error")

    # The sentence. A curated line for a known code wins, EXCEPT where the
    # route's own sentence is more specific and already written for a person:
    # a 413 that names the limit, and the URL scan's fetch/SSRF reasons.
    if resolved == "unsupported_type":
        ext = text.split(":", 1)[-1].strip() if ":" in text else ""
        human = (
            f"We can't open {ext} files. Save it as a PDF or Word document and try again."
            if ext and ext != "(none)" and len(ext) <= 16
            else MESSAGES["unsupported_type"]
        )
    elif resolved in ("too_large", "url_not_allowed", "url_unreachable") and detail_is_human:
        human = text
    elif resolved in MESSAGES:
        human = MESSAGES[resolved]
    elif detail_is_human:
        human = text
    else:
        human = _STATUS_MESSAGES.get(status) or "Something went wrong. Please try again."
    return resolved, human


def coded_error_response(exc: Any, path: str = "") -> JSONResponse:
    """The JSON error body with ``code`` + ``message`` added; headers kept."""
    code, message = describe(exc, path)
    status = int(getattr(exc, "status_code", 500) or 500)
    body: Dict[str, Any] = {
        "detail": jsonable_encoder(getattr(exc, "detail", None)),
        "code": code,
        "message": message,
    }
    headers = dict(getattr(exc, "headers", None) or {})
    retry = headers.get("Retry-After") or headers.get("retry-after")
    if retry and str(retry).isdigit():
        body["retryAfter"] = int(retry)
    return JSONResponse(status_code=status, content=body, headers=headers or None)


class CodedErrorRoute(APIRoute):
    """APIRoute whose HTTP errors carry ``code`` + ``message`` (see module doc).

    Wraps the whole route handler, so errors raised while resolving
    dependencies (authentication) and FastAPI's own request validation are
    covered as well as errors raised in the endpoint body.
    """

    def get_route_handler(self):  # type: ignore[override]
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except StarletteHTTPException as exc:
                return coded_error_response(exc, request.url.path)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": jsonable_encoder(exc.errors()),
                        "code": "invalid_request",
                        "message": MESSAGES["invalid_request"],
                    },
                )

        return handler


__all__ = ["ApiError", "CodedErrorRoute", "MESSAGES", "coded_error_response", "describe"]
