import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { FeedCredentials } from "./FeedCredentials";
import { mirror } from "@/test/factories";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

describe("FeedCredentials", () => {
  it("renders nothing while authentication is off", () => {
    renderWithProviders(<FeedCredentials feed={mirror()} />);
    expect(screen.queryByTestId("feed-credentials")).not.toBeInTheDocument();
  });

  it("hides Rotate where the feed is a snapshot that would not refresh", () => {
    const feed = mirror({ feed_credentials: { username: "k7mpq2xz", password: "p".repeat(24) } });
    renderWithProviders(<FeedCredentials feed={feed} canRotate={false} />);
    expect(screen.getByLabelText("Username")).toHaveValue("k7mpq2xz");
    expect(screen.queryByRole("button", { name: "Rotate…" })).not.toBeInTheDocument();
  });

  it("shows the pair and rotates it after confirmation", async () => {
    const feed = mirror({
      id: "mirror-1",
      feed_url: "http://k7mpq2xz:secretsecretsecretsecret@localhost:8080/feeds/mirror-1.xml",
      feed_credentials: { username: "k7mpq2xz", password: "secretsecretsecretsecret" },
    });
    const calls: string[] = [];
    server.use(
      http.post("/api/feeds/:feedId/credentials/rotate", ({ params }) => {
        calls.push(String(params.feedId));
        return HttpResponse.json({
          ...feed,
          feed_credentials: { username: "newnewne", password: "n".repeat(24) },
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<FeedCredentials feed={feed} />);

    const block = screen.getByTestId("feed-credentials");
    expect(within(block).getByLabelText("Username")).toHaveValue("k7mpq2xz");
    expect(within(block).getByLabelText("Password")).toHaveValue("secretsecretsecretsecret");
    await user.click(within(block).getByRole("button", { name: "Rotate…" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("old ones stop working at once");
    await user.click(within(dialog).getByRole("button", { name: "Rotate" }));
    await waitFor(() => expect(calls).toEqual(["mirror-1"]));
    expect(await screen.findByText("Feed credentials rotated")).toBeInTheDocument();
  });
});
