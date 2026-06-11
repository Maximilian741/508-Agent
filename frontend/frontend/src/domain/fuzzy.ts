/**
 * Tiny fuzzy subsequence matcher for client-side list filtering.
 *
 * `fuzzyScore(query, text)` returns null when `query`'s characters do not
 * appear in `text` in order (case-insensitive), otherwise a score where
 * higher = better (tighter, earlier, word-boundary matches rank up). Good
 * enough for filtering a few hundred filenames; no dependency needed.
 */

export function fuzzyScore(query: string, text: string): number | null {
  const q = (query || "").trim().toLowerCase();
  if (!q) return 0; // empty query matches everything (neutral score)
  const t = (text || "").toLowerCase();
  if (!t) return null;

  let qi = 0;
  let score = 0;
  let lastIdx = -1;
  let streak = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      // Bonuses: consecutive run, start-of-string, after a separator.
      if (lastIdx === ti - 1) {
        streak += 1;
        score += 5 + streak * 2;
      } else {
        streak = 0;
        score += 1;
      }
      if (ti === 0) score += 8;
      else if (/[\s._\-/()]/.test(t[ti - 1])) score += 6;
      lastIdx = ti;
      qi += 1;
    }
  }
  if (qi < q.length) return null; // not all query chars consumed -> no match
  // Prefer shorter haystacks (a match in "ada.pdf" beats one in "metadata.pdf").
  score += Math.max(0, 20 - t.length / 4);
  return score;
}

/** Filter + sort `items` by how well `query` fuzzy-matches `keyOf(item)`. */
export function fuzzyFilter<T>(
  items: T[],
  query: string,
  keyOf: (item: T) => string,
): T[] {
  const q = (query || "").trim();
  if (!q) return items;
  const scored: Array<{ item: T; score: number; idx: number }> = [];
  items.forEach((item, idx) => {
    const s = fuzzyScore(q, keyOf(item));
    if (s !== null) scored.push({ item, score: s, idx });
  });
  // Stable sort: score desc, then original order.
  scored.sort((a, b) => (b.score - a.score) || (a.idx - b.idx));
  return scored.map((s) => s.item);
}
