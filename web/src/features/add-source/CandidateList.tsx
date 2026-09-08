import { Inbox, Loader2 } from "lucide-react";
import { useState } from "react";

import type { ProbeCandidate, ProbeResult } from "@/api/types";
import { Artwork } from "@/components/common/Artwork";
import { ServiceBadge } from "@/components/common/ServiceBadge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { count } from "@/lib/labels";
import { cn } from "@/lib/utils";

export function CandidateList({
  probe,
  onChoose,
  choosing = false,
  onSendToInbox,
  sendingToInbox,
  onBack,
}: {
  probe: ProbeResult;
  onChoose: (candidate: ProbeCandidate) => void;
  /** A Mirror is being created from the chosen Source. */
  choosing?: boolean;
  onSendToInbox?: (candidate: ProbeCandidate) => void;
  sendingToInbox?: boolean;
  onBack: () => void;
}) {
  const [token, setToken] = useState<string>(probe.candidates[0]?.candidate_token ?? "");
  const chosen = probe.candidates.find((c) => c.candidate_token === token) ?? null;

  if (probe.candidates.length === 0) {
    return (
      <div className="space-y-4">
        <p className="text-sm">
          Nothing on that page looks like a feed or a Source the Engine can list.
        </p>
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {probe.candidates.length} Sources were found at{" "}
        <span className="font-mono break-all">{probe.input_url}</span>. Pick the one to mirror.
      </p>
      <RadioGroup
        value={token}
        onValueChange={setToken}
        aria-label="Sources found"
        className="gap-2"
      >
        {probe.candidates.map((candidate) => (
          <Label
            key={candidate.candidate_token}
            htmlFor={`candidate-${candidate.candidate_token}`}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-lg border p-3 font-normal hover:bg-accent/50",
              token === candidate.candidate_token && "border-primary bg-accent/40",
            )}
          >
            <RadioGroupItem
              value={candidate.candidate_token}
              id={`candidate-${candidate.candidate_token}`}
              className="mt-1"
            />
            <Artwork src={candidate.artwork_url} size={48} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{candidate.title ?? candidate.source_url}</span>
                <ServiceBadge service={candidate.service} sourceKind={candidate.source_kind} />
                {candidate.item_count != null ? (
                  <span className="text-xs text-muted-foreground">
                    {count(candidate.item_count, "item")}
                  </span>
                ) : null}
              </div>
              {candidate.author ? (
                <div className="text-xs text-muted-foreground">{candidate.author}</div>
              ) : null}
              <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                {candidate.source_url}
              </div>
              {candidate.description ? (
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                  {candidate.description}
                </p>
              ) : null}
            </div>
          </Label>
        ))}
      </RadioGroup>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => chosen && onChoose(chosen)} disabled={!chosen || choosing}>
          {choosing ? <Loader2 className="animate-spin" /> : null} Mirror this Source
        </Button>
        {chosen?.item_count === 1 && onSendToInbox ? (
          <Button variant="outline" onClick={() => onSendToInbox(chosen)} disabled={sendingToInbox}>
            <Inbox /> Send to Inbox instead
          </Button>
        ) : null}
        <Button variant="ghost" onClick={onBack}>
          Close
        </Button>
      </div>
    </div>
  );
}
