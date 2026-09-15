/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_sync.js  (PHASE 2)
   Scène de synchronisation : GOOGLE SHEET → OBSIDIAN BRAIN → SERVEUR.

   PRÉSENTATION UNIQUEMENT. Le moteur (audit, batch, idempotence, rollback,
   refresh, confirmations) n'est pas touché : on observe SyncFeedback V2.2 —
   ses étapes réelles pilotent la scène, et l'overlay d'origine reste actif
   en dessous (il porte les boutons, les erreurs et le rapport).

   Aucune étape n'est peinte sans que SyncFeedback l'ait déclarée.
   ========================================================================== */
(function () {
  'use strict';

  const TAU = Math.PI*2;
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
  const lerp=(a,b,t)=>a+(b-a)*t;
  const dprOf=()=>clamp(window.devicePixelRatio||1,1,2);

  /* Étapes réelles du pipeline, dans l'ordre d'exécution serveur. */
  const STEPS = [
    ['backup','BACKUP'], ['hash','HASH'], ['write','WRITE'],
    ['reread','VERIFY'], ['verify','VERIFY'], ['done','REFRESH'],
  ];
  const LABELS = [['backup','BACKUP'],['hash','HASH'],['write','WRITE'],['verify','VERIFY'],['done','REFRESH']];

  const Scene = {
    el:null, raf:null, t:0, open:false,
    steps:{}, flows:[], plan:{create:0,update:0,total:0},

    mount(){
      if(this.el) return this.el;
      const el=document.createElement('div');
      el.className='v5-syncscene';
      el.innerHTML=`
        <canvas id="v5SyncCv"></canvas>
        <div class="v5-sync-node" data-n="sheet"><b>GOOGLE SHEET</b><small id="v5SyncSrc">source</small></div>
        <div class="v5-sync-node" data-n="brain"><b>OBSIDIAN BRAIN</b><small>analyse des différences</small></div>
        <div class="v5-sync-node" data-n="server"><b>SERVEUR</b><small id="v5SyncDst">cible</small></div>
        <div class="v5-sync-plan" id="v5SyncPlan"></div>
        <div class="v5-sync-steps" id="v5SyncSteps">
          ${LABELS.map(([id,l])=>`<div class="v5-sync-step" data-step="${id}"><i></i>${l}</div>`).join('')}
        </div>`;
      document.body.appendChild(el);
      this.el=el;
      return el;
    },

    show(entries, plan){
      this.mount();
      this.steps={};
      this.flows=[];
      const list=entries||[];
      const create=list.filter(e=>/create/i.test(e?.action||e?.status||'')).length;
      const update=list.filter(e=>/update/i.test(e?.action||e?.status||'')).length;
      this.plan={create, update, total:list.length};
      const p=document.getElementById('v5SyncPlan');
      if(p) p.innerHTML=`
        <span><b>${create||list.length}</b>CREATE</span>
        <span><b>${update}</b>UPDATE</span>
        <span><b>${list.length}</b>ÉLÉMENTS</span>`;
      const dst=document.getElementById('v5SyncDst');
      if(dst && plan && plan.target) dst.textContent=String(plan.target).slice(0,42);
      this.el.querySelectorAll('.v5-sync-step').forEach(s=>s.className='v5-sync-step');
      setTimeout(()=>this.el.classList.add('in'), 20);
      this.open=true;
      this.start();
    },

    hide(){
      this.open=false;
      this.el?.classList.remove('in');
    },

    /** Une étape réelle passe RUNNING / SUCCESS / ERROR. */
    step(id, status){
      this.steps[id]=status;
      const key = (id==='reread') ? 'verify' : id;
      const el=this.el?.querySelector(`[data-step="${key}"]`);
      if(el){
        el.classList.toggle('run', status==='RUNNING');
        el.classList.toggle('ok', status==='SUCCESS');
        el.classList.toggle('err', status==='ERROR');
      }
      if(status!=='RUNNING') return;
      // Direction du flux selon l'étape : vers le serveur, puis retour de
      // vérification serveur → Brain.
      if(id==='backup'||id==='hash') this.flows.push({from:'server',to:'brain',t:0,tone:'cy'});
      else if(id==='write') this.flows.push({from:'brain',to:'server',t:0,tone:'amber'});
      else if(id==='reread'||id==='verify') this.flows.push({from:'server',to:'brain',t:0,tone:'ok'});
      else if(id==='prepare') this.flows.push({from:'sheet',to:'brain',t:0,tone:'cy'});
    },

    anchors(){
      const W=innerWidth, H=innerHeight;
      const y=H*0.42;
      return {
        sheet:{x:W*0.18,y}, brain:{x:W*0.5,y}, server:{x:W*0.82,y},
      };
    },

    start(){
      if(this.raf) return;
      let last=performance.now();
      const loop=(now)=>{
        this.raf=requestAnimationFrame(loop);
        if(!this.open || document.hidden){ if(!this.open){ cancelAnimationFrame(this.raf); this.raf=null; } return; }
        const dt=clamp((now-last)/1000,0,.1); last=now;
        this.frame(dt);
      };
      this.raf=requestAnimationFrame(loop);
    },

    frame(dt){
      const cv=document.getElementById('v5SyncCv'); if(!cv) return;
      const d=dprOf();
      if(cv.width!==Math.round(innerWidth*d)){ cv.width=Math.round(innerWidth*d); cv.height=Math.round(innerHeight*d); }
      const ctx=cv.getContext('2d');
      ctx.setTransform(d,0,0,d,0,0);
      ctx.clearRect(0,0,innerWidth,innerHeight);
      this.t+=dt;

      const A=this.anchors();
      // positionne les libellés DOM
      for(const k in A){
        const el=this.el.querySelector(`[data-n="${k}"]`);
        if(el){ el.style.left=A[k].x+'px'; el.style.top=(A[k].y-72)+'px'; }
      }
      const plan=document.getElementById('v5SyncPlan');
      if(plan){ plan.style.left='50%'; plan.style.top=(A.brain.y+64)+'px'; }
      const steps=document.getElementById('v5SyncSteps');
      if(steps){ steps.style.left='50%'; steps.style.top=(A.brain.y+118)+'px'; }

      const line=(a,b,alpha)=>{
        ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y);
        ctx.strokeStyle=`rgba(120,180,215,${alpha})`; ctx.lineWidth=1;
        ctx.setLineDash([3,7]); ctx.stroke(); ctx.setLineDash([]);
      };
      line(A.sheet,A.brain,.16); line(A.brain,A.server,.16);

      // pastilles des trois pôles
      const disc=(p,r,col,glow)=>{
        const g=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,r);
        g.addColorStop(0,`rgba(${col},${glow})`); g.addColorStop(1,`rgba(${col},0)`);
        ctx.fillStyle=g; ctx.beginPath(); ctx.arc(p.x,p.y,r,0,TAU); ctx.fill();
      };
      const pulse=1+Math.sin(this.t*1.5)*0.05;
      disc(A.sheet,26,'120,180,215',.28);
      disc(A.brain,42*pulse,'56,223,255',.42);
      disc(A.server,26,'120,180,215',.28);

      const TONE={cy:'56,223,255', amber:'255,176,87', ok:'61,240,166', err:'255,91,110'};
      this.flows=this.flows.filter(f=>f.t<1);
      for(const f of this.flows){
        f.t+=dt*0.55;
        const a=A[f.from], b=A[f.to];
        const e=f.t*f.t*(3-2*f.t);
        const x=lerp(a.x,b.x,e), y=lerp(a.y,b.y,e)+Math.sin(f.t*Math.PI)*-18;
        const c=TONE[f.tone]||TONE.cy;
        const g=ctx.createRadialGradient(x,y,0,x,y,10);
        g.addColorStop(0,'rgba(255,255,255,.95)');
        g.addColorStop(.4,`rgba(${c},.8)`);
        g.addColorStop(1,`rgba(${c},0)`);
        ctx.fillStyle=g; ctx.beginPath(); ctx.arc(x,y,10,0,TAU); ctx.fill();
      }
    },
  };

  /* ------------------------------------------------ greffe sur SyncFeedback */
  function bind(){
    const SF=window.SyncFeedback;
    if(!SF || SF.__v5scene) return false;
    SF.__v5scene=true;

    const open=SF.open.bind(SF);
    SF.open=(entries, plan)=>{
      const r=open(entries, plan);
      if(r!==false) Scene.show(entries, plan);
      return r;
    };
    const setStep=SF.setStep.bind(SF);
    SF.setStep=(id,status)=>{ const r=setStep(id,status); Scene.step(id,status); return r; };
    const close=SF.close.bind(SF);
    SF.close=()=>{ const r=close(); Scene.hide(); return r; };
    return true;
  }

  const start=()=>{
    if(document.documentElement.getAttribute('data-jarvis-ui')!=='spatial_v5'){
      // En mode legacy_v4, la scène ne s'installe pas du tout.
      return;
    }
    if(!bind()) setTimeout(start, 600);
  };
  if(document.readyState==='loading') addEventListener('DOMContentLoaded',()=>setTimeout(start,300));
  else setTimeout(start,300);

  window.JarvisSyncScene = Scene;
})();
