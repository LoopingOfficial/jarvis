/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — BRAIN ACTIVITY + SESSION ACTIVITY
   Uniquement des timestamps réellement présents. Aucun historique inventé.
   ========================================================================== */
(function () {
  'use strict';

  const byId = (id)=>document.getElementById(id);
  const B = ()=>window.JarvisBrainData;
  const INS = ()=>window.JarvisBrainInspector?.Inspector;

  const el = (tag, cls, text)=>{
    const n = document.createElement(tag);
    if(cls) n.className = cls;
    if(text !== undefined) n.textContent = String(text);
    return n;
  };

  const tsOf = (v)=>{
    if(v==null || v==='') return 0;
    if(typeof v==='number') return v>1e12 ? v : v*1000;
    const n=Date.parse(v); return Number.isFinite(n)?n:0;
  };
  const clock = (ms)=> new Date(ms).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
  const day = (ms)=> new Date(ms).toLocaleDateString('fr-FR',{day:'2-digit',month:'short'});

  const Timeline = {
    host(){
      let h=byId('v5Timeline');
      if(!h){
        h=document.createElement('aside');
        h.id='v5Timeline';
        h.className='v5-timeline';
        h.setAttribute('aria-label','Brain Activity');
        document.body.appendChild(h);
      }
      return h;
    },

    events(){
      const data=B(); if(!data) return [];
      const index=data._knowledge||new Map();
      const out=[];
      for(const n of data.nodes){
        const k=n.knowledgeRef?index.get(n.knowledgeRef):null;
        const created=tsOf((k&&k.created_at)||n.meta.created_at);
        const updated=tsOf((k&&k.updated_at)||n.meta.updated_at);
        const verified=tsOf(k&&(k.last_validated_at||k.evidence&&k.evidence.verified_at));
        if(created) out.push({at:created, kind:'CREATED', label:n.label, id:n.id});
        if(updated && updated!==created) out.push({at:updated, kind:'UPDATED', label:n.label, id:n.id});
        if(verified) out.push({at:verified, kind:'VERIFIED', label:n.label, id:n.id});
      }
      out.sort((a,b)=>b.at-a.at);
      return out.slice(0,18);
    },

    render(){
      const data=B(); const h=this.host();
      if(!data||!data.nodes.length){ h.hidden=true; return; }
      const page=document.querySelector('#page-memory.page.on, #page-memory.v5-migrated');
      const vis=!!byId('page-memory') && !byId('page-memory')?.hidden
        && window.JarvisSpatial?.context==='work';
      h.hidden=!vis;
      if(!vis) return;
      h.replaceChildren();
      h.appendChild(el('span','v5-kick','BRAIN ACTIVITY'));
      const list=el('div','list');
      const ev=this.events();
      if(!ev.length) list.appendChild(el('p','empty','Aucun horodatage exposé.'));
      ev.forEach(e=>{
        const b=el('button','row');
        b.appendChild(el('time',null,day(e.at)+' '+clock(e.at)));
        b.appendChild(el('b',null,e.kind));
        b.appendChild(el('span',null,e.label));
        b.onclick=()=>INS()?.focus(e.id);
        list.appendChild(b);
      });
      h.appendChild(list);

      const sess=el('div','sess');
      sess.appendChild(el('span','v5-kick','SESSION ACTIVITY'));
      const recs=data.sessionRecalls||[];
      if(!recs.length){
        sess.appendChild(el('p','empty','Aucun rappel cette session.'));
      } else {
        recs.slice(0,8).forEach(r=>{
          const b=el('button','row');
          b.appendChild(el('time',null,clock(r.at)));
          b.appendChild(el('b',null,'RECALL'));
          b.appendChild(el('span',null, r.query?('« '+r.query+' »'):''));
          b.appendChild(el('em',null,(r.ids.length||0)+' concepts'));
          if(r.ids[0]) b.onclick=()=>INS()?.focus(r.ids[0]);
          sess.appendChild(b);
        });
        sess.appendChild(el('p','note','Non persisté — disparaît au rechargement.'));
      }
      h.appendChild(sess);
    },
  };

  window.JarvisBrainTimeline = Timeline;
  window.addEventListener('jarvis:page',(e)=>{
    if(['memory','brain','knowledge'].includes(e.detail?.page)) setTimeout(()=>Timeline.render(), 1200);
    else { const h=byId('v5Timeline'); if(h) h.hidden=true; }
  });
  window.addEventListener('jarvis:brain-recall',()=>Timeline.render());
})();
