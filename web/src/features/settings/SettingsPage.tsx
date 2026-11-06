import { PageHeader } from "@/components/common/PageHeader";
import { AuthenticationsSection } from "./AuthenticationsSection";
import { MirrorDefaultsSection } from "./MirrorDefaultsSection";
import { OpmlSection } from "./OpmlSection";

/**
 * The signed-in User's own settings, kept in the Object store under `users/{username}/`: the
 * Cookie files the Engine signs in with for their Mirrors, the Defaults their Mirrors inherit,
 * and the OPML export of their Feeds. Every User, Admins included, sees only their own; the
 * instance-wide Engine details are on the About page, for Admins. A Feed's own settings and
 * its files live on its own pages.
 */
export function SettingsPage() {
  return (
    <>
      <PageHeader title="Your settings" />
      <div className="max-w-3xl space-y-10">
        <AuthenticationsSection />
        <MirrorDefaultsSection />
        <OpmlSection />
      </div>
    </>
  );
}
