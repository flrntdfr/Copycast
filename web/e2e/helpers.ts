/**
 * Shared helpers for the Playwright specs. They run against the compose stack from
 * docker-compose.ci.yml, where the fixtures server is reachable from the containers as
 * http://fixtures:8000 (the URL the Mirror's Source must use). Against a local
 * `copycast api` set COPYCAST_E2E_FEED_URL to a feed the api can reach and
 * COPYCAST_E2E_WORKER=0 when no worker archives (waits for archived media are skipped).
 */
import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const FIXTURE_FEED_URL =
  process.env.COPYCAST_E2E_FEED_URL ?? "http://fixtures:8000/rss/e2e_feed.xml";
export const FIXTURE_MEDIA_URL =
  process.env.COPYCAST_E2E_MEDIA_URL ?? "http://fixtures:8000/media/tiny.mp3";
export const FIXTURE_ITEM_COUNT = 3;
export const FIXTURE_TITLE = "Copycast E2E Podcast";

/** Whether a worker runs and can fetch the fixture media (CI: yes; local api-only: no). */
export const WORKER_AVAILABLE =
  (process.env.COPYCAST_E2E_WORKER ?? (process.env.CI ? "1" : "0")) === "1";
export const ARCHIVE_TIMEOUT_MS = 180_000;

interface FeedSummary {
  id: string;
  kind: "mirror" | "inbox";
  title: string;
  source_url?: string;
}

/** Delete every Mirror so each spec starts from an empty Mirrors page. */
export async function deleteAllMirrors(request: APIRequestContext): Promise<void> {
  const response = await request.get("/api/feeds?kind=mirror");
  expect(response.ok()).toBeTruthy();
  const { feeds } = (await response.json()) as { feeds: FeedSummary[] };
  for (const feed of feeds) {
    // A delete can lose a deadlock against a running archive job (500); retry a few times.
    let status = 0;
    for (let attempt = 0; attempt < 5 && status !== 204 && status !== 404; attempt += 1) {
      if (attempt > 0) await new Promise((resolve) => setTimeout(resolve, 1_000));
      status = (await request.delete(`/api/feeds/${feed.id}`)).status();
    }
    expect([204, 404], `deleting Mirror ${feed.id}`).toContain(status);
  }
}

export async function defaultInbox(request: APIRequestContext): Promise<FeedSummary> {
  const response = await request.get("/api/feeds?kind=inbox");
  expect(response.ok()).toBeTruthy();
  const { feeds } = (await response.json()) as { feeds: FeedSummary[] };
  const inbox = feeds.find((feed) => feed.title === "Copycast") ?? feeds[0];
  if (!inbox) throw new Error("the default Inbox exists");
  return inbox;
}

/** Add the fixture feed from the Mirrors page bar and land on the dialog's Created step. */
export async function createFixtureMirror(page: Page): Promise<string> {
  // Everything, so the worker archives every fixture item (the built-in default is Automatic).
  const defaults = await page.request.put("/api/settings/defaults", {
    data: { backfill: { mode: "all" } },
  });
  expect(defaults.ok()).toBeTruthy();
  await page.goto("/mirrors");
  await page.getByLabel("Source URL").fill(FIXTURE_FEED_URL);
  await page.getByRole("main").getByRole("button", { name: "Add Source" }).click();
  await expect(page.getByRole("heading", { name: "Mirror created" })).toBeVisible({
    timeout: 60_000,
  });
  const feedUrl = await page.getByRole("textbox", { name: "Mirror Feed URL" }).inputValue();
  const match = /\/feeds\/([^/]+)\.xml$/.exec(feedUrl);
  expect(match, `feed URL ${feedUrl} names the Mirror`).not.toBeNull();
  return match?.[1] ?? "";
}
