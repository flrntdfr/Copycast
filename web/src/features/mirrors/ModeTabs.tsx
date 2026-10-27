import type { ReactNode } from "react";

import type { BackfillMode } from "@/api/types";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** The two policies offered as tabs; the Archive tab covers `all` (backfill on) and `new`. */
export const POLICY_TABS = ["automatic", "archive"] as const;
type PolicyTab = (typeof POLICY_TABS)[number];

/** Modes no longer offered as tabs: kept for agents and the Mirrors already using them. */
export const LEGACY_MODES = [
  "rolling",
  "selection",
  "latest",
] as const satisfies readonly BackfillMode[];
export type LegacyMode = (typeof LEGACY_MODES)[number];

export function isLegacyMode(mode: BackfillMode): mode is LegacyMode {
  return (LEGACY_MODES as readonly BackfillMode[]).includes(mode);
}

export const MODE_LABELS: Record<BackfillMode, string> = {
  all: "Archive",
  new: "Archive, new only",
  rolling: "Rolling N",
  automatic: "Ephemeral",
  selection: "Selection",
  latest: "Latest N",
};

const TAB_LABELS: Record<PolicyTab | LegacyMode, string> = {
  automatic: "Ephemeral",
  archive: "Archive",
  rolling: "Rolling N",
  selection: "Selection",
  latest: "Latest N",
};

export const MODE_HINTS: Record<PolicyTab | LegacyMode, string> = {
  automatic:
    "Nothing is downloaded ahead of time. The feed lists every item; an Episode is downloaded when your podcast app first asks for it (the app waits up to two minutes) and is deleted after the retention.",
  archive:
    "Every new episode is downloaded as it arrives and kept. Nothing is ever deleted on its own.",
  rolling:
    "Only the newest N stay archived. Older Episodes are deleted as newer ones arrive. No longer offered: switch to Ephemeral or Archive to leave it.",
  selection:
    "Episode numbers such as “1-42, 180”. Leave empty to pick from the Catalog later. No longer offered: switch to Ephemeral or Archive to leave it.",
  latest:
    "The newest N at creation, then every new one. No longer offered: switch to Ephemeral or Archive to leave it.",
};

function tabOf(mode: BackfillMode): PolicyTab | LegacyMode {
  return mode === "all" || mode === "new" ? "archive" : mode;
}

/**
 * The policy as tabs, Ephemeral and Archive, with the mode's own settings under each.
 * Archive's Backfill switch picks `all` (every episode already published too) or `new`;
 * choosing the Archive tab starts with it off.
 * `legacy` keeps a tab for a Mirror still on a mode no longer offered.
 * `children` renders the extra settings for the active mode.
 */
export function ModeTabs({
  value,
  onValueChange,
  total,
  legacy,
  idPrefix = "policy",
  children,
}: {
  value: BackfillMode;
  onValueChange: (mode: BackfillMode) => void;
  /** Items the Source lists, shown next to the Backfill switch. */
  total?: number | null;
  /** A mode no longer offered that this Mirror uses: shown as a third tab. */
  legacy?: LegacyMode;
  idPrefix?: string;
  children?: (mode: BackfillMode) => ReactNode;
}) {
  const tabs: (PolicyTab | LegacyMode)[] = legacy ? [...POLICY_TABS, legacy] : [...POLICY_TABS];
  const backfillId = `${idPrefix}-backfill`;
  return (
    <Tabs
      value={tabOf(value)}
      onValueChange={(next) => onValueChange(next === "archive" ? "new" : (next as BackfillMode))}
    >
      <TabsList
        aria-label="Policy"
        className="w-full group-data-[orientation=horizontal]/tabs:h-12 md:w-fit md:group-data-[orientation=horizontal]/tabs:h-9"
      >
        {tabs.map((tab) => (
          <TabsTrigger key={tab} value={tab} data-testid={`mode-${tab}`}>
            {TAB_LABELS[tab]}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((tab) => (
        <TabsContent key={tab} value={tab} className="space-y-3 rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">{MODE_HINTS[tab]}</p>
          {tab === "archive" ? (
            <div className="flex items-start gap-3">
              <Switch
                id={backfillId}
                checked={value === "all"}
                onCheckedChange={(on) => onValueChange(on ? "all" : "new")}
              />
              <div className="space-y-0.5">
                <Label htmlFor={backfillId}>
                  Backfill
                  {total != null ? (
                    <span className="font-normal text-muted-foreground">
                      {" "}
                      ({total.toLocaleString()} listed)
                    </span>
                  ) : null}
                </Label>
                <p className="text-xs text-muted-foreground">
                  {value === "all"
                    ? "Also downloads every episode already published, from the first one."
                    : "Off: only what the Source publishes from now on."}
                </p>
              </div>
            </div>
          ) : null}
          {children?.(value)}
        </TabsContent>
      ))}
    </Tabs>
  );
}
