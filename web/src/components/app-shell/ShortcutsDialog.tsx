import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export const SHORTCUTS: readonly { keys: readonly string[]; action: string }[] = [
  { keys: ["⌘", "K"], action: "Open the command palette (Ctrl+K on Windows and Linux)" },
  { keys: ["/"], action: "Focus the filter of the current table" },
  { keys: ["Esc"], action: "Close dialogs, menus and the palette" },
  { keys: ["Shift", "click"], action: "Select a range of Episodes in a Catalog" },
  { keys: ["?"], action: "Show these shortcuts" },
];

/** The `?` sheet listing the keyboard shortcuts. */
export function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>They work anywhere outside a text field.</DialogDescription>
        </DialogHeader>
        <dl className="grid grid-cols-[max-content_1fr] items-center gap-x-6 gap-y-3 text-sm">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.action} className="contents">
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
      </DialogContent>
    </Dialog>
  );
}
