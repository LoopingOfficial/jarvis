/* ==========================================================================
   JARVIS_SPATIAL_OS_V5 — spatial_events.js
   Branchement du Brain sur les ÉVÉNEMENTS RÉELS de JARVIS.

   Règle tenue ici : aucune animation ne prétend qu'un outil travaille.
   Chaque zone allumée, chaque flux sortant et chaque impulsion correspond à
   un événement effectivement reçu (SSE `tool.*`, `agent.*`, `memory.*`,
   `brain.*`, `image.generation.*`, `voice.*`) ou à un appel réel du moteur de
   synchronisation. Quand l'événement de fin arrive, la zone s'éteint.
   ========================================================================== */
(function () {
  'use strict';

  const B = () => window.ObsidianBrain;
  const S = () => window.JarvisSpatial;

  /* Classement d'un outil réel → signature visuelle + zone sollicitée. */
  function classify(name){
    const n = String(name||'').toLowerCase();
    if(/ssh|sftp|scp|ftp|rsync|remote/.test(n))        return {state:'READING',  zone:'TOOLS',     label:'SSH'};
    if(/sheet|gsheet|google|csv|xlsx|tableur/.test(n)) return {state:'READING',  zone:'KNOWLEDGE', label:'GOOGLE SHEET'};
    if(/browser\.(navigate|click|type|scroll|wait|back\.)/.test(n)) return {state:'SEARCHING',zone:'KNOWLEDGE', label:'NAVIGATEUR'};
    if(/browser|web\.|search|scrape|http|fetch/.test(n)) return {state:'SEARCHING',zone:'KNOWLEDGE', label:'WEB'};
    if(/memory|recall|souvenir/.test(n))               return {state:'RECALLING',zone:'MEMORY',    label:'MÉMOIRE'};
    if(/knowledge|learn/.test(n))                      return {state:'SEARCHING',zone:'KNOWLEDGE', label:'CONNAISSANCES'};
    if(/code|editor|git|file|fs\.|document/.test(n))   return {state:'CODING',   zone:'TOOLS',     label:'CODE'};
    if(/image|diffusion|comfy|render|blender/.test(n)) return {state:'GENERATING',zone:'TOOLS',    label:'GÉNÉRATION'};
    if(/deploy|docker|server|infra/.test(n))           return {state:'SYNCING',  zone:'TOOLS',     label:'INFRASTRUCTURE'};
    return {state:'READING', zone:'TOOLS', label:'OUTIL'};
  }

  /* États publiés par le backend (jarvis.state) → états du Brain. */
  const BACKEND_STATE = {
    IDLE:'IDLE', THINKING:'THINKING', ACTING:'READING', SPEAKING:'SPEAKING',
    LISTENING:'LISTENING', RECALLING:'RECALLING', SEARCHING:'SEARCHING',
    ERROR:'ERROR', SUCCESS:'SUCCESS',
  };

  /* Kinds de tâches réels créés par le backend (jarvis/tasks.py + orchestrator)
     → libellés exploitables par le Brain. On affiche ce que le backend a
     réellement déclaré ; on n'invente jamais de sous-types. */
  const TASK_KIND_LABELS = {
    brainrot_site_comparison: 'COMPARE · GOOGLE SHEET',
    google_sheet_analysis:    'GOOGLE SHEET',
    security_audit_readonly:  'AUDIT SÉCURITÉ',
    workflow:                 'WORKFLOW',
    file_edit:                'ÉDITION FICHIER',
    manual:                   'TÂCHE MANUELLE',
    chat:                     'TÂCHE',
  };
  function taskLabel(kind, fallback){
    const k = String(kind || '');
    return TASK_KIND_LABELS[k] || (k ? String(k).toUpperCase().slice(0, 18) : fallback);
  }

  const Events = {
    _busy: 0,                       // nombre de travaux réels en cours
    _busyAt: 0,                     // dernier événement de travail reçu
    _lastEvent: null,               // dernier événement de travail vu
    watchdogLog: [],                // interventions du filet de sécurité
    _flashT: null,

    init(){
      if(typeof J === 'undefined' || typeof J.on !== 'function') return;
      if(this._bound) return;
      this._bound = true;
      // Filet : sans nouvel événement pendant 90 s, le compteur est remis à
      // zéro — l'UI ne reste jamais coincée sur un travail fantôme.
      setInterval(()=>{
        if(this._busy && Date.now()-this._busyAt > 90000){
          // Le watchdog est un filet, pas un tapis : chaque intervention est
          // journalisée pour qu'on puisse remonter au travail non soldé.
          const entry={
            at:new Date().toISOString(),
            etat_precedent:B()?.state || null,
            compteur:this._busy,
            dernier_evenement:this._lastEvent || null,
            depuis_ms:Date.now()-this._busyAt,
            raison:'travail non soldé (aucun événement de fin reçu)',
          };
          this.watchdogLog.push(entry);
          if(this.watchdogLog.length>20) this.watchdogLog.shift();
          console.warn('[V5 watchdog] remise à zéro', entry);
          this._busy = 0;
          if(['THINKING','DELEGATING','READING','CODING','GENERATING'].includes(B()?.state))
            B()?.setState('IDLE');
        }
      }, 15000);

      // Trace du dernier événement de travail : sert au diagnostic ci-dessus.
      J.on('*', (type)=>{ if(/^(tool|agent|image.generation)./.test(type)) this._lastEvent=type; });

      /* ---------------------------------------------------------- état */
      J.on('jarvis.state', (d)=>{
        const st = BACKEND_STATE[String(d?.state||'').toUpperCase()];
        if(st) B()?.setState(st, {reason:d?.reason||''});
      });

      /* --------------------------------------------------------- outils */
      J.on('tool.started', (d)=>{
        const name = d?.tool_id || d?.name || '';
        const c = classify(name);
        this._busy++; this._busyAt=Date.now();
        B()?.setState(c.state, {reason:name});
        B()?.activateZone(c.zone, 45000);
        B()?.showToolFlow(c.label);
        B()?.pulsePath(c.zone, 2);
        S()?.toolCard(c.label, d?.name || name);
        S()?.activity('OUTIL', d?.name || name, 'cy');
        this.sourceBadge(c.label, d?.name || name);
      });
      J.on('tool.completed', (d)=>{
        const c = classify(d?.tool_id || d?.name || '');
        this._busy = Math.max(0, this._busy-1); this._busyAt=Date.now();
        B()?.deactivateZone(c.zone);
        if(d && d.ok === false){
          B()?.setState('ERROR', {reason:d?.name||''});
          S()?.activity('OUTIL ÉCHEC', d?.name||d?.tool_id, 'err');
        } else {
          this.flash('SUCCESS', 1400);
          S()?.activity('OUTIL OK', d?.name||d?.tool_id, 'ok');
        }
      });
      // Le brain_manager publie la famille d'outil réellement active.
      J.on('brain.tool.active', (d)=>{
        const c = classify(d?.tool_id || d?.family || '');
        B()?.activateZone(c.zone, 20000);
      });

      /* --------------------------------------------------------- mémoire */
      const memory = (label)=>{
        B()?.setState('RECALLING', {reason:label});
        B()?.activateZone('MEMORY', 6000);
        B()?.pulsePath('MEMORY', 3);
        S()?.activity('MÉMOIRE', label, 'cy');
      };
      J.on('brain.search', (d)=> memory(d?.query || 'recherche mémoire'));
      J.on('brain.path',   (d)=> memory(d?.label || 'chemin mémoire'));
      J.on('memory.created',(d)=> { B()?.activateZone('MEMORY',3000); B()?.pulsePath('MEMORY',2); });
      J.on('memory.updated',(d)=> { B()?.activateZone('MEMORY',3000); B()?.pulsePath('MEMORY',2); });

      /* --------------------------------------------------- connaissances */
      const knowledge = (label)=>{
        B()?.activateZone('KNOWLEDGE', 8000);
        B()?.pulsePath('KNOWLEDGE', 3);
        S()?.activity('CONNAISSANCES', label, 'cy');
      };
      J.on('knowledge.learn.created', (d)=> knowledge(d?.title||'nouveau savoir'));
      J.on('knowledge.learn.updated', (d)=> knowledge(d?.title||'savoir mis à jour'));
      J.on('knowledge.validated',     (d)=> knowledge(d?.title||'savoir validé'));
      J.on('learning.search.started', (d)=>{
        B()?.setState('SEARCHING',{reason:d?.topic||''});
        knowledge(d?.topic||'apprentissage');
      });

      /* ---------------------------------------------------------- agents */
      J.on('agent.started', (d)=>{
        const who = d?.name || d?.id || 'agent';
        this._busy++; this._busyAt=Date.now();
        B()?.setState('DELEGATING', {reason:who});
        B()?.showAgentFlow(who);
        B()?.activateZone('CONTEXT', 60000);
        this._lastAgent = who;
        S()?.toolCard('AGENT · '+who, d?.action || 'délégation');
        S()?.activity('AGENT', who, 'cy');
        this.sourceBadge('AGENT', who);
        S()?.refreshMiniMeta?.({state:'DELEGATING'});
        S()?.setCtx?.(d?.action || d?.objective || d?.goal || 'délégation en cours');
      });
      J.on('agent.progress', (d)=>{ B()?.showAgentFlow(d?.name||d?.id||''); });
      J.on('agent.completed',(d)=>{
        this._busy = Math.max(0, this._busy-1); this._busyAt=Date.now();
        B()?.deactivateZone('CONTEXT');
        this.flash('SUCCESS', 1600);
        S()?.activity('AGENT OK', d?.name||d?.id, 'ok');
        S()?.resetCtx?.();
      });
      J.on('agent.failed', (d)=>{
        this._busy = Math.max(0, this._busy-1); this._busyAt=Date.now();
        B()?.deactivateZone('CONTEXT');
        B()?.setState('ERROR',{reason:d?.name||d?.id||''});
        S()?.activity('AGENT ÉCHEC', d?.name||d?.id, 'err');
        S()?.resetCtx?.();
      });
      J.on('agent.idle', ()=>{ if(!this._busy) B()?.setState('IDLE'); });

      /* ------------------------------------------------ génération d'image */
      J.on('image.generation.started', (d)=>{
        this._busy++; this._busyAt=Date.now();
        B()?.setState('GENERATING',{reason:d?.prompt||''});
        B()?.activateZone('TOOLS', 120000);
        S()?.activity('IMAGE', d?.prompt||'génération', 'cy');
      });
      J.on('image.generation.completed', ()=>{
        this._busy=Math.max(0,this._busy-1); this._busyAt=Date.now();
        B()?.deactivateZone('TOOLS'); this.flash('SUCCESS',1600);
      });
      J.on('image.generation.failed', ()=>{
        this._busy=Math.max(0,this._busy-1); this._busyAt=Date.now();
        B()?.deactivateZone('TOOLS'); B()?.setState('ERROR');
      });

      /* ------------------------------------------------------------ voix */
      J.on('voice.local', (d)=>{
        const s=String(d?.state||'').toUpperCase();
        window.dispatchEvent(new CustomEvent('jarvis:voice-state',{detail:{state:s}}));
        if(s==='LISTENING'||s==='WAKE') B()?.setState('LISTENING');
        else if(s==='PROCESSING') B()?.setState('THINKING');
        else if(s==='SPEAKING') B()?.setState('SPEAKING');
        else if(s==='IDLE'){
          // La parole est terminée : on quitte SPEAKING même si un travail
          // n'a pas publié sa fin — ses propres événements le re-signaleront.
          const cur=B()?.state;
          if(cur==='SPEAKING' || !this._busy) B()?.setState('IDLE');
        }
      });
      J.on('voice.state', (d)=>{
        const s=String(d?.state||'').toUpperCase();
        if(s==='SPEAKING') B()?.setState('SPEAKING');
      });
      // Le niveau sonore réel du TTS pilote la pulsation (aucun faux signal).
      const app=window.App;
      if(app && typeof app.robotAudioLevel==='function' && !app.__v5Audio){
        const orig=app.robotAudioLevel.bind(app);
        app.robotAudioLevel=(lvl)=>{ orig(lvl); B()?.setActivity(lvl); };
        app.__v5Audio=true;
      }

      /* ------------------------------------------------------- tâches */
      const taskTitle=(d)=> d?.title || d?.message || d?.name || '';
      J.on('task.created',  (d)=>{
        this._taskAt=Date.now();
        this._taskName=taskTitle(d);
        S()?.setTask('run', this._taskName||'Travail en cours', taskLabel(d?.kind,''));
      });
      J.on('task.started',  (d)=>{
        const lab = taskLabel(d?.kind, '');
        if(lab){ this._lastAgent = lab; S()?.activity('AGENT', lab, 'cy'); }
        this._taskName=taskTitle(d)||this._taskName;
        S()?.setTask('run', this._taskName||'Travail en cours', lab);
      });
      J.on('task.progress', (d)=>{
        const msg=d?.message||d?.log?.message||'';
        // Un événement de progression ne porte pas toujours le nom de la
        // tâche : on ne l'efface pas pour autant.
        S()?.setTask('run', taskTitle(d)||this._taskName||msg||'Travail en cours');
        const plan=d?.plan;
        if(Array.isArray(plan)){
          // Contrat backend (tasks.py) : {key, label, state ∈ idle|run|done|err}.
          // Les formes héritées (chaîne, {step}/{done}) restent acceptées.
          S()?.setTaskSteps(plan.map(p=>{
            if(typeof p==='string') return {label:p.slice(0,22), state:'idle'};
            const st=p.state || (p.done||p.status==='done' ? 'done'
              : p.status==='err'||p.status==='failed' ? 'err'
              : p.status==='run'||p.active ? 'run' : 'idle');
            return {label:String(p.label||p.step||p.name||p.key||'').slice(0,22), state:st};
          }).filter(s=>s.label));
        }
      });
      J.on('task.completed',(d)=>{
        const sec=this._taskAt?((Date.now()-this._taskAt)/1000).toFixed(1)+' s':'';
        S()?.setTask('done', taskTitle(d)||'Terminé', sec);
        // La checklist n'est pas effacée ici : setTask la retire avec le
        // bandeau, pour qu'on puisse lire le bilan des étapes.
        this.flash('SUCCESS',1200);
      });
      J.on('task.failed',   (d)=>{ S()?.setTask('err', taskTitle(d)||'Échec'); B()?.setState('ERROR'); });

      /* ------------------------------------------------ navigateur live */
      J.on('browser.session.started', (d)=>{
        // L'ouverture du dock est pilotée par spatial_browser.js.
        S()?.activity('NAVIGATEUR', d?.url||'ouverture', 'warn');
      });
      J.on('browser.navigate', (d)=>{
        B()?.activateZone('KNOWLEDGE', 40000);
        S()?.activity('NAVIGATEUR', d?.title||d?.url||'', 'warn');
      });
      J.on('browser.action', (d)=>{
        B()?.activateZone('KNOWLEDGE', 20000);
        window.JarvisBrowser?.log?.((d?.kind||'ACTION')+(d?.target?' · '+d.target:''), d?.private);
      });
      J.on('browser.gate', (d)=>{
        window.JarvisBrowser?.setGate?.(true, d?.message||'Action requise');
        B()?.setState('THINKING', {reason:'gate navigateur'});
      });
      J.on('browser.session.resumed', (d)=>{
        window.JarvisBrowser?.setGate?.(false);
        B()?.setState('IDLE');
      });
      J.on('browser.download', (d)=>{
        window.JarvisBrowser?.log?.(('TÉLÉCHARGÉ · '+(d?.filename||'')).slice(0,60), false);
        S()?.activity('TÉLÉCHARGEMENT', d?.filename||'', 'warn');
      });
      J.on('browser.error', (d)=>{
        window.JarvisBrowser?.log?.('ERREUR · '+(d?.message||''), false);
        B()?.setState('ERROR', {reason:'navigateur'});
      });
      J.on('browser.session.finished', ()=>{
        B()?.deactivateZone('KNOWLEDGE');
        S()?.activity('NAVIGATEUR', 'fermé');
      });

      /* ------------------------------------------------------- système */
      J.on('system.warning', (d)=> S()?.activity('SYSTÈME', d?.message||'alerte', 'warn'));
      J.on('stream.close', ()=> S()?.activity('FLUX', 'reconnexion…', 'warn'));

      this.bindSync();
    },

    /** Trace la source réellement consultée sur la réponse en cours. */
    sourceBadge(kind, detail){
      const nodes = (window.App && window.App.pendingReplyNodes) || [];
      const key = kind + (detail ? ' · ' + String(detail).slice(0,40) : '');
      for(const n of nodes){
        if(!n || !n.isConnected) continue;
        if([...n.querySelectorAll('.v5-src')].some(b => b.textContent === key)) continue;
        const b = document.createElement('span');
        b.className = 'v5-src';
        b.innerHTML = '<i></i>';
        b.append(key);
        n.appendChild(b);
      }
    },

    /** Flash court qui ne vole pas un travail en cours. */
    flash(state, ms){
      const b=B(); if(!b) return;
      const prev=b.state;
      b.setState(state);
      clearTimeout(this._flashT);
      this._flashT=setTimeout(()=>{
        if(b.state===state) b.setState(this._busy ? (prev===state?'THINKING':prev) : 'IDLE');
      }, ms||1500);
    },

    /* ---------------------------------------------------- SYNCHRONISATION
       Le pipeline de synchronisation n'émet pas de SSE : il pilote
       SyncFeedback (V2.2). On s'y greffe sans le remplacer — les étapes
       réelles deviennent l'activité visible autour du Brain. */
    bindSync(){
      const SF = window.SyncFeedback;
      if(!SF || SF.__v5) return;
      SF.__v5 = true;

      const ZONE_OF_STEP = {
        prepare:'CONTEXT', backup:'TOOLS', hash:'KNOWLEDGE', write:'TOOLS',
        reread:'KNOWLEDGE', verify:'CONTEXT', done:'MEMORY',
      };

      const open = SF.open.bind(SF);
      SF.open = (entries, plan)=>{
        const r = open(entries, plan);
        if(r !== false){
          B()?.setState('SYNCING', {reason:'synchronisation'});
          B()?.activateZone('TOOLS', 180000);
          S()?.activity('SYNC', `${(entries||[]).length} élément(s)`, 'warn');
        }
        return r;
      };

      const setStep = SF.setStep.bind(SF);
      SF.setStep = (id, status)=>{
        const r = setStep(id, status);
        const zone = ZONE_OF_STEP[id] || 'TOOLS';
        if(status === 'RUNNING'){
          B()?.activateZone(zone, 60000);
          B()?.pulsePath(zone, 3);
          B()?.showToolFlow(String(id).toUpperCase());
          S()?.activity('SYNC · '+String(id).toUpperCase(), 'en cours', 'warn');
        } else if(status === 'SUCCESS'){
          B()?.deactivateZone(zone);
          B()?.pulsePath('MEMORY', 1);
        } else if(status === 'ERROR'){
          B()?.deactivateZone(zone);
          B()?.setState('ERROR', {reason:'étape '+id});
          S()?.activity('SYNC ÉCHEC', String(id).toUpperCase(), 'err');
        }
        return r;
      };

      const close = SF.close.bind(SF);
      SF.close = ()=>{
        const r = close();
        Object.keys(ZONE_OF_STEP).forEach(k=>B()?.deactivateZone(ZONE_OF_STEP[k]));
        if(B()?.state === 'SYNCING') B()?.setState('IDLE');
        return r;
      };
    },
  };

  window.JarvisSpatialEvents = Events;
})();
