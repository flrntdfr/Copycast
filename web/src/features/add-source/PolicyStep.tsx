import { zodResolver } from "@hookform/resolvers/zod";
import { Inbox, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";

import type { ProbeCandidate } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { RangeInput } from "@/features/catalog/RangeInput";
import {
  DEFAULT_POLICY,
  policySchema,
  type PolicyFormInput,
  type PolicyFormValues,
} from "./policy-form";
import { count } from "@/lib/labels";

export function PolicyStep({
  candidate,
  onSubmit,
  submitting,
  onBack,
  onSendToInbox,
  sendingToInbox,
  onCancelSubmit,
}: {
  candidate: ProbeCandidate;
  onSubmit: (values: PolicyFormValues) => void;
  submitting: boolean;
  onBack: () => void;
  onSendToInbox?: () => void;
  sendingToInbox?: boolean;
  onCancelSubmit?: () => void;
}) {
  const form = useForm<PolicyFormInput, unknown, PolicyFormValues>({
    resolver: zodResolver(policySchema),
    defaultValues: DEFAULT_POLICY,
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
        <p className="text-sm text-muted-foreground">
          Which of the items already listed get archived now.
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
              <div className="flex items-start gap-3">
                <RadioGroupItem value="all" id="backfill-all" className="mt-0.5" />
                <Label
                  htmlFor="backfill-all"
                  className="flex flex-col items-start gap-0.5 font-normal"
                >
                  <span className="font-medium">
                    Everything{total != null ? ` (${total.toLocaleString()})` : ""}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    Every item the Source lists, oldest first.
                  </span>
                </Label>
              </div>
              <div className="flex items-start gap-3">
                <RadioGroupItem value="latest" id="backfill-latest" className="mt-0.5" />
                <div className="flex flex-1 flex-col gap-2">
                  <Label
                    htmlFor="backfill-latest"
                    className="flex flex-col items-start gap-0.5 font-normal"
                  >
                    <span className="font-medium">Latest N</span>
                    <span className="text-xs text-muted-foreground">
                      Only the newest items; older ones stay Available.
                    </span>
                  </Label>
                  {mode === "latest" ? (
                    <div className="flex items-center gap-2">
                      <Label htmlFor="backfill-latest-n" className="sr-only">
                        How many
                      </Label>
                      <Input
                        id="backfill-latest-n"
                        type="number"
                        min={1}
                        max={total ?? undefined}
                        className="w-28"
                        {...form.register("latest_n", { valueAsNumber: true })}
                        aria-invalid={!!form.formState.errors.latest_n}
                      />
                      <span className="text-sm text-muted-foreground">items</span>
                    </div>
                  ) : null}
                  {form.formState.errors.latest_n ? (
                    <p className="text-xs text-destructive">
                      {form.formState.errors.latest_n.message}
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="flex items-start gap-3">
                <RadioGroupItem value="selection" id="backfill-selection" className="mt-0.5" />
                <div className="flex flex-1 flex-col gap-2">
                  <Label
                    htmlFor="backfill-selection"
                    className="flex flex-col items-start gap-0.5 font-normal"
                  >
                    <span className="font-medium">Selection</span>
                    <span className="text-xs text-muted-foreground">
                      Episode numbers such as “1-42, 180”. Leave empty to pick from the Catalog
                      later.
                    </span>
                  </Label>
                  {mode === "selection" ? (
                    <Controller
                      control={form.control}
                      name="selection"
                      render={({ field, fieldState }) => (
                        <RangeInput
                          id="backfill-selection-expression"
                          value={field.value ?? ""}
                          onChange={field.onChange}
                          error={fieldState.error?.message}
                          placeholder="1-42, 180"
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

      <div className="flex items-center justify-between gap-4 rounded-lg border p-3">
        <div>
          <Label htmlFor="follow" className="font-medium">
            Follow
          </Label>
          <p className="text-xs text-muted-foreground">
            Archive new items automatically after each Refresh.
            {mode === "selection" ? " Off by default for a Selection." : ""}
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
