import { describe, expect, it } from "vitest";

import { nextFor, safeNext } from "./next";

describe("safeNext", () => {
  it.each([
    ["/settings", "/settings"],
    ["/feed/m1?tab=catalog#top", "/feed/m1?tab=catalog#top"],
    ["https://evil.example/", "/"],
    ["//evil.example/", "/"],
    ["/\\evil.example", "/"],
    ["/login", "/"],
    ["/login?next=/x", "/"],
    ["", "/"],
    [undefined, "/"],
    [42, "/"],
  ])("keeps %s as %s", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });

  it("puts no next on the home page", () => {
    expect(nextFor("/")).toBeUndefined();
    expect(nextFor("/jobs")).toBe("/jobs");
  });
});
