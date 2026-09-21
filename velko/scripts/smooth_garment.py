import bpy,math
from mathutils import Vector
ROOT='/Users/jerome/Desktop/jarvis-mac/velko'
bpy.ops.wm.open_mainfile(filepath=ROOT+'/assets/avatar/velko_premium.blend')
arm=bpy.data.objects['JARVIS_Armature'];o=bpy.data.objects['VELKO_Jacket']
# Remove residual anatomical topology at the front of the fitted jacket.
for v in o.data.vertices:
 x,y,z=v.co
 if abs(x)<.19 and .99<z<1.49 and y<-.045:
  a=min(1,max(0,(z-.99)/.13));b=min(1,max(0,(1.49-z)/.10));edge=min(1,max(0,(.19-abs(x))/.045));w=a*b*edge
  envelope=-.155+.025*(abs(x)/.19)**2 + (.003 if v.normal.y>0 else -.001)
  v.co.y=y*(1-w)+envelope*w
# Update normals after reshaping.
o.data.update()
for poly in o.data.polygons:poly.use_smooth=True
bpy.ops.object.select_all(action='DESELECT');o.select_set(True);bpy.context.view_layer.objects.active=o
try:bpy.ops.mesh.customdata_custom_splitnormals_clear()
except Exception:pass
# Keep a soft collar free of overlapping rough cloth at the opening.
for mat in o.data.materials:
 if mat and mat.use_nodes:
  p=mat.node_tree.nodes.get('Principled BSDF');p.inputs['Roughness'].default_value=.85
bpy.ops.object.select_all(action='DESELECT')
for ob in bpy.data.objects:
 if ob.type in {'MESH','ARMATURE'}:ob.select_set(True)
bpy.ops.wm.save_as_mainfile(filepath=ROOT+'/assets/avatar/velko_premium.blend')
bpy.ops.export_scene.gltf(filepath=ROOT+'/assets/avatar/velko_premium.glb',export_format='GLB',use_selection=True,export_animations=False,export_skins=True,export_morph=True)
bpy.ops.object.camera_add(location=(.12,-2.4,1.5));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,1.25))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.lens=48;bpy.context.scene.camera=cam
for loc,power,size in [((2,-3,4),350,4),((-2,-2,2),150,3),((0,2,3),500,2)]:
 bpy.ops.object.light_add(type='AREA',location=loc);ob=bpy.context.object;ob.data.energy=power;ob.data.size=size;ob.rotation_euler=(Vector((0,0,1))-ob.location).to_track_quat('-Z','Y').to_euler()
s=bpy.context.scene;s.render.engine='CYCLES';s.cycles.samples=24;s.render.resolution_x=800;s.render.resolution_y=900;s.render.resolution_percentage=100;s.world=bpy.data.worlds.new('World');s.world.color=(.04,.05,.07);s.render.filepath=ROOT+'/assets/avatar/inspection.png';bpy.ops.render.render(write_still=True)
