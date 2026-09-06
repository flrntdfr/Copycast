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
    await page.getByRole("link", { name: "Open Mirror" }).click();
    await expect(page).toHaveURL(new RegExp(`/mirrors/${mirrorId}`));
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
      // The Refreshes tab lists the first Refresh with its counts.
      await page.getByRole("tab", { name: "Refreshes" }).click();
      const refresh = page.getByTestId("refresh-row").first();
      await expect(refresh).toBeVisible({ timeout: 60_000 });
      await expect(refresh).toContainText(`${FIXTURE_ITEM_COUNT} listed`, { timeout: 60_000 });
    } else {
      await page.getByRole("tab", { name: "Refreshes" }).click();
      await expect(
        page.getByTestId("refresh-row").first().or(page.getByText("No Refreshes yet")),
      ).toBeVisible();
    }

    // The Mirror shows up in the list with its feed URL copyable.
    await page.goto("/mirrors");
    await expect(page.getByTestId("mirror-row")).toHaveCount(1);
    await expect(page.getByRole("link", { name: FIXTURE_TITLE })).toBeVisible();
    await expect(page.getByRole("button", { name: /Copy feed URL/ }).first()).toBeVisible();
  });

  test("previews a Catalog range selection with a dry run", async ({ page }) => {
    const mirrorId = await createFixtureMirror(page);
    await page.goto(`/mirrors/${mirrorId}`);
    await expect(page.getByTestId("catalog-row")).toHaveCount(FIXTURE_ITEM_COUNT, {
      timeout: 60_000,
    });

    const input = page.getByRole("textbox", { name: "Archive by episode numbers" });
    await input.fill("1-2, 99");
    const preview = page.getByTestId("dry-run-preview");
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await expect(preview).toContainText("2 Episodes");
    await expect(preview).toContainText("1 not found (99)");

    await input.fill("1-x");
    await expect(page.getByText('Cannot read "1-x"')).toBeVisible();
  });
});
