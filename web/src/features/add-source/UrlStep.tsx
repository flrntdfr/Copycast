import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function UrlStep({ onSubmit }: { onSubmit: (url: string) => void }) {
  const [url, setUrl] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (url.trim()) onSubmit(url.trim());
  };
  return (
    <form onSubmit={submit} className="max-w-2xl space-y-3">
      <Label htmlFor="wizard-url">Source URL</Label>
      <div className="flex gap-2">
        <Input
          id="wizard-url"
          autoFocus
          inputMode="url"
          autoComplete="off"
          placeholder="https://example.com/feed.xml"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <Button type="submit" disabled={!url.trim()}>
          Continue <ArrowRight />
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">
        A podcast feed, a page that advertises one, a YouTube channel or playlist, or anything else
        the Engine can list. A bare YouTube channel URL mirrors its Videos tab.
      </p>
    </form>
  );
}
