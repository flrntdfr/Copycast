import { PageHeader } from "@/components/common/PageHeader";
import { AuthenticationsSection } from "./AuthenticationsSection";
import { BucketsSection } from "./BucketsSection";
import { MirrorDefaultsSection } from "./MirrorDefaultsSection";

/**
 * Operator settings that live in the data directory rather than in the environment: the
 * sites the engine signs in to, then two halves, the Mirror defaults and every Feed's bucket.
 */
export function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" />
      <div className="space-y-10">
        <AuthenticationsSection />
        <div className="grid gap-10 lg:grid-cols-2">
          <MirrorDefaultsSection />
          <BucketsSection />
        </div>
      </div>
    </>
  );
}
