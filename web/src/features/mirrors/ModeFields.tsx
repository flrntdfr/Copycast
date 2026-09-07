import type { ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The "keep the newest N" row of the Rolling N (and legacy Latest N) tab. */
export function WindowField({
  id,
  label,
  max,
  error,
  inputProps,
}: {
  id: string;
  label: string;
  max?: number | null;
  error?: string;
  inputProps: React.InputHTMLAttributes<HTMLInputElement> & { ref?: React.Ref<HTMLInputElement> };
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        min={1}
        max={max ?? undefined}
        className="w-28"
        {...inputProps}
        aria-invalid={!!error}
      />
      <span className="text-sm text-muted-foreground">items</span>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

/** The retention row of the Automatic tab; an empty field keeps Episodes forever. */
export function RetentionField({
  id,
  error,
  inputProps,
  hint = "days after the last download (empty keeps forever)",
}: {
  id: string;
  error?: string;
  inputProps: React.InputHTMLAttributes<HTMLInputElement> & { ref?: React.Ref<HTMLInputElement> };
  hint?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id}>Keep downloaded for</Label>
      <Input
        id={id}
        type="number"
        min={1}
        className="w-28"
        placeholder="forever"
        {...inputProps}
        aria-invalid={!!error}
      />
      <span className="text-sm text-muted-foreground">{hint}</span>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
