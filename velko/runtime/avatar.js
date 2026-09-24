import {GLTFLoader} from '../vendor/GLTFLoader.js';
/** Découpage grossier d'un mot français en visèmes (formes de bouche), pour un
 *  lip-sync qui suit les VRAIS mots prononcés par la synthèse vocale (voir
 *  speakWord ci-dessous) — jamais un métronome. Approximation phonétique
 *  simple : suffisante pour des formes de bouche crédibles en temps réel. */
const VISEME_VOWELS={a:'A',à:'A',â:'A',ä:'A',e:'E',é:'E',è:'E',ê:'E',ë:'E',i:'I',î:'I',ï:'I',y:'I',o:'O',ô:'O',u:'U',û:'U',ù:'U',ü:'U'};
function wordVisemes(word){
  const w=String(word||'').toLowerCase().normalize('NFC');const out=[];let i=0;
  while(i<w.length){
    const two=w.slice(i,i+2);
    if(two==='ou'||two==='oi'||two==='oy'){out.push('WQ');i+=2;continue;}
    if(two==='an'||two==='en'||two==='on'){out.push('O');i+=2;continue;}
    if(two==='in'||two==='un'){out.push('I');i+=2;continue;}
    if(two==='ch'){out.push('CH');i+=2;continue;}
    const c=w[i];
    if(VISEME_VOWELS[c]){out.push(VISEME_VOWELS[c]);i++;continue;}
    if('bmp'.includes(c)){out.push('MBP');i++;continue;}
    if('fv'.includes(c)){out.push('FV');i++;continue;}
    if(c==='l'){out.push('L');i++;continue;}
    if(/[a-z]/.test(c))out.push('REST');
    i++;
  }
  return out.length?out:['REST'];
}
/** Ouverture de mâchoire par visème (0 = fermé, 1 = grand ouvert). */
const VISEME_JAW={viseme_A:.55,viseme_E:.32,viseme_I:.18,viseme_O:.4,viseme_U:.22,viseme_WQ:.16,viseme_MBP:0,viseme_FV:.14,viseme_L:.28,viseme_CH:.18,viseme_TH:.2,viseme_REST:0};
/** Expressions faciales par état réel — calquées sur les planches de la
 *  maquette (neutre, concentration, réflexion, surprise, sourire, rire,
 *  doute, décision). Poids DE BASE, toujours mélangés à la couche humaine
 *  procédurale (micro-mouvements) : jamais une pose figée. */
const EXPRESSION_RULES=[
  [/think/,                  {browDown:.30,squintLeft:.10,squintRight:.16,mouthLeft:.10}],
  [/working|reading_output/, {browDown:.16,squintLeft:.08,squintRight:.08,mouthPress:.05}],
  [/success/,                {browUp:.10,smile:.55,eyeWideLeft:.05,eyeWideRight:.05}],
  [/error/,                  {browUpLeft:.22,browDown:.10,mouthLeft:.14,squintLeft:.10,frown:.08}],
  [/awaiting_user_decision/, {browUp:.14,mouthPress:.06}],
  [/listen/,                 {browUp:.03,smile:.08}],
];
function expressionFor(state){for(const [re,vals] of EXPRESSION_RULES)if(re.test(state))return vals;return {smile:.05};}
/** Impulsion ponctuelle sur un fait réel (nouvelle mission, réussite,
 *  blocage, décision demandée) : monte vite, redescend en décroissance
 *  naturelle — jamais une boucle. Voir VelkoAvatarController#pulse. */
const PULSES={
  surprise:{browUp:.35,eyeWideLeft:.30,eyeWideRight:.30,jawOpen:.06,decay:2.6},
  laugh:{smile:.5,jawOpen:.16,browUp:.06,decay:1.3,osc:9},
  doubt:{browUpLeft:.3,browDown:.12,mouthLeft:.18,squintLeft:.12,decay:1.6},
  decision:{browUp:.22,mouthPress:.08,decay:1.8},
};
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
    this.THREE=THREE;this.root=new THREE.Group();this.root.name='VELKO anatomical human';this.joints={};this.bones={};this.rest=new Map();this.pose='idle';this.seat=0;this.loaded=false;this.morphs={blink:0};
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
      // Parole : file de visèmes RÉELS (issus des mots effectivement
      // prononcés, voir speakWord), jamais un métronome sinusoïdal.
      speechActive:false,visemeQueue:[],visemeCur:'viseme_REST',visemeWeight:0,
      // Impulsion ponctuelle sur un fait réel (voir pulse()).
      pulseType:null,pulseT:0,
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
  /** Début réel d'une prise de parole (onstart de la synthèse vocale). */
  speakStart(){this.human.speechActive=true;this.human.visemeQueue.length=0;}
  /** Un mot vient d'être prononcé (onboundary) : ses visèmes sont répartis
   *  sur sa durée estimée — le mouvement de bouche suit le texte réel, pas
   *  une horloge arbitraire. */
  speakWord(word,durationMs){
    const h=this.human,seq=wordVisemes(word),now=performance.now();
    const step=Math.max(45,(durationMs||seq.length*90)/seq.length);
    h.visemeQueue.push(...seq.map((v,k)=>({viseme:'viseme_'+v,t:now+k*step,dur:step})));
    h.speechActive=true;
  }
  /** Fin réelle de la prise de parole (onend/onerror de la synthèse vocale). */
  speakEnd(){const h=this.human;h.speechActive=false;h.visemeQueue.length=0;h.visemeCur='viseme_REST';}
  /** Impulsion ponctuelle liée à un fait réel : surprise (nouvelle mission),
   *  rire (réussite), doute (blocage), décision (confirmation attendue). */
  pulse(kind){if(PULSES[kind]){this.human.pulseType=kind;this.human.pulseT=0;}}
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
    // --- Lip-sync réel : avance la file de visèmes issus des mots RÉELLEMENT
    //     prononcés (speakWord). Enveloppe triangulaire par visème : jamais figé,
    //     jamais un métronome. ------------------------------------------------
    while(h.visemeQueue.length>1&&now>=h.visemeQueue[1].t)h.visemeQueue.shift();
    if(h.visemeQueue.length&&now>=h.visemeQueue[0].t){
      const cur=h.visemeQueue[0],nextT=h.visemeQueue[1]?h.visemeQueue[1].t:cur.t+cur.dur;
      const span=Math.max(30,nextT-cur.t),age=now-cur.t;
      h.visemeCur=cur.viseme;
      h.visemeWeight=Math.sin(Math.min(1,age/span)*Math.PI)*.85+.05;
      if(age>span&&h.visemeQueue.length===1)h.visemeQueue.shift();
    } else {
      h.visemeWeight=Math.max(0,h.visemeWeight-dt*8);
      if(h.visemeWeight<=0)h.visemeCur='viseme_REST';
    }
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
    // --- Visage humain complet : expression de base (état réel) + micro-couche
    //     procédurale + visèmes de parole réels + impulsion ponctuelle sur un
    //     fait réel. Rien n'est jamais figé : tout est somme de couches vivantes.
    const e=h.expression;
    if(h.pulseType){h.pulseT+=dt;if(Math.exp(-h.pulseT*(PULSES[h.pulseType].decay||2))<.02)h.pulseType=null;}
    const pulse=h.pulseType&&PULSES[h.pulseType];
    const pulseLife=pulse?Math.exp(-h.pulseT*(pulse.decay||2))*(pulse.osc?Math.max(0,Math.sin(h.pulseT*pulse.osc)):1):0;
    const target={};const add=(k,v)=>{target[k]=(target[k]||0)+v;};
    for(const [k,v] of Object.entries(expressionFor(s)))add(k,v);
    if(pulse)for(const [k,v] of Object.entries(pulse)){if(k!=='decay'&&k!=='osc')add(k,v*pulseLife);}
    add('smile',e.smile*8);add('browDown',e.brow*.6);
    this.morphs.blink=h.blink;
    this.model.traverse(o=>{
      if(!o.morphTargetDictionary||!o.morphTargetInfluences)return;
      for(const [name,index] of Object.entries(o.morphTargetDictionary)){
        let v=0;
        if(/^blink/i.test(name))v=h.blink;
        else if(name==='jawOpen')v=Math.min(1,(target.jawOpen||0)+h.visemeWeight*(VISEME_JAW[h.visemeCur]||0));
        else if(name===h.visemeCur)v=h.visemeWeight;
        else if(/^viseme_/.test(name))v=0;
        else if(name==='smile'||name==='mouthSmile')v=target.smile||0;
        else if(name==='smileLeft')v=(target.smile||0)*.94;
        else if(name==='smileRight')v=(target.smile||0)*1.05;
        else if(name==='frown'||name==='mouthFrown')v=target.frown||0;
        else if(name==='browUp')v=target.browUp||0;
        else if(name==='browDown')v=target.browDown||0;
        else if(name==='browUpLeft')v=(target.browUp||0)+(target.browUpLeft||0);
        else if(name==='browUpRight')v=(target.browUp||0)+(target.browUpRight||0)*.4;
        else if(name==='eyeWideLeft')v=target.eyeWideLeft||0;
        else if(name==='eyeWideRight')v=target.eyeWideRight||0;
        else if(name==='squintLeft')v=target.squintLeft||0;
        else if(name==='squintRight')v=target.squintRight||0;
        else if(name==='mouthLeft')v=target.mouthLeft||0;
        else if(name==='mouthRight')v=target.mouthRight||0;
        else if(name==='mouthPress')v=target.mouthPress||0;
        else if(name==='mouthPucker')v=/viseme_WQ|viseme_U/.test(h.visemeCur)?h.visemeWeight*.3:0;
        else if(name==='mouthFunnel')v=h.visemeCur==='viseme_WQ'?h.visemeWeight*.4:0;
        o.morphTargetInfluences[index]=Math.max(0,Math.min(1,v));
      }
    });
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