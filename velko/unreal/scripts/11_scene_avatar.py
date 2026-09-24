import unreal, traceback

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_scene.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
lvl = unreal.EditorLevelLibrary
actor_subsys = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
unreal.EditorLoadingAndSavingUtils.load_map("/Game/VELKO/Maps/VELKO_AvatarLab")
log("map loaded")

subsys = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
mh = ed.load_asset("/Game/VELKO/MHC_VELKO")
MH_CLS = unreal.MetaHumanDefaultEditorPipelineActor if hasattr(unreal, "MetaHumanDefaultEditorPipelineActor") else None
log("MH pipeline class type:", MH_CLS)

avatar = None
for a in actor_subsys.get_all_level_actors():
    if a.get_class().get_name() == "MetaHumanDefaultEditorPipelineActor":
        avatar = a
        log("avatar already in level:", a.get_actor_label())

if avatar is None:
    subsys.try_add_object_to_edit(mh)
    log("added for editing")
    avatar = subsys.spawn_meta_human_actor(mh, keep_transient=False)
    log("spawned:", avatar.get_actor_label() if avatar else "None")
    if avatar:
        try:
            avatar.set_actor_transform(unreal.Transform(location=unreal.Vector(0, 0, 0),
                                                        rotation=unreal.Rotator(0, 0, 0),
                                                        scale3d=unreal.Vector(1, 1, 1)), False)
            log("avatar transform set")
        except Exception as e:
            log("transform set fail:", e)

# dump components + mesh refs
if avatar:
    for comp in avatar.get_components_by_class(unreal.DebugSkelMeshComponent):
        cname = comp.get_name()
        try:
            m = comp.get_editor_property("skeletal_mesh")
            ms = m.get_path_name() if m else None
        except Exception:
            ms = None
        sk = None
        try:
            sko = comp.get_editor_property("skeletal_mesh_asset")
        except Exception:
            sko = None
        if sko:
            try:
                sk = sko.get_path_name()
            except Exception:
                sk = str(sko)
        log("  COMP", cname, "mesh:", ms, "skel:", sk)

# IK target markers
markers = [
    ("IKTarget_LeftFoot",     unreal.Vector(-0.10, 0, 0.04)),
    ("IKTarget_RightFoot",    unreal.Vector(0.10, 0, 0.04)),
    ("IKTarget_LeftHand",     unreal.Vector(-0.45, 0.10, 1.10)),
    ("IKTarget_RightHand",    unreal.Vector(0.45, 0.10, 1.10)),
    ("IKTarget_Keyboard",     unreal.Vector(0, -0.40, 0.80)),
    ("IKTarget_Mouse",        unreal.Vector(0.30, -0.50, 0.80)),
    ("IKTarget_ChairSit",     unreal.Vector(0, 0.50, 0.50)),
    ("IKTarget_ScreenLookLeft",   unreal.Vector(-1.10, -0.95, 1.35)),
    ("IKTarget_ScreenLookCenter", unreal.Vector(0, -1.05, 1.35)),
    ("IKTarget_ScreenLookRight",  unreal.Vector(1.10, -0.95, 1.35)),
]

existing = set(a.get_actor_label() for a in actor_subsys.get_all_level_actors())
for label, loc in markers:
    if label in existing:
        log("marker exists:", label)
        continue
    actor = actor_subsys.spawn_actor_from_class(unreal.StaticMeshActor, loc)
    actor.set_actor_label(label)
    comp = actor.get_components_by_class(unreal.StaticMeshComponent)[0]
    comp.set_editor_property("static_mesh", ed.load_asset("/Engine/BasicShapes/Sphere"))
    actor.set_actor_scale3d(unreal.Vector(0.16, 0.16, 0.16))
    log("spawned marker:", label)

lvl.save_current_level()
log("LEVEL SAVED")

count_avatar = sum(1 for a in actor_subsys.get_all_level_actors() if a.get_class().get_name() == "MetaHumanDefaultEditorPipelineActor")
log("FINAL avatar count in level:", count_avatar, "total actors:", len(actor_subsys.get_all_level_actors()))
out.close()