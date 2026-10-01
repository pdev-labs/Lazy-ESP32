const $ = id => document.getElementById(id);
const api = async (p, o={}) => (await fetch(p, o)).json();
const val = id => $(id).value.trim();
const port = () => val('port');
const baud = () => val('baud') || '460800';

// ---- persisted settings (like CLI remembering your board) ----
function saveSettings(){
  localStorage.setItem('lazy_esp', JSON.stringify({port:port(),baud:val('baud'),fqbn:val('fqbn'),monBaud:val('monBaud')}));
}
function loadSettings(){
  try{
    const s = JSON.parse(localStorage.getItem('lazy_esp')||'{}');
    if(s.baud) $('baud').value = s.baud;
    if(s.fqbn) $('fqbn').value = s.fqbn;
    if(s.monBaud) $('monBaud').value = s.monBaud;
    if(s.port) setTimeout(()=>{ $('port').value = s.port; }, 600);
  }catch{}
}
['port','baud','fqbn','monBaud'].forEach(id=>$(id)?.addEventListener('change', saveSettings));

// ---- generic long-task runner: POST -> job_id -> live poll ----
async function runJob(url, body, logEl, btn, onDone){
  const isForm = body instanceof FormData;
  logEl.textContent = 'starting…\n';
  if(btn){ btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = '⏳ Working…'; }
  let r;
  try{
    r = await (await fetch(url,{method:'POST',body})).json();
  }catch(e){ logEl.textContent = 'network error: '+e; if(btn){btn.disabled=false;btn.textContent=btn.dataset.label;} return; }
  if(r.error){ logEl.textContent = 'error: '+r.error; if(btn){btn.disabled=false;btn.textContent=btn.dataset.label;} return; }
  if(!r.job_id){ logEl.textContent = r.logs || r.error || JSON.stringify(r); if(btn){btn.disabled=false;btn.textContent=btn.dataset.label;} onDone?.(r); return; }
  const jid = r.job_id;
  let shown = 0;
  while(true){
    await new Promise(res=>setTimeout(res, 800));
    let j;
    try{ j = await api('/api/jobs/'+jid); }catch{ continue; }
    if(j.logs && j.logs.length > shown){ logEl.textContent = j.logs; logEl.scrollTop = 1e9; shown = j.logs.length; }
    if(j.done){
      logEl.textContent = j.logs + `\n[${j.status==='done'?'SUCCESS':'FAILED'}]`;
      logEl.scrollTop = 1e9;
      if(btn){ btn.disabled = false; btn.textContent = btn.dataset.label; }
      onDone?.(j);
      return;
    }
  }
}

async function health(){
  try{
    const h = await api('/api/health');
    $('health').textContent = `arduino-cli ${h.arduino_cli?'✔':'✘'} · esptool ${h.esptool?'✔':'✘'} · mklittlefs ${h.mklittlefs?'✔':'✘'}`;
    if(!h.arduino_cli) $('health').textContent += ' — run install.sh for full features';
  }catch{ $('health').textContent = 'server offline'; }
}
async function refreshPorts(){
  const r = await api('/api/ports');
  const s = $('port'); const keep = s.value; s.innerHTML='';
  (r.ports||[]).forEach(p=>{ const o=document.createElement('option'); o.value=p.device; o.textContent=`${p.device} — ${p.description}`; s.appendChild(o); });
  if(!r.ports?.length){ const o=document.createElement('option'); o.textContent='no ports found'; s.appendChild(o); }
  if(keep) s.value = keep;
}
async function detect(){
  $('detectOut').textContent='detecting…';
  const r = await api('/api/detect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({port:port()})});
  $('detectOut').textContent = `chip: ${r.chip} · fqbn: ${r.fqbn}`;
  if(r.fqbn){ $('fqbn').value = r.fqbn; saveSettings(); }
}

// ---- flash / compile ----
async function scanLibs(kind, outId){
  const f = $(kind==='flash'?'flashFile':'compFile').files[0];
  if(!f || !f.name.endsWith('.ino')) return alert('pick a .ino sketch first');
  const body = new FormData(); body.append('sketch', f);
  const r = await (await fetch('/api/libs/scan',{method:'POST',body})).json();
  $(outId).textContent = r.libs?.length ? `📦 will auto-install: ${r.libs.join(', ')}` : '📦 no third-party libs — core headers only';
}
async function doFlash(btn){
  const f = $('flashFile').files[0]; if(!f) return alert('pick .ino/.bin');
  const body = new FormData();
  body.append('file', f);
  body.append('port', port()); body.append('baud', baud());
  body.append('fqbn', val('fqbn')); body.append('addr', val('flashAddr'));
  const p = $('flashParts').files[0]; if(p) body.append('partitions', p);
  for(const d of $('flashData').files) body.append('data', d);
  runJob('/api/flash-usb', body, $('flashLog'), btn);
}
async function doCompile(btn){
  const f = $('compFile').files[0]; if(!f) return alert('pick .ino');
  const body = new FormData();
  body.append('sketch', f); body.append('fqbn', val('fqbn'));
  const p = $('compParts').files[0]; if(p) body.append('partitions', p);
  runJob('/api/compile', body, $('compLog'), btn, j=>{
    if(j.bin_url){ const a=$('binDl'); a.style.display='inline'; a.href=j.bin_url; a.textContent=`⬇ Download ${j.bin_name||'.bin'}`; }
  });
}

// ---- recovery / backup ----
async function doErase(mode, btn){
  if(!confirm(`${mode==='factory'?'FACTORY RESET (erase + clean bootloader)':'Normal erase (wipe everything)'} on ${port()||'auto'}?`)) return;
  runJob('/api/erase', JSON.stringify({port:port(),baud:baud(),mode,fqbn:val('fqbn')}), $('eraseLog'), btn);
}
async function doBackup(btn){
  runJob('/api/backup', JSON.stringify({port:port(),baud:baud()}), $('bakLog'), btn, j=>{
    if(j.download_url){ const a=$('bakDl'); a.style.display='inline'; a.href=j.download_url; a.textContent='⬇ Download backup'; }
  });
}

// ---- pinout / board info ----
async function loadPinout(){
  const r = await api('/api/pinout?chip='+encodeURIComponent(val('pinChip')));
  $('pinOut').innerHTML = `<b>${r.chip}</b> — ${r.summary}<br><pre>${r.ascii}</pre>`;
}
async function boardInfo(){
  $('infoOut').textContent='diagnosing (reads flash, ~15s)…';
  const r = await api(`/api/board-info?port=${encodeURIComponent(port())}&baud=115200`);
  if(!r.ok){ $('infoOut').textContent = r.error||'failed'; return; }
  const badge = r.factory_state==='blank' ? '🆕 BLANK' : r.factory_state==='used' ? '♻️ USED' : '❓ UNKNOWN';
  const fw = r.firmware ? `\nFirmware: ${r.firmware.project||'?'} · compiled ${r.firmware.compiled_date||'?'} ${r.firmware.compiled_time||''} · ${r.firmware.idf_version||''}` : '\nFirmware: no app descriptor found at 0x10000 (blank or custom offset)';
  $('infoOut').textContent = `Chip: ${r.chip}\nFeatures: ${r.features}\nMAC: ${r.mac}\nFlash: ${r.flash}\nFactory: ${badge} — ${r.factory_note||''}${fw}`;
}

// ---- serial monitor with crash highlight (CLI parity) ----
let monWs=null;
function monAppend(text){
  const out = $('monOut');
  const esc = text.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  const html = esc.split('\n').map(l=>l.includes('Backtrace:')||/0x40[0-9a-fA-F]{4,}/.test(l)?`<span class="crash">${l}</span>`:l).join('\n');
  out.innerHTML += html;
  if($('monAuto').checked) out.scrollTop = 1e9;
}
function monConnect(){
  monDisconnect();
  $('monOut').innerHTML='';
  const proto = location.protocol==='https:'?'wss':'ws';
  monWs = new WebSocket(`${proto}://${location.host}/ws/monitor?port=${encodeURIComponent(port())}&baud=${$('monBaud').value||115200}`);
  monWs.onmessage = e => monAppend(e.data);
  monWs.onclose = () => monAppend('\n[disconnected]\n');
  saveSettings();
}
function monDisconnect(){ if(monWs){ try{monWs.close()}catch{} monWs=null; } }
function monSend(){ const t=$('monIn').value; if(monWs&&t){ monWs.send(t+'\n'); $('monIn').value=''; } }
function monClear(){ $('monOut').innerHTML=''; }
$('monIn')?.addEventListener('keydown',e=>{ if(e.key==='Enter') monSend(); });

// ---- pack-web ----
async function packWeb(dl){
  const fs = $('webFiles').files; if(!fs.length) return alert('pick html/css/js');
  const body = new FormData(); for(const f of fs) body.append('files', f);
  if(dl){ const r = await fetch('/api/pack-web/download',{method:'POST',body}); const b = await r.blob();
    const a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download='web_assets.h'; a.click(); return; }
  const r = await (await fetch('/api/pack-web',{method:'POST',body})).json();
  $('webOut').textContent = (r.files||[]).join('\n')+'\n\n'+(r.header||'').slice(0,4000);
}

// ---- core manager ----
async function coreList(){ $('coreLog').textContent='…'; $('coreLog').textContent=(await api('/api/core/list')).logs; }
async function coreSearch(){ $('coreLog').textContent='searching…'; $('coreLog').textContent=(await api('/api/core/search?q='+encodeURIComponent(val('coreVer')||'esp32'))).logs; }
async function coreUpgrade(btn){ runJob('/api/core/upgrade', JSON.stringify({}), $('coreLog'), btn); }
async function coreInstall(btn){
  if(!val('coreVer')) return alert('enter version e.g. 2.0.11');
  runJob('/api/core/install', JSON.stringify({version:val('coreVer')}), $('coreLog'), btn);
}

// ---- partitions + littlefs ----
async function detectFlash(){
  const r = await api('/api/flash-size?port='+encodeURIComponent(port()));
  $('flashLbl').textContent = `${r.flash_label} (${r.flash_mb} MB)`;
  $('pFlash').value = String(r.flash_mb);
}
async function partCalc(){
  const r = await api('/api/partitions/calc',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({flash_mb:+val('pFlash'),ota:$('pOta').checked,fs_mb:+val('pFs')})});
  $('partOut').textContent = r.ok ? `App: ${(r.app_kb/1024).toFixed(2)} MB${r.ota?' ×2 (OTA)':''} · FS: ${(r.fs_kb/1024).toFixed(2)} MB` : r.error;
}
async function partGen(){
  const r = await api('/api/partitions/generate',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({flash_mb:+val('pFlash'),ota:$('pOta').checked,fs_mb:+val('pFs')})});
  $('partCsv').textContent = r.csv||r.error;
  if(r.csv){ $('fsCsv').value = r.csv; }
}
function partDownload(){
  const csv = $('partCsv').textContent; if(!csv) return alert('generate CSV first');
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'})); a.download='partitions.csv'; a.click();
}
async function suggestFs(){
  const fs = $('fsSuggestFiles').files; if(!fs.length) return alert('pick your web/data files first');
  const body = new FormData(); for(const f of fs) body.append('files', f);
  const r = await (await fetch('/api/partitions/suggest',{method:'POST',body})).json();
  $('fsSuggestOut').textContent = `${r.total_kb} KB in ${r.files.length} file(s) → suggested FS: ${r.suggested_mb} MB`;
  $('pFs').value = r.suggested_mb;
}
async function fsBuild(){
  const fs = $('fsFiles').files; if(!fs.length) return alert('pick data files');
  const csv = $('fsCsv').value.trim(); const kb = $('fsKb').value.trim();
  if(!csv && !kb) return alert('paste partitions.csv or enter FS size in KB');
  const body = new FormData(); for(const f of fs) body.append('files', f);
  if(csv) body.append('partitions_csv', csv); if(kb) body.append('fs_kb', kb);
  $('fsLog').textContent='packing…';
  const r = await (await fetch('/api/littlefs/build',{method:'POST',body})).json();
  $('fsLog').textContent = r.ok ? `${r.logs}\nOffset ${r.offset} · ${r.size/1024}K` : (r.error||r.logs);
  if(r.download_url){ const a=$('fsDl'); a.style.display='inline'; a.href=r.download_url; }
}
async function fsFlash(btn){
  const fs = $('fsFiles').files; if(!fs.length) return alert('pick data files');
  const csv = $('fsCsv').value.trim(); if(!csv) return alert('paste partitions.csv first (Generate it above)');
  const body = new FormData(); for(const f of fs) body.append('files', f);
  body.append('partitions_csv', csv); body.append('port', port()); body.append('baud', baud());
  runJob('/api/littlefs/flash', body, $('fsLog'), btn);
}

// ---- OTA ----
async function otaScan(){
  $('otaDevs').textContent='scanning…';
  const r = await api('/api/ota/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({timeout:3})});
  $('otaDevs').innerHTML = (r.devices||[]).map(d=>`<button onclick="document.getElementById('otaIp').value='${d.ip}'">${d.name} (${d.ip})</button>`).join(' ')||'no devices';
}
async function otaFlash(btn){
  const f=$('otaFile').files[0]; if(!f||!val('otaIp')) return alert('need .bin + IP');
  const body=new FormData(); body.append('file',f); body.append('ip',val('otaIp')); body.append('password',val('otaPass'));
  runJob('/api/ota/flash', body, $('otaLog'), btn);
}
async function otaCompileFlash(btn){
  const f=$('otaIno').files[0]; if(!f||!val('otaIp')) return alert('need .ino + IP');
  const body=new FormData(); body.append('sketch',f); body.append('fqbn',val('fqbn'));
  body.append('ip',val('otaIp')); body.append('password',val('otaPass'));
  runJob('/api/ota/compile-flash', body, $('otaLog'), btn);
}

loadSettings(); health(); refreshPorts(); loadPinout();
