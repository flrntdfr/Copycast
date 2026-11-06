import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ownerLabel } from "@/api/session";
import type { MeRead } from "@/api/types";
import { bucket, job, me, mirror } from "@/test/factories";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

const florent = me({ username: "florent", admin: true, auth_mode: "users", via: "session" });
const alice = me({ username: "alice", admin: false, auth_mode: "users", via: "session" });

const feeds = [
  mirror({ id: "m-own", title: "Mine", owner: "florent" }),
  mirror({ id: "m-emilia", title: "Hers", owner: "emilia" }),
  bucket({ id: "b-bob", name: "Bob's box", title: "Bob's box", owner: "bob", owner_removed: true }),
];

function signedInAs(user: MeRead) {
  server.use(
    http.get("/api/me", () => HttpResponse.json(user)),
    http.get("/api/feeds", () => HttpResponse.json({ feeds })),
    http.get("/api/jobs", ({ request }) => {
      const finished = new URL(request.url).searchParams.getAll("status").includes("succeeded");
      const jobs = finished
        ? [
            job({ status: "succeeded", feed_id: "m-emilia", finished_at: "2024-01-15T09:05:00Z" }),
            job({ status: "succeeded", feed_id: null, kind: "prune" }),
          ]
        : [];
      return HttpResponse.json({ jobs, total: jobs.length, limit: 200, offset: 0 });
    }),
  );
}

describe("ownerLabel", () => {
  it("labels another User's Feed for an Admin only", () => {
    expect(ownerLabel(florent, { owner: "emilia" })).toBe("emilia");
    expect(ownerLabel(florent, { owner: "bob", owner_removed: true })).toBe("bob (owner removed)");
    expect(ownerLabel(florent, { owner: "florent" })).toBeNull();
    expect(ownerLabel(alice, { owner: "emilia" })).toBeNull();
    expect(ownerLabel(me(), { owner: "copycast" })).toBeNull();
    expect(ownerLabel(undefined, { owner: "emilia" })).toBeNull();
    expect(ownerLabel(florent, undefined)).toBeNull();
  });
});

describe("owner labels", () => {
  it("show whose Feed it is in the Feeds table, for an Admin", async () => {
    signedInAs(florent);
    renderApp("/");
    const hers = (await screen.findByText("Hers")).closest("tr") as HTMLElement;
    expect(within(hers).getByTestId("owner-badge")).toHaveTextContent("emilia");
    const bobs = screen.getByText("Bob's box").closest("tr") as HTMLElement;
    expect(within(bobs).getByTestId("owner-badge")).toHaveTextContent("bob (owner removed)");
    const mine = screen.getByText("Mine").closest("tr") as HTMLElement;
    expect(within(mine).queryByTestId("owner-badge")).toBeNull();
  });

  it("never show for a Member", async () => {
    signedInAs(alice);
    renderApp("/");
    await screen.findByText("Hers");
    expect(screen.queryByTestId("owner-badge")).toBeNull();
  });

  it("label a job with its Feed's owner, from the Feeds list", async () => {
    signedInAs(florent);
    renderApp("/jobs");
    const rows = await screen.findAllByTestId("job-row");
    expect(rows).toHaveLength(2);
    const [withFeed, withoutFeed] = rows as [HTMLElement, HTMLElement];
    expect(await within(withFeed).findByTestId("owner-badge")).toHaveTextContent("emilia");
    expect(within(withoutFeed).queryByTestId("owner-badge")).toBeNull();
  });

  it("show in another User's Mirror header", async () => {
    signedInAs(florent);
    server.use(
      http.get("/api/feeds/:feedId", () => HttpResponse.json(feeds[1])),
      http.get("/api/feeds/:feedId/items", () =>
        HttpResponse.json({ items: [], total: 0, limit: 100, offset: 0 }),
      ),
      http.get("/api/settings/defaults", () =>
        HttpResponse.json({ language: null, min_duration_seconds: null }),
      ),
    );
    renderApp("/feed/m-emilia");
    const heading = await screen.findByRole("heading", { level: 1, name: "Hers" });
    const header = heading.closest("header") as HTMLElement;
    expect(within(header).getByTestId("owner-badge")).toHaveTextContent("emilia");
  });
});
