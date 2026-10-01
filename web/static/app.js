const $ = id => document.getElementById(id);
const api = async (p, o={}) => (await fetch(p, o)).json();
const val = id => $(id).value.trim();
const port = () => val('port');
const baud = () => val('baud') || '460800';

async function health(){
  try{
    const h = await api('/api/health');
    $('health').textContent = `arduino-cli ${h.arduino_cli?'✔':'✘'} · esptool ${h.esptool?'✔':'✘'} · mklittlefs ${h.mklittlefs?'✔':'✘'}`;
  }catch{ $('health').textContent = 'server offline'; }
}
async function refreshPorts(){
  const r = await api('/api/ports');
  const s = $('port'); s.innerHTML='';
  (r.ports||[]).forEach(p=>{ const o=document.createElement('option'); o.value=p.device; o.textContent=`${p.device} — ${p.description}`; s.appendChild(o); });
  if(!r.ports?.length){ const o=document.createElement('option'); o.textContent='no ports found'; s.appendChild(o); }
}
async function detect(){
  $('detectOut').textContent='detecting…';
  const r = await api('/api/detect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({port:port()})});
  $('detectOut').textContent = `chip: ${r.chip} · fqbn: ${r.fqbn}`;
  if(r.fqbn) $('fqbn').value = r.fqbn;
}
function fd(file, extra={}){
  const f = new FormData(); f.append('file', file);
  Object.entries(extra).forEach(([k,v])=>f.append(k,v));
  return f;
}
async function doFlash(){
  const f = $('flashFile').files[0]; if(!f) return alert('pick .ino/.bin');
  const body = new FormData();
  body.append('file', f);
  body.append('port', port()); body.append('baud', baud());
  body.append('fqbn', val('fqbn')); body.append('addr', val('flashAddr'));
  const p = $('flashParts').files[0]; if(p) body.append('partitions', p);
  $('flashLog').textContent='flashing… (may take minutes)';
  const r = await (await fetch('/api/flash-usb',{method:'POST',body})).json();
  $('flashLog').textContent = (r.logs||r.error||JSON.stringify(r));
}
async function doCompile(){
  const f = $('compFile').files[0]; if(!f) return alert('pick .ino');
  const body = new FormData();
  body.append('sketch', f); body.append('fqbn', val('fqbn'));
  const p = $('compParts').files[0]; if(p) body.append('partitions', p);
  $('compLog').textContent='compiling…';
  const r = await (await fetch('/api/compile',{method:'POST',body})).json();
  $('compLog').textContent = r.logs||r.error;
  if(r.bin_url){ const a=$('binDl'); a.style.display='inline'; a.href=r.bin_url; a.textContent='⬇ Download .bin'; }
}
async function doErase(mode){
  $('eraseLog').textContent='erasing…';
  const r = await api('/api/erase',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({port:port(),baud:baud(),mode,fqbn:val('fqbn')})});
  $('eraseLog').textContent = r.logs;
}
async function loadPinout(){
  const r = await api('/api/pinout?chip='+encodeURIComponent(val('pinChip')));
  $('pinOut').innerHTML = `<b>${r.chip}</b><br>${r.summary}<br><pre>${r.ascii}</pre>`;
}
async function doBackup(){
  $('bakLog').textContent='reading flash…';
  const r = await api('/api/backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({port:port(),baud:baud()})});
  $('bakLog').textContent = r.logs||r.error;
  if(r.download_url){ const a=$('bakDl'); a.style.display='inline'; a.href=r.download_url; }
}
let monWs=null;
function monConnect(){
  monDisconnect();
  const proto = location.protocol==='https:'?'wss':'ws';
  monWs = new WebSocket(`${proto}://${location.host}/ws/monitor?port=${encodeURIComponent(port())}&baud=${$('monBaud').value||115200}`);
  monWs.onmessage = e => { $('monOut').textContent += e.data; $('monOut').scrollTop=1e9; };
  monWs.onclose = () => { $('monOut').textContent += '\n[disconnected]\n'; };
}
function monDisconnect(){ if(monWs){ try{monWs.close()}catch{} monWs=null; } }
function monSend(){ const t=$('monIn').value; if(monWs&&t){ monWs.send(t+'\n'); $('monIn').value=''; } }
$('monIn')?.addEventListener('keydown',e=>{ if(e.key==='Enter') monSend(); });
async function packWeb(dl){
  const fs = $('webFiles').files; if(!fs.length) return alert('pick html/css/js');
  const body = new FormData(); for(const f of fs) body.append('files', f);
  if(dl){ const r = await fetch('/api/pack-web/download',{method:'POST',body}); const b = await r.blob();
    const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='web_assets.h'; a.click(); return; }
  const r = await (await fetch('/api/pack-web',{method:'POST',body})).json();
  $('webOut').textContent = (r.files||[]).join('\n')+'\n\n'+(r.header||'').slice(0,4000);
}
async function boardInfo(){
  $('infoOut').textContent='diagnosing…';
  const r = await api(`/api/board-info?port=${encodeURIComponent(port())}&baud=115200`);
  $('infoOut').textContent = JSON.stringify(r,null,2);
}
async function coreList(){ $('coreLog').textContent='…'; $('coreLog').textContent=(await api('/api/core/list')).logs; }
async function coreUpgrade(){ $('coreLog').textContent='upgrading…'; $('coreLog').textContent=(await api('/api/core/upgrade',{method:'POST'})).logs; }
async function coreInstall(){ $('coreLog').textContent='installing…';
  $('coreLog').textContent=(await api('/api/core/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:val('coreVer')})})).logs||'done'; }
async function detectFlash(){
  const r = await api('/api/flash-size?port='+encodeURIComponent(port()));
  $('flashLbl').textContent = `${r.flash_label} (${r.flash_mb} MB)`;
  $('pFlash').value = String(r.flash_mb);
}
async function partCalc(){
  const r = await api('/api/partitions/calc',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({flash_mb:+val('pFlash'),ota:$('pOta').checked,fs_mb:+val('pFs')})});
  $('partOut').textContent = JSON.stringify(r,null,2);
}
async function partGen(){
  const r = await api('/api/partitions/generate',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({flash_mb:+val('pFlash'),ota:$('pOta').checked,fs_mb:+val('pFs')})});
  $('partCsv').textContent = r.csv||r.error;
}
async function otaScan(){
  $('otaDevs').textContent='scanning…';
  const r = await api('/api/ota/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({timeout:3})});
  $('otaDevs').innerHTML = (r.devices||[]).map(d=>`<button onclick="document.getElementById('otaIp').value='${d.ip}'">${d.name} (${d.ip})</button>`).join(' ')||'no devices';
}
async function otaFlash(){
  const f=$('otaFile').files[0]; if(!f||!val('otaIp')) return alert('need .bin + IP');
  const body=new FormData(); body.append('file',f); body.append('ip',val('otaIp')); body.append('password',val('otaPass'));
  $('otaLog').textContent='pushing OTA…';
  $('otaLog').textContent=((await (await fetch('/api/ota/flash',{method:'POST',body})).json()).logs);
}
health(); refreshPorts(); loadPinout();
