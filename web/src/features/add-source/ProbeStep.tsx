import { Inbox, Loader2 } from "lucide-react";

import { ApiProblem, describeProblem } from "@/api/problem";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

export function ProbeStep({
  url,
  error,
  creating = false,
  onCancel,
  onRetry,
  onSendToInbox,
  sendingToInbox,
}: {
  url: string;
  error: unknown;
  /** The probe found one Source and the Mirror is being created from it. */
  creating?: boolean;
  onCancel: () => void;
  onRetry: () => void;
  onSendToInbox?: () => void;
  sendingToInbox?: boolean;
}) {
  if (error) {
    const aborted = ApiProblem.is(error) && error.slug === "aborted";
    const { title, description } = aborted
      ? {
          title: "The probe took too long",
          description: "The Source did not answer within five minutes.",
        }
      : describeProblem(error);
    const unsupported = ApiProblem.is(error) && error.slug === "source-unsupported";
    return (
      <div className="space-y-4">
        <Alert variant="destructive">
          <AlertTitle>{unsupported ? "This URL cannot be mirrored" : title}</AlertTitle>
          <AlertDescription className="break-all whitespace-pre-line">
            {description ?? url}
          </AlertDescription>
        </Alert>
        <div className="flex flex-wrap gap-2">
          <Button onClick={onRetry}>Try again</Button>
          {onSendToInbox ? (
            <Button variant="outline" onClick={onSendToInbox} disabled={sendingToInbox}>
              <Inbox /> Send to Inbox instead
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onCancel}>
            Close
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        <span>
          {creating ? "Creating the Mirror and listing its Source…" : "Looking at the Source…"}
        </span>
      </div>
      <Progress
        value={null}
        className="[&>[data-slot=progress-indicator]]:animate-pulse"
        aria-label="Probing the Source"
      />
      <p className="text-sm text-muted-foreground">
        Feeds answer in a moment; pages and video sites can take longer while the Engine lists them.
        {creating
          ? " The operator’s default policy applies; change it in the Mirror’s Settings tab."
          : ""}
      </p>
      {creating ? null : (
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      )}
    </div>
  );
}
