"""Self-contained dashboard; no external scripts, fonts, or network resources."""
HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>axispyPTZ · Camera desk</title>
<style>
:root{color-scheme:dark;--bg:#0b1019;--card:#141d2b;--line:#29364a;--text:#edf3fa;--muted:#96a8bf;--accent:#5de3c4}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}main{max-width:1920px;margin:auto;padding:40px 28px}header{display:flex;align-items:center;justify-content:space-between;gap:18px}h1{font-size:25px;letter-spacing:-1px;margin:0}h1 span{color:var(--accent)}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin:8px 0 0}button{background:#1c293b;color:var(--text);border:1px solid #3c506b;border-radius:9px;padding:10px 18px;cursor:pointer;font:inherit}button:hover{border-color:var(--accent)}button[aria-expanded=true],button[aria-pressed=true]{color:var(--accent);border-color:var(--accent)}.actions{display:flex;gap:10px;flex-wrap:wrap}.preview{margin-top:18px}.preview img{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#070c13;border-radius:8px}.preview-placeholder{display:flex;align-items:center;justify-content:center;width:100%;aspect-ratio:16/9;background:#070c13;border:1px solid var(--line);border-radius:8px;color:var(--muted);font-size:18px}.preview .note{margin-top:8px;min-height:0}.resources{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:24px}.resource{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 22px}.resource h2{font-size:13px;font-weight:400;color:var(--muted);margin:0 0 10px}.resource strong{display:block;font-size:24px;margin-bottom:8px}.resource meter{display:block;width:100%;height:12px;accent-color:var(--accent)}.resource .status{margin-top:8px;color:var(--muted)}.summary{margin:32px 0 24px;padding:22px;border:1px solid var(--line);border-radius:12px;display:flex;justify-content:space-between;align-items:center;gap:16px}.summary strong{font-size:24px;display:block;margin-top:5px}.muted{color:var(--muted)}.status{font-size:13px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px;background:#8799b0}.online .dot{background:var(--accent)}.offline .dot{background:#ff868b}.grid{display:grid;grid-template-columns:repeat(var(--camera-columns,4),minmax(0,1fr));gap:16px}.camera{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;min-width:0}.camera.selected{border-color:var(--accent);box-shadow:0 0 0 1px #5de3c41c}.top{display:flex;justify-content:space-between;gap:8px;align-items:center}.key{background:#233246;border:1px solid #3d5069;padding:4px 8px;border-radius:6px;font:bold 12px monospace}.tag{color:var(--accent);font-size:11px;text-transform:uppercase;letter-spacing:1px}.activity{display:flex;align-items:center;gap:9px;margin-top:18px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px;color:var(--muted)}.activity .dot{margin:0;flex-shrink:0}.activity.active{color:var(--accent);border-color:#5de3c466;background:#5de3c40a}.activity.active .dot{background:var(--accent);box-shadow:0 0 8px #5de3c466}.activity.unknown .dot{background:#ffc77d}.camera h2{font-size:19px;margin:22px 0 9px;overflow-wrap:anywhere}.address{font:12px monospace;color:var(--muted);overflow-wrap:anywhere;min-height:34px;margin:14px 0}.detail{border-top:1px solid var(--line);padding-top:16px;margin-top:16px}.mode{font-size:19px;margin:6px 0}.note{font-size:12px;line-height:1.6;color:var(--muted);min-height:38px}.health-note{font-size:12px;line-height:1.6;color:var(--muted);margin-top:8px}.health-note:empty{display:none}.error{color:#ffb4b8;font-size:12px;overflow-wrap:anywhere;min-height:18px;margin-top:12px}.footer{display:flex;justify-content:space-between;gap:20px;font-size:12px;color:var(--muted);margin-top:22px}.debug{margin-top:28px;border:1px solid var(--line);border-radius:12px;overflow:hidden}.debug header{padding:16px 20px;background:var(--card)}.debug h2{font-size:15px;margin:0}pre{margin:0;padding:18px;background:#070c13;color:#c7e2d9;height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.7 ui-monospace,monospace}.alert{color:#ffb4b8;min-height:20px;margin-top:12px}[hidden]{display:none!important}@media(max-width:599px){main{padding:24px 16px}.grid{grid-template-columns:1fr}.resources{grid-template-columns:1fr}.summary{align-items:flex-start;flex-direction:column}.footer{flex-direction:column;gap:6px}}
/* Shared camera layout boundaries: phone <600px, tablet 600–1280px,
   wide desktop >1280px. Single/two-camera preview exceptions are below. */
.grid[data-camera-count="1"]{--camera-columns:1}
.grid[data-camera-count="2"]{--camera-columns:2}
.grid[data-camera-count="3"]{--camera-columns:3}
.grid[data-camera-count="4"]{--camera-columns:4}
.camera-info{min-width:0}
@media(max-width:599px){
 .grid{grid-template-columns:minmax(0,1fr)}
}
@media(min-width:600px) and (max-width:1280px){
 .grid{grid-template-columns:repeat(2,minmax(0,1fr))}
 .grid[data-camera-count="1"]{grid-template-columns:minmax(0,1fr)}
 .grid.video-enabled .camera{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start;padding:12px}
 .grid.video-enabled .preview{margin-top:0;min-width:0}
 .grid.video-enabled .top{flex-wrap:wrap;gap:6px}
 .grid.video-enabled:not([data-camera-count="1"]) h2,
 .grid.video-enabled:not([data-camera-count="1"]) .mode{font-size:16px}
 .grid.video-enabled .preview-placeholder{font-size:13px;text-align:center;overflow-wrap:anywhere}
 .grid.video-enabled[data-camera-count="1"] .camera{grid-template-columns:minmax(180px,0.8fr) minmax(0,1.2fr);gap:16px;padding:20px}
}
/* A single wide camera keeps its preview beside its details. */
@media(min-width:1281px){
 .grid.video-enabled[data-camera-count="1"] .camera{display:grid;grid-template-columns:minmax(220px,300px) minmax(0,1fr);gap:24px;align-items:start}
 .grid.video-enabled[data-camera-count="1"] .preview{margin-top:0}
 .grid.video-enabled[data-camera-count="1"] .preview-frame{width:100%;max-width:calc(clamp(200px,calc(100dvh - 440px),560px) * 16 / 9);margin-inline:auto}
}
/* Two cameras stay beside their details until the full desktop layout,
   avoiding an intermediate full-width preview jump above the tablet range. */
@media(min-width:1281px) and (max-width:1439px){
 .grid.video-enabled[data-camera-count="2"] .camera{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;align-items:start;padding:12px}
 .grid.video-enabled[data-camera-count="2"] .preview{margin-top:0;min-width:0}
 .grid.video-enabled[data-camera-count="2"] .top{flex-wrap:wrap;gap:6px}
 .grid.video-enabled[data-camera-count="2"] h2,
 .grid.video-enabled[data-camera-count="2"] .mode{font-size:16px}
 .grid.video-enabled[data-camera-count="2"] .preview-placeholder{font-size:13px;text-align:center;overflow-wrap:anywhere}
}
/* Header metrics reserve their space, so toggling them never moves the desk. */
.dashboard-header{min-height:80px}
.brand{flex-shrink:0}
.usage-slot{flex:1;max-width:460px;min-width:260px;height:80px;margin:0 auto;display:flex;align-items:center}
.resources{width:100%;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin:0}
.resource{background:none;border:0;border-radius:0;padding:0;min-width:0}
.resource h2{font-size:12px;margin:0 0 4px}
.resource strong{font-size:18px;margin:0 0 4px}
.resource meter{height:6px}
.resource .status{font-size:11px;line-height:1.3;margin-top:4px}
.dashboard-header .actions{flex-shrink:0}
@media(max-width:1100px){
 .dashboard-header{flex-wrap:wrap}
 .dashboard-header .usage-slot{order:3;max-width:none;flex-basis:100%;min-width:0}
 .dashboard-header .actions{flex-shrink:1}
}
/* A desktop desk fills the viewport; debugging remains below it and scrollable. */
@media(min-width:1440px) and (min-height:720px){
 main{padding:24px 28px}
 .dashboard{height:calc(100dvh - 48px);display:flex;flex-direction:column}
 .dashboard-header{height:80px;flex:none}
 .summary{margin:16px 0;padding:16px 20px;flex:none}
 .summary strong{font-size:22px}
 #cameras{flex:1;min-height:0;align-items:stretch}
 .camera{display:flex;flex-direction:column;min-height:0;padding:18px}
 .camera-info{flex:none;min-width:0}
 .camera h2{margin:14px 0 8px}
 .address{min-height:0;margin:10px 0}
 .activity{margin-top:12px;padding:8px 10px}
 .detail{padding-top:12px;margin-top:12px}
 .note{min-height:0}
 .error:empty{display:none}
 .camera-info>.note{margin-top:10px}
 .grid.video-enabled .camera .preview{flex:1;min-height:0;display:grid;grid-template-rows:minmax(0,1fr) auto;gap:8px;margin-top:14px}
 .grid.video-enabled .camera .preview-frame{grid-row:1;min-height:0;width:100%;height:100%;container-type:size}
 .grid.video-enabled .camera .preview img,
 .grid.video-enabled .camera .preview-placeholder{grid-row:1;width:100%;height:100%;min-height:0;max-height:none;aspect-ratio:auto;object-fit:contain}
 .grid.video-enabled .camera .preview .note{grid-row:2;margin:0}
 .grid.video-enabled[data-camera-count="1"] .camera{align-items:stretch;grid-template-rows:minmax(0,1fr)}
 .grid.video-enabled[data-camera-count="1"] .preview{margin-top:0}
 .grid.video-enabled[data-camera-count="1"] .preview-frame{max-width:none}
 #alert:empty{display:none}
 .footer{margin-top:14px;flex:none}
}
.preview-frame{display:grid;place-items:center}
.preview-frame img,.preview-frame .preview-placeholder{grid-area:1/1}
@supports(width:1cqw){
 @media(min-width:1440px) and (min-height:720px){
  .grid.video-enabled .camera .preview img,
  .grid.video-enabled .camera .preview-placeholder{width:min(100cqw,calc(100cqh * 16 / 9));height:auto;aspect-ratio:16/9;max-height:100%}
 }
}
</style></head><body><main><div class="dashboard"><header class="dashboard-header"><div class="brand"><h1>axis<span>py</span>PTZ <small class="muted" style="font-size:13px;letter-spacing:0">v0.2.8</small></h1><p class="eyebrow">Camera control desk</p></div><div class="usage-slot"><section id="resources" class="resources" aria-label="Host resource usage" hidden>
<div class="resource"><h2>Host CPU usage</h2><strong id="cpuUsage">Measuring…</strong><meter id="cpuMeter" min="0" max="100" value="0" aria-label="Host CPU usage" hidden></meter><div class="status">Across all CPU cores</div></div>
<div class="resource"><h2>Host memory usage</h2><strong id="memoryUsage">Measuring…</strong><meter id="memoryMeter" min="0" max="100" value="0" aria-label="Host memory usage" hidden></meter><div class="status" id="memoryDetail">Used / total RAM</div></div>
</section></div><div class="actions"><button id="usageButton" aria-expanded="false" aria-controls="resources">Show system usage</button><button id="videoButton" aria-pressed="false" aria-controls="cameras">Show live video</button><button id="debugButton" aria-expanded="false" aria-controls="debug">Debug logs</button></div></header>
<div class="summary"><div><span class="muted">Currently selected</span><strong id="selected">Waiting for controller…</strong></div><div><div id="joystick">Checking USB joystick…</div><div class="muted status" id="armed" style="margin-top:8px"></div></div></div>

<div id="cameras" class="grid"></div><div id="alert" role="status" class="alert"></div><div class="footer"><span>Read-only monitor · Use the joystick to control cameras</span><span id="updated">Connecting…</span></div>
</div>
<section class="debug" id="debug" hidden><header><h2>Live console log</h2><span class="muted status">Latest 1,000 messages</span></header><pre id="logs" aria-label="Live application logs"></pre></section>
</main><script>
const $=id=>document.getElementById(id);let debug=false,cursor=0,lines=[],pollIntervalMs=1000,videoEnabled=false,previewFps=25,previewResolution='640x360';
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function card(c){const box=node('article','camera'+(c.selected?' selected':''));const top=node('div','top');top.append(node('span','key','J'+c.slot),node('span','tag',c.selected?'Controlling':''));box.append(top,node('h2','',c.name));const availability=c.online===true?'online':c.online===false?'offline':'unknown';const state=node('div','status '+availability);state.append(node('span','dot'),document.createTextNode(c.online===true?'Online':c.online===false?'Offline':'Checking…'));box.append(state,node('div','address',c.url+' · channel '+c.channel));const activity=node('div','activity '+(c.ptz_activity||'unknown'));activity.setAttribute('role','status');activity.setAttribute('aria-label','PTZ command activity');activity.title='Controller command status; not measured camera movement';activity.append(node('span','dot'),document.createTextNode(c.ptz_activity==='active'?'PTZ · Active':c.ptz_activity==='idle'?'PTZ · Idle':'PTZ · Unconfirmed'));box.append(activity);const detail=node('div','detail');detail.append(node('span','muted status','Focus mode'),node('div','mode',c.focus==='auto'?'Autofocus':c.focus==='manual'?'Manual focus':'Unknown'));let note=c.focus_source==='camera'?'Reported by camera':c.focus_source==='command'?'Last successful controller command':'Waiting for focus status';if((c.online===false||c.error)&&c.focus!=='unknown')note+=' · last known';detail.append(node('div','note',note),node('div','error',[c.error,c.control_error].filter(Boolean).join(' · ')));box.append(detail,node('div','health-note',c.status_note||''),node('div','note',c.last_seen?'Last response '+new Date(c.last_seen).toLocaleTimeString():c.checked_at?'Checked '+new Date(c.checked_at).toLocaleTimeString():'Awaiting first check'));return box}
function stopVideo(box){const img=box.querySelector('.preview img');if(img){img.onload=img.onerror=null;img.removeAttribute('src');img.remove()}}
function syncVideo(box){
 const preview=box.querySelector('.preview'),placeholder=preview.querySelector('.preview-placeholder'),note=preview.querySelector('.note');
 const active=videoEnabled&&!document.hidden;preview.hidden=!active;
 if(!active){stopVideo(box);delete preview.dataset.failed;return}
 if(box.dataset.online==='false'){stopVideo(box);placeholder.hidden=false;placeholder.textContent='Camera Offline';note.textContent='Waiting for camera connection…';return}
 if(preview.querySelector('img')||preview.dataset.failed)return;
 const img=node('img');img.alt='Live preview · '+box.dataset.name;img.hidden=true;
 placeholder.hidden=false;placeholder.textContent='Connecting…';note.textContent='Connecting to live video…';
 img.onload=()=>{img.hidden=false;placeholder.hidden=true;note.textContent='Live preview · '+previewResolution.replace('x','×')+' · '+previewFps+' fps requested'};
 img.onerror=()=>{preview.dataset.failed='true';stopVideo(box);placeholder.hidden=false;placeholder.textContent='Preview unavailable';note.textContent='Preview unavailable. Check camera connection, MJPEG support and permissions, then toggle video off and on to retry.'};
 img.src='/api/stream/'+box.dataset.cameraId;preview.querySelector('.preview-frame').prepend(img)
}
function renderCameras(cameras){const grid=$('cameras');grid.dataset.cameraCount=cameras.length;grid.classList.toggle('video-enabled',videoEnabled);const slots=new Set(cameras.map(c=>String(c.slot)));for(const box of [...grid.children])if(!slots.has(box.dataset.slot)){stopVideo(box);box.remove()};for(const c of cameras){let box=[...grid.children].find(b=>b.dataset.slot===String(c.slot));if(!box){box=node('article','camera');box.dataset.slot=c.slot;box.append(node('div','camera-info'));const preview=node('div','preview');preview.hidden=true;const placeholder=node('div','preview-placeholder','Connecting…');placeholder.setAttribute('role','status');const frame=node('div','preview-frame');frame.append(placeholder);preview.append(frame,node('div','note'));box.append(preview);grid.append(box)}if(box.dataset.cameraId!==String(c.id)||(box.dataset.online==='false'&&c.online===true)){stopVideo(box);delete box.querySelector('.preview').dataset.failed}box.dataset.online=String(c.online);box.dataset.cameraId=c.id;box.dataset.name=c.name;box.classList.toggle('selected',c.selected);const details=card(c);box.querySelector('.camera-info').replaceChildren(...details.childNodes);syncVideo(box)}}
function setVideoEnabled(enabled){videoEnabled=enabled;$('cameras').classList.toggle('video-enabled',enabled);$('videoButton').setAttribute('aria-pressed',String(enabled));$('videoButton').textContent=enabled?'Hide live video':'Show live video';for(const box of $('cameras').children)syncVideo(box)}
$('videoButton').addEventListener('click',()=>{setVideoEnabled(!videoEnabled);savePreferences()});
document.addEventListener('visibilitychange',()=>{for(const box of $('cameras').children)syncVideo(box)});
window.addEventListener('pagehide',()=>{for(const box of $('cameras').children)stopVideo(box)});
window.addEventListener('pageshow',()=>{for(const box of $('cameras').children)syncVideo(box)});
function formatBytes(bytes){if(bytes>=1024**3)return (bytes/1024**3).toFixed(2)+' GiB';return (bytes/1024**2).toFixed(1)+' MiB'}
function renderUsage(system){
 const cpu=system?.cpu_percent,memory=system?.memory;
 const cpuKnown=Number.isFinite(cpu),memoryKnown=Number.isFinite(memory?.percent);
 $('cpuUsage').textContent=cpuKnown?cpu.toFixed(1)+'%':system?.cpu_status==='measuring'?'Measuring…':'Unavailable';
 $('cpuMeter').hidden=!cpuKnown;if(cpuKnown)$('cpuMeter').value=cpu;
 $('memoryUsage').textContent=memoryKnown?memory.percent.toFixed(1)+'%':'Unavailable';
 $('memoryMeter').hidden=!memoryKnown;if(memoryKnown)$('memoryMeter').value=memory.percent;
 $('memoryDetail').textContent=memoryKnown?formatBytes(memory.used_bytes)+' / '+formatBytes(memory.total_bytes)+' RAM':'Memory statistics unavailable';
}
async function status(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();const s=await r.json();if(Number.isInteger(s.poll_interval_ms)&&s.poll_interval_ms>=100&&s.poll_interval_ms<=60000)pollIntervalMs=s.poll_interval_ms;if(Number.isInteger(s.preview_fps))previewFps=s.preview_fps;if(typeof s.preview_resolution==='string')previewResolution=s.preview_resolution;$('selected').textContent=s.cameras[s.selected]?.name||'No selection';$('joystick').textContent=s.joystick?'USB · '+s.joystick:'USB joystick disconnected — waiting';$('armed').textContent=s.armed?'Controls ready':'Movement inhibited · center stick and release focus buttons';renderUsage(s.system);renderCameras(s.cameras);$('updated').textContent='Updated '+new Date(s.time).toLocaleTimeString();$('alert').textContent=''}catch{renderUsage(null);$('alert').textContent='Dashboard connection lost. Retrying…'}}
async function logs(){if(!debug)return;try{const r=await fetch('/api/logs?after='+cursor,{cache:'no-store'});if(!r.ok)throw Error();const data=await r.json();if(data.cursor<cursor)lines=[];cursor=data.cursor;lines.push(...data.entries.map(e=>e.time+' '+e.level+' · '+e.message));lines=lines.slice(-1000);const p=$('logs');const follow=p.scrollTop+p.clientHeight>=p.scrollHeight-30;p.textContent=lines.join('\n');if(follow)p.scrollTop=p.scrollHeight}catch{}}
const preferenceKey='axispy.dashboard.preferences.v1';
function readPreferences(){
 try{const saved=JSON.parse(localStorage.getItem(preferenceKey));return {usage:saved?.usage===true,video:saved?.video===true,debug:saved?.debug===true}}
 catch{return {usage:false,video:false,debug:false}}
}
function savePreferences(){
 try{localStorage.setItem(preferenceKey,JSON.stringify({usage:!$('resources').hidden,video:videoEnabled,debug}))}
 catch{} // Storage may be blocked or full; the dashboard controls still work.
}
function setUsageVisible(visible){$('resources').hidden=!visible;$('usageButton').setAttribute('aria-expanded',String(visible));$('usageButton').textContent=visible?'Hide system usage':'Show system usage'}
function setDebugEnabled(enabled){debug=enabled;$('debug').hidden=!enabled;$('debugButton').setAttribute('aria-expanded',String(enabled))}
$('usageButton').addEventListener('click',()=>{setUsageVisible($('resources').hidden);savePreferences()});
$('debugButton').addEventListener('click',()=>{setDebugEnabled(!debug);savePreferences();if(debug)logs()});
const savedPreferences=readPreferences();
setUsageVisible(savedPreferences.usage);setVideoEnabled(savedPreferences.video);setDebugEnabled(savedPreferences.debug);
async function tick(){await status();await logs();setTimeout(tick,pollIntervalMs)}tick();
</script></body></html>'''
