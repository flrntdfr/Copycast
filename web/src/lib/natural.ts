const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

/** Natural file-name order: "Chapter 2" before "Chapter 10", case ignored (ADR 0018). */
export function naturalCompare(a: string, b: string): number {
  return collator.compare(a, b) || (a < b ? -1 : a > b ? 1 : 0);
}
