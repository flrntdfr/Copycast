import { PageHeader } from "@/components/common/PageHeader";
import { AuthenticationsSection } from "./AuthenticationsSection";
import { MirrorDefaultsSection } from "./MirrorDefaultsSection";
import { OpmlSection } from "./OpmlSection";

/**
 * Operator settings that live in the data directory rather than in the environment: the
 * sites the engine signs in to, the defaults every Mirror inherits, and the OPML export.
 * A feed's own settings and its files live on its own pages.
 */
export function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" />
      <div className="max-w-3xl space-y-10">
        <AuthenticationsSection />
        <MirrorDefaultsSection />
        <OpmlSection />
      </div>
    </>
  );
}
