import { Check, CircleAlert, Loader2, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { $api } from "@/api/client";
import type { BackfillMode, BackfillRequest, MirrorDefaults } from "@/api/types";
import { SectionHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ModeTabs, isLegacyMode } from "@/features/mirrors/ModeTabs";
import { minutesToSeconds, secondsToMinutes } from "@/lib/format";
import { HelpTip } from "./HelpTip";
import { useSetDefaults } from "./mutations";

const SAVE_DELAY_MS = 600;
const MAX_HOURS = 720;

/** The default `BackfillRequest` for the chosen mode, or null while a count is missing. */
export function policyOf(
  mode: BackfillMode,
  latestN: string,
  retention: string,
): BackfillRequest | null {
  const asCount = (text: string): number | null => {
    const value = Number(text);
    return text.trim() !== "" && Number.isInteger(value) && value >= 1 ? value : null;
  };
  switch (mode) {
    case "rolling": {
      const n = asCount(latestN);
      return n ? { mode: "rolling", latest_n: n } : null;
    }
    case "automatic":
      if (retention.trim() !== "" && asCount(retention) === null) return null;
      return { mode: "automatic", retention_days: asCount(retention) };
    case "all":
    case "new":
      return { mode };
    default:
      return null;
  }
}

/** The operator defaults every Mirror inherits, saved as they change. */
export function MirrorDefaultsSection() {
  const defaults = $api.useQuery("get", "/api/settings/defaults");
  const save = useSetDefaults();
  return (
    <section aria-labelledby="mirror-defaults-heading">
      <SectionHeader
        id="mirror-defaults-heading"
        title={
          <span className="inline-flex items-center gap-1.5">
            Mirrors
            <HelpTip label="About the Mirror defaults">
              Every Mirror follows these unless its own Settings tab sets a value; new Mirrors, from
              the Add Source dialog or an agent, start from the policy.
            </HelpTip>
          </span>
        }
        actions={
          <SaveStatus pending={save.isPending} error={save.isError} saved={save.isSuccess} />
        }
      />
      {defaults.isPending ? (
        <Skeleton className="h-80" />
      ) : defaults.isError ? (
        <p className="text-sm text-destructive">The defaults could not be loaded.</p>
      ) : (
        // Initialised once: a save updates the cached defaults and must not reset the fields.
        <DefaultsForm stored={defaults.data} onSave={save.mutate} />
      )}
    </section>
  );
}

function SaveStatus({
  pending,
  error,
  saved,
}: {
  pending: boolean;
  error: boolean;
  saved: boolean;
}) {
  const [text, icon] = pending
    ? ["Saving…", <Loader2 key="i" className="size-3.5 animate-spin" />]
    : error
      ? ["Not saved", <CircleAlert key="i" className="size-3.5 text-destructive" />]
      : saved
        ? ["Saved", <Check key="i" className="size-3.5 text-success" />]
        : [null, null];
  return (
    <span
      className="inline-flex items-center gap-1 text-xs text-muted-foreground"
      aria-live="polite"
      data-testid="defaults-status"
    >
      {icon}
      {text}
    </span>
  );
}

function DefaultsForm({
  stored,
  onSave,
}: {
  stored: MirrorDefaults;
  /** Stable across renders (a TanStack `mutate`). */
  onSave: (variables: { body: MirrorDefaults }) => void;
}) {
  const [language, setLanguage] = useState(stored.language ?? "");
  const [minutes, setMinutes] = useState(
    stored.min_duration_seconds ? String(secondsToMinutes(stored.min_duration_seconds)) : "",
  );
  const [hours, setHours] = useState(String(stored.refresh_interval_hours ?? 24));
  const storedPolicy = stored.backfill;
  const [mode, setMode] = useState<BackfillMode>(storedPolicy?.mode ?? "automatic");
  const [legacy] = useState(() =>
    storedPolicy && isLegacyMode(storedPolicy.mode) ? storedPolicy.mode : undefined,
  );
  const [latestN, setLatestN] = useState(String(storedPolicy?.latest_n ?? 10));
  const [retention, setRetention] = useState(
    storedPolicy?.mode === "automatic"
      ? (storedPolicy.retention_days?.toString() ?? "")
      : String(storedPolicy?.retention_days ?? 7),
  );

  const interval = Number(hours);
  const intervalOk = Number.isInteger(interval) && interval >= 1 && interval <= MAX_HOURS;
  const minutesOk = minutes.trim() === "" || minutesToSeconds(minutes) !== null;
  const policy = policyOf(mode, latestN, retention);
  const body: MirrorDefaults | null =
    policy && intervalOk && minutesOk
      ? {
          language: language.trim() || null,
          min_duration_seconds: minutesToSeconds(minutes),
          backfill: policy,
          refresh_interval_hours: interval,
        }
      : null;

  // Save a valid change after a short pause; the stored values are the starting point.
  const json = body ? JSON.stringify(body) : null;
  const lastSaved = useRef<string | null>(null);
  useEffect(() => {
    if (!json) return;
    if (lastSaved.current === null) {
      lastSaved.current = json;
      return;
    }
    if (json === lastSaved.current) return;
    const timer = window.setTimeout(() => {
      lastSaved.current = json;
      onSave({ body: JSON.parse(json) as MirrorDefaults });
    }, SAVE_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [json, onSave]);

  const step = (delta: number) =>
    setHours(String(Math.min(MAX_HOURS, Math.max(1, (intervalOk ? interval : 1) + delta))));

  return (
    <form
      aria-label="Mirror defaults"
      className="space-y-5"
      onSubmit={(event) => event.preventDefault()}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <Label htmlFor="defaults-refresh-hours">Refresh every</Label>{" "}
          <HelpTip label="About the Refresh interval">
            How often every Mirror checks its Source for new items; a Mirror can set its own. A
            podcast app fetching a Mirror Feed refreshes it too.
          </HelpTip>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            size="icon"
            variant="outline"
            className="size-10 md:size-8"
            aria-label="One hour less"
            disabled={intervalOk && interval <= 1}
            onClick={() => step(-1)}
          >
            <Minus />
          </Button>
          <Input
            id="defaults-refresh-hours"
            type="number"
            min={1}
            max={MAX_HOURS}
            inputMode="numeric"
            className="h-10 w-16 text-center tabular-nums md:h-8"
            value={hours}
            onChange={(event) => setHours(event.target.value)}
            aria-invalid={!intervalOk}
          />
          <span className="text-sm text-muted-foreground">h</span>
          <Button
            type="button"
            size="icon"
            variant="outline"
            className="size-10 md:size-8"
            aria-label="One hour more"
            disabled={intervalOk && interval >= MAX_HOURS}
            onClick={() => step(1)}
          >
            <Plus />
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <Label htmlFor="defaults-language">Default language</Label>{" "}
          <HelpTip label="About the default language">
            YouTube translates titles and descriptions to English unless asked for a language: fr,
            pt-BR… Empty keeps YouTube’s.
          </HelpTip>
        </div>
        <Input
          id="defaults-language"
          className="h-10 w-40 md:h-8"
          value={language}
          onChange={(event) => setLanguage(event.target.value)}
          placeholder="fr, pt-BR…"
          maxLength={16}
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <Label htmlFor="defaults-min-minutes">Exclude episodes shorter than</Label>{" "}
          <HelpTip label="About the minimum length">
            Shorter items stay Available and are never archived on their own; keeps YouTube Shorts
            out. Empty archives everything.
          </HelpTip>
        </div>
        <div className="flex items-center gap-1.5">
          <Input
            id="defaults-min-minutes"
            type="number"
            min={1}
            inputMode="numeric"
            className="h-10 w-20 tabular-nums md:h-8"
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
            placeholder="—"
            aria-invalid={!minutesOk}
          />
          <span className="text-sm text-muted-foreground">min</span>
        </div>
      </div>

      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium">Policy</legend>
        <ModeTabs value={mode} onValueChange={setMode} legacy={legacy} idPrefix="defaults">
          {(active) =>
            active === "automatic" ? (
              <div className="flex flex-wrap items-center gap-2">
                <Label htmlFor="defaults-retention-days">Delete after</Label>
                <Input
                  id="defaults-retention-days"
                  type="number"
                  min={1}
                  inputMode="numeric"
                  className="h-10 w-20 tabular-nums md:h-8"
                  placeholder="never"
                  value={retention}
                  onChange={(event) => setRetention(event.target.value)}
                  aria-invalid={!policy}
                />
                <span className="text-sm text-muted-foreground">
                  days without a download (empty never deletes)
                </span>
              </div>
            ) : active === "rolling" ? (
              <div className="flex flex-wrap items-center gap-2">
                <Label htmlFor="defaults-latest-n">Keep the newest</Label>
                <Input
                  id="defaults-latest-n"
                  type="number"
                  min={1}
                  inputMode="numeric"
                  className="h-10 w-20 tabular-nums md:h-8"
                  value={latestN}
                  onChange={(event) => setLatestN(event.target.value)}
                  aria-invalid={!policy}
                />
                <span className="text-sm text-muted-foreground">items</span>
              </div>
            ) : null
          }
        </ModeTabs>
      </fieldset>
    </form>
  );
}
