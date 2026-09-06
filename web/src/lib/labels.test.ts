import { describe, expect, it } from "vitest";

import {
  archiveStateLabel,
  catalogStateLabel,
  count,
  jobKindLabel,
  jobTriggerLabel,
  sourceKindLabel,
} from "./labels";

describe("labels", () => {
  it("maps known enum values to wording", () => {
    expect(archiveStateLabel("wanted")).toBe("Queued");
    expect(catalogStateLabel("delisted")).toBe("Delisted");
    expect(jobKindLabel("archive_item")).toBe("Archive");
    expect(jobTriggerLabel("feed_fetch")).toBe("Feed fetch");
    expect(sourceKindLabel("ytdlp")).toBe("Engine");
  });

  it("falls back to the raw value for anything newer than the UI", () => {
    expect(jobKindLabel("export")).toBe("export");
    expect(archiveStateLabel(null)).toBe("");
  });

  it("pluralises domain nouns", () => {
    expect(count(1, "Episode")).toBe("1 Episode");
    expect(count(43, "Episode")).toBe("43 Episodes");
    expect(count(0, "entry", "entries")).toBe("0 entries");
  });
});
