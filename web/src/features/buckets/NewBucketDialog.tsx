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

/** `create_bucket`: a name now, autoprune later from the Bucket settings. */
export function NewBucketDialog() {
  const navigate = useNavigate();
  const invalidate = useInvalidateFeed();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const create = $api.useMutation("post", "/api/buckets", {
    onSuccess: (bucket) => {
      invalidate();
      toast.success(`Bucket “${bucket.name}” created`);
      setOpen(false);
      setName("");
      void navigate({ to: "/buckets/$bucketId", params: { bucketId: bucket.id } });
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
        <Button variant="outline" className="dark:bg-black dark:hover:bg-accent">
          <Plus /> New Bucket
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>New Bucket</DialogTitle>
            <DialogDescription>
              A Bucket publishes its own feed and is filled by the URLs you push into it.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="new-bucket-name">Name</Label>
            <Input
              id="new-bucket-name"
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
