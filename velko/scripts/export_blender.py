"""Import the live THREE scene's mesh manifest into Blender and export editable masters.
Run: blender --background --python scripts/export_blender.py -- exports/scene.json
"""
import bpy, json, sys, math
from pathlib import Path
from mathutils import Matrix
source=Path(sys.argv[sys.argv.index('--')+1])
out=source.parent
payload=json.loads(source.read_text())

def build(manifest, label):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene
    scene.world=bpy.data.worlds.new('Velko ambient')
    scene.world.color=(.055,.065,.09)
    objects={}
    material_cache={}
    for entry in manifest['objects']:
        name=entry['name']
        if entry.get('positions'):
            raw=entry['positions']; vertices=list(zip(raw[0::3],raw[1::3],raw[2::3]))
            idx=entry.get('indices') or list(range(len(vertices)))
            faces=list(zip(idx[0::3],idx[1::3],idx[2::3]))
            mesh=bpy.data.meshes.new(name+'_geometry'); mesh.from_pydata(vertices,[],faces); mesh.update()
            obj=bpy.data.objects.new(name,mesh)
            uv=entry.get('uv')
            if uv:
                layer=mesh.uv_layers.new(name='UVMap')
                for poly in mesh.polygons:
                    for loop in poly.loop_indices:
                        vi=mesh.loops[loop].vertex_index
                        layer.data[loop].uv=uv[vi*2:vi*2+2]
            mat=entry.get('material',{}); key=json.dumps(mat,sort_keys=True)
            if key not in material_cache:
                m=bpy.data.materials.new(mat.get('name') or name+'_material'); m.use_nodes=True
                bs=m.node_tree.nodes.get('Principled BSDF')
                color=mat.get('color',[.3,.3,.3]); bs.inputs['Base Color'].default_value=(*color[:3],mat.get('opacity',1))
                bs.inputs['Roughness'].default_value=mat.get('roughness',.5)
                bs.inputs['Metallic'].default_value=mat.get('metalness',0)
                bs.inputs['Emission Color'].default_value=(*mat.get('emissive',[0,0,0])[:3],1)
                bs.inputs['Emission Strength'].default_value=mat.get('emissiveIntensity',1)
                m.diffuse_color=(*color[:3],1)
                if mat.get('texture'):
                    path=source.parent.parent / mat['texture']
                    if path.exists():
                        tex=m.node_tree.nodes.new('ShaderNodeTexImage'); tex.image=bpy.data.images.load(str(path)); tex.image.pack()
                        m.node_tree.links.new(tex.outputs['Color'],bs.inputs['Base Color'])
                material_cache[key]=m
            mesh.materials.append(material_cache[key])
            for polygon in mesh.polygons: polygon.use_smooth=entry.get('smooth',True)
        else:
            obj=bpy.data.objects.new(name,None); obj.empty_display_size=.08
        scene.collection.objects.link(obj); objects[name]=obj
    for entry in manifest['objects']:
        obj=objects[entry['name']]
        if entry.get('parent') in objects: obj.parent=objects[entry['parent']]
        raw=entry.get('matrix')
        if raw: obj.matrix_basis=Matrix([raw[i::4] for i in range(4)])
    # THREE is Y-up. Conversion root preserves all authored local transforms.
    root=bpy.data.objects.new(label+'_Yup_to_Zup',None); scene.collection.objects.link(root)
    root.rotation_euler[0]=math.pi/2
    for obj in objects.values():
        if not obj.parent: obj.parent=root
    scene['source']='VELKO live procedural runtime'
    scene['animation_system']='Named transform hierarchy; procedural runtime/avatar.js'
    scene['units']='metres'
    scene.unit_settings.system='METRIC'
    bpy.context.view_layer.update()
    return objects

for section,blend,glb in [('avatar','velko_master.blend','velko_runtime.glb'),('office','velko_office.blend','velko_office.glb')]:
    if section not in payload: continue
    objects=build(payload[section],section)
    clips_path=source.parent.parent/'animations'/'clips.json'
    if section=='avatar' and clips_path.exists():
        scene=bpy.context.scene; scene.render.fps=12; scene.frame_end=25
        for clip in json.loads(clips_path.read_text()):
            actions={}
            for track in clip['tracks']:
                obj=objects.get(track['name'])
                if not obj: continue
                if obj.name not in actions:
                    obj.animation_data_create()
                    action=bpy.data.actions.new(clip['name']+'_'+obj.name)
                    obj.animation_data.action=action; actions[obj.name]=action
                obj.location=track['position']; obj.rotation_euler=track['rotation']; obj.scale=track['scale']
                for key in ['location','rotation_euler','scale']: obj.keyframe_insert(data_path=key,frame=track['frame'])
            for name,action in actions.items():
                obj=objects[name]; obj.animation_data.action=None
                nla=obj.animation_data.nla_tracks.new(); nla.name=clip['name']; nla.strips.new(clip['name'],1,action)
        scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(out/blend))
    bpy.ops.export_scene.gltf(filepath=str(out/glb),export_format='GLB',export_yup=True,export_animations=True,export_extras=True)
    print('VELKO_EXPORTED',section,len(objects),str(out/glb))
