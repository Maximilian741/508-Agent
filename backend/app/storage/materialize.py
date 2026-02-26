from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from app.storage.base import parse_artifact_ref


def _safe_name(name: str) -> str:
    candidate = Path(name or "artifact.bin").name
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in candidate)
    return safe or "artifact.bin"


def materialize_to_path(
    *,
    storage,
    artifact_ref: object,
    base_dir: Path,
    scope: str,
    filename_hint: Optional[str] = None,
    chunk_size: int = 1024 * 1024,
) -> Path:
    parsed = parse_artifact_ref(artifact_ref)
    if parsed.type == "local_path":
        return Path(parsed.value)
    scope_dir = base_dir / _safe_name(scope)
    scope_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(filename_hint or Path(parsed.value).name or "artifact.bin")
    dest = scope_dir / name
    with storage.open_stream(parsed.value) as stream, dest.open("wb") as handle:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            handle.write(chunk)
    return dest


def cleanup_materialized_scope(base_dir: Path, scope: str) -> None:
    target = base_dir / _safe_name(scope)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

