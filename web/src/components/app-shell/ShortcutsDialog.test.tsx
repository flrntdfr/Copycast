import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ShortcutsDialog } from "./ShortcutsDialog";
import { renderWithProviders } from "@/test/render";
import { PLAYER_STORAGE_KEY, getPlayerState, resetPlayerForTests } from "@/stores/player";

describe("ShortcutsDialog", () => {
  beforeEach(() => {
    resetPlayerForTests();
  });

  it("lists the player keys and switches every single-key shortcut off", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ShortcutsDialog open onOpenChange={() => undefined} />);
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(dialog).getByRole("heading", { name: "Player" })).toBeInTheDocument();
    expect(within(dialog).getByText("Previous or next chapter")).toBeInTheDocument();

    const toggle = within(dialog).getByRole("switch", { name: "Single-key shortcuts" });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    expect(getPlayerState().shortcuts).toBe(false);
    expect(JSON.parse(window.localStorage.getItem(PLAYER_STORAGE_KEY) ?? "{}")).toMatchObject({
      shortcuts: false,
    });
    expect(dialog).toHaveTextContent("outside a field or a control that uses the key itself");
  });
});
