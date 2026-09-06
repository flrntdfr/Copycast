import { Link } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";

export function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-16 text-center">
      <h1 className="text-2xl font-semibold">Not found</h1>
      <p className="mt-2 text-muted-foreground">There is nothing at this address.</p>
      <Button asChild className="mt-6">
        <Link to="/mirrors">Go to Mirrors</Link>
      </Button>
    </div>
  );
}
