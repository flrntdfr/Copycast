import { Loader2, Save, SlidersHorizontal } from "lucide-react";
import { useState, type FormEvent } from "react";

import { $api } from "@/api/client";
import type { BackfillMode, BackfillRequest, MirrorDefaults } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSetDefaults } from "./mutations";
import { RetentionField, WindowField } from "@/features/mirrors/ModeFields";
import { DEFAULTABLE_MODES, ModeTabs } from "@/features/mirrors/ModeTabs";
import { minutesToSeconds, secondsToMinutes } from "@/lib/format";

/** The operator defaults every Mirror inherits unless it sets its own value. */
export function DefaultsCard() {
  const defaults = $api.useQuery("get", "/api/settings/defaults");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <SlidersHorizontal className="size-4" aria-hidden /> Defaults for every Mirror
        </CardTitle>
        <CardDescription>
          Applied wherever a Mirror leaves the value empty; each Mirror’s Settings tab can override
          them and shows when it does.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {defaults.isPending ? (
          <p className="text-sm text-muted-foreground">…</p>
        ) : defaults.isError ? (
          <p className="text-sm text-destructive">The defaults could not be loaded.</p>
        ) : (
          <DefaultsForm key={JSON.stringify(defaults.data)} stored={defaults.data} />
        )}
      </CardContent>
    </Card>
  );
}

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
      return { mode: "automatic", retention_days: asCount(retention) };
    case "all":
      return { mode: "all" };
    default:
      return null;
  }
}

/** Remounted (by key) whenever the stored defaults change, so the fields start from them. */
function DefaultsForm({ stored }: { stored: MirrorDefaults }) {
  const save = useSetDefaults();
  const [language, setLanguage] = useState(stored.language ?? "");
  const [minutes, setMinutes] = useState(
    stored.min_duration_seconds ? String(secondsToMinutes(stored.min_duration_seconds)) : "",
  );
  const storedPolicy = stored.backfill;
  const [mode, setMode] = useState<BackfillMode>(
    storedPolicy && storedPolicy.mode !== "selection" && storedPolicy.mode !== "latest"
      ? storedPolicy.mode
      : "automatic",
  );
  const [latestN, setLatestN] = useState(String(storedPolicy?.latest_n ?? 10));
  const [retention, setRetention] = useState(
    storedPolicy?.mode === "automatic"
      ? (storedPolicy.retention_days?.toString() ?? "")
      : String(storedPolicy?.retention_days ?? 7),
  );
  const [intervalHours, setIntervalHours] = useState(String(stored.refresh_interval_hours ?? 24));
  const interval = Number(intervalHours);
  const intervalOk = Number.isInteger(interval) && interval >= 1 && interval <= 720;
  const policy = policyOf(mode, latestN, retention);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!policy || !intervalOk) return;
    const body: MirrorDefaults = {
      language: language.trim() || null,
      min_duration_seconds: minutesToSeconds(minutes),
      backfill: policy,
      refresh_interval_hours: interval,
    };
    save.mutate({ body });
  };
  return (
    <>
      <form onSubmit={submit} className="space-y-4" aria-label="Mirror defaults">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="defaults-language">Metadata language</Label>
            <Input
              id="defaults-language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
              placeholder="fr, pt-BR… empty for YouTube’s English"
              maxLength={16}
            />
            <p className="text-xs text-muted-foreground">
              YouTube translates titles to English unless asked for a language.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="defaults-min-minutes">Minimum length (minutes)</Label>
            <Input
              id="defaults-min-minutes"
              type="number"
              min={1}
              inputMode="numeric"
              value={minutes}
              onChange={(event) => setMinutes(event.target.value)}
              placeholder="empty: archive everything"
            />
            <p className="text-xs text-muted-foreground">
              Shorter items stay Available and are never archived automatically; keeps Shorts out.
            </p>
          </div>
        </div>
        <div className="space-y-1.5 sm:max-w-xs">
          <Label htmlFor="defaults-refresh-hours">Refresh every (hours)</Label>
          <Input
            id="defaults-refresh-hours"
            type="number"
            min={1}
            max={720}
            inputMode="numeric"
            value={intervalHours}
            onChange={(event) => setIntervalHours(event.target.value)}
            aria-invalid={!intervalOk}
          />
          <p className="text-xs text-muted-foreground">
            How often every Mirror checks its Source for new items; a Mirror can set its own. A
            fetch of a Mirror Feed by a podcast app refreshes it too.
          </p>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Policy for new Mirrors</legend>
          <p className="text-xs text-muted-foreground">
            What the Add Source wizard and agents start from; each Mirror can still choose.
          </p>
          <ModeTabs value={mode} onValueChange={setMode} modes={DEFAULTABLE_MODES}>
            {(active) =>
              active === "rolling" ? (
                <WindowField
                  id="defaults-latest-n"
                  label="Keep the newest"
                  error={policy ? undefined : "How many to keep?"}
                  inputProps={{
                    value: latestN,
                    onChange: (event) => setLatestN(event.target.value),
                    inputMode: "numeric",
                  }}
                />
              ) : active === "automatic" ? (
                <RetentionField
                  id="defaults-retention-days"
                  inputProps={{
                    value: retention,
                    onChange: (event) => setRetention(event.target.value),
                    inputMode: "numeric",
                  }}
                />
              ) : null
            }
          </ModeTabs>
        </fieldset>
        <Button type="submit" disabled={save.isPending || !policy || !intervalOk}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Save defaults
        </Button>
      </form>
    </>
  );
}
