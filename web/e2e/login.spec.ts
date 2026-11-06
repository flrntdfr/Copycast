import { expect, test } from "./fixtures";

import { SIGNED_OUT, passwordOf } from "./users";

/**
 * Signing in through the login page, under the UI's Content-Security-Policy like every page
 * (the `cspGuard` fixture). These tests start signed out; the sign-in they make is their own,
 * so signing out never ends the sessions the other specs reuse.
 */
test.describe("Sign-in", () => {
  test.use({ storageState: SIGNED_OUT });

  test("/api answers 401 without a Basic challenge, so the browser never prompts", async ({
    request,
  }) => {
    const me = await request.get("/api/me");
    expect(me.status()).toBe(401);
    expect(me.headers()["www-authenticate"]).toBeUndefined();
    expect(me.headers()["content-type"]).toContain("application/problem+json");
    expect(((await me.json()) as { type: string }).type).toBe("urn:copycast:problem:unauthorized");
  });

  test("a wrong password gets one generic answer", async ({ page }) => {
    await page.goto("/login");
    // A name that is no User: its failures lock nobody the other specs sign in as.
    await page.getByLabel("Username").fill("nobody");
    await page.getByLabel("Password").fill("not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByTestId("login-error")).toHaveText("Wrong username, password or code");
    await expect(page).toHaveURL(/\/login/);
  });

  test("the app sends a visitor to the login page and back, then signs out", async ({ page }) => {
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/login\?next=/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

    await page.getByLabel("Username").fill("alice");
    await page.getByLabel("Password").fill(passwordOf("alice"));
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("heading", { name: "Your settings" })).toBeVisible();
    const cookies = await page.context().cookies();
    const session = cookies.find((cookie) => cookie.name === "copycast_session");
    expect(session?.httpOnly).toBe(true);
    expect(session?.sameSite).toBe("Lax");

    // A Member has no Admin badge; the account menu signs out.
    await page.getByRole("button", { name: "Account: alice" }).click();
    await expect(page.getByTestId("admin-badge")).toHaveCount(0);
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login/);
    expect((await page.request.get("/api/me")).status()).toBe(401);
  });
});
