import { zodResolver } from "@hookform/resolvers/zod";
import { Inbox, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";

import type { MirrorDefaults, ProbeCandidate } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { RangeInput } from "@/features/catalog/RangeInput";
import { RetentionField, WindowField } from "@/features/mirrors/ModeFields";
import { ModeTabs } from "@/features/mirrors/ModeTabs";
import {
  policyFromDefaults,
  policySchema,
  type PolicyFormInput,
  type PolicyFormValues,
} from "./policy-form";
import { count } from "@/lib/labels";

export function PolicyStep({
  candidate,
  defaults,
  onSubmit,
  submitting,
  onBack,
  onSendToInbox,
  sendingToInbox,
  onCancelSubmit,
}: {
  candidate: ProbeCandidate;
  /** The operator's defaults (Settings page); the form starts from their policy. */
  defaults?: MirrorDefaults;
  onSubmit: (values: PolicyFormValues) => void;
  submitting: boolean;
  onBack: () => void;
  onSendToInbox?: () => void;
  sendingToInbox?: boolean;
  onCancelSubmit?: () => void;
}) {
  const form = useForm<PolicyFormInput, unknown, PolicyFormValues>({
    resolver: zodResolver(policySchema),
    defaultValues: policyFromDefaults(defaults),
    mode: "onChange",
  });
  const mode = useWatch({ control: form.control, name: "mode" });
  const follow = useWatch({ control: form.control, name: "follow" });
  const followTouched = useRef(false);

  // Follow switches off under Selection (and back on when leaving it) unless the person touched it.
  useEffect(() => {
    if (followTouched.current) return;
    form.setValue("follow", mode !== "selection", { shouldDirty: false });
  }, [mode, form]);

  const total = candidate.item_count;
  const submit = form.handleSubmit(onSubmit);
  const errors = form.formState.errors;

  return (
    <form onSubmit={submit} className="space-y-6">
      <div className="flex items-start gap-3 rounded-lg border bg-card p-3">
        <Artwork src={candidate.artwork_url} size={56} alt="" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{candidate.title ?? candidate.source_url}</span>
            <ServiceBadge service={candidate.service} sourceKind={candidate.source_kind} />
          </div>
          {candidate.author ? (
            <div className="text-xs text-muted-foreground">{candidate.author}</div>
          ) : null}
          <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {candidate.source_url}
          </div>
        </div>
      </div>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Backfill</legend>
        <p className="text-sm text-muted-foreground">Which items get archived, and when.</p>
        <Controller
          control={form.control}
          name="mode"
          render={({ field }) => (
            <ModeTabs value={field.value} onValueChange={field.onChange} total={total}>
              {(active) =>
                active === "rolling" ? (
                  <WindowField
                    id="backfill-latest-n"
                    label="Keep the newest"
                    max={total}
                    error={errors.latest_n?.message}
                    inputProps={form.register("latest_n", { valueAsNumber: true })}
                  />
                ) : active === "automatic" ? (
                  <RetentionField
                    id="backfill-retention-days"
                    error={errors.retention_days?.message}
                    inputProps={form.register("retention_days", { valueAsNumber: true })}
                  />
                ) : active === "selection" ? (
                  <Controller
                    control={form.control}
                    name="selection"
                    render={({ field: selection, fieldState }) => (
                      <RangeInput
                        id="backfill-selection-expression"
                        value={selection.value ?? ""}
                        onChange={selection.onChange}
                        error={fieldState.error?.message}
                        placeholder="1-42, 180"
                      />
                    )}
                  />
                ) : null
              }
            </ModeTabs>
          )}
        />
      </fieldset>

      <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
        <div>
          <Label htmlFor="follow" className="font-medium">
            Follow
          </Label>
          <p className="text-xs text-muted-foreground">
            Archive new items automatically after each Refresh.
            {mode === "selection" ? " Off by default for a Selection." : ""}
            {mode === "automatic" ? " Not needed: Automatic archives on request." : ""}
          </p>
        </div>
        <Switch
          id="follow"
          checked={follow}
          onCheckedChange={(checked) => {
            followTouched.current = true;
            form.setValue("follow", checked, { shouldDirty: true });
          }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={submitting}>
          {submitting ? <Loader2 className="animate-spin" /> : null}
          Create Mirror
        </Button>
        {submitting && onCancelSubmit ? (
          <Button type="button" variant="outline" onClick={onCancelSubmit}>
            Cancel
          </Button>
        ) : null}
        {!submitting && candidate.item_count === 1 && onSendToInbox ? (
          <Button type="button" variant="outline" onClick={onSendToInbox} disabled={sendingToInbox}>
            <Inbox /> Send to Inbox instead
          </Button>
        ) : null}
        {!submitting ? (
          <Button type="button" variant="ghost" onClick={onBack}>
            Back
          </Button>
        ) : null}
        {submitting && mode === "selection" ? (
          <span className="text-sm text-muted-foreground" role="status">
            Listing the Source and resolving the selection; this can take a few minutes.
          </span>
        ) : null}
      </div>
      {total != null ? (
        <p className="text-xs text-muted-foreground">
          {count(total, "item")} listed by the Source.
        </p>
      ) : null}
    </form>
  );
}
