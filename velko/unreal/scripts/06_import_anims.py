import unreal, os, traceback

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_import.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

DEST = "/Game/VELKO/Anim"
if not ed.does_directory_exist(DEST):
    ed.make_directory(DEST)

# wipe previous partial imports
for p in ed.list_assets(DEST, recursive=False, include_folder=False):
    full = p if str(p).startswith("/") else DEST + "/" + p
    if ed.does_asset_exist(full):
        ed.delete_asset(full)
        log("WIPED", full)

SKEL_PATH = "/Game/Fab/MetaHuman/MHC_Hero/MetaHumans/Common/Female/Medium/NormalWeight/Body/metahuman_base_skel"
skel = ed.load_asset(SKEL_PATH)
log("skeleton:", skel.get_path_name() if skel else "None", "class:", skel.get_class().get_name() if skel else "-")

SRC = r"C:\Users\jerom\Desktop\jarvis-mac\velko\unreal\anim_source"

def sp(obj, name, value):
    try:
        obj.set_editor_property(name, value)
    except Exception as e:
        log("  note prop %s:", name, e)

for sub in ("VELKO_Idle", "VELKO_Walk", "VELKO_Sit", "VELKO_Turn"):
    fbx_path = os.path.join(SRC, sub + ".fbx")
    opts = unreal.FbxImportUI()
    sp(opts, "import_as_skeletal", True)
    sp(opts, "import_mesh", True)
    sp(opts, "import_animations", True)
    sp(opts, "import_materials", False)
    sp(opts, "import_textures", False)
    sp(opts, "import_rigid_mesh", False)
    sp(opts, "create_physics_asset", False)
    sp(opts, "automated_import_should_detect_type", True)
    sp(opts, "skeleton", skel)

    task = unreal.AssetImportTask()
    task.set_editor_property("filename", fbx_path)
    task.set_editor_property("destination_path", DEST)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("options", opts)

    try:
        tools.import_asset_tasks([task])
        log("IMPORT", sub, "->", task.get_editor_property("imported_object_paths"))
    except Exception:
        log("FAIL import task", sub)
        traceback.print_exc(file=out)

# verify & clean up proxy meshes
log("--- FINAL ASSETS ---")
for p in ed.list_assets(DEST, recursive=False, include_folder=False):
    full = p if str(p).startswith("/") else DEST + "/" + p
    ad = ed.find_asset_data(full)
    cls = str(ad.asset_class_path.asset_name) if hasattr(ad, "asset_class_path") else str(ad.asset_class)
    a = ed.load_asset(full)
    if a is None:
        log(full, "class:", cls, "LOAD FAILED"); continue
    if cls == "AnimSequence":
        nf = None; sk = None; rk = None; ls = None
        for prop in ("num_frames", "num_sampled_keys", "sequence_length", "rate_scale", "rate"):
            try:
                v = a.get_editor_property(prop)
                if prop == "num_frames": nf = v
                if prop == "rate": rk = v
                if prop == "sequence_length": ls = v
            except Exception:
                pass
        try:
            sk = a.get_editor_property("skeleton")
        except Exception:
            pass
        ok = "OK" if (sk and sk.get_path_name() == skel.get_path_name()) else "SKELETON MISMATCH"
        log("ANIM", full, "num_frames:", nf, "seq_len:", ls, "rate:", rk,
            "skeleton:", sk.get_path_name() if sk else None, ok)
    elif cls == "SkeletalMesh":
        ed.delete_asset(full)
        log("PROXY_DELETED", full)

out.close()