import { expect, test } from "./fixtures";

import {
  ARCHIVE_TIMEOUT_MS,
  FIXTURE_ITEM_COUNT,
  FIXTURE_TITLE,
  WORKER_AVAILABLE,
  createFixtureMirror,
  deleteAllMirrors,
} from "./helpers";
import { authFile } from "./users";

/**
 * Two Members and an Admin (ADR 0028, ADR 0029). Bob mirrors the fixture feed; his quota is 0,
 * so the worker blocks every Episode ("Over quota") instead of downloading it. Alice cannot see
 * Bob's Mirror at all; the Admin sees it, labelled with its owner, and deletes it. The tests run
 * in order and share Bob's Mirror id.
 */
test.describe.configure({ mode: "serial" });

let bobMirrorId = "";

test.describe("as bob, Over quota", () => {
  test.use({ storageState: authFile("bob") });

  test("bob's Mirror is created; he is Over quota from the start", async ({ page, request }) => {
    await deleteAllMirrors(request); // a Member's own Mirrors only
    const me = await request.get("/api/me");
    expect(me.ok()).toBeTruthy();
    const quota = ((await me.json()) as { quota: { state: string; quota_bytes: number | null } })
      .quota;
    expect(quota).toMatchObject({ state: "over", quota_bytes: 0 });

    bobMirrorId = await createFixtureMirror(page);
    await page.getByRole("link", { name: "Open feed" }).click();
    await expect(page.getByRole("heading", { name: FIXTURE_TITLE })).toBeVisible();
    await expect(page.getByTestId("catalog-row")).toHaveCount(FIXTURE_ITEM_COUNT, {
      timeout: 60_000,
    });
  });

  test("the worker blocks his Episodes: each row says Over quota", async ({ page }) => {
    test.skip(!WORKER_AVAILABLE, "needs a worker to run the archive jobs");
    test.setTimeout(ARCHIVE_TIMEOUT_MS + 60_000);
    expect(bobMirrorId).not.toBe("");
    await page.goto(`/feed/${bobMirrorId}`);
    const badges = page.getByTestId("catalog-row").locator('[data-blocked-reason="quota"]');
    await expect(badges).toHaveCount(FIXTURE_ITEM_COUNT, { timeout: ARCHIVE_TIMEOUT_MS });
    await expect(badges.first()).toHaveText("Over quota");
    // Nothing was downloaded on his behalf.
    await expect(page.getByRole("table").getByText("Listed")).toHaveCount(0);
  });
});

test.describe("as alice", () => {
  test.use({ storageState: authFile("alice") });

  test("Bob's Mirror does not exist for alice", async ({ page, request }) => {
    expect(bobMirrorId).not.toBe("");
    // 404, never 403: another User's Feed answers like a missing one.
    expect((await request.get(`/api/feeds/${bobMirrorId}`)).status()).toBe(404);
    expect((await request.get(`/api/feeds/${bobMirrorId}/items`)).status()).toBe(404);
    // Only the Owner's or an Admin's session opens the feed document.
    expect((await request.get(`/feeds/${bobMirrorId}.xml`)).status()).toBe(401);

    await page.goto(`/feed/${bobMirrorId}`);
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
    await page.goto("/");
    await expect(page.getByRole("link", { name: FIXTURE_TITLE })).toHaveCount(0);
    await expect(page.getByTestId("owner-badge")).toHaveCount(0);
  });
});

test.describe("as the Admin", () => {
  test("the Admin sees Bob's Mirror labelled with its owner, and deletes it", async ({
    page,
    request,
  }) => {
    expect(bobMirrorId).not.toBe("");
    await page.goto(`/feed/${bobMirrorId}`);
    await expect(page.getByRole("heading", { name: FIXTURE_TITLE })).toBeVisible();
    await expect(page.getByTestId("owner-badge").first()).toHaveAttribute("data-owner", "bob");
    await page.goto("/");
    await expect(
      page.getByTestId("mirror-row").filter({ has: page.locator('[data-owner="bob"]') }),
    ).toHaveCount(1);

    expect((await request.delete(`/api/feeds/${bobMirrorId}`)).status()).toBe(204);
  });
});
