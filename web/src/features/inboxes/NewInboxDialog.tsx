import { useNavigate } from "@tanstack/react-router";
import { Loader2, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { $api } from "@/api/client";
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
import { useInvalidateFeed } from "@/features/feeds/mutations";

/** `create_inbox`: a name now, autoprune later from the Inbox settings. */
export function NewInboxDialog() {
  const navigate = useNavigate();
  const invalidate = useInvalidateFeed();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const create = $api.useMutation("post", "/api/inboxes", {
    onSuccess: (inbox) => {
      invalidate();
      toast.success(`Inbox “${inbox.name}” created`);
      setOpen(false);
      setName("");
      void navigate({ to: "/inboxes/$inboxId", params: { inboxId: inbox.id } });
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate({ body: { name: trimmed } });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New Inbox
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>New Inbox</DialogTitle>
            <DialogDescription>
              An Inbox publishes its own feed and is filled by the URLs you push into it.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="new-inbox-name">Name</Label>
            <Input
              id="new-inbox-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              {create.isPending ? <Loader2 className="animate-spin" /> : null}
              Create
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
