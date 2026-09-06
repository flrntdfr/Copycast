import { Loader2, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { ApiKeyCreated, KeyScope } from "@/api/types";
import { CopyField } from "@/components/common/CopyField";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useCreateApiKey } from "./mutations";
import { keyScopeHint, keyScopeLabel } from "@/lib/labels";

const SCOPES: KeyScope[] = ["read", "write", "full"];

/** The `claude mcp add` line for a freshly minted key; the secret is read from the environment. */
export function claudeCodeCommand(mcpUrl: string): string {
  return `claude mcp add --transport http copycast ${mcpUrl} --header "Authorization: Bearer $COPYCAST_MCP_KEY"`;
}

/** `create_api_key`: a name and a scope, then the secret shown exactly once. */
export function NewApiKeyDialog({ mcpUrl }: { mcpUrl: string }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<KeyScope>("write");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const create = useCreateApiKey();

  // Every way out goes through here, so the secret is gone from state before the next open.
  const setOpenAndReset = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setName("");
      setScope("write");
      setCreated(null);
      create.reset();
    }
  };
  const close = () => setOpenAndReset(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate({ body: { name: trimmed, scope } }, { onSuccess: setCreated });
  };

  return (
    <Dialog open={open} onOpenChange={setOpenAndReset}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New key
        </Button>
      </DialogTrigger>
      <DialogContent>
        {created ? (
          <div className="space-y-4">
            <DialogHeader>
              <DialogTitle>Key “{created.key.name}” created</DialogTitle>
              <DialogDescription>
                Copy the secret now. It is shown only this once; Copycast keeps a digest.
              </DialogDescription>
            </DialogHeader>
            <CopyField label="Secret" value={created.secret} />
            <div className="space-y-1.5">
              <p className="text-sm text-muted-foreground">
                For Claude Code, export it as <code className="font-mono">COPYCAST_MCP_KEY</code>{" "}
                and run:
              </p>
              <CopyField label="Claude Code command" value={claudeCodeCommand(mcpUrl)} hideLabel />
            </div>
            <DialogFooter>
              <Button type="button" onClick={close}>
                Done
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <DialogHeader>
              <DialogTitle>New API key</DialogTitle>
              <DialogDescription>
                An MCP client presents the key as a bearer token; its scope caps what the agent may
                do.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="new-key-name">Name</Label>
              <Input
                id="new-key-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={80}
                placeholder="Claude Code on the laptop"
                autoFocus
              />
            </div>
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Scope</legend>
              <RadioGroup
                value={scope}
                onValueChange={(value) => setScope(value as KeyScope)}
                className="gap-2"
                aria-label="Scope"
              >
                {SCOPES.map((value) => (
                  <div key={value} className="flex items-start gap-3">
                    <RadioGroupItem value={value} id={`key-scope-${value}`} className="mt-0.5" />
                    <Label
                      htmlFor={`key-scope-${value}`}
                      className="flex flex-col items-start gap-0.5 font-normal"
                    >
                      <span className="font-medium">{keyScopeLabel(value)}</span>
                      <span className="text-xs text-muted-foreground">{keyScopeHint[value]}</span>
                    </Label>
                  </div>
                ))}
              </RadioGroup>
            </fieldset>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close}>
                Cancel
              </Button>
              <Button type="submit" disabled={!name.trim() || create.isPending}>
                {create.isPending ? <Loader2 className="animate-spin" /> : null}
                Create key
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
