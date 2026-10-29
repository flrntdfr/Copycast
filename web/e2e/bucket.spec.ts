import { expect, test } from "@playwright/test";

import { FIXTURE_MEDIA_URL, defaultBucket } from "./helpers";

test.describe("Bucket", () => {
  test("opens the storage view from its card, adds a Request and previews a prune from Settings", async ({
    page,
    request,
  }) => {
    const bucket = await defaultBucket(request);
    await page.goto("/");
    // The Buckets box is collapsed by default: open it, then the default Bucket's card.
    await page.getByRole("button", { name: /Buckets/ }).click();
    await page.getByTestId("bucket-card").filter({ hasText: bucket.title }).first().click();
    await expect(page).toHaveURL(new RegExp(`/buckets/${bucket.id}`));
    await expect(page.getByRole("heading", { name: bucket.title })).toBeVisible();
    // The storage view: no feed URL field, the published feed as the first stored file.
    await expect(page.getByRole("textbox", { name: "Bucket Feed URL" })).toHaveCount(0);
    const feedRow = page.getByTestId("object-row").first();
    await expect(feedRow).toContainText(`${bucket.id}.xml`);
    await expect(feedRow.getByRole("link", { name: `Download ${bucket.id}.xml` })).toHaveAttribute(
      "href",
      new RegExp(`/feeds/${bucket.id}\\.xml$`),
    );

    // A media file URL is a valid Request (the worker expands it through the Engine).
    await page.getByLabel("Add URL").fill(FIXTURE_MEDIA_URL);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByText("Request queued")).toBeVisible();

    await page.getByRole("button", { name: "Settings" }).click();
    const settings = page.getByRole("dialog", { name: "Bucket settings" });
    await expect(settings.getByRole("form", { name: "Bucket settings" })).toBeVisible();
    await settings.getByRole("button", { name: "Prune now…" }).click();
    const dialog = page.getByRole("dialog", { name: "Prune this Bucket" });
    await expect(dialog).toBeVisible();
    const preview = dialog.getByTestId("prune-preview");
    await expect(preview).not.toContainText("Counting", { timeout: 30_000 });
    await expect(preview).toContainText(/Deletes \d+ Episodes?|Nothing matches/);
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
  });
});
