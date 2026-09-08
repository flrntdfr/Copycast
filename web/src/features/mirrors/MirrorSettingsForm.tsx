import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";

import { Link } from "@tanstack/react-router";

import { $api, ApiProblem, describeProblem } from "@/api/client";
import type { MirrorChangePreview, MirrorRead, MirrorUpdate } from "@/api/types";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { RangeInput } from "@/features/catalog/RangeInput";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { EngineOptionsEditor } from "./EngineOptionsEditor";
import { MODE_LABELS, ModeTabs } from "./ModeTabs";
import {
  isEmptyUpdate,
  mirrorSettingsSchema,
  retentionSummary,
  settingsDefaults,
  toMirrorUpdate,
  type MirrorSettingsInput,
  type MirrorSettingsValues,
} from "./settings-form";
import { formatBytes, secondsToMinutes } from "@/lib/format";
import { count } from "@/lib/labels";

/** Follow, Backfill as mode tabs (previewed, confirmed when it deletes), Source URL, Engine options, overrides and the danger zone. */
export function MirrorSettingsForm({ mirror }: { mirror: MirrorRead }) {
  const invalidate = useInvalidateFeed();
  const [rejectedKeys, setRejectedKeys] = useState<string[]>([]);
  const [rejectedScope, setRejectedScope] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const form = useForm<MirrorSettingsInput, unknown, MirrorSettingsValues>({
    resolver: zodResolver(mirrorSettingsSchema),
    defaultValues: settingsDefaults(mirror),
    mode: "onBlur",
  });
  const mode = useWatch({ control: form.control, name: "mode" });
  const retentionDays = useWatch({ control: form.control, name: "retention_days" });
  const title = useWatch({ control: form.control, name: "title" });
  const sourceUrl = useWatch({ control: form.control, name: "source_url" });
  const retargeting = sourceUrl.trim() !== mirror.source_url;
  const defaults = $api.useQuery("get", "/api/settings/defaults");
  const language = useWatch({ control: form.control, name: "language" });
  const minMinutes = useWatch({ control: form.control, name: "min_duration_minutes" });
  const refreshHours = useWatch({ control: form.control, name: "refresh_interval_hours" });
  const globalHours = defaults.data?.refresh_interval_hours ?? null;
  const globalLanguage = defaults.data?.language ?? null;
  const globalMinutes = defaults.data?.min_duration_seconds
    ? secondsToMinutes(defaults.data.min_duration_seconds)
    : null;

  // A fresh MirrorRead (after save, or an SSE `feed` event) becomes the new baseline unless the form is dirty.
  useEffect(() => {
    if (!form.formState.isDirty) form.reset(settingsDefaults(mirror));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mirror]);

  const update = $api.useMutation("patch", "/api/mirrors/{feed_id}", {
    meta: { silent: true },
    onSuccess: (updated) => {
      setRejectedKeys([]);
      setRejectedScope(null);
      invalidate(updated.id);
      form.reset(settingsDefaults(updated));
      toast.success("Settings saved");
    },
    onError: (error) => {
      if (ApiProblem.is(error) && error.slug === "engine-option-rejected") {
        setRejectedKeys(error.rejectedKeys);
        setRejectedScope(error.extensions.scope ?? null);
        form.setError("engine_options", { type: "server", message: "Some options were rejected" });
        return;
      }
      if (ApiProblem.is(error) && error.slug === "feed-exists") {
        form.setError("source_url", {
          type: "server",
          message: "Another Mirror already tracks this Source",
        });
        return;
      }
      if (ApiProblem.is(error) && error.slug === "source-kind-change") {
        form.setError("source_url", {
          type: "server",
          message: "The new Source is of another kind (RSS vs Engine); create a new Mirror instead",
        });
        return;
      }
      const { title, description } = describeProblem(error);
      toast.error(title, { description });
    },
  });

  const preview = $api.useMutation("post", "/api/mirrors/{feed_id}/preview", {
    meta: { silent: true },
  });
  const [pending, setPending] = useState<{
    body: MirrorUpdate;
    preview: MirrorChangePreview;
  } | null>(null);

  const submit = form.handleSubmit(async (values) => {
    const body = toMirrorUpdate(mirror, values);
    if (isEmptyUpdate(body)) {
      toast.info("Nothing changed");
      return;
    }
    setRejectedKeys([]);
    // A policy change is previewed first: when it would delete archived Episodes, ask.
    if (body.backfill) {
      try {
        const result = await preview.mutateAsync({
          params: { path: { feed_id: mirror.id } },
          body,
        });
        if (result.would_delete_count > 0) {
          setPending({ body, preview: result });
          return;
        }
      } catch (error) {
        const { title, description } = describeProblem(error);
        toast.error(title, { description });
        return;
      }
    }
    update.mutate({ params: { path: { feed_id: mirror.id } }, body });
  });

  const errors = form.formState.errors;

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="max-w-2xl space-y-6" aria-label="Mirror settings">
        <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <div>
            <Label htmlFor="settings-follow" className="font-medium">
              Follow
            </Label>
            <p className="text-xs text-muted-foreground">
              Archive new items automatically after each Refresh; a fetch of the Mirror Feed also
              triggers one.
            </p>
          </div>
          <Controller
            control={form.control}
            name="follow"
            render={({ field }) => (
              <Switch id="settings-follow" checked={field.value} onCheckedChange={field.onChange} />
            )}
          />
        </div>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Backfill</legend>
          <p className="text-xs text-muted-foreground">
            Everything and Latest N apply at the next Refresh; Rolling N applies now (you are asked
            first when it deletes); a Selection with numbers archives those now.
          </p>
          <Controller
            control={form.control}
            name="mode"
            render={({ field }) => (
              <ModeTabs
                value={field.value}
                onValueChange={field.onChange}
                legacyLatest={mirror.backfill.mode === "latest"}
              >
                {(active) =>
                  active === "rolling" || active === "latest" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Label htmlFor="settings-latest-n">
                        {active === "rolling" ? "Keep the newest" : "How many"}
                      </Label>
                      <Input
                        id="settings-latest-n"
                        type="number"
                        min={1}
                        className="w-28"
                        {...form.register("latest_n", { valueAsNumber: true })}
                        aria-invalid={!!errors.latest_n}
                      />
                      <span className="text-sm text-muted-foreground">items</span>
                      {errors.latest_n ? (
                        <p className="text-xs text-destructive">{errors.latest_n.message}</p>
                      ) : null}
                    </div>
                  ) : active === "automatic" ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Label htmlFor="settings-retention-days">Keep downloaded for</Label>
                      <Input
                        id="settings-retention-days"
                        type="number"
                        min={1}
                        className="w-28"
                        placeholder="forever"
                        {...form.register("retention_days", { valueAsNumber: true })}
                        aria-invalid={!!errors.retention_days}
                      />
                      <span className="text-sm text-muted-foreground">
                        days after the last download (empty keeps forever)
                      </span>
                      {errors.retention_days ? (
                        <p className="text-xs text-destructive">{errors.retention_days.message}</p>
                      ) : null}
                    </div>
                  ) : active === "selection" ? (
                    <div className="space-y-2">
                      {mirror.backfill.mode === "selection" && mirror.selection ? (
                        <p className="text-xs text-muted-foreground">
                          {mirror.selection.count.toLocaleString()} selected so far
                        </p>
                      ) : null}
                      <Controller
                        control={form.control}
                        name="selection"
                        render={({ field: selection, fieldState }) => (
                          <RangeInput
                            id="settings-selection"
                            value={selection.value ?? ""}
                            onChange={selection.onChange}
                            error={fieldState.error?.message}
                            placeholder="Numbers to archive now, e.g. 1-42, 180 (optional)"
                          />
                        )}
                      />
                    </div>
                  ) : null
                }
              </ModeTabs>
            )}
          />
        </fieldset>

        <OverrideField
          id="settings-title"
          label="Title"
          own={title.trim()}
          global={mirror.source_title}
          loaded
          onReset={() => form.setValue("title", "", { shouldDirty: true })}
          error={errors.title?.message}
          hint={{
            overriding:
              "Shown in the UI and the Mirror Feed; the Source's title is kept underneath.",
            following: `Using the Source's title: ${mirror.source_title}.`,
          }}
          resetLabel="Use Source title"
        >
          <Input
            id="settings-title"
            placeholder={mirror.source_title}
            maxLength={512}
            {...form.register("title")}
            aria-invalid={!!errors.title}
          />
        </OverrideField>

        <div className="space-y-1.5">
          <Label htmlFor="settings-source-url">Source URL</Label>
          <Input
            id="settings-source-url"
            inputMode="url"
            autoComplete="off"
            {...form.register("source_url")}
            aria-invalid={!!errors.source_url}
            aria-describedby="settings-source-url-hint"
          />
          <p
            id="settings-source-url-hint"
            className={
              errors.source_url ? "text-xs text-destructive" : "text-xs text-muted-foreground"
            }
          >
            {errors.source_url?.message ??
              (retargeting
                ? "Retargeting keeps every archived Episode; the new Source must be of the same kind and not mirrored already."
                : "Change it when the podcast moved; the Mirror keeps its id, feed URL and archive.")}
          </p>
        </div>

        <Controller
          control={form.control}
          name="engine_options"
          render={({ field, fieldState }) => (
            <EngineOptionsEditor
              id="settings-engine-options"
              value={field.value}
              onChange={(next) => {
                setRejectedKeys([]);
                field.onChange(next);
              }}
              error={fieldState.error?.message}
              rejectedKeys={rejectedKeys}
              scope={rejectedScope}
            />
          )}
        />

        <fieldset className="space-y-3 rounded-lg border p-3">
          <legend className="px-1 text-sm font-medium">Overrides</legend>
          <p className="text-xs text-muted-foreground">
            Empty means the value from{" "}
            <Link to="/settings" className="underline">
              Settings
            </Link>
            ; a value here applies to this Mirror only.
          </p>
          <OverrideField
            id="settings-language"
            label="Metadata language"
            own={language.trim()}
            global={globalLanguage}
            loaded={defaults.isSuccess}
            onReset={() => form.setValue("language", "", { shouldDirty: true })}
            error={errors.language?.message}
          >
            <Input
              id="settings-language"
              placeholder={globalLanguage ?? "fr, pt-BR…"}
              maxLength={16}
              {...form.register("language")}
              aria-invalid={!!errors.language}
            />
          </OverrideField>
          <OverrideField
            id="settings-min-minutes"
            label="Minimum length (minutes)"
            own={
              typeof minMinutes === "number" && !Number.isNaN(minMinutes) ? String(minMinutes) : ""
            }
            global={globalMinutes == null ? null : String(globalMinutes)}
            loaded={defaults.isSuccess}
            onReset={() => form.setValue("min_duration_minutes", undefined, { shouldDirty: true })}
            error={errors.min_duration_minutes?.message}
          >
            <Input
              id="settings-min-minutes"
              type="number"
              min={1}
              inputMode="numeric"
              placeholder={globalMinutes == null ? "everything" : String(globalMinutes)}
              {...form.register("min_duration_minutes", { valueAsNumber: true })}
              aria-invalid={!!errors.min_duration_minutes}
            />
          </OverrideField>
          <OverrideField
            id="settings-refresh-hours"
            label="Refresh every (hours)"
            own={
              typeof refreshHours === "number" && !Number.isNaN(refreshHours)
                ? String(refreshHours)
                : ""
            }
            global={globalHours == null ? null : String(globalHours)}
            loaded={defaults.isSuccess}
            onReset={() =>
              form.setValue("refresh_interval_hours", undefined, { shouldDirty: true })
            }
            error={errors.refresh_interval_hours?.message}
          >
            <Input
              id="settings-refresh-hours"
              type="number"
              min={1}
              max={720}
              inputMode="numeric"
              placeholder={globalHours == null ? "24" : String(globalHours)}
              {...form.register("refresh_interval_hours", { valueAsNumber: true })}
              aria-invalid={!!errors.refresh_interval_hours}
            />
          </OverrideField>
        </fieldset>

        <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
          <div>
            <Label htmlFor="settings-sync" className="font-medium">
              Stay in sync with the Source
            </Label>
            <p className="text-xs text-muted-foreground">
              Delete an Episode when its item leaves the Source, instead of keeping it Delisted.
              Meant for a YouTube playlist you curate: remove a video there, it goes here.
            </p>
          </div>
          <Controller
            control={form.control}
            name="sync_deletions"
            render={({ field }) => (
              <Switch id="settings-sync" checked={field.value} onCheckedChange={field.onChange} />
            )}
          />
        </div>

        <div className="space-y-1.5">
          <span className="text-sm font-medium">Retention</span>
          <p className="text-sm text-muted-foreground">
            {retentionSummary(
              mode,
              typeof retentionDays === "number" && !Number.isNaN(retentionDays)
                ? retentionDays
                : undefined,
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" disabled={update.isPending || preview.isPending}>
            {update.isPending || preview.isPending ? <Loader2 className="animate-spin" /> : null}
            Save changes
          </Button>
          <Button
            type="button"
            variant="ghost"
            disabled={!form.formState.isDirty || update.isPending}
            onClick={() => {
              setRejectedKeys([]);
              form.reset(settingsDefaults(mirror));
            }}
          >
            Reset
          </Button>
        </div>
      </form>

      <section
        aria-labelledby="danger-zone"
        className="max-w-2xl rounded-lg border border-destructive/40 p-4"
      >
        <h2 id="danger-zone" className="text-sm font-medium text-destructive">
          Danger zone
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Deleting the Mirror removes every archived Episode and its published feed. Copycast may
          hold the only copy.
        </p>
        <Button variant="destructive" size="sm" className="mt-3" onClick={() => setDeleting(true)}>
          <Trash2 /> Delete Mirror…
        </Button>
        <DeleteFeedDialog feed={mirror} open={deleting} onOpenChange={setDeleting} />
      </section>

      <AlertDialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete {count(pending?.preview.would_delete_count ?? 0, "archived Episode")}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pending ? describeChange(pending.body, pending.preview) : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={update.isPending}>Keep them</AlertDialogCancel>
            <AlertDialogAction
              disabled={update.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (!pending) return;
                update.mutate(
                  { params: { path: { feed_id: mirror.id } }, body: pending.body },
                  { onSettled: () => setPending(null) },
                );
              }}
            >
              {update.isPending ? <Loader2 className="animate-spin" /> : null}
              Delete and switch
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** The confirmation text: what the new policy keeps, what goes now, what it archives next. */
export function describeChange(body: MirrorUpdate, preview: MirrorChangePreview): string {
  const backfill = body.backfill;
  const target =
    backfill?.mode === "rolling"
      ? `Rolling ${backfill.latest_n ?? 1} keeps only the newest ${count(backfill.latest_n ?? 1, "Episode")}.`
      : `Switching to ${MODE_LABELS[backfill?.mode ?? "all"]}.`;
  const deletes = `${count(preview.would_delete_count, "archived Episode")} (${formatBytes(preview.would_delete_bytes)}) will be deleted now; they leave Tombstones and can be archived again on purpose.`;
  const archives =
    preview.would_archive_count > 0
      ? ` ${count(preview.would_archive_count, "Episode")} will be archived.`
      : "";
  return `${target} ${deletes}${archives}`;
}

/** A setting a Mirror may override: the field, the global value it diverges from, and a reset. */
function OverrideField({
  id,
  label,
  own,
  global,
  loaded,
  onReset,
  error,
  hint,
  resetLabel = "Use default",
  children,
}: {
  id: string;
  label: string;
  own: string;
  global: string | null;
  loaded: boolean;
  onReset: () => void;
  error?: string;
  /** Replaces the default wording under the field. */
  hint?: { overriding: string; following: string };
  resetLabel?: string;
  children: React.ReactNode;
}) {
  const overriding = own !== "";
  const diverges = overriding && own !== (global ?? "");
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Label htmlFor={id}>{label}</Label>
        {loaded && diverges ? (
          <Badge variant="secondary" data-testid={`${id}-diverges`}>
            Overrides the default{global ? ` (${global})` : " (none)"}
          </Badge>
        ) : null}
        {loaded && overriding && !diverges ? (
          <Badge variant="outline">Same as the default</Badge>
        ) : null}
      </div>
      <div className="flex gap-2">
        {children}
        {overriding ? (
          <Button type="button" variant="outline" onClick={onReset}>
            {resetLabel}
          </Button>
        ) : null}
      </div>
      <p className={error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
        {error ??
          (loaded
            ? overriding
              ? (hint?.overriding ?? "This Mirror ignores the default.")
              : (hint?.following ?? `Using the default: ${global ?? "none"}.`)
            : "\u2026")}
      </p>
    </div>
  );
}
