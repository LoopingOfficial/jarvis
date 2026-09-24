import unreal, traceback

out = open(r"C:\Users\jerom\AppData\Local\Temp\opencode\velko_ik_build.txt", "w", encoding="utf-8")
def log(*a):
    s = " ".join(str(x) for x in a); print(s); out.write(s + "\n"); out.flush()

ed = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

RIGDIR = "/Game/VELKO/Rig"
SKEL = "/Game/Fab/MetaHuman/MHC_Hero/MetaHumans/Common/Female/Medium/NormalWeight/Body/metahuman_base_skel"
skeleton = ed.load_asset(SKEL)
PREVIEW = "/Game/Fab/MetaHuman/MHC_Hero/MetaHumans/MHC_Hero/SKM_Cloth.SKM_Cloth"
preview = ed.load_asset(PREVIEW)

ik_path = RIGDIR + "/IK_VELKO"
rt_path = RIGDIR + "/RT_VELKO"
ik_rig = ed.load_asset(ik_path)
ret = ed.load_asset(rt_path)

if ik_rig:
    ctrl = unreal.IKRigController.get_controller(ik_rig)
    try:
        ctrl.set_skeletal_mesh(skeletal_mesh=preview)
        log("IK set_skeletal_mesh ok")
    except Exception as e:
        log("IK set_skeletal_mesh fail:", e)
    try:
        ctrl.set_root_bone(root_bone_name="pelvis", solver_index=0)
        log("IK root bone -> pelvis")
    except Exception as e:
        log("IK set_root_bone fail:", e)
    try:
        ctrl.set_root_motion_bone(root_bone_name="pelvis")
        log("IK root motion -> pelvis")
    except Exception as e:
        log("IK set_root_motion_bone fail:", e)
    chains = ctrl.get_retarget_chains()
    log("IK chains:", len(chains), [str(c.chain_name) for c in chains])
    ed.save_asset(ik_path)

if ret and ik_rig:
    rctrl = unreal.IKRetargeterController.get_controller(ret)
    rctrl.remove_all_ops()
    def set_part(part_enum, kind):
        try:
            rctrl.set_ik_rig(source_or_target=part_enum, ik_rig=ik_rig)
            log("RT rig set for", kind)
        except Exception as e:
            log("RT set_ik_rig", kind, "fail:", e)
        try:
            rctrl.set_preview_mesh(source_or_target=part_enum, preview_mesh=preview)
            log("RT preview mesh set for", kind)
        except Exception as e:
            log("RT set_preview_mesh", kind, "fail:", e)
    set_part(unreal.RetargetSourceOrTarget.SOURCE, "SOURCE")
    set_part(unreal.RetargetSourceOrTarget.TARGET, "TARGET")
    try:
        rctrl.auto_map_chains(auto_map_type=unreal.AutoMapChainType.EXACT, force_remap=False)
        log("RT auto map chains (EXACT) ok")
    except Exception as e:
        log("RT auto_map fail:", e)
    try:
        rctrl.add_default_ops()
        log("RT default ops added")
    except Exception as e:
        log("RT add_default_ops fail:", e)
    ed.save_asset(rt_path)
    log("RT ops:", rctrl.get_num_retarget_ops(),
        "has_source:", ret.has_source_ik_rig(),
        "has_target:", ret.has_target_ik_rig())

out.close()