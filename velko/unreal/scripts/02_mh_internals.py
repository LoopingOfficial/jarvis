import unreal

ed = unreal.EditorAssetLibrary
reg = unreal.AssetRegistryHelpers.get_asset_registry()
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_mh.txt", "w", encoding="utf-8")

def log(*args):
    line = " ".join(str(a) for a in args)
    print(line)
    out.write(line + "\n")

mh = ed.load_asset("/Game/VELKO/MHC_VELKO")
log("Loaded class:", mh.get_class().get_name() if mh else "None")

cls = mh.get_class()
chain = [cls.get_name()]
try:
    while cls:
        cls = cls.super_class
        if cls:
            chain.append(cls.get_name())
except Exception as e:
    log("chain err", e)
log("Class chain:", " <- ".join(chain))

log("=== members of MetaHumanCharacter python class ===")
for attr in sorted(dir(unreal.MetaHumanCharacter)):
    if attr.startswith("_"):
        continue
    try:
        v = getattr(unreal.MetaHumanCharacter, attr)
        if callable(v) is False:
            log("  attr", attr, "=", v)
    except Exception:
        pass

log("=== try reading editor props ===")
candidate = []
for attr in dir(mh):
    if attr.startswith("_") or attr in ("get_class", "get_editor_property"):
        continue
    candidate.append(attr)
log("props on instance: %d" % len(candidate))
for p in candidate:
    try:
        v = mh.get_editor_property(p)
        if v is not None:
            log("  %s = %s" % (p, v))
    except Exception:
        pass

# referenced packages
dep = reg.get_dependencies("/Game/VELKO/MHC_VELKO")
log("=== dependencies (%d) ===" % len(dep))
for d in dep:
    s = str(d)
    if any(k in s for k in ["Skeleton", "Anim", "Groom", "Rig", "Body", "Face", "MetaHuman", "Physics", "Morph"]):
        log("  DEP", s)

out.close()