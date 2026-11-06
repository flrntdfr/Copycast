import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { RowActions } from "./RowActions";
import { RowDetails } from "./RowDetails";
import { assetUrlOf, chaptersUrlOf, downloadUrlOf, playUrlOf } from "./media-urls";
import type { AssetRead } from "@/api/types";
import { getPlayerState, resetPlayerForTests } from "@/stores/player";
import { item } from "@/test/factories";
import { renderWithProviders } from "@/test/render";

const chapters: AssetRead = {
  id: "asset-ch",
  feed_id: "mirror-1",
  item_id: "item-1",
  kind: "chapters",
  provenance: "mirrored",
  language: null,
  format: "json",
  url: "http://localhost:8080/feeds/mirror-1/k/secretkey/assets/item-1.chapters.json",
  name: "item-1.chapters.json",
  play_url: "http://localhost:8080/api/feeds/mirror-1/items/item-1/assets/item-1.chapters.json",
  remote_url: null,
  mime: "application/json",
  size_bytes: 120,
  state: "archived",
  last_error: null,
};

const episode = item({
  id: "item-1",
  feed_id: "mirror-1",
  title: "Episode one",
  media: {
    url: "http://localhost:8080/feeds/mirror-1/k/secretkey/media/item-1.m4a",
    bytes: 4_407,
    mime: "audio/mp4",
    ext: "m4a",
    play_url: "http://localhost:8080/api/feeds/mirror-1/items/item-1/media",
  },
  assets: [chapters],
});

afterEach(() => {
  resetPlayerForTests();
});

describe("the web player and downloads use the session's play routes", () => {
  it("plays the play URL, with the chapters asset's play URL", async () => {
    renderWithProviders(<RowActions item={episode} feedTitle="Example Podcast" />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));
    expect(getPlayerState().track).toMatchObject({
      url: "http://localhost:8080/api/feeds/mirror-1/items/item-1/media",
      chaptersUrl:
        "http://localhost:8080/api/feeds/mirror-1/items/item-1/assets/item-1.chapters.json",
    });
    // No feed secret ever reaches the browser's player.
    expect(JSON.stringify(getPlayerState().track)).not.toContain("secretkey");
  });

  it("downloads through the play URL with ?download=1", () => {
    renderWithProviders(<RowActions item={episode} feedTitle="Example Podcast" />);
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "http://localhost:8080/api/feeds/mirror-1/items/item-1/media?download=1",
    );
  });

  it("links assets to their play URLs in the row details", () => {
    renderWithProviders(<RowDetails item={episode} />);
    const assets = screen.getByText("Assets").nextElementSibling as HTMLElement;
    expect(within(assets).getByRole("link")).toHaveAttribute("href", chapters.play_url);
  });
});

describe("media URL helpers", () => {
  it("fall back to the feed URLs when the api gives no play URL", () => {
    const old = item({
      media: { url: "http://x/feeds/f/media/i.m4a", bytes: 1, mime: "audio/mp4", ext: "m4a" },
      assets: [{ ...chapters, play_url: null }],
    });
    expect(playUrlOf(old)).toBe("http://x/feeds/f/media/i.m4a");
    expect(downloadUrlOf(old)).toBe("http://x/feeds/f/media/i.m4a");
    expect(chaptersUrlOf(old)).toBe(chapters.url);
    expect(assetUrlOf({ url: null, play_url: null })).toBeNull();
    expect(playUrlOf(item({ state: "available" }))).toBeNull();
  });
});
