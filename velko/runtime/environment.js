// VELKO studio: textured architectural materials, soft upholstery and twilight glazing.
// Canvas material textures are original procedural artwork. Dimensions in metres.
export class VelkoEnvironmentController {
  constructor(THREE, scene) {
    this.THREE = THREE; this.scene = scene; this.screens = []; this.targets = {}; this.fans = []; this.cityLights = [];
    const T = THREE;
    this.group = new T.Group(); this.group.name = 'VELKO_PremiumOffice'; scene.add(this.group);
    const mat = (color, roughness=.65, metalness=0) => new T.MeshStandardMaterial({color,roughness,metalness});
    const M = this.materials = {wood:mat('#453023'),woodEdge:mat('#231b16'),black:mat('#10171d',.45,.35),stone:mat('#25282a',.35),fabric:mat('#343c42'),pillow:mat('#666a65'),silver:mat('#8c979e',.24,.85),leaf:mat('#234735'),pot:mat('#55564e'),paper:mat('#a49b83')};
    // Deterministic, original material maps: grain, mineral veining and woven linen.
    const surface=(kind,base)=>{const c=document.createElement('canvas');c.width=c.height=512;const g=c.getContext('2d');g.fillStyle=base;g.fillRect(0,0,512,512);let seed=31;const noise=()=>{seed=(seed*16807)%2147483647;return seed/2147483647;};
      if(kind==='wood'){for(let i=0;i<1600;i++){const y=noise()*512;g.strokeStyle=`rgba(${noise()>.5?'218,162,106':'19,10,5'},${.025+noise()*.08})`;g.lineWidth=.3+noise()*1.3;g.beginPath();g.moveTo(0,y);g.bezierCurveTo(150,y+noise()*8,350,y-noise()*8,512,y);g.stroke();}}
      else if(kind==='stone'){for(let i=0;i<12000;i++){g.fillStyle=`rgba(160,158,148,${noise()*.05})`;g.fillRect(noise()*512,noise()*512,2,2);}for(let k=0;k<9;k++){g.strokeStyle='rgba(205,197,175,.07)';g.lineWidth=.3+noise();g.beginPath();g.moveTo(noise()*512,0);g.bezierCurveTo(noise()*512,170,noise()*512,330,noise()*512,512);g.stroke();}}
      else {for(let i=0;i<512;i+=3){g.strokeStyle=i%2?'rgba(255,255,255,.065)':'rgba(0,0,0,.2)';g.lineWidth=1;g.beginPath();g.moveTo(i,0);g.lineTo(i,512);g.stroke();g.beginPath();g.moveTo(0,i);g.lineTo(512,i);g.stroke();}}
      const t=new T.CanvasTexture(c);t.colorSpace=T.SRGBColorSpace;t.wrapS=t.wrapT=T.RepeatWrapping;t.repeat.set(kind==='wood'?2:3,kind==='wood'?4:3);t.anisotropy=4;return t;};
    M.wood.map=surface('wood','#775039');M.wood.color.set('#ffffff');M.wood.roughness=.53;
    M.woodEdge.map=surface('wood','#39281f');M.woodEdge.color.set('#ffffff');
    M.stone.map=surface('stone','#323330');M.stone.color.set('#ffffff');M.stone.roughness=.31;
    M.fabric.map=surface('fabric','#343b3b');M.fabric.color.set('#ffffff');M.fabric.roughness=.96;
    M.pillow.map=surface('fabric','#938370');M.pillow.color.set('#ffffff');M.pillow.roughness=.95;
    M.blue=new T.MeshStandardMaterial({color:'#188bb7',emissive:'#007ace',emissiveIntensity:2});
    M.warm=new T.MeshStandardMaterial({color:'#fff0ce',emissive:'#ffd6a0',emissiveIntensity:2.4});
    const box = this.box = (name,w,h,d,x,y,z,m=M.black,parent=this.group) => { const o=new T.Mesh(new T.BoxGeometry(w,h,d),m);o.name=name;o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;};
    const rounded=(name,w,h,d,x,y,z,m=M.black,parent=this.group,r=.045)=>{r=Math.min(r,w*.45,h*.45,d*.45);const geom=new T.BoxGeometry(w,h,d,6,6,6),pos=geom.attributes.position,v=new T.Vector3(),inner=new T.Vector3();for(let i=0;i<pos.count;i++){v.fromBufferAttribute(pos,i);inner.set(Math.max(-w/2+r,Math.min(w/2-r,v.x)),Math.max(-h/2+r,Math.min(h/2-r,v.y)),Math.max(-d/2+r,Math.min(d/2-r,v.z)));v.sub(inner).normalize().multiplyScalar(r).add(inner);pos.setXYZ(i,v.x,v.y,v.z);}geom.computeVertexNormals();const o=new T.Mesh(geom,m);o.name=name;o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;};
    const cyl = this.cyl = (name,r1,r2,h,x,y,z,m=M.black,parent=this.group) => {const o=new T.Mesh(new T.CylinderGeometry(r1,r2,h,20),m);o.name=name;o.position.set(x,y,z);o.castShadow=true;o.receiveShadow=true;parent.add(o);return o;};
    const sphere=(name,r,x,y,z,m,parent=this.group)=>{const o=new T.Mesh(new T.SphereGeometry(r,16,10),m);o.name=name;o.position.set(x,y,z);parent.add(o);return o;};
    const point=(name,x,y,z)=>{const o=new T.Object3D();o.name=name;o.position.set(x,y,z);this.group.add(o);this.targets[name]=o;return o;};
    box('OakFloor',10,.15,10,0,-.09,0,M.woodEdge);
    for(let i=0;i<20;i++) box('WideOakFloorboard_'+i,.49,.022,10,-4.75+i*.5,.001,0,i%3===0?M.wood:M.woodEdge);
    box('LeftWall',.14,3.6,10,-5,1.7,0,M.stone);box('RightWall',.14,3.6,10,5,1.7,0,M.stone);
    box('RearWindowBase',10,.35,.18,0,.13,-4.7,M.black);
    box('RearWindowHeader',10,.24,.25,0,3.35,-4.7,M.black);
    for(let x=-5;x<=5;x+=2)box('WindowMullion',.075,3.2,.12,x,1.78,-4.7,M.black);
    // Distant, atmospheric skyline painted as one low-cost panorama, softened by glazing.
    let seed=347;const rnd=()=>{seed=(seed*16807)%2147483647;return(seed-1)/2147483646;};
    const skyCanvas=document.createElement('canvas');skyCanvas.width=2048;skyCanvas.height=1024;const sc=skyCanvas.getContext('2d');const grad=sc.createLinearGradient(0,0,0,1024);grad.addColorStop(0,'#0b192d');grad.addColorStop(.48,'#435775');grad.addColorStop(.7,'#ad8a79');grad.addColorStop(.84,'#4c515d');grad.addColorStop(1,'#141e2b');sc.fillStyle=grad;sc.fillRect(0,0,2048,1024);
    for(let layer=0;layer<3;layer++){sc.fillStyle=['#596074','#394557','#202d3d'][layer];for(let x=0;x<2048;){const w=8+rnd()*28,h=15+rnd()*95+layer*22,y=780+layer*65-h;sc.fillRect(x,y,w,h+230);if(rnd()>.9)sc.fillRect(x+w*.48,y-18,1,20);for(let row=y+5;row<940;row+=6)for(let col=x+3;col<x+w-2;col+=5){if(rnd()>.48)continue;sc.fillStyle=rnd()>.35?'rgba(247,202,142,.4)':'rgba(147,189,211,.35)';sc.fillRect(col,row,1.5,2);sc.fillStyle=['#596074','#394557','#202d3d'][layer];}x+=w+3+rnd()*7;}}
    const haze=sc.createLinearGradient(0,620,0,900);haze.addColorStop(0,'rgba(207,163,139,0)');haze.addColorStop(.5,'rgba(207,163,139,.1)');haze.addColorStop(1,'rgba(41,57,75,0)');sc.fillStyle=haze;sc.fillRect(0,620,2048,280);
    const skyTexture=new T.CanvasTexture(skyCanvas);skyTexture.colorSpace=T.SRGBColorSpace;
    const sky=new T.Mesh(new T.PlaneGeometry(42,17),new T.MeshBasicMaterial({map:skyTexture}));sky.name='TwilightCityPanorama';sky.position.set(0,5,-15);this.group.add(sky);
    // Window reveals and translucent glass retain the city view and give the room depth.
    const glassMat=new T.MeshPhysicalMaterial({color:'#a9c5da',transparent:true,opacity:.055,roughness:.14,metalness:.1,depthWrite:false});
    for(let x=-4;x<5;x+=2){box('WindowGlass',1.92,3.0,.008,x,1.78,-4.76,glassMat);box('WindowSill',1.92,.045,.3,x,.32,-4.6,M.stone);}
    // Floating desk, stone top and fine blue reveal.
    rounded('WorkDeskTop',3.25,.09,1.05,1.4,.77,-2.48,M.stone);rounded('DeskOakApron',3.15,.12,.88,1.4,.675,-2.48,M.wood);
    box('DeskBlueReveal',3.07,.009,.014,1.4,.714,-1.96,M.blue);
    for(const x of [.08,2.72]){box('DeskLeg',.07,.65,.7,x,.34,-2.48,M.black);box('DeskFoot',.32,.04,.8,x,.045,-2.48,M.black);}
    box('DeskMat',1.5,.009,.46,1.4,.825,-2.19,M.black);
    for(let i=0;i<3;i++){const x=.56+i*.84;const pivot=new T.Group();pivot.name=['MonitorLeft','MonitorCenter','MonitorRight'][i];pivot.position.set(x,1.24,-2.79);pivot.rotation.y=i===0?.12:i===2?-.12:0;this.group.add(pivot);box('DisplayFrame',.80,.49,.045,0,0,0,M.black,pivot);const screen=new T.Mesh(new T.PlaneGeometry(.748,.426),new T.MeshBasicMaterial({color:'#102336'}));screen.name='LiveDisplay_'+i;screen.position.set(0,.006,.024);pivot.add(screen);this.screens.push(screen);box('DisplayChin',.79,.018,.006,0,-.234,.027,M.silver,pivot);box('MonitorStem',.035,.2,.04,x,.94,-2.80,M.black);rounded('MonitorBase',.31,.014,.19,x,.826,-2.77,M.black);sphere('MonitorPowerLED',.006,.34,-.222,.031,M.blue,pivot);}
    rounded('Keyboard',.47,.024,.17,1.4,.847,-2.14,M.black);
    for(let r=0;r<5;r++)for(let c=0;c<15;c++)box('Key',.023,.006,.022,1.19+c*.029,.863,-2.202+r*.028,c%7===0?M.silver:M.stone);
    box('Spacebar',.15,.007,.018,1.4,.864,-2.082,M.stone);
    const mouse=sphere('Mouse',.057,1.8,.86,-2.12,M.black);mouse.scale.set(.7,.40,1.1);box('MouseLight',.002,.009,.03,1.8,.883,-2.13,M.blue);
    rounded('Tablet',.22,.012,.29,2.43,.836,-2.16,M.black);box('TabletScreen',.194,.002,.246,2.43,.844,-2.16,new T.MeshStandardMaterial({color:'#16436a',emissive:'#123a55',emissiveIntensity:.5}));
    cyl('MicrophoneFoot',.075,.075,.015,.17,.835,-2.17);cyl('MicrophoneStem',.012,.012,.17,.17,.927,-2.17,M.silver);cyl('Microphone',.04,.04,.12,.17,1.065,-2.17);
    cyl('HeadphoneStandBase',.09,.09,.018,2.82,.835,-2.41);cyl('HeadphoneStand',.009,.009,.32,2.82,1,-2.41,M.silver);
    const arch=new T.Mesh(new T.TorusGeometry(.11,.018,8,24,Math.PI),M.black);arch.name='HeadphoneBand';arch.position.set(2.82,1.12,-2.41);this.group.add(arch);for(const x of [2.71,2.93])rounded('HeadphoneEarcup',.05,.09,.07,x,1.08,-2.41,M.fabric);
    const chair=new T.Group();chair.name='ErgonomicWorkChair';chair.position.set(1.4,0,-1.47);this.group.add(chair);
    cyl('ChairPedestal',.033,.045,.39,0,.24,0,M.silver,chair);rounded('ChairSeat',.52,.09,.52,0,.49,0,M.fabric,chair);const back=rounded('ChairBack',.49,.57,.11,0,.85,.23,M.fabric,chair,.05);back.rotation.x=-.10;rounded('ChairLumbar',.39,.15,.085,0,.65,.15,M.black,chair,.04);rounded('ChairHeadrest',.27,.14,.075,0,1.17,.245,M.black,chair);
    for(const x of [-.30,.30]){rounded('ChairArm',.06,.045,.34,x,.71,0,M.black,chair);box('ChairArmSupport',.035,.22,.035,x,.59,.10,M.black,chair);}
    for(let i=0;i<5;i++){const a=i*Math.PI*2/5;const leg=box('ChairStarFoot',.035,.03,.34,Math.sin(a)*.14,.07,Math.cos(a)*.14,M.silver,chair);leg.rotation.y=a;sphere('ChairCaster',.035,Math.sin(a)*.3,.038,Math.cos(a)*.3,M.black,chair);}
    for(const x of [-.19,.19]){const rail=rounded('ChairBackFrame',.035,.6,.035,x,.87,.29,M.black,chair);rail.rotation.x=-.1;}
    // Lounge area with fabric upholstery, rug and low circular table.
    rounded('LoungeRug',3.3,.025,3,-2.6,.021,-1.7,new T.MeshStandardMaterial({map:surface('fabric','#625e52'),roughness:1}));
    rounded('SofaPlinth',2.45,.16,.86,-3.23,.19,-2.88,M.black);rounded('SofaBack',2.5,.65,.18,-3.23,.61,-3.25,M.fabric);
    for(let i=0;i<3;i++)rounded('SofaCushion',.70,.23,.69,-4+i*.77,.38,-2.82,M.fabric);
    for(const x of [-4.46,-2])rounded('SofaArm',.17,.40,.89,x,.45,-2.87,M.fabric);
    for(let i=0;i<3;i++){const back=rounded('SofaBackCushion',.73,.48,.22,-4+i*.77,.65,-3.12,M.fabric,this.group,.085);back.rotation.x=-.10;}
    const cushion=rounded('LinenCushion',.39,.40,.16,-3.85,.63,-3.12,M.pillow);cushion.rotation.z=.12;
    cyl('CoffeeTable',.62,.62,.075,-3,.42,-1.42,M.stone);cyl('CoffeeTableBase',.23,.30,.36,-3,.20,-1.42,M.black);
    box('CoffeeTableBook',.25,.035,.31,-3.15,.48,-1.38,M.paper);cyl('CoffeeCup',.044,.035,.075,-2.73,.495,-1.38,M.black);
    // Architectural wall slats, lit display shelf and a softly glowing floor lamp.
    for(let i=0;i<18;i++)box('OakAcousticSlat',.045,3,.065,-4.86,1.65,-3.8+i*.28,M.wood);
    for(let y=.65;y<2.8;y+=.64){box('FloatingShelf',.45,.045,1.6,-4.7,y,-1.35,M.wood);box('ShelfLight',.012,.015,1.5,-4.47,y-.02,-1.35,M.warm);for(let k=0;k<5;k++){const b=box('ShelfBook',.23,.19+rnd()*.12,.045,-4.65,y+.13,-1.92+k*.073,k%2?M.paper:M.black);}}
    cyl('FloorLampBase',.22,.22,.025,-1.76,.035,-3.24);cyl('FloorLampStem',.016,.016,1.7,-1.76,.85,-3.24,M.silver);cyl('FloorLampShade',.22,.31,.28,-1.76,1.69,-3.24,M.warm);
    const glow=new T.PointLight('#ffd9ad',2,5,2);glow.position.set(-1.76,1.6,-3.24);this.group.add(glow);
    const plant=(x,z,size=1)=>{cyl('Planter',.19*size,.14*size,.37*size,x,.19*size,z,M.pot);for(let i=0;i<9;i++){const a=i*2.4,ht=(.45+rnd()*.65)*size;const stem=cyl('PlantStem',.006,.006,ht,x+Math.sin(a)*.08,.34*size+ht/2,z+Math.cos(a)*.08,M.leaf);const leaf=sphere('PlantLeaf',.16*size,x+Math.sin(a)*.20,.35*size+ht,z+Math.cos(a)*.20,M.leaf);leaf.scale.set(.55,1.65,.22);leaf.rotation.set(Math.sin(a)*.6,a,Math.cos(a)*.5);}};
    plant(-4.3,-4,1.65);plant(3.58,-3.55,1.6);plant(-1.6,-.25,.8);
    // Quiet workstation tower with slowly moving fan blades.
    rounded('WorkstationTower',.34,.58,.57,3.3,.32,-2.55,M.black);box('TowerGlass',.009,.49,.47,3.476,.34,-2.55,mat('#1a2935',.15,.6));
    for(let i=0;i<2;i++){const ring=new T.Mesh(new T.TorusGeometry(.086,.004,6,24),M.blue);ring.name='CoolingRing';ring.position.set(3.3,.2+i*.24,-2.26);this.group.add(ring);const fan=new T.Group();fan.name='CoolingFan';fan.position.copy(ring.position);for(let j=0;j<5;j++){const blade=box('FanBlade',.021,.067,.009,0,.033,0,M.silver,fan);blade.rotation.z=j*Math.PI*2/5;blade.position.set(Math.sin(j*Math.PI*2/5)*.035,Math.cos(j*Math.PI*2/5)*.035,0);}this.group.add(fan);this.fans.push(fan);}
    rounded('ConversationChairSeat',.51,.08,.46,0,.49,2.3,M.fabric);rounded('ConversationChairBack',.5,.48,.06,0,.78,2.07,M.fabric);
    // Conversation desk edge stays below camera, allowing a clear human silhouette.
    rounded('ConversationDesk',2.65,.065,.78,0,.72,3.13,M.woodEdge);rounded('ConversationDeskTop',2.63,.015,.76,0,.76,3.13,M.stone);box('ConversationDeskBlueInlay',1.1,.002,.007,0,.77,3.3,M.blue);
    for(const x of [-1.1,1.1])box('ConversationDeskLeg',.07,.7,.5,x,.35,3.14,M.black);
    rounded('Notebook',.23,.025,.31,-.86,.79,3.08,M.black);const pen=cyl('Pen',.004,.004,.15,-.62,.792,3.09,M.silver);pen.rotation.x=Math.PI/2;
    // Suspended architectural lighting and logo wall.
    box('CeilingRearBeam',10,.13,.18,0,3.3,-3.7,M.black);box('WarmCeilingStrip',8.5,.014,.018,0,3.225,-3.7,M.warm);box('BlueCeilingAccent',.014,.015,6.8,4.7,3.2,-.8,M.blue);
    box('BrandMonolith',1.12,2.4,.12,-1.1,1.3,-4.55,M.stone);
    const loader=new T.TextureLoader();loader.load('assets/logo.png',tex=>{tex.colorSpace=T.SRGBColorSpace;const logo=new T.Mesh(new T.PlaneGeometry(.9,.9),new T.MeshBasicMaterial({map:tex,transparent:true}));logo.name='VELKO_IlluminatedLogo';logo.position.set(-1.1,1.98,-4.478);this.group.add(logo);},undefined,()=>{});
    const labelCanvas=document.createElement('canvas');labelCanvas.width=512;labelCanvas.height=128;const lc=labelCanvas.getContext('2d');lc.clearRect(0,0,512,128);lc.fillStyle='#c6d7e5';lc.font='24px sans-serif';lc.textAlign='center';lc.fillText('P L U S   L O I N .',256,45);lc.fillText('E N S E M B L E .',256,86);const sign=new T.Mesh(new T.PlaneGeometry(.94,.235),new T.MeshBasicMaterial({map:new T.CanvasTexture(labelCanvas),transparent:true}));sign.name='BrandSignature';sign.position.set(-1.1,1.35,-4.476);this.group.add(sign);
    const amb=new T.HemisphereLight('#b5c7df','#453528',.75);amb.name='StudioAmbient';scene.add(amb);
    const key=new T.DirectionalLight('#ffe5cc',2.4);key.name='WarmPortraitKey';key.position.set(-3,4,4);key.castShadow=true;key.shadow.mapSize.set(1024,1024);key.shadow.camera.left=-6;key.shadow.camera.right=6;key.shadow.camera.top=5;key.shadow.camera.bottom=-5;key.shadow.normalBias=.025;scene.add(key);
    const cove=new T.PointLight('#ffd4a2',5,8,2);cove.position.set(-3,2.6,-2.5);this.group.add(cove);
    const taskLight=new T.PointLight('#ffd8b2',3,5,2);taskLight.position.set(1.4,2.55,-2.25);this.group.add(taskLight);
    const fill=new T.PointLight('#71adff',2.5,10,2);fill.name='WindowBlueFill';fill.position.set(3,2.6,-3.8);scene.add(fill);
    const face=new T.PointLight('#fff1e3',5,7,2);face.name='PortraitSoftbox';face.position.set(.6,2.6,4);scene.add(face);
    point('KeyboardTarget',1.4,.88,-2.14);point('MouseTarget',1.8,.88,-2.12);point('MonitorLeftTarget',.56,1.24,-2.77);point('MonitorCenterTarget',1.4,1.24,-2.77);point('MonitorRightTarget',2.24,1.24,-2.77);point('ChairTarget',1.4,0,-1.47);point('WorkstationPosition',1.4,0,-1.6);point('ConversationPoint',0,0,2.3);
  }
  update(dt,time) { for(const fan of this.fans) fan.rotation.z+=dt*4; }
}
