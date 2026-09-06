import { $api } from "@/api/client";
import { Badge } from "@/components/ui/badge";

/** The running-job count shown next to "Jobs" in the top nav. */
export function useRunningJobCount(): number {
  const { data } = $api.useQuery(
    "get",
    "/api/jobs",
    { params: { query: { status: ["running"], limit: 1 } } },
    { refetchInterval: 30_000 },
  );
  return data?.total ?? 0;
}

export function JobsIndicator() {
  const running = useRunningJobCount();
  if (running === 0) return null;
  return (
    <Badge
      variant="default"
      className="ml-1 h-5 min-w-5 px-1.5 tabular-nums"
      aria-label={`${running} running`}
    >
      {running}
    </Badge>
  );
}
