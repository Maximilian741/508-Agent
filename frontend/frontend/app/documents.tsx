/**
 * /documents: the retired legacy upload -> scan entry point.
 *
 * It led into the /scan fix flow, whose /documents/* fix and fixed-download
 * endpoints handed out remediated files without charging and now answer 410.
 * The route stays so a bookmark lands on a pointer to Audit. See LegacyFlowMoved.
 */
import { LegacyFlowMoved } from "../src/components/LegacyFlowMoved";

export default function DocumentsScreen() {
  return <LegacyFlowMoved screenTitle="Documents" />;
}
