import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { $api, ApiProblem } from "@/api/client";
import type { BucketRead, BucketUpdate } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { optionalCount } from "@/features/add-source/policy-form";
import { DeleteFeedDialog } from "./DeleteFeedDialog";
import { useInvalidateFeed } from "./mutations";

const localFeedSettingsSchema = z
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

type LocalFeedSettingsInput = z.input<typeof localFeedSettingsSchema>;
type LocalFeedSettingsValues = z.output<typeof localFeedSettingsSchema>;

function localFeedDefaults(feed: BucketRead): LocalFeedSettingsInput {
  return {
    name: feed.name,
    autoprune: feed.autoprune_days != null,
    autoprune_days: feed.autoprune_days ?? 30,
  };
}

/** Only changed fields; `autoprune_days: null` switches autoprune off. */
function toBucketUpdate(feed: BucketRead, values: LocalFeedSettingsValues): BucketUpdate {
  const update: BucketUpdate = {};
  if (values.name.trim() !== feed.name) update.name = values.name.trim();
  const days = values.autoprune ? (values.autoprune_days ?? null) : null;
  if (days !== (feed.autoprune_days ?? null)) update.autoprune_days = days;
  return update;
}

/** Name, autoprune and the Danger zone of a Local feed. */
function LocalFeedSettings({ feed }: { feed: BucketRead }) {
  const invalidate = useInvalidateFeed();
  const [deleting, setDeleting] = useState(false);
  const form = useForm<LocalFeedSettingsInput, unknown, LocalFeedSettingsValues>({
    resolver: zodResolver(localFeedSettingsSchema),
    defaultValues: localFeedDefaults(feed),
  });
  const autoprune = useWatch({ control: form.control, name: "autoprune" });

  useEffect(() => {
    if (!form.formState.isDirty) form.reset(localFeedDefaults(feed));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed]);

  const update = $api.useMutation("patch", "/api/buckets/{bucket_id}", {
    onSuccess: (updated) => {
      invalidate(updated.id);
      form.reset(localFeedDefaults(updated));
      toast.success("Local feed settings saved");
    },
    onError: (error) => {
      if (ApiProblem.is(error) && error.slug === "conflict")
        form.setError("name", { type: "server", message: error.detail ?? error.title });
    },
  });

  const submit = form.handleSubmit((values) => {
    const body = toBucketUpdate(feed, values);
    if (Object.keys(body).length === 0) {
      toast.info("Nothing changed");
      return;
    }
    update.mutate({ params: { path: { bucket_id: feed.id } }, body });
  });
  const errors = form.formState.errors;

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="space-y-6" aria-label="Local feed settings">
        <div className="space-y-1.5">
          <Label htmlFor="local-feed-name">Name</Label>
          <Input id="local-feed-name" {...form.register("name")} aria-invalid={!!errors.name} />
          {errors.name ? <p className="text-xs text-destructive">{errors.name.message}</p> : null}
        </div>
        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label htmlFor="local-feed-autoprune" className="font-medium">
                Autoprune
              </Label>
              <p className="text-xs text-muted-foreground">
                Delete Episodes N days after their first download; never-downloaded and locked
                Episodes are kept.
              </p>
            </div>
            <Controller
              control={form.control}
              name="autoprune"
              render={({ field }) => (
                <Switch
                  id="local-feed-autoprune"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              )}
            />
          </div>
          {autoprune ? (
            <div className="flex items-center gap-2">
              <Label htmlFor="local-feed-autoprune-days" className="sr-only">
                Days after first download
              </Label>
              <Input
                id="local-feed-autoprune-days"
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
      <section
        aria-labelledby="local-feed-danger-zone"
        className="rounded-lg border border-destructive/40 p-4"
      >
        <h2 id="local-feed-danger-zone" className="text-sm font-medium text-destructive">
          Danger zone
        </h2>
        {feed.is_default ? (
          <p className="mt-1 text-sm text-muted-foreground">
            This is the default Local feed: it can be renamed but never deleted.
          </p>
        ) : (
          <>
            <p className="mt-1 text-sm text-muted-foreground">
              Deleting the Local feed removes every Episode in it, locked ones too, its Requests and
              its published feed.
            </p>
            <Button
              variant="destructive"
              size="sm"
              className="mt-3"
              onClick={() => setDeleting(true)}
            >
              <Trash2 /> Delete Local feed…
            </Button>
            <DeleteFeedDialog feed={feed} open={deleting} onOpenChange={setDeleting} />
          </>
        )}
      </section>
    </div>
  );
}

/** A Local feed's settings (name, autoprune, Danger zone) in a dialog. */
export function LocalFeedSettingsDialog({
  feed,
  open,
  onOpenChange,
}: {
  feed: BucketRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Local feed settings</DialogTitle>
          <DialogDescription>{feed.name}</DialogDescription>
        </DialogHeader>
        <LocalFeedSettings feed={feed} />
      </DialogContent>
    </Dialog>
  );
}
