import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { setShortcuts, usePlayerSelector } from "@/stores/player";

interface Shortcut {
  keys: readonly string[];
  action: string;
  /** A key without Ctrl, Alt or ⌘, which the switch turns off. */
  single?: boolean;
}

export const SHORTCUTS: readonly Shortcut[] = [
  { keys: ["⌘", "K"], action: "Open the command palette (Ctrl+K on Windows and Linux)" },
  { keys: ["/"], action: "Focus the filter of the current table", single: true },
  { keys: ["Esc"], action: "Close dialogs, menus and the palette" },
  { keys: ["Shift", "click"], action: "Select a range of Episodes in a Catalog" },
  { keys: ["?"], action: "Show these shortcuts (also in the command palette)", single: true },
];

export const PLAYER_SHORTCUTS: readonly Shortcut[] = [
  { keys: ["Space"], action: "Play or pause", single: true },
  { keys: ["←", "→"], action: "Back 15 s, forward 30 s", single: true },
  { keys: ["Shift", "←", "→"], action: "Previous or next chapter", single: true },
  { keys: ["[", "]"], action: "Slower or faster by 0.1×", single: true },
  { keys: ["↑", "↓"], action: "Volume, while the player has focus", single: true },
  { keys: ["m"], action: "Mute or unmute", single: true },
];

function ShortcutList({
  shortcuts,
  singleKeys,
}: {
  shortcuts: readonly Shortcut[];
  /** Whether single-key shortcuts are on; the ones that are off are dimmed. */
  singleKeys: boolean;
}) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] items-center gap-x-6 gap-y-3 text-sm">
      {shortcuts.map((shortcut) => (
        <div
          key={shortcut.action}
          className={cn("contents", !singleKeys && shortcut.single && "*:opacity-50")}
        >
          <dt className="flex items-center gap-1">
            {shortcut.keys.map((key) => (
              <kbd
                key={key}
                className="rounded border bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground"
              >
                {key}
              </kbd>
            ))}
          </dt>
          <dd className="text-muted-foreground">{shortcut.action}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The `?` sheet listing the keyboard shortcuts, with the switch for every single-key one
 * (`/`, `?` and the player's keys): off for screen reader or speech input users whose keys
 * would trigger them (WCAG 2.1.4). ⌘K, Esc and the command palette's entry stay.
 */
export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const singleKeys = usePlayerSelector((state) => state.shortcuts);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            They work anywhere outside a field or a control that uses the key itself.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center justify-between gap-4">
          <Label htmlFor="single-key-shortcuts" className="font-normal text-muted-foreground">
            Single-key shortcuts
          </Label>
          <Switch id="single-key-shortcuts" checked={singleKeys} onCheckedChange={setShortcuts} />
        </div>
        <ShortcutList shortcuts={SHORTCUTS} singleKeys={singleKeys} />
        <section
          aria-labelledby="player-shortcuts-heading"
          className="flex flex-col gap-3 border-t pt-4"
        >
          <h3 id="player-shortcuts-heading" className="text-sm font-medium">
            Player
          </h3>
          <ShortcutList shortcuts={PLAYER_SHORTCUTS} singleKeys={singleKeys} />
        </section>
      </DialogContent>
    </Dialog>
  );
}
