import { useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { $api } from "@/api/client";
import { OPS } from "@/api/ops";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Push one URL into a Bucket (`add_request`). */
export function AddRequestForm({
  bucketId,
  initialUrl = "",
}: {
  bucketId: string;
  initialUrl?: string;
}) {
  const [url, setUrl] = useState(initialUrl);
  const queryClient = useQueryClient();
  const mutation = $api.useMutation("post", "/api/buckets/{bucket_id}/requests", {
    onSuccess: (request) => {
      toast.success("Request queued", { description: request.url });
      setUrl("");
      // The `request` event does this too; invalidating here covers a dropped stream.
      void queryClient.invalidateQueries({
        queryKey: [
          OPS.list_requests[0],
          OPS.list_requests[1],
          { params: { path: { bucket_id: bucketId } } },
        ],
      });
      void queryClient.invalidateQueries({
        queryKey: [OPS.get_feed[0], OPS.get_feed[1], { params: { path: { feed_id: bucketId } } }],
      });
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    mutation.mutate({ params: { path: { bucket_id: bucketId } }, body: { url: trimmed } });
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-1.5">
      <Label htmlFor="add-request-url">Add URL</Label>
      <div className="flex gap-2">
        <Input
          id="add-request-url"
          inputMode="url"
          autoComplete="off"
          placeholder="A video, a playlist, a page…"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <Button type="submit" disabled={!url.trim() || mutation.isPending}>
          <Send /> Add
        </Button>
      </div>
    </form>
  );
}
