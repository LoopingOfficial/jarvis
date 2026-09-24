import unreal

ed = unreal.EditorAssetLibrary
reg = unreal.AssetRegistryHelpers.get_asset_registry()
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_discover.txt", "w", encoding="utf-8")

def log(*args):
    line = " ".join(str(a) for a in args)
    print(line)
    out.write(line + "\n")

def dump_assets(folder):
    log("=" * 60)
    log("FOLDER:", folder)
    try:
        for a in reg.get_assets_by_path(folder, recursive=True):
            log("  %s | %s | %s" % (str(a.asset_class), str(a.asset_name), str(a)))
    except Exception as e:
        log("  ERR", e)

dump_assets("/Game/VELKO")
dump_assets("/Game/Fab/MetaHuman/MHC_Hero")

log("=== All assets anywhere containing VELKO in path ===")
for a in reg.get_all_assets():
    if "VELKO" in str(a.package_name):
        log("  %s | %s | %s" % (str(a.asset_class), str(a.asset_name), str(a)))

log("=== Skeleton/AnimBP/ControlRig/IK assets in project ===")
for a in reg.get_all_assets():
    if str(a.package_name).startswith("/Game"):
        cls = str(a.asset_class)
        if cls in ("Skeleton", "AnimBlueprint", "ControlRigBlueprint", "PhysicsAsset", "SkeletalMesh", "IKRigDefinition", "IKRetargeter", "AnimSequence", "GroomAsset", "PoseAsset", "AnimMontage", "BlendSpace"):
            log("  %s | %s | %s" % (cls, str(a.asset_name), str(a)))
out.close()