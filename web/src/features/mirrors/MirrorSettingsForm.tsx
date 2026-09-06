import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";

import { $api, ApiProblem, describeProblem } from "@/api/client";
import type { MirrorRead } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { RangeInput } from "@/features/catalog/RangeInput";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { EngineOptionsEditor } from "./EngineOptionsEditor";
import {
  isEmptyUpdate,
  mirrorSettingsSchema,
  settingsDefaults,
  toMirrorUpdate,
  type MirrorSettingsInput,
  type MirrorSettingsValues,
} from "./settings-form";

/** Follow, Backfill, Source URL, Engine options (per-key errors), read-only retention and the danger zone. */
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
  const sourceUrl = useWatch({ control: form.control, name: "source_url" });
  const retargeting = sourceUrl.trim() !== mirror.source_url;

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

  const submit = form.handleSubmit((values) => {
    const body = toMirrorUpdate(mirror, values);
    if (isEmptyUpdate(body)) {
      toast.info("Nothing changed");
      return;
    }
    setRejectedKeys([]);
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
            Changing the policy applies it at the next Refresh; a Selection with numbers archives
            those now.
          </p>
          <Controller
            control={form.control}
            name="mode"
            render={({ field }) => (
              <RadioGroup
                value={field.value}
                onValueChange={field.onChange}
                className="gap-3"
                aria-label="Backfill"
              >
                <div className="flex items-center gap-3">
                  <RadioGroupItem value="all" id="settings-backfill-all" />
                  <Label htmlFor="settings-backfill-all" className="font-normal">
                    Everything
                  </Label>
                </div>
                <div className="flex items-start gap-3">
                  <RadioGroupItem value="latest" id="settings-backfill-latest" className="mt-0.5" />
                  <div className="flex flex-1 flex-col gap-2">
                    <Label htmlFor="settings-backfill-latest" className="font-normal">
                      Latest N
                    </Label>
                    {mode === "latest" ? (
                      <div className="flex items-center gap-2">
                        <Label htmlFor="settings-latest-n" className="sr-only">
                          How many
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
                      </div>
                    ) : null}
                    {errors.latest_n ? (
                      <p className="text-xs text-destructive">{errors.latest_n.message}</p>
                    ) : null}
                  </div>
                </div>
                <div className="flex items-start gap-3">
                  <RadioGroupItem
                    value="selection"
                    id="settings-backfill-selection"
                    className="mt-0.5"
                  />
                  <div className="flex flex-1 flex-col gap-2">
                    <Label htmlFor="settings-backfill-selection" className="font-normal">
                      Selection
                      {mirror.backfill.mode === "selection" && mirror.selection ? (
                        <span className="ml-2 text-xs text-muted-foreground">
                          {mirror.selection.count.toLocaleString()} selected so far
                        </span>
                      ) : null}
                    </Label>
                    {mode === "selection" ? (
                      <Controller
                        control={form.control}
                        name="selection"
                        render={({ field, fieldState }) => (
                          <RangeInput
                            id="settings-selection"
                            value={field.value ?? ""}
                            onChange={field.onChange}
                            error={fieldState.error?.message}
                            placeholder="Numbers to archive now, e.g. 1-42, 180 (optional)"
                          />
                        )}
                      />
                    ) : null}
                  </div>
                </div>
              </RadioGroup>
            )}
          />
        </fieldset>

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

        <div className="space-y-1.5">
          <span className="text-sm font-medium">Retention</span>
          <p className="text-sm text-muted-foreground">
            None. Copycast never deletes from a Mirror on its own.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? <Loader2 className="animate-spin" /> : null}
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
    </div>
  );
}
