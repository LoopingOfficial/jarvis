import unreal

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_rig_validate.txt", "w", encoding="utf-8")
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

for comp in avatar.get_components_by_class(unreal.DebugSkelMeshComponent):
    cname = comp.get_name()
    try:
        sm = comp.get_skeletal_mesh_asset()
    except Exception:
        try:
            sm = comp.get_skeletal_mesh()
        except Exception:
            sm = None
    if sm is None:
        try:
            sm = comp.get_editor_property("skeletal_mesh")
        except Exception:
            sm = None
    log("COMP", cname, "class:", sm.get_class().get_name(), "path:", sm.get_path_name())
    for prop in ("skeleton", "skinned_asset"):
        try:
            v = sm.get_editor_property(prop)
            if v:
                log("   %s:" % prop, v.get_path_name())
        except Exception:
            pass
    for prop in ("morph_targets",):
        try:
            v = sm.get_editor_property(prop)
            log("   %s count:" % prop, len(v) if v else 0)
        except Exception as e:
            log("   %s read fail:" % prop, e)

log("---- AnimSequences ----")
for seq in ("VELKO_Idle_Anim", "VELKO_Walk_Anim", "VELKO_Sit_Anim", "VELKO_Turn_Anim"):
    a = ed.load_asset("/Game/VELKO/Anim/" + seq)
    if a is None:
        log("  MISSING", seq); continue
    sk = None
    try:
        sk = a.get_editor_property("skeleton")
    except Exception:
        pass
    try:
        nf = a.get_editor_property("num_frames")
    except Exception:
        nf = None
    log("  %s frames=%s skeleton=%s" % (seq, nf, sk.get_path_name() if sk else None))

log("---- IK assets ----")
for p in ("/Game/VELKO/Rig/IK_VELKO", "/Game/VELKO/Rig/RT_VELKO"):
    a = ed.load_asset(p)
    log("  %s class=%s" % (p, a.get_class().get_name() if a else "MISSING"))

out.close()