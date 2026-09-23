import {GLTFLoader} from '../vendor/GLTFLoader.js';
/** Continuous, skinned MPFB human with anatomical limb solving. +Z is forward.
 *
 *  Couche humaine : aucune immobilité complète, aucune mécanique. Respiration
 *  irrégulière, clignements espacés 2-7 s (double clignotement parfois), micro-
 *  saccades du regard, hochements/acquiescements, posture qui varie, frappe par
 *  rafales avec pauses, chaîne souris (atteinte → prise → micro-mouvements →
 *  clic → pause → défilement), anticipation du regard puis de la main,
 *  follow-through, expressions faciales et lip-sync. 100 % procédural, léger. */
export class VelkoAvatarController {
  constructor(THREE){
    this.THREE=THREE;this.root=new THREE.Group();this.root.name='VELKO anatomical human';this.joints={};this.bones={};this.rest=new Map();this.pose='idle';this.seat=0;this.loaded=false;this.morphs={blink:0,mouth:0,smile:0,innerBrow:0,outerBrow:0};
    this.human={
      // Respiration irrégulière : deux composantes déphasées + soupir occasionnel.
      breath:0,breathT:Math.random()*7,breathVar:0,nextSigh:3+Math.random()*9,sigh:0,
      // Regard : micro-saccades et mini-erreurs réelles.
      gazeY:0,gazeX:0,gazeTarget:0,nextGaze:400+Math.random()*900,saccade:0,nod:0,nodNext:0,
      // Clignements : espacés et parfois doubles, jamais réguliers (en ms).
      blink:0,nextBlink:800+Math.random()*500,blinkPrev:-2000,
      // Posture : appui alterné, redressement léger.
      sway:0,lean:0,leanTarget:0,exhale:0,
      // Frappe humaine : rafales entrecoupées de pauses, cadence qui varie.
      typingEnv:1,burstEnd:0,burstNext:0,burstCadence:12,
      // Souris : atteinte ménagée puis micro-précision, relance discrète.
      mouseReach:0,mouseMicro:0,scrollRelay:0,
      // Anticipation : les yeux et la tête partent avant la main.
      anticipation:0,anticipating:false,
      // Follow-through : petit débordement décroissant après chaque geste.
      follow:0,
      // Expression faciale soutenue selon l'état (micro).
      expression:{smile:0,mouthOffset:0,brow:0},look:0,
    };
    this.lastAction='';this.ready=this.load();
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
  /** Couche humaine procédurale : rien de périodique, tout est irrégulier. */
  humanLayer(dt,time,state,context,g){
    const h=this.human,now=performance.now();
    // --- Respiration : déphasée + légère irrégularité + soupir occasionnel ---
    h.breathT+=dt*(1+Math.sin(time*.7)*.08);
    h.breathVar=Math.sin(time*.53)*.22+Math.sin(time*1.7+.6)*.11;
    if(now>h.nextSigh){h.sigh=.5;h.nextSigh=now+(2400+Math.random()*6000);}
    h.sigh=Math.max(0,h.sigh-dt*1.4);
    h.breath=Math.sin(h.breathT)*(1+h.breathVar*.35)+h.sigh*.35;
    // --- Clignements : intervalle 2-7 s, double clignotement occasionnel ---
    if(now>h.nextBlink){
      h.blink=1;h.blinkPrev=now;h.nextBlink=now+(2000+Math.random()*5000);
      // Double clignotement : deuxième fermeture 120 ms plus tard.
      if(Math.random()<.22)h.nextBlink=now+120;
    }
    if(h.blink>0)h.blink-=dt*6.5;
    // --- Regard : micro-saccades vers un point, corrections, re-prise ---
    if(now>h.nextGaze){h.gazeTarget=(Math.random()-.5)*2;h.nextGaze=now+(400+Math.random()*2600);h.anticipation=.5;}
    h.anticipation=Math.max(0,h.anticipation-dt*1.8);
    h.gazeX+=(h.gazeTarget*0.030+h.gazeX)*-Math.min(1,dt*3);
    h.gazeY+=( -h.gazeX*.35 + (Math.random()-.5)*.008 - h.gazeY)*-Math.min(1,dt*6);
    // --- Posture : appui alterné lent + redressement léger -----------------
    h.lean+=(h.leanTarget-h.lean)*Math.min(1,dt*.8);
    if(Math.random()<.0018)h.leanTarget=(Math.random()-.5)*.06;
    h.sway=Math.sin(time*.6)*.01+h.lean;
    h.exhale+=(-h.exhale)*Math.min(1,dt*4);
    h.exhale+=h.breath*-.004;h.exhale=Math.max(-.02,Math.min(.02,h.exhale));
    // --- Frappe humaine : rafales entrecoupées de pauses ------------------
    if(context.typing&&g.rate>0){
      if(now>h.burstEnd){h.typingEnv=0;h.burstNext=now+90+Math.random()*340;}   // pause humain
      if(now>h.burstNext){h.typingEnv=1;h.burstEnd=now+500+Math.random()*1200;h.burstCadence=g.rate*(.8+Math.random()*.5);}
    } else if(!context.typing){h.typingEnv=0;}
    // --- Souris : atteinte ménagée puis micro-précision ---------------------
    const mousing=g.mouse&&(context.mouse||context.action==='click'||context.action==='scroll'||context.action==='drag'||context.action==='reach');
    if(mousing&&context.mouse){
      h.mouseReach=Math.min(1,h.mouseReach+dt*2.2);                       // bras → souris
      if(h.mouseReach>.82)h.mouseMicro=(Math.random()-.5)*.008;           // micro-mouvements
      h.scrollRelay=g.scroll?Math.max(0,1-Math.abs(h.scrollRelay)):0;
      if(g.scroll&&h.scrollRelay<.02)h.scrollRelay=1;
    } else if(!mousing){h.mouseReach=Math.max(0,h.mouseReach-dt*1.6);h.scrollRelay=0;}
    // --- Anticipation : yeux puis tête partent AVANT l'action ---------------
    if(this.lastAction!==(context.action||'')){h.anticipating=true;h.follow=.8;this.lastAction=context.action||'';}
    if(h.anticipating){h.anticipation=Math.max(h.anticipation,.6);if(h.mouseReach>.25||context.typing)h.anticipating=false;}
    if(h.follow>0)h.follow-=dt*3.2;
    // --- Expression faciale soutenue (micro) selon l'état -------------------
    const e=h.expression;
    const smileTarget=/success/.test(state)?.05:/error/.test(state)?0:.008;
    e.smile+=(Math.min(.06,smileTarget)-e.smile)*Math.min(1,dt*2.4);
    const browTarget=/error|think/.test(state)?.05:0;
    e.brow+=(browTarget-e.brow)*Math.min(1,dt*3);
    e.mouthOffset=(/think/.test(state)?.012:0)+(/listen/.test(state)?.008:0);
    // --- Lip-sync : chaque mot est une ouverture, jamais un métronome --------
    if(context.speaking||/speak|success/.test(state)){
      const word=Math.max(0,Math.sin(time*13.3+Math.floor(time*13.3)*1.7)*.9+Math.sin(time*23.7)*.25+.7);
      h.mouth=Math.min(.5,word*.22+Math.random()*.06*(h.sigh+1));
    } else h.mouth=0;
    // --- Hochements de tête pendant l'écoute / acquiescement ----------------
    if(/listen/i.test(state)){
      if(now>h.nodNext){h.nod=1;h.nodNext=now+(800+Math.random()*2200);}
      h.nod=Math.max(0,h.nod-dt*1.6);
    } else h.nod=0;
    h.look=context.look??0;
  }
  update(dt,time,state,context={}){
    if(!this.loaded)return;
    const T=this.THREE,s=state||this.pose,B=this.bones;
    const walking=context.walking??/walk|moving/i.test(s),seated=context.seated??/sit|typing|working|mouse/i.test(s),typing=context.typing??/typing|working/i.test(s),speaking=context.speaking??/speak|talk|success/i.test(s);
    const g=this.gesture(context),tap=g.rate>0&&typing;
    const mousing=g.mouse&&(context.mouse||context.action==='click'||context.action==='scroll'||context.action==='drag');
    const ease=1-Math.exp(-Math.min(dt,.1)*9);this.seat+=(Number(seated)-this.seat)*ease;
    this.humanLayer(dt,time,s,context,g);
    const h=this.human;
    for(const [bone,r] of this.rest){bone.quaternion.copy(r.q);bone.position.copy(r.p);}
    const seat=this.seat,step=Math.sin(time*7);
    // Respiration irrégulière + soupir : amplitude jamais constante.
    const breath=(h.breath*2.2)*.002;         // ±4-5 mm, soupir amplifié
    B.pelvis.position.y-=seat*.445;
    B.pelvis.position.y+=walking?Math.abs(step)*.016:breath;
    // Posture : appui alterné latéral et arrière jamais figé.
    B.pelvis.position.x+=h.sway*(seated?.20:0);
    B.pelvis.position.z+=h.lean*-.10*(seated?1:0);
    B.chest.rotateY(h.sway*(seated?.10:0));
    // Lecture d'écran : la tête s'incline vers le moniteur, avec micro-errance.
    const reading=(context.inputMode==='READING'||context.action==='read')&&seat>.5?-.10:0;
    const listening=(context.inputMode==='NONE'&&seat>.5)||/waiting|listen/i.test(s);
    const look=h.look*.35+reading*.4+mousing*.25;
    // Anticipation : le regard glisse vers la cible avant le geste.
    const reachLead=h.anticipation*.06*Math.sign(h.gazeTarget||1);
    B.head.rotateY(look+h.gazeX*.024+reachLead+Math.sin(time*.43)*.016);
    // Regard pensif en THINKING : décalé, qui se redresse en LISTENING.
    const thinkingGaze=/think/i.test(s)?-.08+Math.sin(time*.7)*.02:0;
    B.head.rotateX(speaking?Math.max(0,Math.sin(time*2)*.018)*1.15:reading+thinkingGaze);
    if(listening)B.head.rotateY(Math.sin(time*1.9)*(.014+h.nod*.05));   // acquiescement
    B.chest.rotateX(reading?-.05:listening?-.03:h.exhale);
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
        const deskPt=this.deskTarget(context.desk,side,mouse);
        // Chaîne souris : ATTEINTE (approche) → prise → micro-mouvements.
        if(mouse&&h.mouseReach<1&&deskPt&&context.desk?.mouse){
          const from=this.point([sign*.245,.93,.045]);
          handTarget=from.clone().lerp(deskPt,h.mouseReach*.9);
        } else if(deskPt){
          handTarget=deskPt;
          if(mouse)handTarget.y+=h.mouseMicro*2;    // micro-précision après prise
        } else {
          const rest=[sign*.245,.93,.045];
          const drift=mouse&&g.drift?Math.sin(time*2.1)*g.drift:0;
          const wheel=mouse&&g.scroll&&h.scrollRelay>0?Math.sin(time*6+h.scrollRelay*2)*.012:0;
          const work=[mouse?-.36+drift:sign*.115,
                      .90+(tap?Math.max(0,Math.sin(time*h.burstCadence+sign))*h.typingEnv*.002:0)+wheel,
                      mouse?.43+(g.reach?Math.sin(time*1.4)*.02:0):.425];
          handTarget=this.point(rest.map((v,i)=>v+(work[i]-v)*seat));
        }
      }else handTarget=this.point([sign*(speaking?.28:.245),speaking?1.05:.9,walking?-step*sign*.15:speaking?.20:.065]);
      this.limb('upperArm_'+side,'lowerArm_'+side,'hand_'+side,handTarget,this.point([sign*.46,.8,.02]));
      const hand=B['hand_'+side],finger=B['middle_01_'+side];
      if(hand&&finger&&seat>.1)this.aim(hand,finger,this.deskTarget(context.desk,side,mouse)||this.point([mouse?-.37:sign*.115,.878,mouse?.535:.545]));
      for(let i=0;i<4;i++)for(let k=1;k<=3;k++){
        const name=['index','middle','ring','pinky'][i]+'_0'+k+'_'+side,bone=B[name];if(!bone)continue;
        // Frappe par RAFALES : chaque doigt suit la cadence du burst, jamais un
        // métronome continu. Un raccourci (hold) reste enfoncé.
        const burst=g.rate>0&&tap&&seat>.5
          ? Math.max(0,Math.sin(time*h.burstCadence+i*2+sign))*g.amp*h.typingEnv
          : 0;
        const hold=g.hold&&typing?g.amp:0;
        const enter=g.enter&&i===3&&side==='R'?Math.max(0,Math.sin(time*4))*.18:0;      // Entrée finale
        const clic=mouse&&i===0&&g.click&&h.mouseReach>.98
          ? Math.max(0,Math.sin(time*(g.double?11:5.5)))*g.click*(.5+h.scrollRelay*.5):0;
        const follow=h.follow*.02;                                                     // follow-through
        bone.rotateX(burst+hold+enter+clic+follow);
      }
    }
    // Blink irrégulier + micro smiles/mouth/brows, lip-sync réelle.
    const e=h.expression;
    this.morphs.blink=h.blink;
    this.morphs.mouth=speaking?h.mouth:(e.mouthOffset+e.smile*.4);
    this.morphs.smile=Math.max(0,(/success/.test(s)?.4:0))+e.smile*12;
    const mouth=this.morphs.mouth,smile=this.morphs.smile;
    this.model.traverse(o=>{if(o.morphTargetDictionary&&o.morphTargetInfluences)for(const [name,index]of Object.entries(o.morphTargetDictionary)){if(/blink/i.test(name))o.morphTargetInfluences[index]=this.morphs.blink;else if(/jaw.?open|mouth.?open/i.test(name))o.morphTargetInfluences[index]=mouth;else if(/smile/i.test(name))o.morphTargetInfluences[index]=smile;}});
    this.root.updateMatrixWorld(true);
  }
  /** Point MONDIAL visé par une main assise : le vrai clavier ou la vraie souris.
   *  `desk` = {keyboard, mouse} positions mondiales mesurées sur l'environnement.
   *  La main droite rejoint la souris quand VELKO clique ; sinon les deux mains
   *  restent sur les touches du vrai clavier (décalées du centre). */
  deskTarget(desk, side, mouse) {
    const T=this.THREE;
    if (mouse && desk && desk.mouse) return desk.mouse.clone().add(new T.Vector3(0,0,0));
    if (desk && desk.keyboard) {
      const off = side === 'R' ? -.09 : .09;
      return desk.keyboard.clone().add(new T.Vector3(off, -.008, .015));
    }
    return null;
  }
}