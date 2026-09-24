import unreal
import traceback

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_build.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
elevlib = unreal.EditorLevelLibrary
subsys = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)

MAP_PATH = "/Game/VELKO/Maps/VELKO_AvatarLab"

if ed.does_asset_exist(MAP_PATH):
    ed.delete_asset(MAP_PATH)
    log("deleted pre-existing", MAP_PATH)

result = elevlib.new_level(MAP_PATH)
log("new_level result:", result)

mh = ed.load_asset("/Game/VELKO/MHC_VELKO")
log("character loaded:", mh.get_class().get_name())

added = subsys.try_add_object_to_edit(mh)
log("try_add_object_to_edit:", added)

actor = None
try:
    actor = subsys.spawn_meta_human_actor(mh, keep_transient=False)
    log("spawned actor:", actor.get_name() if actor else "None", "class:", actor.get_class().get_name() if actor else "-")
except Exception as e:
    log("spawn failed:", e)
    traceback.print_exc(file=out)

# floor via class spawn
try:
    plane = ed.load_asset("/Engine/BasicShapes/Plane")
    log("plane asset:", plane)
    if plane:
        floor_actor = elevlib.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
        floor_actor.set_actor_label("VELKO_Floor")
        floor_actor.set_actor_scale3d(unreal.Vector(60, 60, 60))
        smcs = floor_actor.get_components_by_class(unreal.StaticMeshComponent)
        if smcs:
            smcs[0].set_editor_property("static_mesh", plane)
            smcs[0].set_mobility(unreal.ComponentMobility.STATIC)
        log("floor ok")
except Exception as e:
    log("floor fail:", e)
    traceback.print_exc(file=out)

try:
    light = elevlib.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(300, 300, 500), unreal.Rotator(-40, 30, 0))
    light.set_actor_label("VELKO_KeyLight")
    light.set_actor_scale3d(unreal.Vector(4, 4, 4))
    log("light ok")
except Exception as e:
    log("light fail:", e)

def make_cam(label, loc, look_at):
    try:
        rot = unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*loc), unreal.Vector(*look_at))
        cam = elevlib.spawn_actor_from_class(unreal.CameraActor, unreal.Vector(*loc), rot)
        cam.set_actor_label(label)
        camcomps = cam.get_components_by_class(unreal.CameraComponent)
        if camcomps:
            camcomps[0].set_editor_property("field_of_view", 60.0)
        return True
    except Exception as e:
        log("cam fail %s: %s" % (label, e))
        return False

make_cam("VELKO_Cam_Front", (0, 350, 130), (0, 0, 110))
make_cam("VELKO_Cam_ThreeQuarter", (130, 300, 120), (0, 20, 110))
make_cam("VELKO_Cam_Profile", (360, 60, 120), (0, 40, 110))
make_cam("VELKO_Cam_FullBody", (0, 550, 150), (0, 0, 90))

if actor:
    log("=== actor components ===")
    for c in actor.get_components_by_class(unreal.ActorComponent):
        log("  comp:", c.get_name(), "-", c.get_class().get_name())
    skel_comps = [c for c in actor.get_components_by_class(unreal.SkeletalMeshComponent) if c]
    log("skeletal mesh components:", len(skel_comps))
    for sc in skel_comps:
        try:
            sk = sc.get_editor_property("skeletal_mesh")
            if sk:
                log("  SM:", sk.get_name())
                skel = sk.get_editor_property("skeleton")
                log("    Skeleton:", skel.get_name() if skel else "None")
        except Exception as e:
            log("    insp err:", e)

try:
    elevlib.save_current_level()
    log("level saved")
except Exception as e:
    log("save fail:", e)
    traceback.print_exc(file=out)

if actor:
    subsys.remove_object_to_edit(mh)
    log("removed from edit")

log("DONE")
out.close()