import {GLTFLoader} from '../vendor/GLTFLoader.js';
/** Continuous, skinned MPFB human with anatomical limb solving. +Z is forward. */
export class VelkoAvatarController {
  constructor(THREE){
    this.THREE=THREE;this.root=new THREE.Group();this.root.name='VELKO anatomical human';this.joints={};this.bones={};this.rest=new Map();this.pose='idle';this.seat=0;this.loaded=false;this.morphs={blink:0,mouth:0,smile:0};
    this.ready=this.load();
  }
  async load(){
    const T=this.THREE;
    const gltf=await new GLTFLoader().loadAsync(new URL('../assets/avatar/velko_premium.glb',import.meta.url).href);
    this.model=gltf.scene;this.model.scale.setScalar(1.025);this.root.add(this.model);
    this.model.traverse(o=>{
      if(o.isBone){this.bones[o.name]=o;this.rest.set(o,{q:o.quaternion.clone(),p:o.position.clone()});}
      if(o.isMesh){o.castShadow=true;o.receiveShadow=false;o.frustumCulled=false;const materials=Array.isArray(o.material)?o.material:[o.material];for(const m of materials){m.side=T.FrontSide;}}
    });
    Object.assign(this.joints,{head:this.bones.head,pelvis:this.bones.pelvis,spine:this.bones.chest});
    for(const side of ['L','R']){
      for(const [alias,name] of Object.entries({shoulder:'upperArm',elbow:'lowerArm',hand:'hand',hip:'upperLeg',knee:'lowerLeg',foot:'foot'}))this.joints[alias+side]=this.bones[name+'_'+side];
      ['index','middle','ring','pinky','thumb'].forEach((f,i)=>this.joints['finger'+side+i]=this.bones[f+'_01_'+side]);
    }
    this.loaded=true;this.update(1,0,'IDLE');return this;
  }
  setPose(name){this.pose=name;}
  point(local){return this.root.localToWorld(new this.THREE.Vector3(...local));}
  position(b){return b.getWorldPosition(new this.THREE.Vector3());}
  aim(b,child,target){
    const T=this.THREE;b.updateWorldMatrix(true,true);
    const p=this.position(b),dir=this.position(child).sub(p).normalize(),desired=target.clone().sub(p).normalize();
    const delta=new T.Quaternion().setFromUnitVectors(dir,desired),world=b.getWorldQuaternion(new T.Quaternion());
    const parent=b.parent.getWorldQuaternion(new T.Quaternion());
    b.quaternion.copy(parent.invert().multiply(delta.multiply(world)));b.updateWorldMatrix(false,true);
  }
  limb(a,b,c,target,pole){
    const T=this.THREE,A=this.bones[a],B=this.bones[b],C=this.bones[c];if(!A||!B||!C)return;
    this.root.updateMatrixWorld(true);
    const start=this.position(A),mid=this.position(B),end=this.position(C),l1=start.distanceTo(mid),l2=mid.distanceTo(end);
    const direction=target.clone().sub(start),distance=Math.min(direction.length(),l1+l2-.002);direction.normalize();
    const x=(l1*l1-l2*l2+distance*distance)/(2*Math.max(distance,.001));
    const height=Math.sqrt(Math.max(0,l1*l1-x*x));
    const bend=pole.clone().sub(start);bend.addScaledVector(direction,-bend.dot(direction)).normalize();
    const elbow=start.clone().addScaledVector(direction,x).addScaledVector(bend,height);
    this.aim(A,B,elbow);this.aim(B,C,target);
  }
  /** Gestes nommés. Chacun n'est joué QUE sur un fait réel du moteur :
   *  une frappe quand VELKO écrit, la souris quand il clique ou défile,
   *  la lecture quand un processus travaille seul. Aucun geste décoratif. */
  static GESTURES={
    TypingSlow:    {rate:7,  amp:.07, mouse:false},
    TypingNormal:  {rate:12, amp:.10, mouse:false},
    TypingFast:    {rate:17, amp:.13, mouse:false},
    TypingShortcut:{rate:0,  amp:.20, mouse:false, hold:true},
    PressEnter:    {rate:12, amp:.10, mouse:false, enter:true},
    PauseTyping:   {rate:0,  amp:.04, mouse:false},
    ReadScreen:    {rate:0,  amp:.03, mouse:false},
    MouseReach:    {rate:0,  amp:.03, mouse:true,  reach:1},
    MouseMove:     {rate:0,  amp:.03, mouse:true,  drift:.05},
    MouseClick:    {rate:0,  amp:.03, mouse:true,  click:.22},
    MouseDoubleClick:{rate:0,amp:.03, mouse:true,  click:.22, double:true},
    MouseScroll:   {rate:0,  amp:.03, mouse:true,  scroll:1},
    MouseDrag:     {rate:0,  amp:.03, mouse:true,  click:.14, drift:.09},
    MouseRelease:  {rate:0,  amp:.03, mouse:true},
  };
  /** Geste effectif : celui demandé, sinon déduit de l'action réelle. */
  gesture(context){
    const G=VelkoAvatarController.GESTURES;
    if(context.gesture&&G[context.gesture])return G[context.gesture];
    if(context.typing)return G.TypingNormal;
    if(context.mouse)return G.MouseMove;
    return G.ReadScreen;
  }
  update(dt,time,state,context={}){
    if(!this.loaded)return;
    const T=this.THREE,s=state||this.pose,B=this.bones;
    const walking=context.walking??/walk|moving/i.test(s),seated=context.seated??/sit|typing|working|mouse/i.test(s),typing=context.typing??/typing|working/i.test(s),speaking=context.speaking??/speak|talk|success/i.test(s);
    const g=this.gesture(context),tap=g.rate>0&&typing;
    // Un geste souris ne joue que si le moteur a réellement produit une action souris.
    const mousing=g.mouse&&(context.mouse||context.action==='click'||context.action==='scroll'||context.action==='drag');
    const ease=1-Math.exp(-Math.min(dt,.1)*9);this.seat+=(Number(seated)-this.seat)*ease;
    for(const [bone,r] of this.rest){bone.quaternion.copy(r.q);bone.position.copy(r.p);}
    const seat=this.seat,step=Math.sin(time*7),breath=Math.sin(time*1.6)*.002;
    B.pelvis.position.y-=seat*.445;B.pelvis.position.y+=walking?Math.abs(step)*.016:breath;
    B.head.rotateY((context.look||0)*.35+Math.sin(time*.43)*.016);B.head.rotateX(speaking?Math.sin(time*2)*.018:0);
    this.root.updateMatrixWorld(true);
    for(const [side,sign] of [['L',1],['R',-1]]){
      // Knees follow a forward pole. Feet remain on the floor while seated.
      const foot=this.point([sign*.105,.085,seat*.44+(walking?step*sign*.22:0)]);
      const knee=this.point([sign*.12,.5,seat*.65+.3]);
      this.limb('upperLeg_'+side,'lowerLeg_'+side,'foot_'+side,foot,knee);
      const f=B['foot_'+side],toe=B['toe_'+side];if(f&&toe)this.aim(f,toe,this.point([sign*.105,.07,seat*.44+.18+(walking?step*sign*.22:0)]));
      const mouse=mousing&&side==='R';
      let handTarget;
      if(seat>.01){
        const rest=[sign*.245,.93,.045];
        const drift=mouse&&g.drift?Math.sin(time*2.1)*g.drift:0;
        const wheel=mouse&&g.scroll?Math.sin(time*6)*.012:0;
        const work=[mouse?-.36+drift:sign*.115,
                    .90+(tap?Math.sin(time*g.rate+sign)*.002:0)+wheel,
                    mouse?.43+(g.reach?Math.sin(time*1.4)*.02:0):.425];
        handTarget=this.point(rest.map((v,i)=>v+(work[i]-v)*seat));
      }else handTarget=this.point([sign*(speaking?.28:.245),speaking?1.05:.9,walking?-step*sign*.15:speaking?.20:.065]);
      this.limb('upperArm_'+side,'lowerArm_'+side,'hand_'+side,handTarget,this.point([sign*.46,.8,.02]));
      const hand=B['hand_'+side],finger=B['middle_01_'+side];
      if(hand&&finger&&seat>.1)this.aim(hand,finger,this.point([mouse?-.37:sign*.115,.878,mouse?.535:.545]));
      for(let i=0;i<4;i++)for(let k=1;k<=3;k++){
        const name=['index','middle','ring','pinky'][i]+'_0'+k+'_'+side, bone=B[name];if(!bone)continue;
        // Frappe : amplitude et cadence du geste demandé. Raccourci : doigts maintenus.
        const strike=tap&&seat>.5?Math.sin(time*g.rate+i*2+sign)*g.amp
                    :(g.hold&&typing?g.amp:.05);
        // Validation : l'auriculaire frappe Entrée à la fin de la saisie.
        const enter=g.enter&&i===3&&side==='R'?Math.max(0,Math.sin(time*4))*.18:0;
        const clic=mouse&&i===0&&g.click?Math.max(0,Math.sin(time*(g.double?11:5.5)))*g.click:0;
        bone.rotateX(strike+enter+clic);
      }
    }
    const phase=time%4.6,blink=phase<.15?Math.sin(phase/.15*Math.PI):0;this.morphs.blink=blink;this.morphs.mouth=speaking?Math.max(0,Math.sin(time*14))*.35:0;
    this.model.traverse(o=>{if(o.morphTargetDictionary&&o.morphTargetInfluences)for(const [name,index]of Object.entries(o.morphTargetDictionary)){if(/blink/i.test(name))o.morphTargetInfluences[index]=blink;if(/jaw.?open|mouth.?open/i.test(name))o.morphTargetInfluences[index]=this.morphs.mouth;}});
    this.root.updateMatrixWorld(true);
  }
}
