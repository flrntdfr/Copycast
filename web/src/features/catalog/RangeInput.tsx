import { useId, useMemo } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { describeSelection, parseSelection } from "@/lib/selection";
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
