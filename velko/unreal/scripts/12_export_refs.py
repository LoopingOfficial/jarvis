import unreal, os, traceback

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_body_export.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
actor_subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
unreal.EditorLoadingAndSavingUtils.load_map("/Game/VELKO/Maps/VELKO_AvatarLab")

subsys = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
mh = ed.load_asset("/Game/VELKO/MHC_VELKO")

avatar = None
for a in actor_subsys.get_all_level_actors():
    if a.get_class().get_name() == "MetaHumanDefaultEditorPipelineActor":
        avatar = a
        break
if avatar is None:
    subsys.try_add_object_to_edit(mh)
    avatar = subsys.spawn_meta_human_actor(mh, keep_transient=False)
    log("avatar spawned for export")

meshes = {}
if avatar:
    for comp in avatar.get_components_by_class(unreal.DebugSkelMeshComponent):
        cname = comp.get_name()
        try:
            m = comp.get_editor_property("skeletal_mesh")
        except Exception:
            m = None
        if m:
            meshes[cname] = m
            log("comp", cname, "->", m.get_path_name())

def export(name, mesh):
    dst = r"C:\Users\jerom\Desktop\jarvis-mac\velko\unreal\anim_source\%s.fbx" % name
    task = unreal.AssetExportTask()
    task.set_editor_property("object", mesh)
    task.set_editor_property("filename", dst)
    task.set_editor_property("automated", True)
    unreal.Exporter.run_asset_export_tasks([task])
    log("EXPORT", name, "exists:", os.path.exists(dst), "size:", os.path.getsize(dst) if os.path.exists(dst) else 0)

for part in ("Body", "Face"):
    if part in meshes:
        export("MHC_VELKO_%s_REFERENCE" % part, meshes[part])
        sm = meshes[part]
        # evidence: morph target count
        try:
            morphs = sm.get_editor_property("morph_targets")
            log(part, "morph_targets count:", len(morphs) if morphs else 0)
        except Exception as e:
            log(part, "morph_targets read fail:", e)
        # skeleton info
        try:
            sk = sm.get_editor_property("skeleton")
            log(part, "skeleton:", sk.get_path_name() if sk else None)
        except Exception:
            pass

out.close()