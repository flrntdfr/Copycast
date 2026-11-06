import { expect, test as setup } from "./fixtures";

import { USERNAMES, authFile, passwordOf } from "./users";

/**
 * Signs every e2e User in through `POST /auth/login` and keeps each session as a storageState
 * file, so the specs start signed in (the Admin by default) without a login round trip each.
 * The sign-in page itself is exercised by login.spec.ts.
 */
for (const username of USERNAMES) {
  setup(`sign in as ${username}`, async ({ request }) => {
    const response = await request.post("/auth/login", {
      data: { username, password: passwordOf(username) },
    });
    expect(response.status(), await response.text()).toBe(204);
    const me = await request.get("/api/me");
    expect(me.ok()).toBeTruthy();
    expect(((await me.json()) as { username: string }).username).toBe(username);
    await request.storageState({ path: authFile(username) });
  });
}
