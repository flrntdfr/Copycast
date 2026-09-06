import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { $api, ApiProblem } from "@/api/client";
import type { InboxRead, InboxUpdate } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { optionalCount } from "@/features/add-source/policy-form";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { useInvalidateFeed } from "@/features/feeds/mutations";

export const inboxSettingsSchema = z
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

export type InboxSettingsInput = z.input<typeof inboxSettingsSchema>;
export type InboxSettingsValues = z.output<typeof inboxSettingsSchema>;

export function inboxDefaults(inbox: InboxRead): InboxSettingsInput {
  return {
    name: inbox.name,
    autoprune: inbox.autoprune_days != null,
    autoprune_days: inbox.autoprune_days ?? 30,
  };
}

/** Only changed fields; `autoprune_days: null` switches autoprune off. */
export function toInboxUpdate(inbox: InboxRead, values: InboxSettingsValues): InboxUpdate {
  const update: InboxUpdate = {};
  if (values.name.trim() !== inbox.name) update.name = values.name.trim();
  const days = values.autoprune ? (values.autoprune_days ?? null) : null;
  if (days !== (inbox.autoprune_days ?? null)) update.autoprune_days = days;
  return update;
}

export function InboxSettings({ inbox }: { inbox: InboxRead }) {
  const invalidate = useInvalidateFeed();
  const [deleting, setDeleting] = useState(false);
  const form = useForm<InboxSettingsInput, unknown, InboxSettingsValues>({
    resolver: zodResolver(inboxSettingsSchema),
    defaultValues: inboxDefaults(inbox),
  });
  const autoprune = useWatch({ control: form.control, name: "autoprune" });

  useEffect(() => {
    if (!form.formState.isDirty) form.reset(inboxDefaults(inbox));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inbox]);

  const update = $api.useMutation("patch", "/api/inboxes/{inbox_id}", {
    onSuccess: (updated) => {
      invalidate(updated.id);
      form.reset(inboxDefaults(updated));
      toast.success("Inbox settings saved");
    },
    onError: (error) => {
      if (ApiProblem.is(error) && error.slug === "conflict")
        form.setError("name", { type: "server", message: error.detail ?? error.title });
    },
  });

  const submit = form.handleSubmit((values) => {
    const body = toInboxUpdate(inbox, values);
    if (Object.keys(body).length === 0) {
      toast.info("Nothing changed");
      return;
    }
    update.mutate({ params: { path: { inbox_id: inbox.id } }, body });
  });
  const errors = form.formState.errors;

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="max-w-2xl space-y-6" aria-label="Inbox settings">
        <div className="space-y-1.5">
          <Label htmlFor="inbox-name">Name</Label>
          <Input id="inbox-name" {...form.register("name")} aria-invalid={!!errors.name} />
          {errors.name ? <p className="text-xs text-destructive">{errors.name.message}</p> : null}
        </div>
        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label htmlFor="inbox-autoprune" className="font-medium">
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
                  id="inbox-autoprune"
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              )}
            />
          </div>
          {autoprune ? (
            <div className="flex items-center gap-2">
              <Label htmlFor="inbox-autoprune-days" className="sr-only">
                Days after first download
              </Label>
              <Input
                id="inbox-autoprune-days"
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
        <div className="flex items-center gap-2">
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? <Loader2 className="animate-spin" /> : null}
            Save changes
          </Button>
          <span className="text-xs text-muted-foreground">
            Prune on demand from the “Prune now…” button in the header.
          </span>
        </div>
      </form>
      <section
        aria-labelledby="inbox-danger-zone"
        className="max-w-2xl rounded-lg border border-destructive/40 p-4"
      >
        <h2 id="inbox-danger-zone" className="text-sm font-medium text-destructive">
          Danger zone
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Deleting the Inbox removes every archived Episode, its Requests and its feed. The default
          Inbox cannot be deleted.
        </p>
        <Button variant="destructive" size="sm" className="mt-3" onClick={() => setDeleting(true)}>
          <Trash2 /> Delete Inbox…
        </Button>
      </section>
      <DeleteFeedDialog feed={inbox} open={deleting} onOpenChange={setDeleting} />
    </div>
  );
}
