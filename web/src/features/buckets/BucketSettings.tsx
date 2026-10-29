import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Scissors, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { $api, ApiProblem } from "@/api/client";
import type { BucketRead, BucketUpdate } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { optionalCount } from "@/features/add-source/policy-form";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { useInvalidateFeed } from "@/features/feeds/mutations";
import { PruneDialog } from "./PruneDialog";

export const bucketSettingsSchema = z
  .object({
    name: z.string().trim().min(1, "A name is required").max(80, "At most 80 characters"),
    autoprune: z.boolean(),
    autoprune_days: optionalCount,
  })
  .superRefine((value, ctx) => {
    if (value.autoprune && !value.autoprune_days) {
      ctx.addIssue({
        code: "custom",
        path: ["autoprune_days"],
        message: "How many days after the first download?",
      });
    }
  });

export type BucketSettingsInput = z.input<typeof bucketSettingsSchema>;
export type BucketSettingsValues = z.output<typeof bucketSettingsSchema>;

export function bucketDefaults(bucket: BucketRead): BucketSettingsInput {
  return {
    name: bucket.name,
    autoprune: bucket.autoprune_days != null,
    autoprune_days: bucket.autoprune_days ?? 30,
  };
}

/** Only changed fields; `autoprune_days: null` switches autoprune off. */
export function toBucketUpdate(bucket: BucketRead, values: BucketSettingsValues): BucketUpdate {
  const update: BucketUpdate = {};
  if (values.name.trim() !== bucket.name) update.name = values.name.trim();
  const days = values.autoprune ? (values.autoprune_days ?? null) : null;
  if (days !== (bucket.autoprune_days ?? null)) update.autoprune_days = days;
  return update;
}

/** Name, autoprune, Prune now… and the Danger zone (shown in `BucketSettingsDialog`). */
export function BucketSettings({ bucket }: { bucket: BucketRead }) {
  const invalidate = useInvalidateFeed();
  const [deleting, setDeleting] = useState(false);
  const [pruning, setPruning] = useState(false);
  const form = useForm<BucketSettingsInput, unknown, BucketSettingsValues>({
    resolver: zodResolver(bucketSettingsSchema),
    defaultValues: bucketDefaults(bucket),
  });
  const autoprune = useWatch({ control: form.control, name: "autoprune" });

  useEffect(() => {
    if (!form.formState.isDirty) form.reset(bucketDefaults(bucket));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bucket]);

  const update = $api.useMutation("patch", "/api/buckets/{bucket_id}", {
    onSuccess: (updated) => {
      invalidate(updated.id);
      form.reset(bucketDefaults(updated));
      toast.success("Bucket settings saved");
    },
    onError: (error) => {
      if (ApiProblem.is(error) && error.slug === "conflict")
        form.setError("name", { type: "server", message: error.detail ?? error.title });
    },
  });

  const submit = form.handleSubmit((values) => {
    const body = toBucketUpdate(bucket, values);
    if (Object.keys(body).length === 0) {
      toast.info("Nothing changed");
      return;
    }
    update.mutate({ params: { path: { bucket_id: bucket.id } }, body });
  });
  const errors = form.formState.errors;

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="max-w-2xl space-y-6" aria-label="Bucket settings">
        <div className="space-y-1.5">
          <Label htmlFor="bucket-name">Name</Label>
          <Input id="bucket-name" {...form.register("name")} aria-invalid={!!errors.name} />
          {errors.name ? <p className="text-xs text-destructive">{errors.name.message}</p> : null}
        </div>
        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label htmlFor="bucket-autoprune" className="font-medium">
                Autoprune
              </Label>
              <p className="text-xs text-muted-foreground">
                Delete Episodes N days after their first download; never-downloaded Episodes are
                kept.
              </p>
            </div>
            <Controller
              control={form.control}
              name="autoprune"
              render={({ field }) => (
                <Switch
                  id="bucket-autoprune"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              )}
            />
          </div>
          {autoprune ? (
            <div className="flex items-center gap-2">
              <Label htmlFor="bucket-autoprune-days" className="sr-only">
                Days after first download
              </Label>
              <Input
                id="bucket-autoprune-days"
                type="number"
                min={1}
                className="w-24"
                {...form.register("autoprune_days", { valueAsNumber: true })}
                aria-invalid={!!errors.autoprune_days}
              />
              <span className="text-sm text-muted-foreground">days after the first download</span>
            </div>
          ) : null}
          {errors.autoprune_days ? (
            <p className="text-xs text-destructive">{errors.autoprune_days.message}</p>
          ) : null}
        </div>
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? <Loader2 className="animate-spin" /> : null}
          Save changes
        </Button>
      </form>
      <section aria-labelledby="bucket-prune" className="max-w-2xl rounded-lg border p-4">
        <h2 id="bucket-prune" className="text-sm font-medium">
          Prune
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Delete downloaded or old Episodes now, with a preview first; their Requests stay.
        </p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => setPruning(true)}>
          <Scissors /> Prune now…
        </Button>
      </section>
      <section
        aria-labelledby="bucket-danger-zone"
        className="max-w-2xl rounded-lg border border-destructive/40 p-4"
      >
        <h2 id="bucket-danger-zone" className="text-sm font-medium text-destructive">
          Danger zone
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Deleting the Bucket removes every archived Episode, its Requests and its feed. The default
          Bucket cannot be deleted.
        </p>
        <Button variant="destructive" size="sm" className="mt-3" onClick={() => setDeleting(true)}>
          <Trash2 /> Delete Bucket…
        </Button>
      </section>
      <PruneDialog bucketId={bucket.id} open={pruning} onOpenChange={setPruning} />
      <DeleteFeedDialog feed={bucket} open={deleting} onOpenChange={setDeleting} />
    </div>
  );
}
