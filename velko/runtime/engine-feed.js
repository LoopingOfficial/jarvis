/** Flux du moteur, ouvert UNE seule fois par onglet.
 *  Un navigateur ne tient que six connexions par origine : si chaque écran
 *  ouvrait son propre EventSource, la file serait pleine et les commandes
 *  n'atteindraient jamais le moteur. Les cadres et fenêtres filles réutilisent
 *  donc le flux de leur parent, qui est de même origine. */
const KEY='__velkoEngineFeed';
function host(){
 for(const candidate of [window,window.parent,window.opener]){
  try{if(candidate&&candidate!==window&&candidate[KEY])return candidate[KEY];}catch{/* origine différente */}
 }
 return null;
}
class Feed {
 constructor(){this.listeners=new Set();this.source=new EventSource('/api/events');
  this.source.onmessage=e=>{let frame;try{frame=JSON.parse(e.data);}catch{return;}this.dispatch(frame);};
  this.source.onopen=()=>this.dispatch({type:'feed.state',data:{online:true}});
  this.source.onerror=()=>this.dispatch({type:'feed.state',data:{online:false}});
 }
 dispatch(frame){for(const fn of [...this.listeners]){try{fn(frame);}catch(error){console.warn('Abonné du flux en erreur',error);}}}
 subscribe(fn){this.listeners.add(fn);return ()=>this.listeners.delete(fn);}
}
/** Un écran fille peut charger avant son parent : on attend son flux plutôt
 *  que d'en ouvrir un second, puis on lui transmet les abonnés en attente. */
class ProxyFeed {
 constructor(){this.pending=new Set();this.target=null;this.tries=0;this.attach();}
 attach(){
  const shared=host();
  if(shared){this.target=shared;for(const fn of this.pending)shared.subscribe(fn);this.pending.clear();return;}
  if(this.tries++>40){this.target=own();for(const fn of this.pending)this.target.subscribe(fn);this.pending.clear();return;}
  setTimeout(()=>this.attach(),150);
 }
 subscribe(fn){if(this.target)return this.target.subscribe(fn);this.pending.add(fn);return ()=>this.pending.delete(fn);}
}
function own(){if(!window[KEY])window[KEY]=new Feed();return window[KEY];}
const framed=()=>{try{return (window.parent&&window.parent!==window)||!!window.opener;}catch{return false;}};
/** @returns {{subscribe:(fn:Function)=>Function}} */
export function engineFeed(){
 if(window.__velkoFeedHandle)return window.__velkoFeedHandle;
 return window.__velkoFeedHandle=framed()?new ProxyFeed():own();
}
