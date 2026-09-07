import { Loader2, Save, SlidersHorizontal } from "lucide-react";
import { useState, type FormEvent } from "react";

import { $api } from "@/api/client";
import type { MirrorDefaults } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSetDefaults } from "./mutations";
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

/** Remounted (by key) whenever the stored defaults change, so the fields start from them. */
function DefaultsForm({ stored }: { stored: MirrorDefaults }) {
  const save = useSetDefaults();
  const [language, setLanguage] = useState(stored.language ?? "");
  const [minutes, setMinutes] = useState(
    stored.min_duration_seconds ? String(secondsToMinutes(stored.min_duration_seconds)) : "",
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: MirrorDefaults = {
      language: language.trim() || null,
      min_duration_seconds: minutesToSeconds(minutes),
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
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Save defaults
        </Button>
      </form>
    </>
  );
}
