// DumpOutfitState.java -- was an NPC's outfit changed at runtime in this save?
// Usage: java -cp ReSaver.jar DumpOutfitState.java <save.ess> <achr-refid-hex> <npc-base-refid-hex>
// Prints: the save's plugin table indices for the top bytes involved, the ACHR
// changeform's flags, and the base NPC_ changeform's flags -- decoding
// CHANGE_DEFAULT_OUTFIT (bit 12), CHANGE_SLEEP_OUTFIT (13), CHANGE_NPC_RACE (25).
// If the NPC_ changeform parses, prints the DEFAULT_OUTFIT / RACE refids it carries.
import resaver.ProgressModel;
import resaver.ess.*;
import java.nio.file.*;
import java.util.*;

public class DumpOutfitState {
    public static void main(String[] args) throws Exception {
        Path savePath = Paths.get(args[0]);
        int achrId = (int) Long.parseLong(args[1], 16);
        int npcId  = (int) Long.parseLong(args[2], 16);

        ESS ess = ESS.readESS(savePath, new ModelBuilder(new ProgressModel(1))).ESS;
        ESS.ESSContext ctx = ess.getContext();

        // plugin table around the indices of interest
        List<Plugin> full = ess.getPluginInfo().getFullPlugins();
        int[] idx = { achrId >>> 24, npcId >>> 24 };
        System.out.println("Full (non-ESL) plugin count: " + full.size());
        for (int i : idx) {
            String name = (i < full.size()) ? full.get(i).NAME : "(out of range / ESL space)";
            System.out.printf("  plugin index %02X = %s%n", i, name);
        }

        for (ChangeForm cf : ess.getChangeForms()) {
            RefID r = cf.getRefID();
            if (r == null) continue;
            if (r.FORMID == achrId) {
                System.out.printf("%n=== ACHR ChangeForm %08X  type=%s%n", r.FORMID, cf.getType());
                Flags.Int f = cf.getChangeFlags();
                System.out.println("  raw flags: " + f);
                for (ChangeFlagConstantsAchr c : ChangeFlagConstantsAchr.values())
                    if (f.getFlag(c)) System.out.println("    set: " + c + " (bit " + c.getPosition() + ")");
            }
            if (r.FORMID == npcId) {
                System.out.printf("%n=== NPC_ ChangeForm %08X  type=%s%n", r.FORMID, cf.getType());
                Flags.Int f = cf.getChangeFlags();
                System.out.println("  raw flags: " + f);
                for (ChangeFlagConstantsNPC c : ChangeFlagConstantsNPC.values())
                    if (f.getFlag(c)) System.out.println("    set: " + c + " (bit " + c.getPosition() + ")");
                System.out.println("  CHANGE_DEFAULT_OUTFIT (bit12) set? "
                    + f.getFlag(ChangeFlagConstantsNPC.CHANGE_DEFAULT_OUTFIT));
                System.out.println("  CHANGE_NPC_RACE (bit25) set? "
                    + f.getFlag(ChangeFlagConstantsNPC.CHANGE_NPC_RACE));
                try {
                    ChangeFormData d = cf.getData(Optional.empty(), ctx, true);
                    if (d instanceof GeneralElement) {
                        GeneralElement g = (GeneralElement) d;
                        for (String k : new String[]{"DEFAULT_OUTFIT","SLEEP_OUTFIT","RACE","OLDRACE"}) {
                            if (g.hasVal(k)) System.out.println("  " + k + " = " + g.getVal(k));
                        }
                    }
                } catch (Exception e) {
                    System.out.println("  (body parse failed: " + e + " -- flags above are still authoritative)");
                }
            }
        }
    }
}
