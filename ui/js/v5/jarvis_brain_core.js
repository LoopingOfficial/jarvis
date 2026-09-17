/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — jarvis_brain_core.js
   OBSIDIAN BRAIN : le composant central permanent de JARVIS.

   Rendu 2.5D canvas — silhouette anatomique gyrifiée, matière obsidienne,
   réseau neuronal interne, zones fonctionnelles, cascades d'impulsions,
   flux entrants/sortants.

   RÈGLE : rien n'est simulé. Une zone ne s'allume que si un événement réel
   l'a demandée, un flux ne part vers un agent que si cet agent travaille.
   L'animation de repos (respiration, quelques impulsions) est le SEUL
   mouvement autonome, et elle ne prétend pas qu'un outil s'exécute.

   API publique — window.ObsidianBrain :
     mount(canvas)            attache un rendu
     setState(name, extra)    IDLE | LISTENING | THINKING | RECALLING |
                              SEARCHING | READING | CODING | DELEGATING |
                              SYNCING | GENERATING | SPEAKING | SUCCESS | ERROR
     activateZone(z, ttl)     MEMORY | KNOWLEDGE | CONTEXT | TOOLS
     deactivateZone(z)
     pulsePath(zone, n)       impulsions visibles sur un chemin réel
     showToolFlow(label)      flux sortant « outil »
     showAgentFlow(agent)     flux sortant « agent »
     setActivity(v)           0..1 — niveau réel (TTS, charge)
     reset()

   Note d'intégration :  est déjà pris par le Brain Atlas
   (graphe de connaissances). Ce composant s'expose donc en ObsidianBrain, avec
   l'alias window.JarvisBrain5 pour les appels courts.
   ========================================================================== */
(function () {
  'use strict';

  const TAU = Math.PI * 2;
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const lerp = (a,b,t)=>a+(b-a)*t;
  const reduced = ()=>!!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  const dprOf = ()=>clamp(window.devicePixelRatio||1,1,2);

const ZONES = {
  MEMORY:    {label:'MEMORY',    at:[-0.42, 0.18], col:[155,140,255]},
  KNOWLEDGE: {label:'KNOWLEDGE', at:[-0.22,-0.48], col:[56,223,255]},
  CONTEXT:   {label:'CONTEXT',   at:[ 0.34,-0.30], col:[157,244,255]},
  TOOLS:     {label:'TOOLS',     at:[ 0.40, 0.20], col:[255,176,87]},
};

/* Chaque état décrit une activité NEURONALE, pas une couleur. */
const STATES = {
  IDLE:      {rate:.20, dens:.16, speed:.5,  glow:.40, hot:[],                     scan:0, absorb:0, emit:null,  col:[56,223,255], breath:.020, bhz:.16},
  LISTENING: {rate:.55, dens:.34, speed:.8,  glow:.70, hot:['CONTEXT'],            scan:0, absorb:0, emit:null,  col:[157,244,255],breath:.030, bhz:.55},
  THINKING:  {rate:.82, dens:.70, speed:1.7, glow:.95, hot:['KNOWLEDGE','CONTEXT'],scan:0, absorb:0, emit:null,  col:[56,223,255], breath:.024, bhz:.42},
  RECALLING: {rate:.8,  dens:.7,  speed:1.2, glow:.9,  hot:['MEMORY'],             scan:0, absorb:0, emit:null,  col:[155,140,255],breath:.024, bhz:.4},
  SEARCHING: {rate:.75, dens:.6,  speed:1.4, glow:.85, hot:['KNOWLEDGE'],          scan:1, absorb:0, emit:null,  col:[29,108,255], breath:.022, bhz:.4},
  CODING:    {rate:.85, dens:.7,  speed:1.5, glow:.9,  hot:['TOOLS','CONTEXT'],    scan:0, absorb:0, emit:'agents', col:[155,140,255],breath:.024,bhz:.5},
  READING:   {rate:.7,  dens:.55, speed:1.1, glow:.85, hot:['MEMORY','CONTEXT'],   scan:0, absorb:1, emit:'tools',col:[157,244,255],breath:.022, bhz:.45},
  SYNCING:   {rate:.95, dens:.8,  speed:1.6, glow:1,   hot:['TOOLS','MEMORY'],     scan:0, absorb:.6,emit:'tools',col:[255,176,87], breath:.030, bhz:.7},
  SPEAKING:  {rate:.6,  dens:.45, speed:1.0, glow:.9,  hot:['CONTEXT'],            scan:0, absorb:0, emit:'avatar',col:[157,244,255],breath:.055,bhz:1.1},
  SUCCESS:   {rate:.9,  dens:.8,  speed:1.8, glow:1,   hot:[],                     scan:0, absorb:0, emit:null,  col:[61,240,166], breath:.040, bhz:.8},
  ERROR:     {rate:.5,  dens:.35, speed:.9,  glow:.95, hot:['TOOLS'],              scan:0, absorb:0, emit:null,  col:[255,91,110], breath:.05,  bhz:1.5},
};

/* -- silhouette : cerveau de profil, orienté vers la gauche ---------------- */
const CEREBRUM = [
  [-0.98,-0.10],[-0.93,-0.40],[-0.74,-0.66],[-0.44,-0.82],[-0.06,-0.87],
  [ 0.30,-0.80],[ 0.62,-0.60],[ 0.82,-0.32],[ 0.87,-0.02],[ 0.79, 0.17],
  [ 0.56, 0.27],[ 0.28, 0.28],[ 0.09, 0.35],[-0.12, 0.47],[-0.42, 0.53],
  [-0.70, 0.44],[-0.88, 0.25],[-0.98, 0.05],
];

/* Contour festonné : ce sont les festons qui font lire « cerveau » et non
   « galet ». On ré-échantillonne la polyligne puis on module le rayon. */
function contour(S, scale=1, lobe=1, phase=0){
  const n=CEREBRUM.length, out=[], STEP=7;
  const P=(i)=>CEREBRUM[(i%n+n)%n];
  for(let i=0;i<n;i++){
    const a=P(i-1), b=P(i), c=P(i+1), d=P(i+2);
    for(let s=0;s<STEP;s++){
      const t=s/STEP, t2=t*t, t3=t2*t;                 // Catmull-Rom
      const x=.5*((2*b[0])+(-a[0]+c[0])*t+(2*a[0]-5*b[0]+4*c[0]-d[0])*t2+(-a[0]+3*b[0]-3*c[0]+d[0])*t3);
      const y=.5*((2*b[1])+(-a[1]+c[1])*t+(2*a[1]-5*b[1]+4*c[1]-d[1])*t2+(-a[1]+3*b[1]-3*c[1]+d[1])*t3);
      const u=(i+t)/n;
      const bump=lobe*(Math.sin(u*TAU*6+phase)*.034+Math.sin(u*TAU*11-phase*.6)*.014
                      +Math.sin(u*TAU*3+phase*.35)*.022);
      const L=Math.hypot(x,y)||1;
      out.push([(x+x/L*bump)*S*scale,(y+y/L*bump)*S*scale]);
    }
  }
  return out;
}
function tracePts(ctx,pts){
  ctx.beginPath();
  const n=pts.length;
  ctx.moveTo((pts[0][0]+pts[1][0])/2,(pts[0][1]+pts[1][1])/2);
  for(let i=1;i<=n;i++){
    const c=pts[i%n], nx=pts[(i+1)%n];
    ctx.quadraticCurveTo(c[0],c[1],(c[0]+nx[0])/2,(c[1]+nx[1])/2);
  }
  ctx.closePath();
}
function smoothPath(ctx, pts, S, wob, t){
  tracePts(ctx, contour(S, 1, wob?1:0, t*.35));
}

class ObsidianBrain{
  constructor(canvas){
    this.cv=canvas; this.ctx=canvas.getContext('2d');
    this.w=0;this.h=0;this.dpr=1;this.S=1;
    this.state='IDLE'; this.p={...STATES.IDLE}; this.target=STATES.IDLE;
    this.col=[56,223,255]; this.colFrom=[56,223,255]; this.colT=1;
    this.t=0; this.level=0; this.aim={x:0,y:0}; this.tilt={x:0,y:0};
    this.zoneHeat={MEMORY:0,KNOWLEDGE:0,CONTEXT:0,TOOLS:0};
    this.nodes=[]; this.edges=[]; this.pulses=[]; this.absorbers=[]; this.beams=[];
    this.flows=[];                       // flux sortants (outil / agent) réels
    this.zoneForce={MEMORY:false,KNOWLEDGE:false,CONTEXT:false,TOOLS:false};
    this.scanT=-1;
  }

  resize(){
    const r=this.cv.getBoundingClientRect(), d=dprOf();
    const w=Math.round(r.width), h=Math.round(r.height);
    if(w===this.w&&h===this.h&&d===this.dpr) return false;
    this.w=w;this.h=h;this.dpr=d;
    this.cv.width=Math.round(w*d); this.cv.height=Math.round(h*d);
    this.S=Math.min(this.cv.width,this.cv.height)*0.40;
    this.build();
    return true;
  }

  /* Réseau neuronal : nœuds tirés à l'intérieur de la matière, arêtes vers
     les plus proches voisins, veines = plus longs chemins. */
  build(){
    const ctx=this.ctx, S=this.S;
    // Test d'appartenance fait SANS translation : le point et le tracé
    // partagent alors exactement le même repère (sinon les nœuds dérivent).
    ctx.setTransform(1,0,0,1,0,0);
    ctx.save();
    smoothPath(ctx,CEREBRUM,S,0,0);
    const inside=(x,y)=>ctx.isPointInPath(x*S,y*S);
    this.nodes=[];
    let guard=0;
    while(this.nodes.length<78 && guard++<6000){
      const x=(Math.random()*2-1)*1.0, y=(Math.random()*2-1)*.9;
      if(!inside(x,y)) continue;
      // marge intérieure : aucun nœud collé au bord
      if(!inside(x*1.06,y*1.06)) continue;
      let zone='CONTEXT', best=9;
      for(const k in ZONES){
        const z=ZONES[k], d=Math.hypot(x-z.at[0],y-z.at[1]);
        if(d<best){best=d;zone=k;}
      }
      this.nodes.push({x,y,zone,z:Math.random(),ph:Math.random()*TAU,
        sz:1.1+Math.random()*2.0, base:.42+Math.random()*.38});
    }
    ctx.restore();

    this.edges=[];
    this.nodes.forEach((a,i)=>{
      const near=this.nodes.map((b,j)=>({j,d:Math.hypot(a.x-b.x,a.y-b.y)}))
        .filter(o=>o.j!==i).sort((u,v)=>u.d-v.d).slice(0,3);
      for(const o of near){
        if(o.d>.52) continue;
        if(this.edges.some(e=>(e.a===o.j&&e.b===i))) continue;
        this.edges.push({a:i,b:o.j,d:o.d,vein:Math.random()<.16});
      }
    });
    this.pulses=[];
  }

  setState(name){
    if(!STATES[name]||this.state===name) return;
    this.state=name; this.target=STATES[name];
    this.colFrom=this.col.slice(); this.colT=0;
    if(STATES[name].scan) this.scanT=0;
    if(STATES[name].emit) this.beams.push({to:STATES[name].emit,t:0,life:3.6});
  }

  /** Une pensée n'est pas un scintillement : c'est une cascade qui se
      propage de proche en proche. On allume donc un chemin, pas un point. */
  fire(zone){
    const seeds=this.nodes.map((n,i)=>({n,i})).filter(o=>o.n.zone===zone);
    if(!seeds.length) return;
    let cur=seeds[(Math.random()*seeds.length)|0].i;
    let delay=0;
    const seen=new Set([cur]);
    const hops=2+((Math.random()*3)|0);
    for(let h=0;h<hops;h++){
      const opts=this.edges.filter(e=>(e.a===cur||e.b===cur))
        .map(e=>({e,to:e.a===cur?e.b:e.a})).filter(o=>!seen.has(o.to));
      if(!opts.length) break;
      const pick=opts[(Math.random()*opts.length)|0];
      const fwd=pick.e.a===cur;
      this.pulses.push({e:pick.e,t:0,sp:.8+Math.random()*.7,zone,delay,fwd});
      seen.add(pick.to); cur=pick.to; delay+=.09+Math.random()*.07;
    }
  }

  /** Flux sortant : "j'envoie du travail à" — jamais décoratif. */
  emit(kind,label){
    this.flows.push({kind,label,t:0,life:2.2,a:(kind==='AGENT'?-0.5:0.35)});
  }

  frame(dt){
    this.resize();
    const ctx=this.ctx, red=reduced();
    const P=this.p, T=this.target, k=1-Math.pow(.06,dt);
    for(const key in T) if(typeof T[key]==='number') P[key]=lerp(P[key]??T[key],T[key],k);
    this.colT=clamp(this.colT+dt*.9,0,1);
    this.col=this.colFrom.map((c,i)=>Math.round(lerp(c,T.col[i],this.colT)));
    const C=(a)=>`rgba(${this.col[0]},${this.col[1]},${this.col[2]},${a})`;
    this.t+=dt;

    // chaleur des zones : monte si l'état les sollicite, retombe sinon
    for(const z in this.zoneHeat){
      const want=(T.hot.includes(z)||this.zoneForce[z])?1:0;
      this.zoneHeat[z]=lerp(this.zoneHeat[z],want,1-Math.pow(.008,dt));
    }
    // inclinaison douce vers la souris → volume
    this.tilt.x=lerp(this.tilt.x,this.aim.x,1-Math.pow(.004,dt));
    this.tilt.y=lerp(this.tilt.y,this.aim.y,1-Math.pow(.004,dt));

    const br = red?0:(Math.sin(this.t*TAU*P.bhz)*P.breath
                      + Math.sin(this.t*TAU*P.bhz*2.7+1.3)*P.breath*.28);
    const S=this.S*(1+br+this.level*.03);
    ctx.setTransform(1,0,0,1,0,0);
    ctx.clearRect(0,0,this.cv.width,this.cv.height);
    ctx.save();
    ctx.translate(this.cv.width/2+this.tilt.x*6*this.dpr, this.cv.height/2+this.tilt.y*6*this.dpr);

    /* ---- halo volumétrique (lueur interne qui traverse la matière) --- */
    ctx.globalCompositeOperation='lighter';
    const halo=ctx.createRadialGradient(0,0,S*.1,0,0,S*1.5);
    halo.addColorStop(0,C(.13*P.glow)); halo.addColorStop(.4,C(.05*P.glow)); halo.addColorStop(1,C(0));
    ctx.fillStyle=halo; ctx.beginPath(); ctx.arc(0,0,S*1.5,0,TAU); ctx.fill();
    ctx.globalCompositeOperation='source-over';

    /* ---- hémisphère arrière (masse, profondeur) ---------------------- */
    ctx.save();
    ctx.translate(S*.07+this.tilt.x*10*this.dpr, -S*.05);
    smoothPath(ctx,CEREBRUM,S*.98,.03,this.t);
    ctx.fillStyle='#05080d'; ctx.fill();
    ctx.strokeStyle='rgba(120,190,225,.06)'; ctx.lineWidth=1*this.dpr; ctx.stroke();
    ctx.restore();

    /* ---- cervelet + tronc cérébral ----------------------------------- */
    const cbx=S*.52, cby=S*.42;
    ctx.save();
    ctx.beginPath(); ctx.ellipse(cbx,cby,S*.26,S*.165,-.22,0,TAU);
    const cbg=ctx.createLinearGradient(cbx-S*.3,cby-S*.2,cbx+S*.3,cby+S*.2);
    cbg.addColorStop(0,'#0a1018'); cbg.addColorStop(1,'#03060a');
    ctx.fillStyle=cbg; ctx.fill();
    ctx.save(); ctx.clip();
    for(let i=-9;i<=9;i++){                        // striations du cervelet
      ctx.beginPath();
      ctx.moveTo(cbx-S*.32, cby+i*S*.022);
      ctx.quadraticCurveTo(cbx, cby+i*S*.022-S*.03, cbx+S*.32, cby+i*S*.022);
      ctx.strokeStyle='rgba(140,200,235,.07)'; ctx.lineWidth=1*this.dpr; ctx.stroke();
    }
    ctx.restore();
    ctx.strokeStyle=C(.10); ctx.lineWidth=1.1*this.dpr; ctx.stroke();
    ctx.restore();

    ctx.beginPath();                                 // tronc
    ctx.moveTo(S*.26,S*.30); ctx.quadraticCurveTo(S*.30,S*.66,S*.19,S*.84);
    ctx.lineTo(S*.06,S*.80); ctx.quadraticCurveTo(S*.14,S*.58,S*.10,S*.30);
    ctx.closePath();
    ctx.fillStyle='#060a10'; ctx.fill();
    ctx.strokeStyle=C(.08); ctx.lineWidth=1*this.dpr; ctx.stroke();

    /* ---- matière obsidienne principale ------------------------------- */
    smoothPath(ctx,CEREBRUM,S,.035,this.t);
    const body=ctx.createLinearGradient(-S,-S,S*.8,S);
    body.addColorStop(0,'#0c141d'); body.addColorStop(.30,'#070d14');
    body.addColorStop(.66,'#03060b'); body.addColorStop(1,'#010204');
    ctx.fillStyle=body; ctx.fill();

    ctx.save(); ctx.clip();                          // tout l'intérieur est clippé

    /* CIRCONVOLUTIONS : chaque gyrus est un bourrelet plein, éclairé sur le
       dessus et creusé en dessous. C'est ce relief — et non des traits — qui
       fait lire « cerveau ». Le sillon est le fond noir qui reste entre eux. */
    const worms=this.gyri(S);
    const W=0.112*S;
    for(const w of worms){
      ctx.lineCap='round'; ctx.lineJoin='round';
      const trace=(dx,dy)=>{
        ctx.beginPath();
        ctx.moveTo(w[0][0]+dx,w[0][1]+dy);
        for(let i=1;i<w.length-1;i++){
          const c=w[i], n2=w[i+1];
          ctx.quadraticCurveTo(c[0]+dx,c[1]+dy,(c[0]+n2[0])/2+dx,(c[1]+n2[1])/2+dy);
        }
      };
      trace(0,0);                       // ombre portée du bourrelet
      ctx.strokeStyle='rgba(0,0,0,.9)'; ctx.lineWidth=W*1.22; ctx.stroke();
      trace(0,0);                       // corps obsidienne
      ctx.strokeStyle='#080e16'; ctx.lineWidth=W; ctx.stroke();
      trace(-W*.12,-W*.18);             // arête éclairée (lumière haut-gauche)
      ctx.strokeStyle='rgba(138,190,224,.105)'; ctx.lineWidth=W*.34; ctx.stroke();
      trace(-W*.2,-W*.30);
      ctx.strokeStyle='rgba(206,238,255,.115)'; ctx.lineWidth=W*.12; ctx.stroke();
    }
    /* scissure de Sylvius + sillon central : lecture anatomique */
    ctx.beginPath();
    ctx.moveTo(-S*.86,S*.10); ctx.quadraticCurveTo(-S*.30,S*.34,S*.24,S*.10);
    ctx.strokeStyle='rgba(0,0,0,.7)'; ctx.lineWidth=4.5*this.dpr; ctx.stroke();
    ctx.strokeStyle=C(.07); ctx.lineWidth=1*this.dpr; ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-S*.08,-S*.84); ctx.quadraticCurveTo(S*.02,-S*.4,S*.20,-S*.02);
    ctx.strokeStyle='rgba(0,0,0,.6)'; ctx.lineWidth=3.6*this.dpr; ctx.stroke();

    /* réseau neuronal interne */
    ctx.globalCompositeOperation='lighter';
    for(const e of this.edges){
      const a=this.nodes[e.a], b=this.nodes[e.b];
      const heat=(this.zoneHeat[a.zone]+this.zoneHeat[b.zone])/2;
      const al=(e.vein?.28:.10)+heat*.26+P.glow*.05;
      ctx.beginPath(); ctx.moveTo(a.x*S,a.y*S); ctx.lineTo(b.x*S,b.y*S);
      ctx.lineWidth=(e.vein?2.6:1.3)*this.dpr;
      ctx.strokeStyle=C(al*(0.5+0.5*P.dens));
      if(e.vein){ ctx.shadowBlur=9*this.dpr; ctx.shadowColor=C(.7); }
      ctx.stroke(); ctx.shadowBlur=0;
    }
    for(const n of this.nodes){
      const heat=this.zoneHeat[n.zone];
      // halo : la lueur traverse l'obsidienne, c'est elle qu'on voit d'abord
      {
        const zc0=ZONES[n.zone].col;
        const hr=(7+heat*16+P.dens*6)*this.dpr;
        const tw0=.5+.5*Math.sin(this.t*(1.1+P.speed)+n.ph);
        const hg=ctx.createRadialGradient(n.x*S,n.y*S,0,n.x*S,n.y*S,hr);
        const ha=(.038+heat*.175+P.dens*.05)*(0.5+tw0*.6);
        hg.addColorStop(0,`rgba(${zc0[0]},${zc0[1]},${zc0[2]},${ha})`);
        hg.addColorStop(1,`rgba(${zc0[0]},${zc0[1]},${zc0[2]},0)`);
        ctx.fillStyle=hg; ctx.beginPath(); ctx.arc(n.x*S,n.y*S,hr,0,TAU); ctx.fill();
      }
      const tw=.5+.5*Math.sin(this.t*(1.2+P.speed)+n.ph);
      const a=(n.base*.58 + heat*.5 + P.dens*.16)*(0.45+tw*.7);
      const zc=ZONES[n.zone].col;
      ctx.beginPath(); ctx.arc(n.x*S,n.y*S,n.sz*this.dpr*(0.72+heat*.85),0,TAU);
      ctx.fillStyle=`rgba(${zc[0]},${zc[1]},${zc[2]},${clamp(a,0,1)})`;
      ctx.shadowBlur=9*this.dpr; ctx.shadowColor=`rgba(${zc[0]},${zc[1]},${zc[2]},.8)`;
      ctx.fill(); ctx.shadowBlur=0;
    }

    /* impulsions : le trajet réellement emprunté */
    const want=P.rate*P.dens*7+.8;
    this._acc=(this._acc||0)+dt*want;
    while(this._acc>1){
      this._acc-=1;
      const hot=T.hot.length?T.hot:Object.keys(ZONES);
      this.fire(hot[(Math.random()*hot.length)|0]);
    }
    this.pulses=this.pulses.filter(p=>p.t<1);
    for(const p of this.pulses){
      if(p.delay>0){ p.delay-=dt; continue; }           // la cascade se déroule
      p.t+=dt*p.sp*(0.5+P.speed*0.7);
      const a=this.nodes[p.fwd===false?p.e.b:p.e.a], b=this.nodes[p.fwd===false?p.e.a:p.e.b];
      const ease=p.t*p.t*(3-2*p.t);                     // départ/arrivée adoucis
      const x=lerp(a.x,b.x,ease)*S, y=lerp(a.y,b.y,ease)*S;
      const zc=ZONES[p.zone].col;
      const g=ctx.createRadialGradient(x,y,0,x,y,9*this.dpr);
      g.addColorStop(0,`rgba(255,255,255,.9)`);
      g.addColorStop(.4,`rgba(${zc[0]},${zc[1]},${zc[2]},.75)`);
      g.addColorStop(1,`rgba(${zc[0]},${zc[1]},${zc[2]},0)`);
      ctx.fillStyle=g; ctx.beginPath(); ctx.arc(x,y,9*this.dpr,0,TAU); ctx.fill();
    }

    /* balayage (SEARCHING) : propagation qui traverse la matière */
    if(this.scanT>=0){
      this.scanT+=dt*.75;
      if(this.scanT>1.6) this.scanT=T.scan?0:-1;
      const x=lerp(-1.05,1.05,this.scanT%1)*S;
      const g=ctx.createLinearGradient(x-S*.22,0,x+S*.22,0);
      g.addColorStop(0,C(0)); g.addColorStop(.5,C(.22)); g.addColorStop(1,C(0));
      ctx.fillStyle=g; ctx.fillRect(x-S*.22,-S,S*.44,S*2);
    }
    ctx.globalCompositeOperation='source-over';

    /* brillance : verre volcanique, spéculaire mobile */
    const sx=-S*.42+this.tilt.x*S*.16, sy=-S*.50+this.tilt.y*S*.12;
    const spec=ctx.createRadialGradient(sx,sy,0,sx,sy,S*.78);
    spec.addColorStop(0,'rgba(200,232,255,.105)');
    spec.addColorStop(.45,'rgba(140,196,230,.028)');
    spec.addColorStop(1,'rgba(0,0,0,0)');
    ctx.fillStyle=spec; ctx.fillRect(-S*1.2,-S*1.2,S*2.4,S*2.4);
    const gx=-S*.30+this.tilt.x*S*.2, gy=-S*.62+this.tilt.y*S*.1;
    const glint=ctx.createRadialGradient(gx,gy,0,gx,gy,S*.115);
    glint.addColorStop(0,'rgba(255,255,255,.34)'); glint.addColorStop(.55,'rgba(210,240,255,.06)'); glint.addColorStop(1,'rgba(255,255,255,0)');
    ctx.fillStyle=glint; ctx.fillRect(-S*1.2,-S*1.2,S*2.4,S*2.4);
    ctx.restore();                                    // fin du clip

    /* contour + rim light */
    smoothPath(ctx,CEREBRUM,S,.035,this.t);
    ctx.strokeStyle='rgba(0,0,0,.85)'; ctx.lineWidth=2.4*this.dpr; ctx.stroke();
    ctx.save(); ctx.globalCompositeOperation='lighter';
    smoothPath(ctx,CEREBRUM,S*1.005,.035,this.t);
    ctx.strokeStyle=C(.30+P.glow*.3); ctx.lineWidth=1.3*this.dpr;
    ctx.shadowBlur=16*this.dpr; ctx.shadowColor=C(.5); ctx.stroke();
    ctx.restore();

    /* flux sortants : le travail qui quitte le cerveau vers un module réel */
    if(this.flows.length){
      ctx.globalCompositeOperation='lighter';
      this.flows=this.flows.filter(f=>f.t<f.life);
      for(const f of this.flows){
        f.t+=dt;
        const p=clamp(f.t/f.life,0,1);
        const head=lerp(.25,1.85,p), tail=Math.max(.2,head-.45);
        const ang=f.a;
        const pt=(r)=>[Math.cos(ang)*S*r, Math.sin(ang)*S*r*.8];
        const [x0,y0]=pt(head), [x1,y1]=pt(tail);
        const fade=Math.sin(p*Math.PI);
        ctx.beginPath(); ctx.moveTo(x0,y0); ctx.lineTo(x1,y1);
        ctx.lineWidth=2*this.dpr; ctx.strokeStyle=C(.5*fade);
        ctx.shadowBlur=10*this.dpr; ctx.shadowColor=C(.6*fade);
        ctx.stroke(); ctx.shadowBlur=0;
        ctx.beginPath(); ctx.arc(x0,y0,2.6*this.dpr,0,TAU);
        ctx.fillStyle=C(.85*fade); ctx.fill();
      }
      ctx.globalCompositeOperation='source-over';
    }

    /* absorption (READING) : la donnée entre dans le cerveau */
    if(P.absorb>.04){
      if(this.absorbers.length<26 && Math.random()<P.absorb*.9)
        this.absorbers.push({a:Math.random()*TAU,r:1.75,sp:.5+Math.random()*.6});
      ctx.globalCompositeOperation='lighter';
      this.absorbers=this.absorbers.filter(p=>p.r>.15);
      for(const p of this.absorbers){
        p.r-=dt*p.sp;
        const x=Math.cos(p.a)*S*p.r, y=Math.sin(p.a)*S*p.r*.8;
        ctx.beginPath(); ctx.arc(x,y,1.7*this.dpr,0,TAU);
        ctx.fillStyle=C(.7*clamp((1.75-p.r)/.6,0,1)); ctx.fill();
      }
      ctx.globalCompositeOperation='source-over';
    }
    ctx.restore();
  }

  /* Circonvolutions : filaments suivant un champ de directions parallèle au
     contour (comme les gyri réels), tracés une fois par taille. */
  gyri(S){
    if(this._gyriS===S && this._gyri) return this._gyri;
    this._gyriS=S;
    let seed=11;
    const rnd=()=>{ seed=(seed*9301+49297)%233280; return seed/233280; };
    // champ : tangent au contour + ondulation → trajets sinueux, jamais droits
    const dir=(x,y)=>{
      const a=Math.atan2(y,x);
      return a+Math.PI/2 + .75*Math.sin(a*2.3) + .45*Math.sin((x/S)*3.1+(y/S)*2.2);
    };
    const inside=(x,y)=>{
      const r=Math.hypot(x/S,(y/S)/0.94);
      return r < 0.9 - .1*Math.sin(Math.atan2(y,x)*3);
    };
    const out=[];
    for(let i=0;i<26;i++){
      const a=rnd()*TAU, rr=.12+rnd()*.74;
      let x=Math.cos(a)*rr*S, y=Math.sin(a)*rr*S*.88;
      const pts=[[x,y]];
      const step=S*.105;
      for(let k=0;k<9;k++){                        // avance
        const d=dir(x,y)+(rnd()-.5)*.22;
        x+=Math.cos(d)*step; y+=Math.sin(d)*step;
        if(!inside(x,y)) break;
        pts.push([x,y]);
      }
      [x,y]=pts[0];
      for(let k=0;k<9;k++){                        // recule
        const d=dir(x,y)+Math.PI+(rnd()-.5)*.22;
        x+=Math.cos(d)*step; y+=Math.sin(d)*step;
        if(!inside(x,y)) break;
        pts.unshift([x,y]);
      }
      if(pts.length>3) out.push(pts);
    }
    this._gyri=out; return out;
  }
}


  /* ======================================================== API publique */
  /* Alias d'états : les noms métier de JARVIS retombent sur une signature. */
  const ALIASES = {
    DELEGATING:'CODING', GENERATING:'CODING', USING_TOOL:'READING',
    BROWSING:'SEARCHING', DEPLOYING:'SYNCING', VERIFYING:'SEARCHING',
    LEARNING:'RECALLING', WARNING:'ERROR', SLEEPING:'IDLE', WAKE:'LISTENING',
    PROCESSING:'THINKING', EXECUTING:'CODING',
  };

  const API = {
    instances: [],
    state: 'IDLE',
    reason: '',
    _raf: null, _last: 0, _zoneTimers: {},

    mount(canvas){
      if(!canvas) return null;
      const b = new ObsidianBrain(canvas);
      b.setState(this.state);
      this.instances.push(b);
      this.start();
      return b;
    },
    unmount(canvas){ this.instances = this.instances.filter(b=>b.cv!==canvas); },

    setState(name, extra){
      const key = String(name||'').toUpperCase();
      const real = STATES[key] ? key : (STATES[ALIASES[key]] ? ALIASES[key] : 'IDLE');
      this.state = real;
      this.reason = (extra && extra.reason) || '';
      this.instances.forEach(b=>b.setState(real));
      document.documentElement.setAttribute('data-brain-state', real);
      window.dispatchEvent(new CustomEvent('jarvis:brain-state',
        { detail:{ state:real, requested:key, reason:this.reason } }));
      return real;
    },

    /** Une zone reste chaude tant que le travail dure (ttl = filet de sécurité). */
    activateZone(zone, ttl){
      if(!ZONES[zone]) return false;
      this.instances.forEach(b=>{ b.zoneForce[zone]=true; });
      clearTimeout(this._zoneTimers[zone]);
      if(ttl) this._zoneTimers[zone]=setTimeout(()=>this.deactivateZone(zone), ttl);
      return true;
    },
    deactivateZone(zone){
      if(!ZONES[zone]) return false;
      clearTimeout(this._zoneTimers[zone]);
      this.instances.forEach(b=>{ b.zoneForce[zone]=false; });
      return true;
    },

    pulsePath(zone, count){
      if(!ZONES[zone]) return false;
      for(let i=0;i<(count||1);i++) this.instances.forEach(b=>b.fire(zone));
      return true;
    },

    showToolFlow(label){
      this.activateZone('TOOLS', 9000);
      this.instances.forEach(b=>b.emit('TOOLS', label||''));
      return true;
    },
    showAgentFlow(agent){
      this.activateZone('CONTEXT', 9000);
      this.instances.forEach(b=>b.emit('AGENT', agent||''));
      return true;
    },

    setActivity(v){ const n=clamp(+v||0,0,1); this.instances.forEach(b=>{ b.level=n; }); },

    reset(){
      Object.keys(ZONES).forEach(z=>this.deactivateZone(z));
      this.setState('IDLE');
      this.instances.forEach(b=>{ b.pulses.length=0; b.absorbers.length=0; b.flows.length=0; b.level=0; });
    },

    aim(x,y){ this.instances.forEach(b=>{ b.aim.x=x; b.aim.y=y; }); },

    start(){
      if(this._raf) return;
      this._last = performance.now();
      const loop=(now)=>{
        this._raf = requestAnimationFrame(loop);
        const dt = clamp((now-this._last)/1000, 0, .1);
        this._last = now;
        if(document.hidden) return;                     // onglet caché : pause
        for(const b of this.instances){
          if(!b.cv.isConnected) continue;
          if(b.cv.offsetParent === null) continue;       // module invisible : pause
          const r = b.cv.getBoundingClientRect();
           if(r.bottom < -80 || r.top > innerHeight+80) continue;
           if(r.width < 200){
             b._skip = (b._skip||0)+1;
             if(b._skip % 3) continue;
             b.frame(Math.min(dt*3,.1));
           } else b.frame(dt);
        }
      };
      this._raf = requestAnimationFrame(loop);
    },
    stop(){ if(this._raf) cancelAnimationFrame(this._raf); this._raf=null; },

    ZONES, STATES,
  };

  window.ObsidianBrain = API;
  window.JarvisBrain5 = API;
})();
