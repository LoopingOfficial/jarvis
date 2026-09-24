import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_collection.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n")

ed = unreal.EditorAssetLibrary
mh = ed.load_asset("/Game/VELKO/MHC_VELKO")
coll = mh.get_editor_property("internal_collection")
log("InternalCollection:", coll)
if coll:
    log("get_all_item_keys:")
    for k in coll.get_all_item_keys():
        name = str(k)
        try:
            dn = str(coll.get_item_display_name(k))
        except Exception:
            dn = "?"
        try:
            slot = str(coll.get_item_slot_name(k))
        except Exception:
            slot = "?"
        log("  slot=%s key=%s disp=%s" % (slot, name, dn))
    log("slot_names:", [str(x) for x in coll.get_slot_names()])

# dependencies of the character package
reg = unreal.AssetRegistryHelpers.get_asset_registry()
deps = reg.get_dependencies("/Game/VELKO/MHC_VELKO")
log("=== deps on character (%d) ===" % len(deps))
import collections
counts = collections.Counter()
for d in deps:
    s = str(d)
    counts[s] += 1
for s, c in counts.most_common():
    log("  %s  (%d)" % (s, c))
out.close()