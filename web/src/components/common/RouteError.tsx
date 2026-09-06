import { useQueryErrorResetBoundary } from "@tanstack/react-query";
import { type ErrorComponentProps, useRouter } from "@tanstack/react-router";
import { RotateCcw } from "lucide-react";
import { useEffect } from "react";

import { describeProblem } from "@/api/problem";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** Route-level error boundary with a Retry button (the plan's `errorComponent`). */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const router = useRouter();
  const queryReset = useQueryErrorResetBoundary();
  const { title, description } = describeProblem(error);

  useEffect(() => {
    queryReset.reset();
  }, [queryReset]);

  return (
    <div className="mx-auto max-w-xl py-16">
      <Alert variant="destructive">
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription className="whitespace-pre-line">
          {description ?? "The page could not be loaded."}
        </AlertDescription>
      </Alert>
      <div className="mt-4 flex gap-2">
        <Button
          onClick={() => {
            reset();
            void router.invalidate();
          }}
        >
          <RotateCcw /> Retry
        </Button>
        <Button variant="outline" onClick={() => router.history.back()}>
          Go back
        </Button>
      </div>
    </div>
  );
}
