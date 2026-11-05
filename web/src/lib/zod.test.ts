import { describe, expect, it } from "vitest";
import { z } from "zod";

import "./zod";

describe("zod configuration", () => {
  it("never compiles schemas with eval, which the CSP forbids", () => {
    expect(z.config().jitless).toBe(true);
    expect(z.object({ a: z.string() }).parse({ a: "x" })).toEqual({ a: "x" });
  });
});
