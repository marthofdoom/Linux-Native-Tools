// DumpNPCInventory.java -- headless dump of an NPC's (ACHR) inventory from a Skyrim SE save.
//
// Usage:
//   java -cp /path/to/ReSaver.jar DumpNPCInventory.java <save.ess> <placed-refid-hex> [-v]
//   e.g. java -cp ReSaver.jar DumpNPCInventory.java inigo.ess 6E008AE9
//
// Why this exists: ReSaver's ChangeFormACHR parser dies inside the EXTRADATA block on
// modded saves ("Failed to read ACHR ..."), so its INVENTORY field is null. This tool
// uses ReSaver's low-level readers (ESS/RefID/VSVal) but parses the inventory array
// itself, with three fixes over ReSaver's ChangeFormExtraDataData:
//   1. Wrapper extra types are 0,4,8,12,16,24 carrying type/4 sub-entries
//      (ReSaver implements only 0..12; 16 and 24 occur in real saves).
//   2. Extra type 35 (Rank) is RefID + 1 rank byte (4 bytes); ReSaver reads only 3.
//   3. The inventory array start is found by scanning: ReSaver's top-level EXTRADATA
//      lengths desync on modded saves, so every offset after the fixed header is tried
//      and validated by a strict full-array parse.
//
// Item format:   [RefID (3 bytes)] [int32 count] [vsval M] [M extra-data entries]
// Worn detection: extra type 22 = Worn, 23 = WornLeft (possibly nested in wrappers).
// A count<=0 item carrying Worn is a "phantom": the actor renders it but the
// inventory count is zero.

import resaver.ProgressModel;
import resaver.ess.*;
import java.nio.Buffer;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.*;
import java.util.*;

public class DumpNPCInventory {

    static class G extends GeneralElement { }  // exposes ReSaver's protected ctor

    static ESS.ESSContext ctx;
    static boolean verbose;

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: java -cp ReSaver.jar DumpNPCInventory.java <save.ess> <refid-hex> [-v]");
            System.exit(2);
        }
        Path savePath = Paths.get(args[0]);
        int target = (int) Long.parseLong(args[1], 16);
        verbose = args.length > 2 && args[2].equals("-v");

        ESS ess = ESS.readESS(savePath, new ModelBuilder(new ProgressModel(1))).ESS;
        ctx = ess.getContext();

        ChangeForm cf = null;
        for (ChangeForm c : ess.getChangeForms()) {
            RefID r = c.getRefID();
            if (r != null && r.FORMID == target) { cf = c; break; }
        }
        if (cf == null) {
            System.out.println("NO ChangeForm for " + args[1]
                + " -> reference unchanged from plugin defaults (no runtime inventory edits recorded).");
            return;
        }

        Flags.Int flags = cf.getChangeFlags();
        System.out.printf("ChangeForm %08X  type=%s%n", target, cf.getType());
        System.out.println("changeFlags: " + flags);

        if (!flags.getFlags(ChangeFlagConstantsAchr.CHANGE_REFR_INVENTORY,
                            ChangeFlagConstantsAchr.CHANGE_REFR_LEVELED_INVENTORY)) {
            System.out.println("No CHANGE_REFR_INVENTORY flag -> inventory equals plugin defaults.");
            return;
        }

        ByteBuffer in = cf.getBodyData();
        in.order(ByteOrder.LITTLE_ENDIAN);
        ((Buffer) in).position(0);
        int total = in.limit();

        // ---- fixed header (mirrors ChangeFormACHR) ----
        int initialType;
        if (cf.getRefID().getType() == RefID.Type.CREATED) initialType = 5;
        else if (flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_PROMOTED)
              || flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_CELL_CHANGED)) initialType = 6;
        else if (flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_HAVOK_MOVE)
              || flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_MOVE)) initialType = 4;
        else initialType = 0;
        G g = new G();
        switch (initialType) {
            case 4: ctx.readRefID(in); skip(in, 24); break;
            case 5: ctx.readRefID(in); skip(in, 25); ctx.readRefID(in); break;
            case 6: ctx.readRefID(in); skip(in, 24); ctx.readRefID(in); skip(in, 4); break;
            default:
        }
        if (flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_HAVOK_MOVE)) g.readBytesVS(in, "HAVOK");
        skip(in, 8); // UNKNOWN_INTEGER + UNKNOWN_BYTES
        if (flags.getFlag(ChangeFlagConstantsAchr.CHANGE_FORM_FLAGS)) new ChangeFormFlags(in);
        if (flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_BASEOBJECT)) ctx.readRefID(in);
        if (flags.getFlag(ChangeFlagConstantsAchr.CHANGE_REFR_SCALE)) in.getFloat();
        int headerEnd = in.position();
        log("fixed header ends @" + headerEnd + " of " + total);

        // ---- find the inventory array by validated scan ----
        List<int[]> candidates = new ArrayList<>(); // {offset, itemCount}
        for (int start = headerEnd; start < total - 2; start++) {
            int n = tryParse(in, start, null);
            if (n > 0) candidates.add(new int[]{start, n});
        }
        if (candidates.isEmpty()) {
            System.out.println("FAILED: no offset in [" + headerEnd + "," + total + ") parses as a full inventory array.");
            System.exit(1);
        }
        // Prefer the candidate with the most items (coincidental matches are tiny).
        candidates.sort((a, b) -> Integer.compare(b[1], a[1]));
        if (candidates.size() > 1) {
            log("multiple candidates (offset,items): "
                + Arrays.deepToString(candidates.toArray(new int[0][])));
        }
        int start = candidates.get(0)[0];

        List<Item> items = new ArrayList<>();
        tryParse(in, start, items);
        System.out.println("INVENTORY: " + items.size() + " changed entries (array offset " + start + ")");
        System.out.println("  FormID    count  flags        plugin                       name/extras");
        for (Item it : items) {
            String tag = (it.worn ? "WORN " : "") + (it.wornLeft ? "WORN-L " : "")
                       + (it.count <= 0 && (it.worn || it.wornLeft) ? "PHANTOM" : "");
            System.out.printf("  %08X  %-6d %-12s %-28s %s%n",
                it.formID, it.count, tag.trim(), it.plugin, it.label());
        }
    }

    static class Item {
        int formID; int count; String plugin = "?"; String baseName = "";
        boolean worn, wornLeft; String displayName; String ench; List<String> extraNames = new ArrayList<>();
        String label() {
            StringBuilder sb = new StringBuilder(baseName);
            if (displayName != null) sb.append(sb.length() > 0 ? " " : "").append("\"").append(displayName).append("\"");
            if (ench != null) sb.append(" ench=").append(ench);
            if (!extraNames.isEmpty()) sb.append(" extras=").append(extraNames);
            return sb.toString();
        }
    }

    /** Strict parse of a vsval-counted inventory array at 'start'.
     *  Returns item count on full success, -1 on failure. Fills 'out' if non-null. */
    static int tryParse(ByteBuffer in, int start, List<Item> out) {
        try {
            ((Buffer) in).position(start);
            G g = new G();
            int n = g.readVSVal(in, "N").getValue();
            if (n < 1 || n > 4096) return -1;
            List<Item> items = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                Item it = new Item();
                RefID ref = ctx.readRefID(in);
                if (ref.getType() == RefID.Type.INVALID || ref.isZero()) return -1;
                it.formID = ref.FORMID;
                it.plugin = ref.PLUGIN != null ? ref.PLUGIN.NAME : "?";
                it.baseName = ref.getName().orElse("");
                it.count = in.getInt();
                if (it.count < -100000 || it.count > 1000000) return -1;
                int m = g.readVSVal(in, "M").getValue();
                if (m < 0 || m > 64) return -1;
                for (int k = 0; k < m; k++) readExtra(in, g, it, 0);
                items.add(it);
            }
            if (out != null) out.addAll(items);
            return n;
        } catch (Exception | AssertionError ex) {
            return -1;
        }
    }

    /** Extra-data entry reader with the wrapper + Rank fixes. Falls back to ReSaver
     *  (ChangeFormExtraDataData) for types not handled here. */
    static void readExtra(ByteBuffer in, G g, Item it, int depth) throws Exception {
        if (depth > 8) throw new IllegalStateException("extra nesting too deep");
        int type = Byte.toUnsignedInt(in.get(in.position()));
        // Wrapper types: 0,4,8,12,16,20,24 carry type/4 sub-entries.
        // NOTE: 28 is NOT a wrapper (it is ReferenceHandle, verified in real data).
        if (type % 4 == 0 && type <= 24 && type != 0) {
            in.get();
            int subs = type / 4;
            for (int s = 0; s < subs; s++) readExtra(in, g, it, depth + 1);
            return;
        }
        switch (type) {
            case 0:  in.get(); return;                                     // NULL
            case 22: in.get(); it.worn = true; it.extraNames.add("Worn"); return;
            case 23: in.get(); it.wornLeft = true; it.extraNames.add("WornLeft"); return;
            case 28: in.get(); ctx.readRefID(in); it.extraNames.add("ReferenceHandle"); return;
            case 33: in.get(); ctx.readRefID(in); it.extraNames.add("Ownership"); return;
            case 35: in.get(); ctx.readRefID(in); in.get();                // RefID + rank byte (ReSaver misses the byte)
                     it.extraNames.add("Rank"); return;
            case 36: in.get(); in.getShort(); it.extraNames.add("Count"); return;
            case 37: in.get(); in.getFloat(); it.extraNames.add("Health"); return;
            case 40: in.get(); in.getFloat(); it.extraNames.add("Charge"); return;
            case 136: { in.get();                                          // AliasInstanceArray
                     int c = g.readVSVal(in, "AC").getValue();
                     if (c < 0 || c > 1024) throw new IllegalStateException("bad alias count");
                     for (int k = 0; k < c; k++) { ctx.readRefID(in); in.getInt(); }
                     it.extraNames.add("AliasInstanceArray"); return; }
            case 142: in.get(); ctx.readRefID(in); it.extraNames.add("OutfitItem"); return;
            case 153: { in.get();                                          // TextDisplayData
                     RefID r1 = ctx.readRefID(in); RefID r2 = ctx.readRefID(in);
                     int unk = in.getInt();
                     if (r1.isZero() && r2.isZero() && unk == -2) {
                         int len = Short.toUnsignedInt(in.getShort());
                         if (len > 512) throw new IllegalStateException("bad name len");
                         byte[] b = new byte[len]; in.get(b);
                         it.displayName = new String(b, java.nio.charset.StandardCharsets.UTF_8);
                     }
                     return; }
            case 155: { in.get();                                          // Enchantment
                     RefID r = ctx.readRefID(in); int charge = Short.toUnsignedInt(in.getShort());
                     it.ench = String.format("%08X charge=%d", r.FORMID, charge); return; }
            case 156: in.get(); in.get(); it.extraNames.add("Soul"); return;
            case 159: in.get(); in.getInt(); in.getShort(); it.extraNames.add("UniqueId"); return;
            default: {                                                     // delegate to ReSaver
                ChangeFormExtraDataData d = new ChangeFormExtraDataData(in, ctx);
                it.extraNames.add(type + ":" + d.NAME);
            }
        }
    }

    static void skip(ByteBuffer in, int n) { ((Buffer) in).position(in.position() + n); }
    static void log(String s) { if (verbose) System.out.println(s); }
}
