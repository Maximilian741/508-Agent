"""Legacy / OpenDocument -> OOXML with LibreOffice (``soffice --headless``).

Optional by design: LibreOffice adds ~400-600 MB to the image, so the
Dockerfile installs it only with ``--build-arg INSTALL_LIBREOFFICE=true``.
Without it, a .doc/.xls/.ppt/.rtf/.odt/.ods/.odp upload is refused with a
sentence telling the person how to save the modern format themselves — the
upload is never half-handled.

Each conversion runs in a sandbox of its own:

* a fresh temporary directory holding a COPY of the upload under a neutral
  name (a customer's filename never reaches a command line), a throwaway
  LibreOffice user profile (so concurrent conversions don't fight over a
  shared profile lock, and nothing persists between customers), and the
  output directory;
* macros disabled and external links set never to update, in that profile;
* an environment built from an allowlist — the app's secrets (database URL,
  signing keys, payment keys) are not inherited;
* a hard timeout, after which the whole process tree is killed;
* a small concurrency limit, since each soffice process costs ~200 MB.

The network boundary is the container's, not LibreOffice's: run the backend
without outbound access to internal services if that matters for you (see
deploy/GO-LIVE.md).

Configuration (environment):
  SOFFICE_PATH                    explicit path to soffice (else PATH lookup)
  OFFICE_CONVERT_TIMEOUT_SECONDS  default 90
  OFFICE_CONVERT_CONCURRENCY      default 2
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# LibreOffice export filters for each target.
_FILTERS = {
    "docx": "docx:MS Word 2007 XML",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
}
_SAVE_AS = {
    "docx": ("Word (or Google Docs, or LibreOffice Writer)", "Word Document (.docx)"),
    "xlsx": ("Excel (or Google Sheets, or LibreOffice Calc)", "Excel Workbook (.xlsx)"),
    "pptx": ("PowerPoint (or Google Slides, or LibreOffice Impress)", "PowerPoint Presentation (.pptx)"),
}

# Profile settings for every conversion: no macros, no link refresh.
_REGISTRY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>1</value></prop></item>
</oor:items>
"""

# Environment variables a converter may see. Everything else — including the
# app's secrets — is withheld.
_ENV_ALLOW = (
    "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
    "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMFILES", "PROGRAMDATA",
)

_UNSET = object()
_command_override: object = _UNSET


def set_soffice_command_for_testing(command: Optional[List[str]]) -> None:
    """Tests only. ``[...]`` = run this argv prefix instead of soffice;
    ``[]`` = behave as if LibreOffice is not installed; ``None`` = clear."""
    global _command_override
    _command_override = _UNSET if command is None else list(command)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


_SEM = threading.BoundedSemaphore(_env_int("OFFICE_CONVERT_CONCURRENCY", 2, 1, 16))


def soffice_command() -> Optional[List[str]]:
    """The argv prefix that runs LibreOffice here, or None when it is absent."""
    if _command_override is not _UNSET:
        return list(_command_override) or None  # type: ignore[arg-type]
    explicit = (os.environ.get("SOFFICE_PATH") or "").strip()
    if explicit:
        return [explicit] if Path(explicit).is_file() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return [found]
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if base:
                candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
                if candidate.is_file():
                    return [str(candidate)]
    return None


def conversion_available() -> bool:
    return soffice_command() is not None


def _save_as_message(suffix: str, target: str, lead: str) -> str:
    app, choice = _SAVE_AS[target]
    return f"{lead} Open it in {app}, choose File > Save As, pick {choice}, and upload that file."


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def convert_office_file(src: Path, suffix: str, target: str, dest: Path) -> Path:
    """Convert ``src`` (a ``suffix`` file) to ``target`` at ``dest``."""
    s = suffix.lower()
    cmd = soffice_command()
    if cmd is None:
        raise HTTPException(
            status_code=415,
            detail=_save_as_message(s, target, f"We can't open {s} files on this server yet."),
        )
    timeout = _env_int("OFFICE_CONVERT_TIMEOUT_SECONDS", 90, 5, 600)
    if not _SEM.acquire(timeout=30):
        raise HTTPException(
            status_code=503,
            detail=_save_as_message(s, target, "Our file converter is busy right now. Try again in a minute, or save it yourself:"),
        )
    try:
        work = Path(tempfile.mkdtemp(prefix="508_convert_"))
        try:
            return _run_conversion(cmd, work, src, s, target, dest, timeout)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        _SEM.release()


def _run_conversion(cmd: List[str], work: Path, src: Path, s: str, target: str, dest: Path, timeout: int) -> Path:
    inp = work / f"input{s}"
    shutil.copyfile(src, inp)
    profile = work / "profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(_REGISTRY, encoding="utf-8")
    outdir = work / "out"
    outdir.mkdir()
    argv = list(cmd) + [
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--headless",
        "--invisible",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--norestore",
        "--convert-to",
        _FILTERS[target],
        "--outdir",
        str(outdir),
        str(inp),
    ]
    env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOW}
    env.update({"HOME": str(work), "TMPDIR": str(work), "TMP": str(work), "TEMP": str(work)})
    env.setdefault("LANG", "C.UTF-8")
    popen_kwargs = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        argv,
        cwd=str(work),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    try:
        _out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        logger.warning("office conversion timed out after %ss (%s -> %s)", timeout, s, target)
        raise HTTPException(
            status_code=422,
            detail=_save_as_message(s, target, f"Converting this {s} file took too long, so we stopped."),
        )
    produced = outdir / f"input.{target}"
    if proc.returncode != 0 or not produced.is_file() or produced.stat().st_size == 0:
        logger.warning(
            "office conversion failed rc=%s (%s -> %s): %s",
            proc.returncode, s, target, (err or b"")[-400:].decode("utf-8", "replace"),
        )
        raise HTTPException(
            status_code=422,
            detail=_save_as_message(
                s, target,
                f"We couldn't convert this {s} file; it may be damaged or password-protected.",
            ),
        )
    from app.security.uploads import validate_ooxml_package

    try:
        validate_ooxml_package(produced, expected_suffix="." + target)
    except HTTPException:
        raise HTTPException(
            status_code=422,
            detail=_save_as_message(s, target, f"Converting this {s} file did not produce a usable .{target}."),
        )
    shutil.move(str(produced), str(dest))
    return dest


__all__ = [
    "convert_office_file",
    "conversion_available",
    "set_soffice_command_for_testing",
    "soffice_command",
]
