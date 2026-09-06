import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { PruneDialog, describePrune, toPruneRequest } from "./PruneDialog";
import type { PruneRequest } from "@/api/types";
import { pruneResult } from "@/test/factories";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";

describe("PruneDialog helpers", () => {
  it("builds the request body and the wording", () => {
    expect(toPruneRequest({ downloaded: true, olderThanDays: null }, true)).toEqual({
      downloaded: true,
      dry_run: true,
    });
    expect(toPruneRequest({ downloaded: false, olderThanDays: 30 }, false)).toEqual({
      downloaded: false,
      older_than_days: 30,
      dry_run: false,
    });
    expect(describePrune(pruneResult({ matched: 12, bytes_freed: 1_200_000_000 }))).toBe(
      "Deletes 12 Episodes (1.2 GB)",
    );
    expect(describePrune(pruneResult({ matched: 0 }))).toBe("Nothing matches these criteria.");
  });
});

describe("PruneDialog", () => {
  it("runs a debounced dry run, then deletes after the confirmation", async () => {
    const bodies: PruneRequest[] = [];
    server.use(
      http.post("/api/inboxes/:inboxId/prune", async ({ request }) => {
        const body = (await request.json()) as PruneRequest;
        bodies.push(body);
        return HttpResponse.json(
          pruneResult({
            matched: 12,
            bytes_freed: 1_200_000_000,
            deleted_count: body.dry_run ? 0 : 12,
            dry_run: body.dry_run ?? false,
          }),
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<PruneDialog inboxId="inbox-1" open onOpenChange={() => undefined} />);

    const preview = await screen.findByTestId("prune-preview");
    await waitFor(() => expect(preview).toHaveTextContent("Deletes 12 Episodes (1.2 GB)"));
    expect(bodies).toEqual([{ downloaded: true, dry_run: true }]);

    await user.click(screen.getByRole("checkbox", { name: /Added more than N days ago/ }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({ downloaded: true, older_than_days: 30, dry_run: true });

    await user.click(screen.getByRole("button", { name: "Delete 12" }));
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(bodies).toHaveLength(3));
    expect(bodies[2]).toEqual({ downloaded: true, older_than_days: 30, dry_run: false });
  });

  it("asks for a criterion when none is selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PruneDialog inboxId="inbox-1" open onOpenChange={() => undefined} />);
    await user.click(screen.getByRole("checkbox", { name: /Downloaded at least once/ }));
    expect(await screen.findByText("Pick at least one criterion.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });
});
