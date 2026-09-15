/**
 * /scan: the retired legacy workflow (scan -> apply fixes -> finalize -> download).
 *
 * Every action on the old screen called a /documents/* fix or fixed-download
 * endpoint that handed out remediated files without charging; those endpoints
 * now answer 410. The route stays so a bookmark lands on a pointer to Audit.
 * See LegacyFlowMoved.
 */
import { LegacyFlowMoved } from "../src/components/LegacyFlowMoved";

export default function ScanScreen() {
  return <LegacyFlowMoved screenTitle="Scan" />;
}
