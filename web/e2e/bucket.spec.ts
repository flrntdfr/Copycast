import { expect, test } from "@playwright/test";

import { FIXTURE_MEDIA_URL, defaultBucket } from "./helpers";

test.describe("Bucket", () => {
  test("adds a Request and opens the prune dialog with a dry-run preview", async ({
    page,
    request,
  }) => {
    const bucket = await defaultBucket(request);
    await page.goto("/");
    await page.getByTestId("bucket-card").filter({ hasText: bucket.title }).first().click();
    await expect(page).toHaveURL(new RegExp(`/buckets/${bucket.id}`));
    await expect(page.getByRole("heading", { name: bucket.title })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Bucket Feed URL" })).toHaveValue(
      new RegExp(`/feeds/${bucket.id}\\.xml$`),
    );

    // A media file URL is a valid Request (the worker expands it through the Engine).
    await page.getByLabel("Add URL").fill(FIXTURE_MEDIA_URL);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByText("Request queued")).toBeVisible();
    await page.getByRole("tab", { name: "Requests" }).click();
    const row = page.getByTestId("request-row").filter({ hasText: FIXTURE_MEDIA_URL }).first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(row).toContainText("UI");

    await page.getByRole("tab", { name: "Settings" }).click();
    await expect(page.getByRole("form", { name: "Bucket settings" })).toBeVisible();
    await page.getByRole("button", { name: "Prune now…" }).click();
    const dialog = page.getByRole("dialog", { name: "Prune this Bucket" });
    await expect(dialog).toBeVisible();
    const preview = dialog.getByTestId("prune-preview");
    await expect(preview).not.toContainText("Counting", { timeout: 30_000 });
    await expect(preview).toContainText(/Deletes \d+ Episodes?|Nothing matches/);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
  });
});
