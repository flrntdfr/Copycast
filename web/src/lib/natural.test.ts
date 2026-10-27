import { describe, expect, it } from "vitest";

import { naturalCompare } from "./natural";

describe("naturalCompare", () => {
  it("sorts digit runs as numbers and ignores case", () => {
    const names = ["Chapter 10.mp3", "chapter 2.mp3", "Chapter 1.mp3", "Intro.mp3"];
    expect([...names].sort(naturalCompare)).toEqual([
      "Chapter 1.mp3",
      "chapter 2.mp3",
      "Chapter 10.mp3",
      "Intro.mp3",
    ]);
  });
});
