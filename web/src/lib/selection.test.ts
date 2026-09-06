import { describe, expect, it } from "vitest";

import { describeSelection, formatSelection, parseSelection, selectionIsValid } from "./selection";

describe("parseSelection", () => {
  it("resolves the canonical example to 43 numbers", () => {
    const parsed = parseSelection("1-42, 180");
    expect(parsed.invalid).toEqual([]);
    expect(parsed.numbers).toHaveLength(43);
    expect(parsed.numbers[0]).toBe(1);
    expect(parsed.numbers.at(-1)).toBe(180);
    expect(parsed.ranges).toEqual([
      { start: 1, end: 42, raw: "1-42" },
      { start: 180, end: 180, raw: "180" },
    ]);
  });

  it("accepts the tolerant separators and range operators", () => {
    expect(parseSelection("5 - 6; 9..10 12").numbers).toEqual([5, 6, 9, 10, 12]);
    expect(parseSelection("3–4").numbers).toEqual([3, 4]);
  });

  it("deduplicates overlapping ranges", () => {
    expect(parseSelection("1-3, 2-5").numbers).toEqual([1, 2, 3, 4, 5]);
  });

  it("reports invalid tokens in input order", () => {
    const parsed = parseSelection("1-2, abc, 9-3, 0");
    expect(parsed.invalid).toEqual(["abc", "9-3", "0"]);
    expect(selectionIsValid(parsed)).toBe(false);
    expect(describeSelection(parsed)).toBe('Cannot read "abc", "9-3", "0"');
  });

  it("treats blank input as empty, not invalid", () => {
    const parsed = parseSelection("   ");
    expect(parsed.empty).toBe(true);
    expect(selectionIsValid(parsed)).toBe(false);
    expect(describeSelection(parsed)).toBe("");
  });

  it("describes a valid selection by its size", () => {
    expect(describeSelection(parseSelection("7"))).toBe("1 number");
    expect(describeSelection(parseSelection("1-42, 180"))).toBe("43 numbers");
  });
});

describe("formatSelection", () => {
  it("collapses runs back into ranges", () => {
    expect(formatSelection([3, 1, 2, 7, 9, 10])).toBe("1-3, 7, 9-10");
    expect(formatSelection([])).toBe("");
  });
});
