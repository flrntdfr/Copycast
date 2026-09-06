import { expect, test } from "@playwright/test";

test("About shows the app and engine versions and the worker heartbeat", async ({
  page,
  request,
}) => {
  const about = await request.get("/api/about");
  expect(about.ok()).toBeTruthy();
  const info = (await about.json()) as {
    version: string;
    engine: { name: string; version: string; channel: string };
    ffmpeg_version: string | null;
  };

  await page.goto("/about");
  await expect(page.getByRole("heading", { name: "About" })).toBeVisible();
  await expect(page.getByText(`Copycast ${info.version}`)).toBeVisible();
  const engine = page.getByTestId("about-engine");
  await expect(engine).toContainText(`${info.engine.name} ${info.engine.version}`);
  await expect(engine).toContainText(`(${info.engine.channel})`);
  if (info.ffmpeg_version) await expect(page.getByText(info.ffmpeg_version)).toBeVisible();
  await expect(page.getByText("Worker last seen")).toBeVisible();
  const worker = page.getByTestId("about-worker");
  await expect(worker).not.toHaveText("…", { timeout: 30_000 });
  await expect(worker).toContainText(/ago|just now|never|unknown/);
  await expect(page.getByRole("heading", { name: "Storage per feed" })).toBeVisible();
});
