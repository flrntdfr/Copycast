import { useNavigate } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The prominent "paste a URL" bar above the Mirrors table; hands off to the wizard. */
export function AddSourceBar({ autoFocus = false }: { autoFocus?: boolean }) {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    void navigate({ to: "/mirrors/new", search: { url: trimmed, step: "probe" } });
  };

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-2 rounded-lg border bg-card p-3 sm:flex-row sm:items-end"
    >
      <div className="flex-1">
        <Label htmlFor="add-source-url" className="sr-only">
          Source URL
        </Label>
        <Input
          id="add-source-url"
          name="url"
          type="text"
          inputMode="url"
          autoComplete="off"
          autoFocus={autoFocus}
          placeholder="Paste a podcast feed, a page advertising one, a YouTube channel or playlist…"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
      </div>
      <Button type="submit" disabled={!url.trim()}>
        Add Source <ArrowRight />
      </Button>
    </form>
  );
}
