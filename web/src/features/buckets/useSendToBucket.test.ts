import { describe, expect, it } from "vitest";

import { pickDefaultBucket } from "./useSendToBucket";
import { bucket } from "@/test/factories";

describe("pickDefaultBucket", () => {
  const theirs = bucket({ id: "b-emilia", owner: "emilia", is_default: true });
  const mine = bucket({ id: "b-florent", owner: "florent", is_default: true });
  const extra = bucket({ id: "b-extra", owner: "florent", name: "Audiobooks" });

  it("picks the User's own default when an Admin lists several", () => {
    expect(pickDefaultBucket([theirs, extra, mine], "florent")?.id).toBe("b-florent");
    expect(pickDefaultBucket([theirs, extra, mine], "emilia")?.id).toBe("b-emilia");
  });

  it("falls back to the User's own Copycast, then their first", () => {
    const named = bucket({ id: "b-named", owner: "florent", name: "Copycast" });
    expect(pickDefaultBucket([theirs, extra, named], "florent")?.id).toBe("b-named");
    expect(pickDefaultBucket([theirs, extra], "florent")?.id).toBe("b-extra");
  });

  it("uses every Local feed when the User owns none, or is not known", () => {
    expect(pickDefaultBucket([extra, theirs], "nobody")?.id).toBe("b-emilia");
    expect(pickDefaultBucket([extra, theirs])?.id).toBe("b-emilia");
    expect(pickDefaultBucket([])).toBeNull();
  });
});
