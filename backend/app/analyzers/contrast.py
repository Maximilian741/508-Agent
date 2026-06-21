"""WCAG 2.1 colour-contrast math (SC 1.4.3 / 1.4.11).

Pure functions — no document model — so they are trivially unit-testable and
reusable. Implements the exact WCAG relative-luminance + contrast-ratio formula.
"""

from __future__ import annotations

from typing import Optional, Tuple

RGB = Tuple[int, int, int]

# WCAG AA thresholds.
AA_NORMAL = 4.5
AA_LARGE = 3.0


def parse_hex(value: object) -> Optional[RGB]:
    """Parse ``#RRGGBB`` / ``RRGGBB`` / ``RGB`` into an (r, g, b) tuple, or None."""
    if value is None:
        return None
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _channel_luminance(c: float) -> float:
    # c is 0..1
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: RGB) -> float:
    """WCAG relative luminance of an sRGB colour."""
    r, g, b = (channel / 255.0 for channel in rgb)
    return 0.2126 * _channel_luminance(r) + 0.7152 * _channel_luminance(g) + 0.0722 * _channel_luminance(b)


def contrast_ratio(fg: object, bg: object) -> Optional[float]:
    """Contrast ratio (1..21) between two colours, or None if unparseable."""
    f = parse_hex(fg)
    b = parse_hex(bg)
    if f is None or b is None:
        return None
    l1 = relative_luminance(f)
    l2 = relative_luminance(b)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def is_large_text(size_pt: Optional[float], bold: bool) -> bool:
    """WCAG "large text": >= 18pt, or >= 14pt bold."""
    if size_pt is None:
        return False
    try:
        pt = float(size_pt)
    except (TypeError, ValueError):
        return False
    return pt >= 18.0 or (bold and pt >= 14.0)


def required_ratio(size_pt: Optional[float], bold: bool = False) -> float:
    """The AA minimum ratio for text of this size/weight (3.0 large, else 4.5)."""
    return AA_LARGE if is_large_text(size_pt, bold) else AA_NORMAL


def _to_hex(rgb: RGB) -> str:
    return "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb)


def _toward(src: RGB, target: RGB, t: float) -> RGB:
    return (
        src[0] + (target[0] - src[0]) * t,
        src[1] + (target[1] - src[1]) * t,
        src[2] + (target[2] - src[2]) * t,
    )


def suggest_passing_fg(fg: object, bg: object, required: float = AA_NORMAL) -> Optional[str]:
    """The nearest foreground colour to ``fg`` that meets ``required`` contrast
    against ``bg``, as ``RRGGBB`` (no ``#``), or None.

    We never change the background (recolouring a page is a bigger design
    decision than nudging text), and we change the text colour as little as
    possible: scan from ``fg`` toward black and toward white, take the first
    passing colour found along each direction (the nearest in that direction),
    and return whichever of the two is the smaller change. Returns None if
    ``fg``/``bg`` are unparseable or ``fg`` already passes (nothing to suggest)."""
    f = parse_hex(fg)
    b = parse_hex(bg)
    if f is None or b is None:
        return None
    current = contrast_ratio(fg, bg)
    if current is not None and current >= required:
        return None

    best: Optional[Tuple[float, str]] = None  # (squared-distance, hex)
    steps = 64
    for target in ((0, 0, 0), (255, 255, 255)):
        for i in range(1, steps + 1):
            cand = _toward(f, target, i / steps)
            cand_hex = _to_hex(cand)
            r = contrast_ratio(cand_hex, bg)
            if r is not None and r >= required:
                dist = sum((cand[k] - f[k]) ** 2 for k in range(3))
                if best is None or dist < best[0]:
                    best = (dist, cand_hex)
                break  # first passing along this direction is the nearest in it
    return best[1] if best else None


__all__ = [
    "AA_NORMAL",
    "AA_LARGE",
    "parse_hex",
    "relative_luminance",
    "contrast_ratio",
    "is_large_text",
    "required_ratio",
    "suggest_passing_fg",
]
