import type { ReactNode } from "react";

import type { BackfillMode } from "@/api/types";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** The modes offered as tabs; `latest` only shows on a Mirror that already uses it. */
export const OFFERED_MODES = [
  "all",
  "rolling",
  "automatic",
  "selection",
] as const satisfies readonly BackfillMode[];

export const MODE_LABELS: Record<BackfillMode, string> = {
  all: "Everything",
  rolling: "Rolling N",
  automatic: "Automatic",
  selection: "Selection",
  latest: "Latest N",
};

export const MODE_HINTS: Record<BackfillMode, string> = {
  all: "Every item the Source lists, and every new one as it arrives. Nothing is ever deleted.",
  rolling:
    "Only the newest N stay archived. Older Episodes are deleted as newer ones arrive; they can be archived again on purpose.",
  automatic:
    "Nothing is downloaded ahead of time. The feed lists every item; an Episode is downloaded when your podcast app first asks for it (the app waits up to two minutes) and expires after the retention.",
  selection: "Episode numbers such as “1-42, 180”. Leave empty to pick from the Catalog later.",
  latest:
    "The newest N at creation, then whatever Follow brings. No longer offered for new Mirrors; switch to Rolling N to keep only the newest.",
};

/**
 * Backfill as tabs: one per mode, the mode's own settings under it.
 * `children` renders those settings for the active mode.
 */
export function ModeTabs({
  value,
  onValueChange,
  total,
  legacyLatest = false,
  children,
}: {
  value: BackfillMode;
  onValueChange: (mode: BackfillMode) => void;
  /** Items the Source lists, shown on the Everything tab. */
  total?: number | null;
  /** Keep a Latest N tab for a Mirror created with it. */
  legacyLatest?: boolean;
  children: (mode: BackfillMode) => ReactNode;
}) {
  const modes: BackfillMode[] = legacyLatest ? [...OFFERED_MODES, "latest"] : [...OFFERED_MODES];
  return (
    <Tabs value={value} onValueChange={(next) => onValueChange(next as BackfillMode)}>
      <TabsList aria-label="Backfill" className="flex-wrap">
        {modes.map((mode) => (
          <TabsTrigger key={mode} value={mode} data-testid={`mode-${mode}`}>
            {mode === "all" && total != null
              ? `${MODE_LABELS.all} (${total.toLocaleString()})`
              : MODE_LABELS[mode]}
          </TabsTrigger>
        ))}
      </TabsList>
      {modes.map((mode) => (
        <TabsContent key={mode} value={mode} className="space-y-3 rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">{MODE_HINTS[mode]}</p>
          {children(mode)}
        </TabsContent>
      ))}
    </Tabs>
  );
}
