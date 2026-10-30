import { expect, test } from "@playwright/test";

import { defaultLocalFeed } from "./helpers";

test.describe("Bucket", () => {
  test("opens a feed's bucket from its card, lists its files and previews a purge", async ({
    page,
    request,
  }) => {
    // Every Feed has a bucket; the default Local feed always exists, so its bucket does too.
    const feed = await defaultLocalFeed(request);
    await page.goto("/");
    // The Buckets box is collapsed by default: open it, then the default Local feed's card.
    await page.getByRole("button", { name: /Buckets/ }).click();
    await page.getByTestId("bucket-card").filter({ hasText: feed.name }).first().click();
    await expect(page).toHaveURL(new RegExp(`/buckets/${feed.id}`));
    await expect(page.getByRole("heading", { name: feed.name })).toBeVisible();
    await expect(page.getByTestId("bucket-summary")).toContainText(/[\d,]+ files? · [\d.,]+ MB/);
    await expect(page.getByRole("link", { name: "Open feed" })).toHaveAttribute(
      "href",
      `/feed/${feed.id}`,
    );
    // Every setting lives on the feed page: no Settings or Add URL on the bucket.
    await expect(page.getByRole("button", { name: "Settings" })).toHaveCount(0);
    await expect(page.getByLabel("Add URL")).toHaveCount(0);

    // The published feed comes first, never purged.
    const feedRow = page.getByTestId("object-row").first();
    await expect(feedRow).toContainText(`${feed.id}.xml`);
    await expect(feedRow.getByRole("link", { name: `Download ${feed.id}.xml` })).toHaveAttribute(
      "href",
      new RegExp(`/feeds/${feed.id}\\.xml$`),
    );
    await expect(
      feedRow.getByRole("button", { name: "Part of the feed itself: never purged" }),
    ).toHaveAttribute("aria-disabled", "true");

    await page.getByRole("button", { name: "Purge…" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    const summary = dialog.getByTestId("purge-summary");
    await expect(summary).not.toContainText("Counting", { timeout: 30_000 });
    await expect(summary).toContainText(/Purging deletes \d+ Episodes?|Nothing to purge/);
    await dialog.getByRole("button", { name: "No" }).click();
    await expect(dialog).toBeHidden();
  });
});
