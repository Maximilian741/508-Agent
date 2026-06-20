/**
 * Readability metrics — Flesch Reading Ease + Flesch–Kincaid Grade Level.
 *
 * Plain-language is an accessibility concern (WCAG 2.1 §3.1.5 Reading Level).
 * These are the standard, well-defined formulas; syllable counting uses the
 * conventional English vowel-group heuristic (good enough for guidance, not
 * a linguistics engine).
 *
 *   Flesch Reading Ease   = 206.835 − 1.015·(words/sentences) − 84.6·(syll/words)
 *   Flesch–Kincaid Grade  = 0.39·(words/sentences) + 11.8·(syll/words) − 15.59
 */

export interface ReadabilityResult {
  words: number;
  sentences: number;
  syllables: number;
  complexWords: number; // words with 3+ syllables
  avgWordsPerSentence: number;
  avgSyllablesPerWord: number;
  fleschReadingEase: number; // 0–100 (higher = easier); clamped
  fleschKincaidGrade: number; // US grade level; clamped at 0
  /** A short human label for the reading-ease band. */
  band: string;
  /** Sentences longer than 25 words (1-based index + length), worst first. */
  longSentences: { index: number; words: number }[];
}

const _VOWELS = "aeiouy";

/** Heuristic English syllable count for a single word. Minimum 1. */
export function countSyllables(word: string): number {
  const w = word.toLowerCase().replace(/[^a-z]/g, "");
  if (!w) return 0;
  if (w.length <= 3) return 1;

  let count = 0;
  let prevVowel = false;
  for (const ch of w) {
    const isVowel = _VOWELS.includes(ch);
    if (isVowel && !prevVowel) count += 1;
    prevVowel = isVowel;
  }
  // Silent trailing 'e' (but keep the 'le' syllable, e.g. "table").
  if (w.endsWith("e") && !w.endsWith("le")) count -= 1;
  // Common endings that add a syllable the vowel-group rule misses.
  if (/(?:[^aeiou]le)$/.test(w)) count += 1;

  return Math.max(1, count);
}

function _splitSentences(text: string): string[] {
  return text
    .split(/[.!?]+/)
    .map((s) => s.trim())
    .filter((s) => /[a-z0-9]/i.test(s));
}

function _splitWords(text: string): string[] {
  return text
    .split(/\s+/)
    .map((w) => w.trim())
    .filter((w) => /[a-z0-9]/i.test(w));
}

function _round(n: number, places = 1): number {
  const f = 10 ** places;
  return Math.round(n * f) / f;
}

export function analyzeReadability(text: string): ReadabilityResult | null {
  const wordTokens = _splitWords(text);
  const sentenceTexts = _splitSentences(text);
  const words = wordTokens.length;
  const sentences = Math.max(1, sentenceTexts.length);
  if (words === 0) return null;

  let syllables = 0;
  let complexWords = 0;
  for (const w of wordTokens) {
    const s = countSyllables(w);
    syllables += s;
    if (s >= 3) complexWords += 1;
  }

  const avgWordsPerSentence = words / sentences;
  const avgSyllablesPerWord = syllables / words;

  const easeRaw = 206.835 - 1.015 * avgWordsPerSentence - 84.6 * avgSyllablesPerWord;
  const gradeRaw = 0.39 * avgWordsPerSentence + 11.8 * avgSyllablesPerWord - 15.59;
  const ease = Math.max(0, Math.min(100, easeRaw));
  const grade = Math.max(0, gradeRaw);

  const longSentences = sentenceTexts
    .map((s, i) => ({ index: i + 1, words: _splitWords(s).length }))
    .filter((s) => s.words > 25)
    .sort((a, b) => b.words - a.words)
    .slice(0, 5);

  return {
    words,
    sentences,
    syllables,
    complexWords,
    avgWordsPerSentence: _round(avgWordsPerSentence),
    avgSyllablesPerWord: _round(avgSyllablesPerWord, 2),
    fleschReadingEase: _round(ease),
    fleschKincaidGrade: _round(grade),
    band: easeBand(ease),
    longSentences,
  };
}

export function easeBand(score: number): string {
  if (score >= 90) return "Very easy — 5th grade";
  if (score >= 80) return "Easy — 6th grade";
  if (score >= 70) return "Fairly easy — 7th grade";
  if (score >= 60) return "Plain English — 8th–9th grade";
  if (score >= 50) return "Fairly difficult — 10th–12th grade";
  if (score >= 30) return "Difficult — college";
  return "Very difficult — college graduate";
}
