import unreal, os, traceback

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_body_export.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
lvl = unreal.EditorLevelLibrary.load_level("/Game/VELKO/Maps/VELKO_AvatarLab")
log("level loaded:", bool(lvl))

body_mesh = None
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    log("actor:", actor.get_class().get_name(), actor.get_actor_label())
    for comp in actor.get_components_by_class(unreal.DebugSkelMeshComponent):
        cname = comp.get_name()
        for prop in ("skeletal_mesh", "mesh"):
            try:
                m = comp.get_editor_property(prop)
                if m:
                    log(" component", cname, "prop", prop, "->", m.get_path_name())
                    if cname == "Body" or "Body" in str(m.get_name()):
                        body_mesh = m
            except Exception as e:
                pass

if not body_mesh:
    log("NO BODY MESH FOUND — cannot export")
    out.close(); raise SystemExit(1)

DEST_FBX = r"C:\Users\jerom\Desktop\jarvis-mac\velko\unreal\anim_source\MHC_VELKO_BODY_REFERENCE.fbx"
opts = None
for optname in ("FbxExportOption", "FbxExportOptions"):
    if hasattr(unreal, optname):
        opts = getattr(unreal, optname)()
        log("using export options class:", optname)
        for pname, val in (("export_skeleton", True), ("level_of_detail", True),
                           ("export_morph_targets", True), ("use_mesh_sequence", False),
                           ("transform_offset", None)):
            if val is None:
                continue
            try:
                opts.set_editor_property(pname, val)
                log(" opt", pname, "=", val)
            except Exception as e:
                log(" opt miss", pname)

task = unreal.AssetExportTask()
task.set_editor_property("object", body_mesh)
task.set_editor_property("filename", DEST_FBX)
task.set_editor_property("automated", True)
task.set_editor_property("save", True)
if opts is not None:
    try:
        task.set_editor_property("options", opts)
    except Exception as e:
        log("options attach fail:", e)

try:
    unreal.Exporter.run_asset_export_tasks([task])
    log("export task ran; file exists:", os.path.isfile(DEST_FBX),
        "size:", os.path.getsize(DEST_FBX) if os.path.isfile(DEST_FBX) else 0)
except Exception:
    traceback.print_exc(file=out)

out.close()