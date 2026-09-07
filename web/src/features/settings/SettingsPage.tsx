import { PageHeader } from "@/components/common/PageHeader";
import { CookiesCard } from "./CookiesCard";
import { DefaultsCard } from "./DefaultsCard";

/** Operator settings that live in the data directory rather than in the environment. */
export function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Defaults every Mirror inherits, and what the engine carries along on every fetch. The rest is configured through the environment (see docs/operations.md)."
      />
      <div className="space-y-6">
        <DefaultsCard />
        <CookiesCard />
      </div>
    </>
  );
}
