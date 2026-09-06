import { Loader2 } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { $api, describeProblem } from "@/api/client";
import type { Numbering, SelectionResult } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { count } from "@/lib/labels";
import { describeSelection, parseSelection, selectionIsValid } from "@/lib/selection";
import { cn } from "@/lib/utils";

export interface RangeInputProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | undefined;
  placeholder?: string;
  label?: string;
  hideLabel?: boolean;
  disabled?: boolean;
  className?: string;
  /** Extra wording under the input (the dry-run preview lives here). */
  hint?: React.ReactNode;
}

/** A selection expression input validated client-side with `parseSelection`. */
export function RangeInput({
  id,
  value,
  onChange,
  error,
  placeholder = "1-42, 180",
  label = "Episode numbers",
  hideLabel = true,
  disabled,
  className,
  hint,
}: RangeInputProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const parsed = useMemo(() => parseSelection(value), [value]);
  const invalid = !parsed.empty && parsed.invalid.length > 0;
  const message = error ?? (invalid ? describeSelection(parsed) : null);

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <Label htmlFor={inputId} className={cn(hideLabel && "sr-only")}>
        {label}
      </Label>
      <Input
        id={inputId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        inputMode="numeric"
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        aria-invalid={!!message}
        aria-describedby={`${inputId}-hint`}
        className="font-mono"
      />
      <div
        id={`${inputId}-hint`}
        className={cn("min-h-4 text-xs", message ? "text-destructive" : "text-muted-foreground")}
      >
        {message ?? hint ?? (parsed.empty ? "" : describeSelection(parsed))}
      </div>
    </div>
  );
}

export const DRY_RUN_DEBOUNCE_MS = 400;

/** "43 Episodes, 2 already archived" wording for a dry-run result. */
export function describeDryRun(result: SelectionResult): string {
  const parts = [count(result.resolved.length, "Episode")];
  if (result.already_archived_count > 0)
    parts.push(`${result.already_archived_count} already archived`);
  if (result.unresolved.length > 0) {
    parts.push(
      `${result.unresolved.length} not found (${result.unresolved.slice(0, 5).join(", ")}${result.unresolved.length > 5 ? "…" : ""})`,
    );
  }
  return parts.join(", ");
}

/**
 * RangeInput wired to `select_items`: a debounced dry-run previews the selection,
 * Confirm queues the archive jobs. Used by the Catalog toolbar.
 */
export function RangeArchiveInput({
  feedId,
  numbering = "source",
  onQueued,
  className,
}: {
  feedId: string;
  numbering?: Numbering;
  onQueued?: (result: SelectionResult) => void;
  className?: string;
}) {
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<SelectionResult | null>(null);
  const [previewFor, setPreviewFor] = useState<string>("");
  const parsed = useMemo(() => parseSelection(value), [value]);
  const valid = selectionIsValid(parsed);
  const expression = value.trim();
  const requestSeq = useRef(0);

  const dryRun = $api.useMutation("post", "/api/mirrors/{feed_id}/selections", {
    meta: { silent: true },
  });
  const confirm = $api.useMutation("post", "/api/mirrors/{feed_id}/selections", {
    onSuccess: (result) => {
      toast.success(
        `Queued ${count(result.jobs?.length ?? result.resolved.length, "Episode")} for archiving`,
      );
      setValue("");
      setPreview(null);
      setPreviewFor("");
      onQueued?.(result);
    },
  });
  const { mutateAsync: runDryRun, reset: resetDryRun } = dryRun;

  useEffect(() => {
    if (!valid) {
      resetDryRun();
      return undefined;
    }
    const seq = ++requestSeq.current;
    const timer = setTimeout(() => {
      runDryRun({
        params: { path: { feed_id: feedId } },
        body: { selection: expression, numbering, dry_run: true },
      })
        .then((result) => {
          if (seq !== requestSeq.current) return;
          setPreview(result);
          setPreviewFor(expression);
        })
        .catch(() => {
          if (seq !== requestSeq.current) return;
          setPreview(null);
          setPreviewFor("");
        });
    }, DRY_RUN_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [expression, valid, feedId, numbering, runDryRun, resetDryRun]);

  const previewCurrent = preview && previewFor === expression;
  const toArchive = previewCurrent ? preview.resolved.length - preview.already_archived_count : 0;
  const dryRunError = dryRun.error ? describeProblem(dryRun.error).title : undefined;

  return (
    <div className={cn("flex flex-col gap-2 sm:flex-row sm:items-start", className)}>
      <RangeInput
        value={value}
        onChange={setValue}
        label="Archive by episode numbers"
        placeholder="Archive by numbers, e.g. 1-42, 180"
        error={valid && dryRun.isError ? dryRunError : undefined}
        className="min-w-0 flex-1 sm:w-72"
        hint={
          valid ? (
            dryRun.isPending || !previewCurrent ? (
              <span className="inline-flex items-center gap-1">
                <Loader2 className="size-3 animate-spin" aria-hidden /> Resolving…
              </span>
            ) : (
              <span data-testid="dry-run-preview">{describeDryRun(preview)}</span>
            )
          ) : undefined
        }
      />
      <Button
        type="button"
        onClick={() =>
          confirm.mutate({
            params: { path: { feed_id: feedId } },
            body: { selection: expression, numbering, dry_run: false },
          })
        }
        disabled={!valid || !previewCurrent || toArchive <= 0 || confirm.isPending}
      >
        {confirm.isPending ? <Loader2 className="animate-spin" /> : null}
        Archive{previewCurrent && toArchive > 0 ? ` ${toArchive.toLocaleString()}` : ""}
      </Button>
    </div>
  );
}
