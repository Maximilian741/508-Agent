"""Development launcher that selects a free port safely."""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _find_free_port(start: int = 8000, end: int = 8010) -> int:
    for port in range(start, end + 1):
        if _is_port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write_backend_url(port: int) -> Path:
    runtime_dir = Path(__file__).parent / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    url_path = runtime_dir / "backend_url.txt"
    url = f"http://127.0.0.1:{port}"
    url_path.write_text(url, encoding="utf-8")
    frontend_public = Path(__file__).parent.parent / "frontend" / "frontend" / "public"
    frontend_public.mkdir(parents=True, exist_ok=True)
    (frontend_public / "backend_url.txt").write_text(url, encoding="utf-8")
    return url_path


def main() -> int:
    port = _find_free_port()
    url_path = _write_backend_url(port)
    print(f"Backend running at http://127.0.0.1:{port}")
    print(f"Wrote backend URL to {url_path}")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    try:
        return subprocess.call(cmd, cwd=Path(__file__).parent)
    except KeyboardInterrupt:
        print("Backend stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
