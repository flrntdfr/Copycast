import { expect, test } from "@playwright/test";

import {
  ARCHIVE_TIMEOUT_MS,
  FIXTURE_ITEM_COUNT,
  FIXTURE_TITLE,
  WORKER_AVAILABLE,
  createFixtureMirror,
  deleteAllMirrors,
} from "./helpers";

test.describe("Mirror", () => {
  test.beforeEach(async ({ request }) => {
    await deleteAllMirrors(request);
  });

  test("adds a Mirror from the fixture feed and watches it archive", async ({ page }) => {
    const mirrorId = await createFixtureMirror(page);
    await page.getByRole("link", { name: "Open feed" }).click();
    await expect(page).toHaveURL(new RegExp(`/feed/${mirrorId}$`));
    await expect(page.getByRole("heading", { name: FIXTURE_TITLE })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Mirror Feed URL" })).toHaveValue(
      new RegExp(`/feeds/${mirrorId}\\.xml$`),
    );

    // The Catalog is populated from the probe's listing at creation time.
    const rows = page.getByTestId("catalog-row");
    await expect(rows).toHaveCount(FIXTURE_ITEM_COUNT, { timeout: 60_000 });

    if (WORKER_AVAILABLE) {
      // The worker archives every item; the rows go Queued -> Archiving -> Listed live.
      await expect(page.getByRole("table").getByText("Listed")).toHaveCount(FIXTURE_ITEM_COUNT, {
        timeout: ARCHIVE_TIMEOUT_MS,
      });
    }

    // Settings live in a dialog behind the header's button.
    await page.getByRole("button", { name: "Settings" }).click();
    const settings = page.getByRole("dialog", { name: "Mirror settings" });
    await expect(settings.getByRole("textbox", { name: "Source URL" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(settings).toBeHidden();

    // Old Mirror links land on the feed page, and the server serves that page on a reload.
    await page.goto(`/mirrors/${mirrorId}`);
    await expect(page).toHaveURL(new RegExp(`/feed/${mirrorId}$`));
    await page.reload();
    await expect(page.getByRole("heading", { name: FIXTURE_TITLE })).toBeVisible();

    // The Mirror shows up in the list with its feed URL copyable.
    await page.goto("/");
    await expect(page.getByTestId("mirror-row")).toHaveCount(1);
    await expect(page.getByRole("link", { name: FIXTURE_TITLE })).toBeVisible();
    await expect(page.getByRole("button", { name: /Copy feed URL/ }).first()).toBeVisible();
  });
});
