// Native canvas charts keep the dashboard usable without a CDN or extra runtime library.
const chartViews=new Map(),chartScopes=new Map();
const chartStyle=document.createElement('style');chartStyle.textContent=`
.chart-tools{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:14px 0 10px}.chart-tools>div{display:flex;gap:6px;flex-wrap:wrap;align-items:center}.chart-tools button{min-height:36px;padding:6px 10px;font-size:12px}.chart-tools select{width:auto;padding:7px;font-size:12px}.chart-key{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}.chart-key button{display:flex;align-items:center;gap:7px;padding:7px 10px;font-size:12px;min-height:36px;background:#0d172a}.chart-key button[aria-pressed=false]{opacity:.5;text-decoration:line-through}.chart-key i{width:18px;height:3px;display:inline-block;background:var(--series-color)}.chart-key button[data-right=true] i{height:0;border-top:3px dashed var(--series-color);background:none}.chart-key strong{font-weight:500;color:#eaf2ff}.chart-shell{position:relative;isolation:isolate}.chart-shell canvas{height:300px;touch-action:pan-y pinch-zoom;cursor:crosshair;border-radius:8px;background:linear-gradient(180deg,#111c31,#0e182a)}.chart-shell canvas:focus-visible{outline:2px solid var(--mint);outline-offset:3px}.chart-tip{position:absolute;z-index:2;pointer-events:none;background:#081222f5;border:1px solid #426184;border-radius:10px;padding:12px;max-width:min(340px,95%);box-shadow:0 12px 35px #0007;font-size:12px}.chart-tip>b{display:block;margin-bottom:8px}.chart-tip p{display:flex;justify-content:space-between;gap:16px;margin:5px 0;color:#edf3ff;font-size:12px}.chart-tip small{font-size:11px;display:block}.chart-summary{display:block;font-size:12px;margin:12px 0 6px;color:#b8c8df;min-height:18px}.chart-hint{font-size:11px;margin:4px 0;color:var(--muted)}.chart-modal{width:96vw;max-width:1600px;max-height:94vh;padding:16px}.chart-modal>.panel{border:0;margin:0;padding:8px}.chart-modal canvas{height:clamp(280px,58vh,620px)}.chart-modal-close{float:right;margin:0 0 12px 12px}.chart-panel>.legend{display:none}@media(max-width:650px){.chart-shell canvas{height:260px}.chart-key{gap:6px}.chart-key button{flex-wrap:wrap;max-width:100%}.chart-tools>div{gap:4px}.chart-tools button{min-height:40px}.chart-modal{padding:8px}.chart-modal canvas{height:50vh}}
`;document.head.append(chartStyle);

function chartNearest(points,x){let lo=0,hi=points.length;while(lo<hi){const mid=(lo+hi)>>1;if(points[mid].x<x)lo=mid+1;else hi=mid}const a=points[lo-1],b=points[lo];return !a?b:!b?a:Math.abs(a.x-x)<=Math.abs(b.x-x)?a:b}
function chartSample(points,limit){
 if(points.length<=limit)return points;
 const step=Math.ceil(points.length/Math.max(1,Math.floor(limit/4))),out=[];
 for(let i=0;i<points.length;i+=step){const bucket=points.slice(i,i+step);let min=0,max=0;for(let j=1;j<bucket.length;j++){if(bucket[j].y<bucket[min].y)min=j;if(bucket[j].y>bucket[max].y)max=j}for(const j of [...new Set([0,min,max,bucket.length-1])].sort((a,b)=>a-b))out.push(bucket[j])}
 return out;
}
function chartDate(t,short=false){return new Date(t*1000).toLocaleString('pt-BR',{timeZone:state.settings?.timezone||'America/Sao_Paulo',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',...(short?{}:{second:'2-digit'})})}
function chartRepaint(scope){for(const view of chartViews.values())if(view.scope===scope)view.schedule()}

class PanelChart{
 constructor(canvas,scope){
  this.canvas=canvas;this.scope=scope;this.muted=new Set();this.series=[];this.key='';this.drag=null;this.pinned=false;
  this.panel=canvas.closest('.panel');this.panel.classList.add('chart-panel');
  this.tools=document.createElement('div');this.tools.className='chart-tools';
  const title=this.panel.querySelector('h2')?.textContent||'Gráfico';
  this.tools.innerHTML=`<div><label class="hidden" for="${canvas.id}Range">Período visível de ${esc(title)}</label><select id="${canvas.id}Range" aria-label="Período visível de ${esc(title)}"><option value="all">Todo o período</option><option value="custom" disabled>Zoom personalizado</option><option value="3600">Última hora</option><option value="21600">Últimas 6 horas</option><option value="86400">Últimas 24 horas</option><option value="604800">Últimos 7 dias</option></select></div><div>${[['back','←','Mover período para trás'],['in','+','Aproximar gráfico'],['out','−','Afastar gráfico'],['next','→','Mover período para frente'],['reset','Restaurar','Restaurar período completo'],['expand','Ampliar','Ampliar gráfico']].map(([action,label,hint])=>`<button type="button" data-chart-action="${action}" title="${hint}" aria-label="${hint}">${label}</button>`).join('')}</div>`;
  this.legend=document.createElement('div');this.legend.className='chart-key';this.legend.setAttribute('aria-label','Séries do gráfico '+title);
  this.shell=document.createElement('div');this.shell.className='chart-shell';canvas.before(this.tools,this.legend,this.shell);this.shell.append(canvas);
  this.tip=document.createElement('div');this.tip.className='chart-tip hidden';this.tip.setAttribute('aria-hidden','true');this.shell.append(this.tip);
  this.summary=document.createElement('output');this.summary.className='chart-summary';this.summary.id=canvas.id+'Summary';
  this.hint=document.createElement('p');this.hint.className='chart-hint';this.hint.id=canvas.id+'Hint';this.hint.textContent='Arraste para aproximar • toque/clique para fixar • legenda mostra/oculta séries • teclado: ← →, + − e Esc. Recortes relativos à última amostra; zoom altera somente a visualização.';
  this.shell.after(this.summary,this.hint);canvas.tabIndex=0;canvas.setAttribute('aria-describedby',this.summary.id+' '+this.hint.id);canvas.setAttribute('aria-keyshortcuts','ArrowLeft ArrowRight + - Home End Escape');
  this.tools.querySelector('select').onchange=e=>{const n=Number(e.target.value);this.setRange(n?[Math.max(this.full[0],this.full[1]-n),this.full[1]]:null,e.target.value)};
  this.tools.querySelectorAll('button').forEach(b=>b.onclick=()=>{const action=b.dataset.chartAction;if(action==='expand')return this.expand();if(action==='reset')return this.setRange(null);if(action==='in'||action==='out')return this.zoom(action==='in'?.5:2);this.pan(action==='back'?-.5:.5)});
  canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;this.drag={start:e.clientX,top:e.clientY,current:e.clientX,select:false};if(e.pointerType==='mouse')canvas.setPointerCapture(e.pointerId)});
  canvas.addEventListener('pointermove',e=>this.move(e));canvas.addEventListener('pointerup',e=>this.release(e));canvas.addEventListener('pointercancel',()=>{this.drag=null;this.schedule()});
  canvas.addEventListener('pointerleave',()=>{if(!this.drag&&!this.pinned){this.tip.classList.add('hidden');this.scope.hover=null;chartRepaint(this.scope)}});
  canvas.addEventListener('keydown',e=>this.keyboard(e));
  this.observer=new ResizeObserver(()=>this.schedule());this.observer.observe(this.shell);
 }
 update(series,unit,dual){
  this.unit=unit;this.dual=dual;this.series=series.map(s=>({...s,key:s.name+'|'+Boolean(s.right),points:s.points.filter(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)).slice().sort((a,b)=>a.x-b.x)}));
  let lo=Infinity,hi=-Infinity;for(const s of this.series)for(const p of s.points){lo=Math.min(lo,p.x);hi=Math.max(hi,p.x)}
  this.full=Number.isFinite(lo)?(lo===hi?[lo-1800,hi+1800]:[lo,hi]):[0,1];
  this.hasPoints=Number.isFinite(lo);this.tools.querySelector('select').disabled=!this.hasPoints;
  this.tools.querySelectorAll('button').forEach(b=>b.disabled=(b.dataset.chartAction==='expand'&&this.expanded)||(!this.hasPoints&&!['reset','expand'].includes(b.dataset.chartAction)));
  const keys=[...new Map(this.series.map(s=>[s.key,s])).values()],signature=keys.map(s=>s.key+s.color).join(';');
  if(signature!==this.key){
   this.key=signature;this.legend.replaceChildren();
   keys.forEach(s=>{const b=document.createElement('button');b.type='button';b.dataset.series=s.key;b.dataset.right=Boolean(s.right);b.style.setProperty('--series-color',s.color);b.innerHTML=`<i aria-hidden="true"></i><span>${esc(s.name)}</span><strong></strong>`;b.onclick=()=>{this.muted.has(s.key)?this.muted.delete(s.key):this.muted.add(s.key);this.schedule()};this.legend.append(b)});
  }
  this.schedule();
 }
 bounds(){
  if(!this.scope.range)return this.full;
  const [lo,hi]=this.scope.range;return [lo,Math.max(lo+1,hi)];
 }
 setRange(range,preset=range?'custom':'all'){
  this.scope.range=range;this.scope.hover=null;this.pinned=false;this.tip.classList.add('hidden');
  for(const v of chartViews.values())if(v.scope===this.scope){v.tools.querySelector('select').value=preset;v.pinned=false;v.tip.classList.add('hidden')}
  chartRepaint(this.scope);
 }
 zoom(factor){if(!this.hasPoints)return;const [a,b]=this.bounds(),center=(a+b)/2,span=Math.max(1,Math.min(this.full[1]-this.full[0],(b-a)*factor));const start=Math.max(this.full[0],Math.min(this.full[1]-span,center-span/2));this.setRange([start,start+span])}
 pan(fraction){if(!this.hasPoints)return;const [a,b]=this.bounds(),span=b-a,start=Math.max(this.full[0],Math.min(this.full[1]-span,a+span*fraction));this.setRange([start,start+span])}
 schedule(){if(this.frame)return;this.frame=requestAnimationFrame(()=>{this.frame=null;this.render()})}
 xFrom(client){const r=this.canvas.getBoundingClientRect(),[a,b]=this.bounds();return a+Math.max(0,Math.min(1,(client-r.left-this.left)/this.pw))*(b-a)}
 move(e){
  if(!this.pw)return;
  if(this.drag){this.drag.current=e.clientX;const dx=Math.abs(e.clientX-this.drag.start),dy=Math.abs(e.clientY-this.drag.top);if(dx>8&&dx>dy*1.2){this.drag.select=true;if(!this.canvas.hasPointerCapture(e.pointerId))this.canvas.setPointerCapture(e.pointerId)}if(this.drag.select){this.schedule();return}}
  if(this.pinned)return;
  this.showAt(this.xFrom(e.clientX));
 }
 release(e){
  if(!this.drag||!this.pw)return;
  const d=this.drag;this.drag=null;
  if(d.select){const a=this.xFrom(d.start),b=this.xFrom(e.clientX);if(Math.abs(b-a)>=1)this.setRange([Math.min(a,b),Math.max(a,b)]);else this.schedule();return}
  this.pinned=!this.pinned;this.showAt(this.xFrom(e.clientX),true);
 }
 keyboard(e){
  if(e.key==='Escape'&&!this.pinned&&this.canvas.closest('.chart-modal'))return;
  if(['+','=','-','0','Escape','ArrowLeft','ArrowRight','Home','End'].includes(e.key))e.preventDefault();else return;
  if(e.key==='+'||e.key==='=')return this.zoom(.5);if(e.key==='-')return this.zoom(2);if(e.key==='0')return this.setRange(null);
  if(e.key==='Escape'){this.pinned=false;this.tip.classList.add('hidden');this.scope.hover=null;chartRepaint(this.scope);return}
  const [a,b]=this.bounds(),times=[...new Set(this.series.filter(s=>!this.muted.has(s.key)).flatMap(s=>s.points.filter(p=>p.x>=a&&p.x<=b).map(p=>p.x)))].sort((a,b)=>a-b);
  if(!times.length)return;const current=this.scope.hover??times[0];let i=times.findIndex(t=>t>=current);if(i<0)i=times.length-1;
  i=e.key==='Home'?0:e.key==='End'?times.length-1:Math.max(0,Math.min(times.length-1,i+(e.key==='ArrowLeft'?-1:1)));
  this.pinned=true;this.showAt(times[i],true);
 }
 showAt(x,announce=false){
  const [a,b]=this.bounds(),candidates=new Map(),tolerance=(b-a)/Math.max(1,this.pw)*24;
  for(const s of this.series){if(this.muted.has(s.key))continue;const p=chartNearest(s.points,x);if(!p||p.x<a||p.x>b||Math.abs(p.x-x)>tolerance)continue;const old=candidates.get(s.key);if(!old||Math.abs(p.x-x)<Math.abs(old.p.x-x))candidates.set(s.key,{s,p})}
  const items=[...candidates.values()];let selected=x;if(items.length)selected=items.reduce((best,v)=>Math.abs(v.p.x-x)<Math.abs(best.p.x-x)?v:best).p.x;
  this.scope.hover=selected;this.tip.innerHTML=`<b>${esc(chartDate(selected))}${this.pinned?' · fixado':''}</b>`+(items.map(({s,p})=>`<p><span style="color:${s.color}">${esc(s.name)}</span><strong>${fmt(p.y)} ${s.right?'%':esc(this.unit)}</strong></p><small>${esc(chartDate(p.x))}</small>`).join('')||'<small>Sem amostra próxima deste instante.</small>');
  this.tip.classList.remove('hidden');const px=this.left+(selected-a)/(b-a)*this.pw,tipWidth=Math.min(340,this.shell.clientWidth*.95);this.tip.style.left=Math.max(0,Math.min(this.shell.clientWidth-tipWidth,px+16))+'px';this.tip.style.top='16px';
  if(announce){this.summary.setAttribute('aria-live','polite');this.summary.textContent=chartDate(selected)+' · '+(items.map(({s,p})=>s.name+': '+fmt(p.y)+' '+(s.right?'%':this.unit)).join(' · ')||'Sem amostra próxima.')}
  chartRepaint(this.scope);
 }
 render(){
  const w=this.canvas.clientWidth,h=this.canvas.clientHeight;if(w<20||h<20)return;
  const dpr=window.devicePixelRatio||1;this.canvas.width=w*dpr;this.canvas.height=h*dpr;const c=this.canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);
  if(!this.hasPoints){c.font='13px system-ui';c.fillStyle='#b6c7de';c.textAlign='center';c.fillText('Os gráficos aparecerão após as medições.',w/2,h/2);this.summary.textContent='Aguardando medições neste período.';this.canvas.dataset.visiblePoints=0;return}
  this.left=w<400?42:52;const right=this.dual?48:18,top=28,bottom=45;this.pw=Math.max(1,w-this.left-right);const ph=h-top-bottom;
  const [a,b]=this.bounds(),shown=this.series.filter(s=>!this.muted.has(s.key)).map(s=>({...s,visible:s.points.filter(p=>p.x>=a&&p.x<=b)}));
  const points=shown.flatMap(s=>s.visible),maxima=[1,1];for(const s of shown)for(const p of s.visible)maxima[s.right?1:0]=Math.max(maxima[s.right?1:0],p.y*1.12);
  const xp=x=>this.left+(x-a)/(b-a)*this.pw,yp=(s,y)=>top+ph-y/maxima[s.right?1:0]*ph;
  c.font='11px system-ui';c.fillStyle='#b6c7de';c.textAlign='left';c.fillText(this.unit,this.left,15);if(this.dual)c.fillText('%',w-right+12,15);
  for(let i=0;i<=4;i++){const y=top+ph*(1-i/4);c.strokeStyle='#263650';c.lineWidth=1;c.setLineDash([3,5]);c.beginPath();c.moveTo(this.left,y);c.lineTo(w-right,y);c.stroke();c.setLineDash([]);c.textAlign='right';c.fillText(fmt(maxima[0]*i/4),this.left-8,y+4);if(this.dual){c.textAlign='left';c.fillText(fmt(maxima[1]*i/4),w-right+8,y+4)}}
  const ticks=w<460?1:4;for(let i=0;i<=ticks;i++){const x=a+(b-a)*i/ticks;c.textAlign=i===0?'left':i===ticks?'right':'center';c.fillText(chartDate(x,true),xp(x),h-14)}
  c.save();c.beginPath();c.rect(this.left,top,this.pw,ph);c.clip();
  for(const [index,s] of shown.entries()){
   const sampled=chartSample(s.visible,Math.max(80,Math.floor(this.pw*2)));if(!sampled.length)continue;
   c.strokeStyle=s.color;c.fillStyle=s.color;c.lineWidth=2.2;c.lineJoin='round';c.setLineDash(s.right?[5,5]:index%3===1?[9,3]:index%3===2?[2,3]:[]);c.beginPath();sampled.forEach((p,i)=>i?c.lineTo(xp(p.x),yp(s,p.y)):c.moveTo(xp(p.x),yp(s,p.y)));c.stroke();c.setLineDash([]);
   if(!s.right&&shown.length<=3&&sampled.length>1){const gradient=c.createLinearGradient(0,top,0,top+ph);gradient.addColorStop(0,s.color+'22');gradient.addColorStop(1,s.color+'00');c.lineTo(xp(sampled.at(-1).x),top+ph);c.lineTo(xp(sampled[0].x),top+ph);c.closePath();c.fillStyle=gradient;c.fill()}
   c.fillStyle=s.color;if(sampled.length<90)for(const p of sampled){c.beginPath();c.arc(xp(p.x),yp(s,p.y),sampled.length<20?3:2,0,Math.PI*2);c.fill()}
   if(this.scope.hover!=null){const p=chartNearest(s.visible,this.scope.hover);if(p&&Math.abs(p.x-this.scope.hover)<(b-a)/this.pw*24){c.beginPath();c.arc(xp(p.x),yp(s,p.y),5,0,Math.PI*2);c.fill();c.strokeStyle='#edf3ff';c.lineWidth=1.4;c.stroke()}}
  }
  if(this.scope.hover!=null&&this.scope.hover>=a&&this.scope.hover<=b){c.strokeStyle='#bfd5ef';c.lineWidth=1;c.setLineDash([4,4]);c.beginPath();c.moveTo(xp(this.scope.hover),top);c.lineTo(xp(this.scope.hover),top+ph);c.stroke();c.setLineDash([])}
  if(this.drag?.select){const rect=this.canvas.getBoundingClientRect(),x=Math.min(this.drag.start,this.drag.current)-rect.left,width=Math.abs(this.drag.current-this.drag.start);c.fillStyle='#76aaff35';c.fillRect(x,top,width,ph);c.strokeStyle='#76aaff';c.strokeRect(x,top,width,ph)}
  c.restore();
  if(!points.length){c.fillStyle='#b6c7de';c.textAlign='center';c.fillText(this.series.some(s=>s.points.length)?'Sem amostras visíveis. Ajuste o período ou ative uma série.':'Os gráficos aparecerão após as medições.',w/2,top+ph/2)}
  this.legend.querySelectorAll('button').forEach(button=>{button.setAttribute('aria-pressed',String(!this.muted.has(button.dataset.series)));const candidates=shown.filter(s=>s.key===button.dataset.series).flatMap(s=>s.visible),last=candidates.reduce((best,p)=>!best||p.x>best.x?p:best,null);button.querySelector('strong').textContent=last?fmt(last.y)+' '+(button.dataset.right==='true'?'%':this.unit):'—'});
  if(!this.pinned){const values=shown.filter(s=>!s.right).flatMap(s=>s.visible.map(p=>p.y));let sum=0,min=Infinity,max=-Infinity;for(const v of values){sum+=v;min=Math.min(min,v);max=Math.max(max,v)}this.summary.removeAttribute('aria-live');this.summary.textContent=points.length+' ponto(s) · '+chartDate(a,true)+' → '+chartDate(b,true)+(values.length?' · média dos pontos '+fmt(sum/values.length)+' '+this.unit+' · mín '+fmt(min)+' · máx '+fmt(max):'')}
  this.canvas.dataset.rangeStart=a;this.canvas.dataset.rangeEnd=b;this.canvas.dataset.visiblePoints=points.length;
 }
 expand(){
  if(this.expanded)return;this.expanded=true;this.tools.querySelector('[data-chart-action=expand]').disabled=true;
  const modal=document.createElement('dialog');modal.className='chart-modal';modal.setAttribute('aria-label','Gráfico ampliado: '+(this.panel.querySelector('h2')?.textContent||''));
  const close=document.createElement('button');close.type='button';close.className='chart-modal-close';close.textContent='Fechar gráfico';modal.append(close);const placeholder=document.createComment('chart position');this.panel.before(placeholder);modal.append(this.panel);document.body.append(modal);close.onclick=()=>modal.close();
  modal.addEventListener('close',()=>{placeholder.replaceWith(this.panel);modal.remove();this.expanded=false;this.tools.querySelector('[data-chart-action=expand]').disabled=false;this.schedule();this.tools.querySelector('[data-chart-action=expand]').focus()},{once:true});modal.showModal();this.schedule();
 }
}

function draw(canvas,series,unit,dual=false){
 const kind=canvas.id==='uptimeChart'?'uptime':'speed',filter=kind==='speed'?params().toString():$('uptimeDays').value+'|'+$('uptimeMonitor').value;
 if(!chartScopes.has(kind))chartScopes.set(kind,{range:null,hover:null,filter});const scope=chartScopes.get(kind);
 if(scope.filter!==filter){scope.range=null;scope.hover=null;scope.filter=filter;for(const v of chartViews.values())if(v.scope===scope){v.pinned=false;v.tip.classList.add('hidden');v.tools.querySelector('select').value='all'}}
 if(!chartViews.has(canvas.id))chartViews.set(canvas.id,new PanelChart(canvas,scope));chartViews.get(canvas.id).update(series,unit,dual);
}
drawAll();
