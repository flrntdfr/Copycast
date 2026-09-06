/**
 * Client-side parsing of selection expressions such as "1-42, 180" for
 * validation and highlighting only; the server resolves them to item ids.
 * Accepted separators: ",", ";" and whitespace; ranges use "-", an en dash or "..".
 */

export interface SelectionRange {
  start: number;
  end: number;
  /** The token as typed. */
  raw: string;
}

export interface ParsedSelection {
  ranges: SelectionRange[];
  /** Tokens that could not be parsed, in input order. */
  invalid: string[];
  /** Every number covered by the valid ranges, ascending, without duplicates. */
  numbers: number[];
  /** True when the expression has no tokens at all. */
  empty: boolean;
}

export const SELECTION_MAX_SPAN = 100_000;

const RANGE_RE = /^(\d+)\s*(?:-|–|\.\.)\s*(\d+)$/;
const SINGLE_RE = /^\d+$/;

export function parseSelection(expression: string): ParsedSelection {
  const tokens = expression
    .split(/[,;\n]+|\s{2,}/)
    .flatMap((chunk) => splitLooseTokens(chunk))
    .map((token) => token.trim())
    .filter((token) => token.length > 0);

  const ranges: SelectionRange[] = [];
  const invalid: string[] = [];
  for (const token of tokens) {
    if (SINGLE_RE.test(token)) {
      const value = Number.parseInt(token, 10);
      if (value < 1) {
        invalid.push(token);
        continue;
      }
      ranges.push({ start: value, end: value, raw: token });
      continue;
    }
    const match = RANGE_RE.exec(token);
    if (!match) {
      invalid.push(token);
      continue;
    }
    const start = Number.parseInt(match[1] ?? "", 10);
    const end = Number.parseInt(match[2] ?? "", 10);
    if (start < 1 || end < start || end - start > SELECTION_MAX_SPAN) {
      invalid.push(token);
      continue;
    }
    ranges.push({ start, end, raw: token });
  }

  const seen = new Set<number>();
  for (const range of ranges) {
    for (let n = range.start; n <= range.end; n += 1) seen.add(n);
  }
  const numbers = [...seen].sort((a, b) => a - b);
  return { ranges, invalid, numbers, empty: tokens.length === 0 };
}

/**
 * A chunk like "5 - 6 12" contains a spaced range and a single; split on single
 * spaces except when they surround a range operator.
 */
function splitLooseTokens(chunk: string): string[] {
  const joined = chunk.replace(/\s*(-|–|\.\.)\s*/g, "$1");
  return joined.split(/\s+/);
}

export function selectionIsValid(parsed: ParsedSelection): boolean {
  return !parsed.empty && parsed.invalid.length === 0;
}

/** Wording under a RangeInput: "43 numbers" or the first invalid token. */
export function describeSelection(parsed: ParsedSelection): string {
  if (parsed.empty) return "";
  if (parsed.invalid.length > 0) {
    const shown = parsed.invalid
      .slice(0, 3)
      .map((t) => `"${t}"`)
      .join(", ");
    return `Cannot read ${shown}${parsed.invalid.length > 3 ? "…" : ""}`;
  }
  return `${parsed.numbers.length.toLocaleString()} ${parsed.numbers.length === 1 ? "number" : "numbers"}`;
}

/** Turn a set of numbers back into a compact expression ("1-3, 7"). */
export function formatSelection(numbers: Iterable<number>): string {
  const sorted = [...new Set(numbers)]
    .filter((n) => Number.isInteger(n) && n > 0)
    .sort((a, b) => a - b);
  const parts: string[] = [];
  let i = 0;
  while (i < sorted.length) {
    const start = sorted[i] ?? 0;
    let end = start;
    while (i + 1 < sorted.length && sorted[i + 1] === end + 1) {
      i += 1;
      end = sorted[i] ?? end;
    }
    parts.push(start === end ? String(start) : `${start}-${end}`);
    i += 1;
  }
  return parts.join(", ");
}
