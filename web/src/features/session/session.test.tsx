import { act, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory } from "@tanstack/react-router";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import { storageKey } from "@/lib/storage";
import { getPlayerState, play, resetPlayerForTests } from "@/stores/player";
import { me, problem } from "@/test/factories";
import { renderApp, testQueryClient } from "@/test/render";
import { server } from "@/test/server";

const alice = me({ username: "alice", admin: false, auth_mode: "users", via: "session" });

/** `/api/me` answers 401 until a successful `POST /auth/login`, then `user`. */
function sessionHandlers(user = alice) {
  let signedIn = false;
  const logins: unknown[] = [];
  let logouts = 0;
  server.use(
    http.get("/api/me", () =>
      signedIn
        ? HttpResponse.json(user)
        : HttpResponse.json(problem("unauthorized", 401), { status: 401 }),
    ),
    http.post("/auth/login", async ({ request }) => {
      logins.push(await request.json());
      signedIn = true;
      return new HttpResponse(null, { status: 204 });
    }),
    http.post("/auth/logout", () => {
      logouts += 1;
      signedIn = false;
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })),
    http.get("/api/jobs", () => HttpResponse.json({ jobs: [], total: 0, limit: 1, offset: 0 })),
    http.get("/api/engine/cookies", () =>
      HttpResponse.json({ present: false, files: [], services: [] }),
    ),
    http.get("/api/settings/defaults", () =>
      HttpResponse.json({
        language: null,
        min_duration_seconds: null,
        backfill: { mode: "automatic", latest_n: null, retention_days: 7, selection: null },
        refresh_interval_hours: 24,
      }),
    ),
  );
  return {
    logins,
    get logouts() {
      return logouts;
    },
    signIn: () => {
      signedIn = true;
    },
  };
}

async function signIn(user: ReturnType<typeof userEvent.setup>, totp = "") {
  await user.type(await screen.findByLabelText("Username"), " Alice ");
  await user.type(screen.getByLabelText("Password"), "correct horse");
  if (totp) await user.type(screen.getByLabelText("Code"), totp);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

afterEach(() => {
  resetPlayerForTests();
});

describe("signing in", () => {
  it("sends a page without a session to /login, then back to it once signed in", async () => {
    const session = sessionHandlers();
    const user = userEvent.setup();
    const { history } = renderApp("/jobs");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(history.location.pathname).toBe("/login");
    expect(history.location.search).toContain("next=%2Fjobs");
    // No shell, no status dot polling, no Basic dialog: just the form.
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();

    await signIn(user, "123 456");
    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
    expect(history.location.pathname).toBe("/jobs");
    expect(session.logins).toEqual([
      { username: "Alice", password: "correct horse", totp: "123456" },
    ]);
  });

  it("goes to the Feeds page when there is nowhere to go back to", async () => {
    sessionHandlers();
    const user = userEvent.setup();
    const { history } = renderApp("/login?next=https%3A%2F%2Fevil.example%2F");
    await signIn(user);
    await waitFor(() => expect(history.location.pathname).toBe("/"));
  });

  it.each([
    [401, "login-failed", {}, "Wrong username, password or code"],
    [429, "login-locked", { retry_after: 60 }, "Too many attempts, try again in 60 s"],
    [503, "login-busy", { retry_after: 5 }, "Busy, try again in a few seconds"],
    [
      400,
      "https-required",
      { detail: "sign in over https: https://copycast.example" },
      "Sign in over https: https://copycast.example",
    ],
  ] as const)("says why a %i (%s) sign-in failed", async (status, slug, extra, text) => {
    sessionHandlers();
    server.use(
      http.post("/auth/login", () => HttpResponse.json(problem(slug, status, extra), { status })),
    );
    const user = userEvent.setup();
    const { history } = renderApp("/login");
    await signIn(user);
    expect(await screen.findByTestId("login-error")).toHaveTextContent(text);
    expect(history.location.pathname).toBe("/login");
    // The form answers inline; nothing toasts.
    expect(document.querySelector("[data-sonner-toast]")).toBeNull();
  });

  it("skips the sign-in page in open mode", async () => {
    server.use(http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })));
    const { history } = renderApp("/login?next=%2Fabout");
    await waitFor(() => expect(history.location.pathname).toBe("/about"));
  });

  it("skips the sign-in page when a session exists already", async () => {
    const session = sessionHandlers();
    session.signIn();
    const { history } = renderApp("/login");
    await waitFor(() => expect(history.location.pathname).toBe("/"));
  });
});

describe("per-user storage", () => {
  it("lets the first User adopt this browser's pre-3.0 player settings", async () => {
    window.localStorage.setItem("copycast.player", JSON.stringify({ rate: 1.5 }));
    const session = sessionHandlers();
    session.signIn();
    renderApp("/");
    await screen.findByRole("button", { name: "Account: alice" });
    expect(getPlayerState().rate).toBe(1.5);
    expect(window.localStorage.getItem("copycast.u.alice.player")).toBe('{"rate":1.5}');
    expect(window.localStorage.getItem("copycast.player")).toBeNull();
  });

  it("brings back the signed-in User's own track, never another User's", async () => {
    const track = (user: string) => ({
      itemId: `item-${user}`,
      feedId: "m1",
      title: `${user}'s episode`,
      feedTitle: "Feed",
      url: `http://localhost:8080/api/feeds/m1/items/item-${user}/media`,
      mime: "audio/mp4",
      artworkUrl: null,
      durationSeconds: 60,
    });
    window.localStorage.setItem("copycast.u.alice.player.track", JSON.stringify(track("alice")));
    window.localStorage.setItem("copycast.u.bob.player.track", JSON.stringify(track("bob")));
    const session = sessionHandlers();
    session.signIn();
    renderApp("/");
    await screen.findByRole("button", { name: "Account: alice" });
    expect(getPlayerState().track?.itemId).toBe("item-alice");
  });
});

describe("a session that ends", () => {
  it("sends any /api 401 to /login once, without a toast, keeping the page as next", async () => {
    const session = sessionHandlers();
    session.signIn();
    const { history, client } = renderApp("/jobs");
    await screen.findByRole("heading", { name: "Jobs" });
    await screen.findByRole("button", { name: "Account: alice" });

    // The session expires: every /api route answers 401 from now on.
    server.use(
      http.get("/api/me", () => HttpResponse.json(problem("unauthorized", 401), { status: 401 })),
      http.get("/api/jobs", () => HttpResponse.json(problem("unauthorized", 401), { status: 401 })),
    );
    await act(() => client.refetchQueries({ queryKey: ["get", "/api/jobs"] }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(history.location.pathname).toBe("/login");
    expect(history.location.search).toContain("next=%2Fjobs");
    expect(document.querySelector("[data-sonner-toast]")).toBeNull();
    // The cache went with the session.
    expect(client.getQueryData(["get", "/api/jobs"])).toBeUndefined();
  });
});

describe("the user menu", () => {
  it("is hidden in open mode", async () => {
    server.use(http.get("/api/feeds", () => HttpResponse.json({ feeds: [] })));
    renderApp("/");
    await screen.findByRole("button", { name: /^System status/ });
    expect(screen.queryByRole("button", { name: /^Account:/ })).toBeNull();
  });

  it("shows the User and an Admin badge", async () => {
    const session = sessionHandlers(me({ username: "florent", auth_mode: "users", admin: true }));
    session.signIn();
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: "Account: florent" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByText("florent")).toBeInTheDocument();
    expect(within(menu).getByTestId("admin-badge")).toHaveTextContent("Admin");
  });

  it("signs out: the session, playback, the cache and that User's keys go", async () => {
    const session = sessionHandlers();
    session.signIn();
    window.localStorage.setItem("copycast.u.bob.player", '{"rate":2}');
    const user = userEvent.setup();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
    const { history, client } = renderApp("/");
    const account = await screen.findByRole("button", { name: "Account: alice" });

    // Alice listens to something; her keys fill up.
    act(() =>
      play({
        itemId: "i1",
        feedId: "m1",
        title: "Episode",
        feedTitle: "Feed",
        url: "http://localhost:8080/api/feeds/m1/items/i1/media",
        mime: "audio/mp4",
        artworkUrl: null,
        durationSeconds: 60,
      }),
    );
    expect(storageKey("player.track")).toBe("copycast.u.alice.player.track");
    expect(window.localStorage.getItem("copycast.u.alice.player.track")).not.toBeNull();

    await user.click(account);
    expect(screen.queryByTestId("admin-badge")).toBeNull();
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(history.location.pathname).toBe("/login");
    expect(history.location.search).toBe("");
    expect(session.logouts).toBe(1);
    expect(getPlayerState().track).toBeNull();
    const keys = Object.keys(window.localStorage);
    expect(keys.filter((key) => key.startsWith("copycast.u.alice."))).toEqual([]);
    expect(window.localStorage.getItem("copycast.u.bob.player")).toBe('{"rate":2}');
    expect(client.getQueryData(["get", "/api/feeds"])).toBeUndefined();
  });

  it("signs out from the phone navigation sheet too", async () => {
    const session = sessionHandlers();
    session.signIn();
    const user = userEvent.setup();
    const { history } = renderApp("/");
    await user.click(await screen.findByRole("button", { name: "Open navigation" }));
    const sheet = await screen.findByTestId("user-sheet");
    expect(sheet).toHaveTextContent("alice");
    await user.click(within(sheet).getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(history.location.pathname).toBe("/login"));
    expect(session.logouts).toBe(1);
  });
});

describe("the event stream", () => {
  const sources: { url: string; closed: boolean }[] = [];

  beforeEach(() => {
    sources.length = 0;
    vi.stubGlobal(
      "EventSource",
      class {
        onopen = null;
        onerror = null;
        readonly record: { url: string; closed: boolean };
        constructor(url: string) {
          this.record = { url, closed: false };
          sources.push(this.record);
        }
        addEventListener(): void {}
        removeEventListener(): void {}
        close(): void {
          this.record.closed = true;
        }
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens only once signed in and closes on sign-out", async () => {
    sessionHandlers();
    const user = userEvent.setup();
    const history = createMemoryHistory({ initialEntries: ["/"] });
    render(<App history={history} queryClient={testQueryClient()} />);

    await screen.findByRole("heading", { name: "Sign in" });
    expect(sources).toHaveLength(0);

    await signIn(user);
    await screen.findByRole("button", { name: "Account: alice" });
    await waitFor(() => expect(sources.filter((source) => !source.closed)).toHaveLength(1));
    expect(sources.at(-1)?.url).toBe("/api/events");

    await user.click(screen.getByRole("button", { name: "Account: alice" }));
    await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(sources.every((source) => source.closed)).toBe(true);
  });
});
