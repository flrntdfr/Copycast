import { expect, test } from "./fixtures";

import {
  ARCHIVE_TIMEOUT_MS,
  FIXTURE_ITEM_COUNT,
  WORKER_AVAILABLE,
  createFixtureMirror,
  defaultLocalFeed,
  deleteAllMirrors,
} from "./helpers";

/**
 * The UI under its Content-Security-Policy: every page and the parts that inject styles
 * (Radix dialogs, toasts), markup (the QR code) or media (the player) run without a single
 * violation; the `cspGuard` fixture fails the test on the first one.
 */
test.describe("Security headers", () => {
  test("the shell is served under a strict policy and every page renders under it", async ({
    page,
    request,
  }) => {
    const shell = await page.goto("/");
    expect(shell?.status()).toBe(200);
    const headers = shell?.headers() ?? {};
    const policy = headers["content-security-policy"] ?? "";
    for (const directive of [
      "default-src 'self'",
      "frame-ancestors 'none'",
      "base-uri 'none'",
      "object-src 'none'",
    ]) {
      expect(policy).toContain(directive);
    }
    expect(policy).not.toContain("'unsafe-eval'");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["referrer-policy"]).toBe("no-referrer");
    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["cross-origin-opener-policy"]).toBe("same-origin");

    for (const [path, heading] of [
      ["/about", "About"],
      ["/settings", "Settings"],
      ["/jobs", "Jobs"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    }

    // A dialog (Radix injects a <style> to lock scrolling) and the QR code (SVG markup).
    const feed = await defaultLocalFeed(request);
    await page.goto(`/feed/${feed.id}`);
    await page.getByRole("button", { name: "Show feed url as a QR code" }).click();
    await expect(page.getByTestId("qr-code")).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: /switch theme/ }).click();

    // Podcast paths get no page policy, only the sandbox (opened in a browser, an image or
    // a mirrored document runs nothing on Copycast's origin).
    const feeds = await request.get("/feeds/copycast-artwork.png");
    expect(feeds.status()).toBe(200);
    expect(feeds.headers()["content-security-policy"]).toBe("sandbox");
    expect(feeds.headers()["x-content-type-options"]).toBe("nosniff");
    expect(feeds.headers()["x-frame-options"]).toBeUndefined();
    expect(feeds.headers()["cross-origin-opener-policy"]).toBeUndefined();
  });

  test("the inline theme script runs under its hash", async ({ page }) => {
    // With the bundle blocked only the inline script can set the class and the inline
    // color-scheme (app.css and src/lib/theme.ts would satisfy a looser check).
    await page.addInitScript(() => {
      try {
        localStorage.setItem("copycast.theme", "dark");
      } catch {
        // storage unavailable: the assertions below fail and say so
      }
    });
    await page.route("**/assets/*.js", (route) => route.abort());
    await page.goto("/");
    await expect(page.locator("html")).toHaveClass(/(^|\s)dark(\s|$)/);
    const scheme = await page.evaluate(() => document.documentElement.style.colorScheme);
    expect(scheme).toBe("dark");
  });

  test("another site cannot write; the UI itself can", async ({ request }) => {
    const refused = await request.post("/api/buckets", {
      data: { name: "Cross-site" },
      headers: { "Sec-Fetch-Site": "cross-site", Origin: "https://evil.example" },
    });
    expect(refused.status()).toBe(403);
    expect(refused.headers()["content-type"]).toContain("application/problem+json");
    const sameOrigin = await request.put("/api/settings/defaults", {
      data: { backfill: { mode: "all" } },
      headers: { "Sec-Fetch-Site": "same-origin" },
    });
    expect(sameOrigin.ok()).toBeTruthy();
  });

  test("the player streams an archived Episode under the policy", async ({ page, request }) => {
    test.skip(!WORKER_AVAILABLE, "needs a worker to archive the fixture feed");
    test.setTimeout(ARCHIVE_TIMEOUT_MS + 60_000);
    await deleteAllMirrors(request);
    const mirrorId = await createFixtureMirror(page);
    await page.goto(`/feed/${mirrorId}`);
    await expect(page.getByRole("table").getByText("Listed")).toHaveCount(FIXTURE_ITEM_COUNT, {
      timeout: ARCHIVE_TIMEOUT_MS,
    });
    const media = page.waitForRequest(/\/feeds\/[^/]+\/(k\/[^/]+\/)?media\//);
    await page.getByTestId("catalog-row").first().getByRole("button", { name: "Play" }).click();
    await media;
  });
});
