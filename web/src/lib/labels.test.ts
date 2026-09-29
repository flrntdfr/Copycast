import { describe, expect, it } from "vitest";

import {
  archiveStateLabel,
  catalogStateLabel,
  count,
  jobKindLabel,
  jobTriggerLabel,
  liveStatusLabel,
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

  it("names every live status yt-dlp reports", () => {
    expect(liveStatusLabel("is_live")).toBe("Live");
    expect(liveStatusLabel("is_upcoming")).toBe("Upcoming");
    expect(liveStatusLabel("post_live")).toBe("Recording being processed");
    expect(liveStatusLabel("was_live")).toBe("Recorded stream");
    expect(liveStatusLabel("not_live")).toBe("Not a stream");
    expect(liveStatusLabel(null)).toBe("");
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
