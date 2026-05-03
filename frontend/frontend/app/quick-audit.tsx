/**
 * Backwards-compat redirect.  /quick-audit was a short-lived route name from
 * an earlier iteration; the canonical workflow is now /audit.  We redirect on
 * mount so any bookmarks or external links keep working.
 */

import { useEffect } from "react";
import { useRouter } from "expo-router";

export default function QuickAuditRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/audit");
  }, [router]);
  return null;
}
