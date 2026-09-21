"""Editable VELKO garment shells over the existing MPFB anatomical rig.
Original skin/face assets are preserved; all garment and hair geometry below is authored here.
"""
import bpy,bmesh,math,random
from mathutils import Vector
ROOT='/Users/jerome/Desktop/jarvis-mac'
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=ROOT+'/ui/assets/avatar/jarvis_v2_3_high.glb')
arm=bpy.data.objects['JARVIS_Armature']; body=bpy.data.objects['JARVIS_Body']
for o in list(bpy.data.objects):
 if o.type=='MESH' and (o.name.startswith(('JARVIS_Top','JARVIS_Sleeve','JARVIS_Trousers','JARVIS_Collar','JARVIS_Shoes','JARVIS_Hair')) or o.name=='Icosphere'):bpy.data.objects.remove(o,do_unlink=True)
def mat(name,c,r=.7,metal=0):
 m=bpy.data.materials.new(name);m.diffuse_color=(*c,1);m.use_nodes=True;p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1);p.inputs['Roughness'].default_value=r;p.inputs['Metallic'].default_value=metal;return m
cloth=mat('VELKO charcoal woven jacket',(.012,.017,.024),.78)
pants=mat('VELKO black technical trousers',(.009,.012,.018),.86)
sole=mat('VELKO sneaker graphite rubber',(.012,.016,.022),.7)
shoe=mat('VELKO sneaker black leather',(.018,.024,.032),.44)
hair=mat('VELKO dark chestnut hair',(.028,.012,.006),.56)
highlight=mat('VELKO warm chestnut strands',(.055,.026,.011),.55)
blue=mat('VELKO azure embroidery',(.025,.25,.61),.4)
metal=mat('VELKO brushed zipper',(.12,.15,.19),.44,.65)
# Fine woven roughness; deliberately subtle, not a noisy bump pattern.
for material in [cloth,pants]:
 n=material.node_tree.nodes; l=material.node_tree.links;p=n.get('Principled BSDF');noise=n.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=330;noise.inputs['Detail'].default_value=2;bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.12;bump.inputs['Distance'].default_value=.0006;l.new(noise.outputs['Fac'],bump.inputs['Height']);l.new(bump.outputs['Normal'],p.inputs['Normal'])
def activate(o):
 bpy.ops.object.select_all(action='DESELECT');o.select_set(True);bpy.context.view_layer.objects.active=o
def apply(o,mod):
 activate(o);bpy.ops.object.modifier_apply(modifier=mod.name)
def bind(o,bone):
 g=o.vertex_groups.new(name=bone);g.add(list(range(len(o.data.vertices))),1,'REPLACE');mod=o.modifiers.new('Anatomical rig','ARMATURE');mod.object=arm;o.parent=arm
for p in body.data.polygons:p.use_smooth=True
# Continuous duplicate topology keeps shoulders/elbows attached and source skin weights.
def garment(name,keep,material,fullness):
 o=body.copy();o.data=body.data.copy();bpy.context.collection.objects.link(o);o.name=name
 if o.data.shape_keys:o.shape_key_clear()
 for mod in list(o.modifiers):o.modifiers.remove(mod)
 bm=bmesh.new();bm.from_mesh(o.data);bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00005);remove=[v for v in bm.verts if not keep(v.co)];bmesh.ops.delete(bm,geom=remove,context='VERTS');bm.to_mesh(o.data);bm.free()
 activate(o)
 try:bpy.ops.mesh.customdata_custom_splitnormals_clear()
 except Exception:pass
 o.data.materials.clear();o.data.materials.append(material)
 for p in o.data.polygons:p.material_index=0;p.use_smooth=True
 # Relax anatomical breast/abdominal detail before adding a real fabric offset.
 smooth=o.modifiers.new('Tailored surface relaxation','SMOOTH');smooth.factor=.85;smooth.iterations=9;apply(o,smooth)
 o.data.update()
 for v in o.data.vertices:
  v.co+=v.normal*fullness
  # Jacket front is a cloth panel, not an imprint of the pectorals.
  x,y,z=v.co
  if name=='VELKO_Jacket' and abs(x)<.165 and 1.06<z<1.445 and y<-.04:
   envelope=-.14+.023*(abs(x)/.165)**2
   v.co.y=min(y,envelope)
 # Two tiny garment wrinkles rather than simulated skin muscles.
 for v in o.data.vertices:
  x,y,z=v.co
  if name=='VELKO_Jacket' and .99<z<1.1:v.co.y+=.003*math.sin(z*160)*max(0,1-abs(x)/.22)
 solid=o.modifiers.new('Fabric thickness 2mm','SOLIDIFY');solid.thickness=.002;solid.offset=0;apply(o,solid)
 sub=o.modifiers.new('Tailoring finish','SUBSURF');sub.levels=1;apply(o,sub)
 mod=o.modifiers.new('Original deform rig','ARMATURE');mod.object=arm
 return o
# Wrist plane is perpendicular to the forearm; a horizontal cut produces a ragged cuff.
def jacket_keep(co):
 x,y,z=co
 if z<.98 or z>1.56:return False
 if abs(x)>.42:
  wrist=Vector((.505,-.203,1.112));v=Vector((abs(x),y,z))-wrist
  if v.dot(Vector((.137,-.196,-.132)).normalized())>0:return False
 return True
jacket=garment('VELKO_Jacket',jacket_keep,cloth,.025)
trousers=garment('VELKO_Trousers',lambda c:.08<c.z<1.10 and abs(c.x)<.3,pants,.021)
# Suppress hidden body triangles: no toes, chest, or buttocks can poke through clothes.
bm=bmesh.new();bm.from_mesh(body.data)
bmesh.ops.delete(bm,geom=[f for f in bm.faces if all(jacket_keep(v.co) or (.0<=v.co.z<1.04 and abs(v.co.x)<.3) for v in f.verts)],context='FACES');bm.to_mesh(body.data);bm.free()
def mesh(name,verts,faces,material,bone):
 m=bpy.data.meshes.new(name);m.from_pydata(verts,[],faces);m.update();o=bpy.data.objects.new(name,m);bpy.context.collection.objects.link(o);m.materials.append(material)
 for p in m.polygons:p.use_smooth=True
 bind(o,bone);return o
def curve_mesh(name,pts,r,material,bone):
 c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.resolution_u=3;c.bevel_depth=r;c.bevel_resolution=2;s=c.splines.new('POLY');s.points.add(len(pts)-1)
 for p,co in zip(s.points,pts):p.co=(*co,1)
 o=bpy.data.objects.new(name,c);bpy.context.collection.objects.link(o);o.data.materials.append(material);activate(o);bpy.ops.object.convert(target='MESH');o=bpy.context.object;bind(o,bone);o.select_set(False);return o
# Stand collar, with an open front split and a visible folded rim.
verts=[];faces=[];N=56
for z,rx,ry in [(1.485,.082,.075),(1.56,.062,.060)]:
 for i in range(N):
  a=.15+(2*math.pi-.3)*i/(N-1);verts.append((rx*math.sin(a),-.018-ry*math.cos(a),z))
for i in range(N-1):faces.append((i,i+1,N+i+1,N+i))
collar=mesh('VELKO_Stand_Collar',verts,faces,cloth,'neck');solid=collar.modifiers.new('Collar thickness','SOLIDIFY');solid.thickness=.006;apply(collar,solid)
curve_mesh('Velko collar piping',verts[N:],.0018,sole,'neck')
# Ribbed cuffs align to the actual wrist axis.
for side in [-1,1]:
 bone='lowerArm_'+('L' if side>0 else 'R');center=Vector((side*.502,-.198,1.117));axis=Vector((side*.137,-.196,-.132)).normalized();u=axis.cross(Vector((0,0,1))).normalized();v=axis.cross(u)
 for d in [-.008,0,.008]:curve_mesh('Velko ribbed cuff',[center+axis*d+.036*(u*math.cos(a)+v*math.sin(a)) for a in [2*math.pi*k/48 for k in range(49)]],.0045,sole,bone)
# Shaped trainer shells: rounded toe box, tall heel collar, continuous rubber sole.
for side in [-1,1]:
 bone='foot_'+('L' if side>0 else 'R');x0=side*.2
 for name,levels,material in [('Sole',[(.008,.06,.174),(.027,.064,.18),(.039,.06,.173)],sole),('Upper',[(.032,.06,.174),(.058,.061,.172),(.087,.053,.151),(.116,.041,.094),(.141,.034,.048)],shoe)]:
  vs=[];fs=[]
  for z,rx,ry in levels:
   cy=-.108 if z<.09 else -.09+(z-.09)*1.2
   for i in range(48):a=2*math.pi*i/48;vs.append((x0+rx*math.cos(a),cy+ry*math.sin(a),z))
  for j in range(len(levels)-1):
   for i in range(48):a=j*48+i;b=j*48+(i+1)%48;fs.append((a,b,b+48,a+48))
  fs.append(tuple(reversed(range(48))));fs.append(tuple((len(levels)-1)*48+i for i in range(48)))
  mesh('VELKO_Sneaker_'+name+('_L' if side>0 else '_R'),vs,fs,material,bone)
 for k in range(5):
  y=-.185+k*.019;z=.09+k*.005
  curve_mesh('Velko sneaker lace',[(x0-.032,y,z),(x0+.032,y+.009,z)],.0022,sole,bone)
 curve_mesh('Velko sneaker blue seam',[(x0+side*.06,-.2,.049),(x0+side*.064,-.1,.05),(x0+side*.049,.02,.06)],.0018,blue,bone)
# Scalp cap fills the volume behind strands; the front hairline is lower than crown.
vs=[];fs=[];N=72;R=18
for j in range(R+1):
 for i in range(N):
  a=2*math.pi*i/N;theta=(.015+(1.52+.3*math.sin(a))*(j/R))
  vs.append((.088*math.sin(theta)*math.cos(a),-.022+.097*math.sin(theta)*math.sin(a),1.681+.101*math.cos(theta)))
for j in range(R):
 for i in range(N):a=j*N+i;b=j*N+(i+1)%N;fs.append((a,b,b+N,a+N))
mesh('VELKO_Scalp',vs,fs,hair,'head')
random.seed(31)
for i in range(950):
 a=random.uniform(0,2*math.pi);theta=random.uniform(.04,1.52+.29*math.sin(a));pts=[]
 for j in range(18):
  t=j/17;aa=a+.32*t;th=max(.025,theta-.3*t);lift=.004+.019*math.sin(math.pi*t);wave=.003*math.sin(t*math.pi*2.4+i)
  pts.append(((.088+lift)*math.sin(th)*math.cos(aa)+.012*t, -.022+(.097+lift)*math.sin(th)*math.sin(aa)+wave,1.681+(.101+lift)*math.cos(th)))
 curve_mesh('Velko hair strand',pts,.0009 if i%3 else .0013,hair if i%8 else highlight,'head')
# Longer forward locks form an asymmetrical fringe over the scalp.
for i in range(170):
 x=random.uniform(-.08,.065);pts=[]
 for j in range(20):
  t=j/19;pts.append((x+.018*math.sin(t*3+i*.02),-.005-.139*t,1.78+.035*math.sin(t*math.pi)-.065*t+.005*math.sin(t*9+i)))
 curve_mesh('Velko hair strand',pts,.0014,hair if i%8 else highlight,'head')
strands=[o for o in bpy.data.objects if o.name.startswith('Velko hair strand')];activate(strands[0])
for o in strands:o.select_set(True)
bpy.ops.object.join();strands[0].name='VELKO_Wavy_Hair'
# Front closure, a stitched placket and discreet logo, above the cloth envelope.
curve_mesh('Velko zipper',[(0,-.145,1.015),(0,-.146,1.2),(0,-.143,1.405),(0,-.099,1.487),(0,-.081,1.552)],.002,metal,'chest')
for side in [-1,1]:
 curve_mesh('Velko placket',[(side*.008,-.146,1.02),(side*.008,-.147,1.2),(side*.008,-.144,1.4)],.0025,cloth,'chest')
 curve_mesh('Velko shoulder seam',[(side*.08,-.07,1.515),(side*.15,-.085,1.485),(side*.215,-.073,1.443)],.0016,blue,'chest')
 curve_mesh('Velko jacket pocket',[(side*.07,-.144,1.10),(side*.14,-.127,1.18)],.002,sole,'spine_01')
curve_mesh('Velko emblem',[(.076,-.142,1.39),(.093,-.146,1.362),(.113,-.139,1.395)],.003,blue,'chest')
for m in body.data.materials:
 if m and m.name.startswith('JARVIS_Skin'):
  bs=m.node_tree.nodes.get('Principled BSDF');bs.inputs['Roughness'].default_value=.62
bpy.ops.object.select_all(action='DESELECT')
for o in bpy.data.objects:
 if o.type in {'MESH','ARMATURE'}:o.select_set(True)
bpy.ops.wm.save_as_mainfile(filepath=ROOT+'/velko/assets/avatar/velko_premium.blend')
bpy.ops.export_scene.gltf(filepath=ROOT+'/velko/assets/avatar/velko_premium.glb',export_format='GLB',use_selection=True,export_animations=False,export_skins=True,export_morph=True)
center=Vector((0,0,1))
bpy.ops.object.camera_add(location=(.2,-3.4,1.42));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,1.0))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.lens=48;bpy.context.scene.camera=cam
for loc,power,size in [((2,-3,4),350,4),((-2,-2,2),150,3),((0,2,3),500,2)]:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.data.energy=power;o.data.size=size;o.rotation_euler=(center-o.location).to_track_quat('-Z','Y').to_euler()
s=bpy.context.scene;s.render.engine='CYCLES';s.cycles.samples=32;s.render.resolution_x=800;s.render.resolution_y=1000;s.render.resolution_percentage=100;s.world=bpy.data.worlds.new('World');s.world.color=(.04,.05,.07);s.render.filepath=ROOT+'/velko/assets/avatar/inspection.png';bpy.ops.render.render(write_still=True)
