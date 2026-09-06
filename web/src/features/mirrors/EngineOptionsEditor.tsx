import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

/** JSON textarea for feed-level Engine options with the rejected keys listed per key. */
export function EngineOptionsEditor({
  id = "engine-options",
  value,
  onChange,
  error,
  rejectedKeys = [],
  scope,
  disabled,
  className,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  /** Parse or validation error for the whole text. */
  error?: string | undefined;
  /** Keys the API refused (`engine-option-rejected`). */
  rejectedKeys?: readonly string[];
  scope?: string | null;
  disabled?: boolean;
  className?: string;
}) {
  const invalid = !!error || rejectedKeys.length > 0;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id}>Engine options</Label>
      <Textarea
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={'{\n  "format": "bestaudio[ext=m4a]/bestaudio"\n}'}
        spellCheck={false}
        disabled={disabled}
        aria-invalid={invalid}
        aria-describedby={`${id}-hint`}
        className="min-h-32 font-mono text-xs"
      />
      <div id={`${id}-hint`} className="text-xs">
        {error ? <p className="text-destructive">{error}</p> : null}
        {rejectedKeys.length ? (
          <ul
            className="list-inside list-disc text-destructive"
            aria-label="Rejected engine options"
          >
            {rejectedKeys.map((key) => (
              <li key={key}>
                <code className="font-mono">{key}</code>: not allowed
                {scope ? ` for a ${scope}` : ""}; Copycast owns this option.
              </li>
            ))}
          </ul>
        ) : null}
        {!error && !rejectedKeys.length ? (
          <p className="text-muted-foreground">
            Raw yt-dlp options as a JSON object, layered over the server defaults for this Mirror
            only. Options Copycast owns (output paths, format selection, post-processors) are
            rejected.
          </p>
        ) : null}
      </div>
    </div>
  );
}
