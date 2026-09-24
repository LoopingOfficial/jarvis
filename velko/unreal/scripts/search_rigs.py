import unreal
out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_rigs.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
paths = ed.list_assets("/Game", recursive=True, include_folder=False)
log("project assets:", len(paths))
for p in paths:
    ad = ed.find_asset_data(p if str(p).startswith("/") else "/" + p)
    if ad:
        try:
            cls = str(ad.asset_class_path.asset_name)
        except Exception:
            cls = str(ad.asset_class)
        if cls in ("IKRigDefinition", "IKRetargeter", "AnimSequence", "AnimBlueprint", "ControlRigBlueprint", "Skeleton", "SkeletalMesh"):
            log(cls, p)
out.close()