/* RoadGuard ZW — Website 2 (validator portal). Every record here comes from the shared backend. */
const CATS = {accident:'Road accident', hazard:'Road hazard', medical:'Medical', fire_rescue:'Fire / rescue', other:'Other'};
const CCOL = {accident:'#c2534a', hazard:'#c08a2e', medical:'#2f7f86', fire_rescue:'#d9692a', other:'#6b7a70'};
const CICO = {accident:'🚗', medical:'🚑', fire_rescue:'🔥', other:'⚠️', hazard:'⚠️'};
const SVGI = {
  accident:'<path d="M5 17h14M6 17l1.5-6h9L18 17M7 17v2M17 17v2"/>', hazard:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  medical:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>', fire_rescue:'<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-1.5-2-1.5-4 1.8 0 3 1.5 3 3 .5-.5 1-1.6 1-3 2 1 4 4 4 6a6 6 0 1 1-9 0z"/>', other:'<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>'
};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = iso => iso ? new Date(iso).toLocaleString([], {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}) : '';
const fmtFull = iso => iso ? new Date(iso).toLocaleString([], {day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
async function api(path, opts = {}){
  if(opts.json){ opts.method = opts.method || 'POST'; opts.headers = {'Content-Type':'application/json'}; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, opts);
  let j = {}; try { j = await r.json(); } catch(e){}
  if(r.status === 401 && !path.includes('/auth/')){ showLogin(); throw new Error('Session expired — sign in again'); }
  if(!r.ok) throw new Error(j.error || 'Request failed (' + r.status + ')');
  return j;
}
const mapOK = typeof L !== 'undefined';

const S = {me:null, inc:new Map(), sos:new Map(), tab:'PENDING REVIEW', sosFilter:'all', sel:null, open:new Set(), fresh:new Set(), feed:[], unseen:0, es:null};
const TABS = [['PENDING REVIEW','Pending review'],['APPROVED','Approved'],['DECLINED','Declined'],['ARCHIVED','Archived'],['ALL','All']];
const stCls = s => ({'PENDING REVIEW':'s-pend','PENDING':'s-pend','APPROVED':'s-appr','DECLINED':'s-decl','ARCHIVED':'s-arch'}[s] || 's-pend');

/* ------------------------------ auth ------------------------------ */
function showLogin(){ $('app').classList.add('hidden'); $('login').classList.remove('hidden'); if(S.es){ S.es.close(); S.es = null; } }
async function doLogin(e){
  e.preventDefault(); $('loginErr').classList.remove('show');
  try { S.me = await api('/api/auth/login', {json:{username:$('lu').value, password:$('lp').value}}); $('lp').value = ''; boot(); }
  catch(ex){ $('loginErr').textContent = ex.message; $('loginErr').classList.add('show'); }
}
async function doLogout(){ try { await api('/api/auth/logout', {method:'POST'}); } catch(e){} S.me = null; showLogin(); }
async function boot(){
  $('login').classList.add('hidden'); $('app').classList.remove('hidden');
  $('whoName').textContent = S.me.name + ' · ' + S.me.role; $('navAdmin').classList.toggle('hidden', S.me.role !== 'admin');
  try { const m = await api('/api/meta'); $('aProv').innerHTML = '<option value="">All</option>' + m.provinces.map(p => `<option>${esc(p)}</option>`).join(''); } catch(e){}
  await loadAll(); connect(); route();
}

/* ------------------------------ data ------------------------------ */
async function loadAll(){
  const [inc, sos] = await Promise.all([api('/api/v/incidents'), api('/api/v/sos')]);
  S.inc = new Map(inc.map(x => [x.ref, x])); S.sos = new Map(sos.map(x => [x.ref, x]));
  renderSOS(); renderInc(); renderMap();
}
function connect(){
  if(S.es) S.es.close();
  const es = S.es = new EventSource('/api/v/stream');
  es.onopen = () => { $('live').classList.add('on'); $('liveLbl').textContent = 'Live'; loadAll().catch(()=>{}); if(currentPage()==='analytics') loadAnalytics(); };
  es.onerror = () => { $('live').classList.remove('on'); $('liveLbl').textContent = 'Reconnecting…'; };
  es.addEventListener('new_report', e => { const r = JSON.parse(e.data); S.inc.set(r.ref, r); S.fresh.add(r.ref); renderInc(); renderMap();
    addFeed('New incident report', `${r.ref} · ${CATS[r.category]} · ${r.road}, ${r.town}`); toast('New report ' + r.ref, `${CATS[r.category]} — ${r.road}, ${r.town}`); });
  es.addEventListener('incident_updated', e => { const r = JSON.parse(e.data); S.inc.set(r.ref, r); renderInc(); renderMap(); });
  es.addEventListener('new_sos', e => { const r = JSON.parse(e.data); S.sos.set(r.ref, r); S.fresh.add(r.ref); renderSOS(); renderMap(); beep();
    addFeed('🆘 NEW EMERGENCY SOS', `${r.ref} · ${CATS[r.category]} · ${r.gps_granted ? r.lat.toFixed(4)+', '+r.lng.toFixed(4) : (r.location||'location described')}`, true);
    toast('🆘 SOS ' + r.ref, `${CATS[r.category]} — ${r.description}`, 'sos', 20000); });
  es.addEventListener('sos_updated', e => { const r = JSON.parse(e.data); S.sos.set(r.ref, r); renderSOS(); renderMap(); });
  es.addEventListener('alert', e => { const a = JSON.parse(e.data); addFeed('Alert published', a.title); if(currentPage()==='alerts') loadAlertsPage(); });
  es.addEventListener('stats', () => { clearTimeout(S.st); S.st = setTimeout(() => { if(currentPage()==='analytics') loadAnalytics(); }, 250); });
}
function beep(){ try { const c = new (window.AudioContext||window.webkitAudioContext)(), o = c.createOscillator(), g = c.createGain(); o.connect(g); g.connect(c.destination); o.frequency.value = 880; g.gain.value = .08; o.start(); setTimeout(()=>{o.stop();c.close();}, 350); } catch(e){} }
function toast(title, text, cls='', ttl=7000){ const el = document.createElement('div'); el.className = 'toast ' + cls; el.innerHTML = `<b>${esc(title)}</b>${esc(text)}`; $('toasts').appendChild(el); setTimeout(() => el.remove(), ttl); el.onclick = () => el.remove(); }
function addFeed(t, d, sos){ S.feed.unshift({t, d, sos, at:new Date().toISOString()}); S.feed = S.feed.slice(0, 40); S.unseen++; renderFeed(); }
function renderFeed(){
  $('bellN').textContent = S.unseen; $('bellN').classList.toggle('hidden', !S.unseen);
  $('feedList').innerHTML = S.feed.map(f => `<div class="feed-i ${f.sos?'sos':''}"><b>${esc(f.t)}</b>${esc(f.d)}<br><small>${esc(fmt(f.at))}</small></div>`).join('') || '<div class="empty">No new activity this session.</div>';
}
function toggleFeed(){ $('feed').classList.toggle('hidden'); S.unseen = 0; renderFeed(); }
function clearFeed(){ S.feed = []; S.unseen = 0; renderFeed(); }

/* ------------------------------ SOS panel ------------------------------ */
function renderSOS(){
  const f = [['all','All'],['accident','🚗 Road accident'],['medical','🚑 Medical'],['fire_rescue','🔥 Fire / rescue'],['other','⚠ Other']];
  $('sosFilters').innerHTML = f.map(([k,l]) => `<button class="${k==='all'?'all':''} ${S.sosFilter===k?'on':''}" data-k="${k}">${l}</button>`).join('');
  $('sosFilters').querySelectorAll('button').forEach(b => b.onclick = () => { S.sosFilter = b.dataset.k; renderSOS(); });
 const all = [...S.sos.values()].filter(s => s.status !== 'ARCHIVED').sort((a,b) => (b.status==='PENDING') - (a.status==='PENDING') || b.created_at.localeCompare(a.created_at));
  const list = all.filter(s => S.sosFilter==='all' || s.category===S.sosFilter);
  const pend = all.filter(s => s.status==='PENDING').length;
  $('sosCount').textContent = pend + ' pending · ' + all.length + ' total';
  $('sosList').innerHTML = list.map(s => {
    const where = s.gps_granted ? `📍 ${s.lat.toFixed(5)}, ${s.lng.toFixed(5)}${s.accuracy_m?` (±${Math.round(s.accuracy_m)} m)`:''}` : '📍 No GPS — location described by sender';
    const pend = s.status === 'PENDING';
    return `<div class="sos-row ${pend?'pending':'done'} ${S.fresh.has(s.ref)?'new':''}" data-ref="${esc(s.ref)}">
      <div class="sos-ico">${CICO[s.category]}</div>
      <div class="sos-main"><b>${esc(CATS[s.category])} · ${esc(s.ref)}</b><small>${esc(fmtFull(s.created_at))}</small><small>${where}</small>${s.location||s.road?`<small>${esc([s.road,s.location].filter(Boolean).join(' · '))}</small>`:''}</div>
      <div class="sos-desc">${esc(s.description)}<small><b>AI-assisted triage: ${esc(s.assessment?.level||'REVIEW')}</b> · Score ${esc(s.assessment?.score??'—')}/100. Human review required.</small>${s.contact_phone||s.contact_name?`<small>☎ ${esc([s.contact_name,s.contact_phone].filter(Boolean).join(' · '))}</small>`:''}
        ${s.decision_reason?`<small>Declined: ${esc(s.decision_reason)}</small>`:''}${s.decided_by?`<small>${esc(s.status)} by ${esc(s.decided_by)} · ${esc(fmt(s.decided_at))}</small>`:''}</div>
      <div><span class="pri">${esc(s.priority)}</span><br><span class="stat ${stCls(s.status)}" style="margin-top:4px">${esc(s.status)}</span></div>
      <div class="acts">${pend ? `<button class="abtn a-ok" onclick="sosApprove('${esc(s.ref)}',this)">Approve</button><button class="abtn a-no" onclick="openDecline('sos','${esc(s.ref)}')">Decline</button>` : ''}
        <button class="abtn a-al" onclick="assignFacility('${esc(s.ref)}')">Assign facility</button><button class="abtn a-al" onclick="openAlert({sos:'${esc(s.ref)}'})">Alert</button>
       ${S.me.role==='admin' ? `<button class="abtn a-ar" onclick="sosArchive('${esc(s.ref)}',this)">Archive</button>` : ''}
        <button class="abtn a-ar" onclick="showHistory('${esc(s.ref)}')">History</button></div></div>`;
  }).join('') || '<div class="empty">No emergency SOS requests' + (S.sosFilter!=='all' ? ' in this category' : '') + '. New SOS requests from the public site appear here instantly.</div>';
  setTimeout(() => S.fresh.forEach(r => { if(S.sos.has(r)) S.fresh.delete(r); }), 4000);
}
async function sosApprove(ref, btn){ btn.disabled = true; try { S.sos.set(ref, await api(`/api/v/sos/${ref}/approve`, {method:'POST'})); renderSOS(); renderMap(); } catch(e){ toast('Could not approve', e.message, 'bad'); loadAll(); } }
async function sosArchive(ref, btn){ if(!confirm('Archive this SOS request?')) return; btn.disabled = true; try { S.sos.set(ref, await api(`/api/v/sos/${ref}/archive`, {method:'POST'})); renderSOS(); renderMap(); } catch(e){ toast('Could not archive', e.message, 'bad'); loadAll(); } }
/* ------------------------------ incident list ------------------------------ */
function renderInc(){
  const all = [...S.inc.values()];
  const cnt = k => k==='ALL' ? all.length : all.filter(x => x.status===k).length;
  $('incTabs').innerHTML = TABS.map(([k,l]) => `<button class="${S.tab===k?'on':''}" data-k="${k}">${l} (${cnt(k)})</button>`).join('');
  $('incTabs').querySelectorAll('button').forEach(b => b.onclick = () => { S.tab = b.dataset.k; renderInc(); renderMap(); });
  const list = all.filter(x => S.tab==='ALL' || x.status===S.tab).sort((a,b) => b.created_at.localeCompare(a.created_at));
  $('incCount').textContent = list.length + ' shown';
  $('incList').innerHTML = list.map(r => {
    const open = S.open.has(r.ref), st = r.status;
    const act = [];
    if(st==='PENDING REVIEW' || st==='DECLINED' || st==='ARCHIVED') act.push(`<button class="abtn a-ok" onclick="approve('${esc(r.ref)}',this)">${st==='PENDING REVIEW'?'Approve':'Re-approve'}</button>`);
    if(st==='PENDING REVIEW' || st==='APPROVED') act.push(`<button class="abtn a-no" onclick="openDecline('incident','${esc(r.ref)}')">Decline</button>`);
    act.push(`<button class="abtn a-al" onclick="openAlert({inc:'${esc(r.ref)}'})">Alert</button>`);
    act.push(`<button class="abtn a-ok" onclick="openCommunityValidation('${esc(r.ref)}')">Ask nearby community</button>`);
    if(S.me.role==='admin' && st!=='ARCHIVED') act.push(`<button class="abtn a-ar" onclick="archive('${esc(r.ref)}',this)">Archive</button>`);
    return `<div class="inc ${S.sel===r.ref?'sel':''} ${open?'open':''} ${S.fresh.has(r.ref)?'new':''}" data-ref="${esc(r.ref)}">
      <div class="inc-in" onclick="selectInc('${esc(r.ref)}')">
        <div class="inc-top"><div class="l"><span class="ci ${esc(r.category)}"><svg viewBox="0 0 24 24">${SVGI[r.category]||SVGI.other}</svg></span><b>${esc(r.road)}</b></div><small>${esc(r.ref)}</small></div>
        <div class="inc-sub">${esc(r.town)}${r.province?', '+esc(r.province):''} · ${esc(CATS[r.category])}</div>
        <p class="inc-desc">${esc(r.description)}</p>
        <div class="inc-meta"><span class="stat ${stCls(st)}">${esc(st)}</span><small>${esc(fmt(r.created_at))}</small>${r.evidence.length?`<small>📎 ${r.evidence.length} photo${r.evidence.length>1?'s':''}</small>`:''}<small style="margin-left:auto;color:#1f7571;font-weight:600">${open?'Hide':'Details'}</small></div>
      </div>
      <div class="inc-more"><dl>
        <dt>Reference</dt><dd>${esc(r.ref)}</dd><dt>Occurred</dt><dd>${esc(fmtFull(r.occurred_at))}</dd><dt>Submitted</dt><dd>${esc(fmtFull(r.created_at))}</dd>
        <dt>Location</dt><dd>${esc(r.location)}</dd><dt>GPS</dt><dd>${r.lat!=null?`<a target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat=${r.lat}&mlon=${r.lng}#map=14/${r.lat}/${r.lng}">${r.lat.toFixed(5)}, ${r.lng.toFixed(5)}</a>`:'—'}</dd>
        <dt>Reporter</dt><dd>${esc(r.reporter_name || 'Anonymous')}${r.reporter_phone?' · '+esc(r.reporter_phone):''}${r.reporter_email?' · '+esc(r.reporter_email):''}${S.me.role!=='admin'?' <small>(contact details: admin only)</small>':''}</dd>
        ${r.decided_by?`<dt>Decision</dt><dd>${esc(st)} by ${esc(r.decided_by)} · ${esc(fmtFull(r.decided_at))}</dd>`:''}${r.decision_reason?`<dt>Reason</dt><dd>${esc(r.decision_reason)}</dd>`:''}</dl>
        ${r.evidence.length?`<div class="ev">${r.evidence.map(e => `<a href="${esc(e.url)}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(e.url)}" alt="${esc(e.name)}"></a>`).join('')}</div>`:'<small>No evidence attached.</small>'}
        <div class="community-validation" id="community-${esc(r.ref)}"><b>Community verification</b><div class="muted">Load witness responses for this incident.</div><button class="abtn a-al" onclick="loadCommunityValidation('${esc(r.ref)}')">View witness results</button></div>
        <button class="link" onclick="showHistory('${esc(r.ref)}')">View history</button></div>
      <div class="acts">${act.join('')}</div></div>`;
  }).join('') || `<div class="empty" style="color:#dfeee9">No reports in “${esc((TABS.find(t=>t[0]===S.tab)||[])[1])}”. New submissions from the public site appear here automatically.</div>`;
  setTimeout(() => S.fresh.forEach(r => { if(S.inc.has(r)) S.fresh.delete(r); }), 4000);
}
function selectInc(ref){
  S.sel = ref; S.open.has(ref) ? S.open.delete(ref) : S.open.add(ref); renderInc();
  const r = S.inc.get(ref); if(mapOK && r && r.lat!=null && vmap){ vmap.setView([r.lat, r.lng], Math.max(vmap.getZoom(), 9)); if(vmarkers[ref]) vmarkers[ref].openPopup(); }
}
async function approve(ref, btn){ btn.disabled = true; try { S.inc.set(ref, await api(`/api/v/incidents/${ref}/approve`, {method:'POST'})); toast('Approved ' + ref, 'Now visible on the public map. The reporter has been notified.'); renderInc(); renderMap(); } catch(e){ toast('Could not approve', e.message, 'bad'); loadAll(); } }
async function archive(ref, btn){ btn.disabled = true; try { S.inc.set(ref, await api(`/api/v/incidents/${ref}/archive`, {method:'POST'})); renderInc(); renderMap(); } catch(e){ toast('Could not archive', e.message, 'bad'); loadAll(); } }

/* ------------------------------ map ------------------------------ */
let vmap = null, vmarkers = {}, vlayer = null;
function pin(c, sel){ return L.divIcon({className:'', iconSize:[30,38], iconAnchor:[15,38], popupAnchor:[0,-34], html:`<svg width="30" height="38" viewBox="0 0 30 38"><path d="M15 0C6.7 0 0 6.7 0 15c0 10.5 15 23 15 23s15-12.5 15-23C30 6.7 23.3 0 15 0z" fill="${sel?'#152a21':c}"/><circle cx="15" cy="15" r="6" fill="white"/></svg>`}); }
function renderMap(){
  if(!mapOK){ $('vmap').innerHTML = '<div class="empty">Map library could not load (offline?). Reports and SOS lists still work.</div>'; return; }
  if(!vmap){
    vmap = L.map('vmap', {zoomControl:false}).setView([-19.0154, 29.1549], 6); L.control.zoom({position:'topleft'}).addTo(vmap);
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {attribution:'Tiles &copy; Esri', maxZoom:18}).addTo(vmap); vlayer = L.layerGroup().addTo(vmap);
  }
  vlayer.clearLayers(); vmarkers = {};
  [...S.inc.values()].filter(r => (S.tab==='ALL' || r.status===S.tab) && r.lat!=null).forEach(r => {
    const m = L.marker([r.lat, r.lng], {icon:pin(CCOL[r.category], S.sel===r.ref)}).addTo(vlayer);
    m.bindPopup(`<b>${esc(r.road)}</b><br>${esc(CATS[r.category])} · ${esc(r.status)}<br>${esc(r.town)}<br><small>${esc(r.ref)}</small>`); m.on('click', () => { S.sel = r.ref; S.open.add(r.ref); renderInc(); document.querySelector(`.inc[data-ref="${r.ref}"]`)?.scrollIntoView({block:'nearest',behavior:'smooth'}); });
    vmarkers[r.ref] = m;
  });
  [...S.sos.values()].filter(s => s.status==='PENDING' && s.gps_granted).forEach(s => {
    const m = L.marker([s.lat, s.lng], {icon:L.divIcon({className:'', html:'<div class="sos-pin"></div>', iconSize:[22,22], iconAnchor:[11,11]}), zIndexOffset:1000}).addTo(vlayer);
    m.bindPopup(`<b>🆘 SOS · ${esc(CATS[s.category])}</b><br>${esc(s.description)}<br><small>${esc(s.ref)}</small>`); vmarkers[s.ref] = m;
  });
}

/* ------------------------------ decline / alert modals ------------------------------ */
let declineCtx = null, alertCtx = null;
function closeModal(id){ $(id).classList.remove('open'); }
function openDecline(kind, ref){ declineCtx = {kind, ref}; $('declineTitle').textContent = 'Decline ' + ref; $('declineSub').textContent = kind==='sos' ? 'The SOS request will be marked DECLINED and the decision recorded.' : 'The report stays hidden from the public map and the reporter is notified.';
  $('declineReason').value = ''; $('declineErr').classList.remove('show'); $('declineOv').classList.add('open'); $('declineReason').focus(); }
async function confirmDecline(){
  const reason = $('declineReason').value.trim(); if(reason.length < 3){ $('declineErr').textContent = 'A reason is required.'; $('declineErr').classList.add('show'); return; }
  const {kind, ref} = declineCtx;
  try { const r = await api(`/api/v/${kind==='sos'?'sos':'incidents'}/${ref}/decline`, {json:{reason}}); (kind==='sos'?S.sos:S.inc).set(ref, r); closeModal('declineOv'); renderSOS(); renderInc(); renderMap(); toast('Declined ' + ref, 'Reason recorded and sent to the reporter.'); }
  catch(e){ $('declineErr').textContent = e.message; $('declineErr').classList.add('show'); }
}
function openAlert(ctx){
  alertCtx = ctx; $('alertErr').classList.remove('show');
  let t = '', d = '', loc = '', cat = 'hazard', ref = '';
  if(ctx.inc){ const r = S.inc.get(ctx.inc); t = `${CATS[r.category]} reported on ${r.road}`; d = `${r.description.slice(0,300)} Please drive carefully.`; loc = `${r.road}, ${r.town}`; cat = r.category; ref = r.ref; }
  if(ctx.sos){ const s = S.sos.get(ctx.sos); t = `${CATS[s.category]} emergency reported`; d = 'An emergency has been reported. Emergency response may be in progress. Please drive carefully and give way to emergency vehicles.'; loc = [s.road, s.location].filter(Boolean).join(', '); cat = s.category; ref = s.ref; }
  $('al-title').value = t; $('al-desc').value = d; $('al-loc').value = loc; $('al-cat').value = cat; $('al-ref').value = ref; $('al-sev').value = ctx.sos ? 'critical' : 'warning';
  $('alertOv').classList.add('open'); $('al-title').focus();
}
async function sendAlert(){
  const btn = $('alertSend'); btn.disabled = true;
  try {
    const a = await api('/api/v/alerts', {json:{title:$('al-title').value, description:$('al-desc').value, location:$('al-loc').value, category:$('al-cat').value, severity:$('al-sev').value,
      expires_in_minutes:$('al-exp').value || null, related_ref:$('al-ref').value || null}});
    closeModal('alertOv'); toast('Alert published', `“${a.title}” is now live on the public website.`); addFeed('Alert published', a.title); if(currentPage()==='alerts') loadAlertsPage();
  } catch(e){ $('alertErr').textContent = e.message; $('alertErr').classList.add('show'); }
  finally { btn.disabled = false; }
}
async function showHistory(ref){
  $('histTitle').textContent = 'History · ' + ref; $('histBody').innerHTML = 'Loading…'; $('histOv').classList.add('open');
  try { const h = await api('/api/v/history?ref=' + encodeURIComponent(ref));
    $('histBody').innerHTML = h.length ? `<table><tr><th>When</th><th>Action</th><th>By</th><th>Reason / detail</th></tr>${h.map(x => `<tr><td>${esc(fmtFull(x.at))}</td><td>${esc(x.action)}${x.to?` <small>(${esc(x.from||'—')} → ${esc(x.to)})</small>`:''}</td><td>${esc(x.by)}</td><td>${esc(x.reason||'')}</td></tr>`).join('')}</table>` : '<div class="empty">No history.</div>'; }
  catch(e){ $('histBody').textContent = e.message; }
}

/* ------------------------------ emergency facility directory ------------------------------ */
async function loadFacilities(){
  try{let rows=await api('/api/v/facilities');const q=($('facilitySearch')?.value||'').toLowerCase(),rd=$('facilityReadiness')?.value||'',ty=$('facilityTypeFilter')?.value||'';rows=rows.filter(f=>(!q||[f.name,f.facility_id,f.address,f.town,f.road,f.province].join(' ').toLowerCase().includes(q))&&(!rd||f.readiness===rd)&&(!ty||f.facility_type===ty));$('facilitiesTable').innerHTML=rows.length?`<table><tr><th>Facility</th><th>Type</th><th>Location</th><th>Readiness</th><th>Verification</th><th>Contacts</th><th>Actions</th></tr>${rows.map(f=>`<tr><td><b>${esc(f.name)}</b><br><small>${esc(f.facility_id)}</small></td><td>${esc(f.facility_type)}</td><td>${esc(f.address)}, ${esc(f.town)}<br><small>${f.lat??'—'}, ${f.lng??'—'}</small></td><td>${esc(f.readiness)}</td><td>${f.verified?'VERIFIED':'UNVERIFIED'}<br><small>${esc(f.verified_at||'')}</small></td><td>${esc([f.main_phone,f.emergency_phone,f.ambulance_phone,f.alternative_phone].filter(Boolean).join(' · ')||'No contacts recorded')}</td><td>${S.me.role==='admin'?`<button class="abtn a-ok" onclick="toggleFacility(${f.id},${f.verified?0:1})">${f.verified?'Unverify':'Verify'}</button><button class="abtn a-al" onclick="toggleReadiness(${f.id},'${f.readiness==='ACTIVE STANDBY'?'OFF STANDBY':'ACTIVE STANDBY'}')">Toggle standby</button>`:'Read only'}</td></tr>`).join('')}</table>`:'<div class="empty">No facilities entered yet. Add only real facilities and verified details.</div>';if(S.me.role!=='admin')$('facilityCreatePanel').classList.add('hidden');}
  catch(e){toast('Facilities',e.message,'bad');}
}
async function createFacility(e){e.preventDefault();$('facilityErr').classList.remove('show');const g=id=>$(id).value;try{await api('/api/v/facilities',{json:{name:g('facName'),facility_id:g('facId'),facility_type:g('facType'),address:g('facAddress'),town:g('facTown'),province:g('facProvince'),road:g('facRoad'),lat:g('facLat'),lng:g('facLng'),main_phone:g('facPhone'),emergency_phone:g('facEmergency'),ambulance_phone:g('facAmbulance'),alternative_phone:g('facAlt'),equipment:g('facEquipment'),verified:$('facVerified').checked}});e.target.reset();loadFacilities();toast('Facility saved','Facility information stored.');}catch(ex){$('facilityErr').textContent=ex.message;$('facilityErr').classList.add('show');}}
async function toggleFacility(id,v){try{await api('/api/v/facilities/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({verified:!!v})});loadFacilities();}catch(e){toast('Update failed',e.message,'bad');}}
async function toggleReadiness(id,v){try{await api('/api/v/facilities/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({readiness:v})});loadFacilities();}catch(e){toast('Update failed',e.message,'bad');}}
async function assignFacility(ref){try{const fs=(await api('/api/v/facilities')).filter(x=>x.verified);if(!fs.length){toast('No verified facilities','Add and verify a facility before assigning an SOS.','bad');return;}const opts=fs.map(f=>`${f.facility_id} — ${f.name} (${f.town})`).join('\n');const id=prompt('Enter the Facility ID to assign:\n'+opts);if(!id)return;const instructions=prompt('Instructions for the reporter (optional):')||'';await api(`/api/v/sos/${ref}/assign`,{json:{facility_id:id.trim().split(' — ')[0],instructions}});toast('Facility assigned',ref+' has been updated.');loadAll();}catch(e){toast('Assignment failed',e.message,'bad');}}

/* ------------------------------ analytics ------------------------------ */
const ORDER = ['fire_rescue','other','accident','medical','hazard'];
const LCOL = {accident:'#8c1c13', hazard:'#1c3a2d', medical:'#0b4f6c', fire_rescue:'#ffffff', other:'#4a3b00'};
let selSeries = new Set(Object.keys(CATS)), lastA = null;
function aParams(){ const p = new URLSearchParams(); [['from','aFrom'],['to','aTo'],['town','aTown'],['province','aProv'],['category','aCat'],['status','aStat']].forEach(([k,id]) => { if($(id).value.trim()) p.set(k, $(id).value.trim()); }); return p; }
async function loadAnalytics(){ try { lastA = await api('/api/v/analytics?' + aParams()); renderCards(); renderBar(); renderLine(); } catch(e){ toast('Analytics', e.message, 'bad'); } }
function resetFilters(){ ['aFrom','aTo','aTown','aProv','aCat','aStat'].forEach(id => $(id).value = ''); loadAnalytics(); }
['aFrom','aTo','aProv','aCat','aStat'].forEach(id => $(id).addEventListener('change', loadAnalytics));
$('aTown').addEventListener('input', () => { clearTimeout(S.tt); S.tt = setTimeout(loadAnalytics, 350); });
function renderCards(){
  const s = lastA.summary;
  const C = [['Total submitted reports', s.total_submitted, ''], ['Approved reports', s.approved, 'c-ok'], ['Pending reports', s.pending, 'c-pend'], ['Declined reports', s.declined, 'c-no'], ['Emergency SOS requests', s.sos_requests, 'c-sos', s.sos_pending + ' pending'], ['Active alerts', s.active_alerts, 'c-al']];
  $('sumCards').innerHTML = C.map(([l,v,c,n]) => `<div class="card ${c}"><small>${l}</small><b>${v}</b>${n?`<i>${n}</i>`:'<i>&nbsp;</i>'}</div>`).join('');
}
function niceMax(m){ if(m<=5) return 5; const p = Math.pow(10, Math.floor(Math.log10(m))); return Math.ceil(m/p)*p; }
function renderBar(){
  const d = lastA.by_category, W = 900, H = 330, L0 = 54, B = 56, T = 26, max = niceMax(Math.max(...Object.values(d), 1));
  const bw = (W - L0 - 20) / ORDER.length; let g = '';
  for(let i = 0; i <= 5; i++){ const v = max*i/5, y = T + (H-T-B)*(1-i/5); g += `<line x1="${L0}" x2="${W}" y1="${y}" y2="${y}" stroke="rgba(0,0,0,.15)"/><text x="${L0-8}" y="${y+4}" text-anchor="end" font-size="12" fill="#2b3100">${Math.round(v*10)/10}</text>`; }
  ORDER.forEach((k,i) => { const v = d[k], h = (H-T-B)*v/max, x = L0 + i*bw + bw*.2, y = H-B-h;
    g += `<rect x="${x}" y="${y}" width="${bw*.6}" height="${Math.max(h,0)}" rx="6" fill="#1c3a2d"><title>${CATS[k]}: ${v}</title></rect><text x="${x+bw*.3}" y="${y-8}" text-anchor="middle" font-size="15" font-weight="700" fill="#1a1f06">${v}</text><text x="${x+bw*.3}" y="${H-B+24}" text-anchor="middle" font-size="15" fill="#1a1f06">${CATS[k].toLowerCase()}</text>`; });
  $('barChart').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Bar graph of incident totals by category">${g}</svg>`;
  $('barSub').textContent = 'all submitted reports (any status) matching the filters';
}
function renderLine(){
  $('seriesPick').innerHTML = Object.keys(CATS).map(k => `<button class="${selSeries.has(k)?'on':''}" data-k="${k}" style="color:#241c00"><i style="background:${LCOL[k]};border:1px solid #0003"></i>${CATS[k]}</button>`).join('');
  $('seriesPick').querySelectorAll('button').forEach(b => b.onclick = () => { selSeries.has(b.dataset.k) ? selSeries.delete(b.dataset.k) : selSeries.add(b.dataset.k); renderLine(); });
  const rows = lastA.series; if(!rows.length){ $('lineChart').innerHTML = '<div class="empty" style="color:#241c00">No reports match the filters yet.</div>'; return; }
  const day = s => Date.parse(s + 'T00:00:00Z'), first = day(rows[0].day), last = day(rows[rows.length-1].day), days = [];
  const startT = Math.min(first, last - 6*864e5);   // always show at least a 7-day window so short histories still draw lines
  for(let t = startT; t <= last && days.length < 120; t += 864e5) days.push(new Date(t).toISOString().slice(0,10));
  const map = Object.fromEntries(rows.map(r => [r.day, r])), W = 900, H = 330, L0 = 54, B = 56, T = 20, R0 = 24;
  const keys = Object.keys(CATS).filter(k => selSeries.has(k));
  const max = niceMax(Math.max(1, ...keys.flatMap(k => days.map(dd => (map[dd]||{})[k]||0))));
  const X = i => days.length===1 ? (L0+W-R0)/2 : L0 + (W-L0-R0)*i/(days.length-1), Y = v => T + (H-T-B)*(1 - v/max);
  let g = '';
  for(let i = 0; i <= 5; i++){ const y = T + (H-T-B)*(1-i/5); g += `<line x1="${L0}" x2="${W-R0}" y1="${y}" y2="${y}" stroke="rgba(0,0,0,.18)"/><text x="${L0-8}" y="${y+4}" text-anchor="end" font-size="12" fill="#241c00">${Math.round(max*i/5*10)/10}</text>`; }
  const step = Math.ceil(days.length/8); days.forEach((dd,i) => { if(i%step===0 || days.length<=8) g += `<text x="${X(i)}" y="${H-B+22}" text-anchor="middle" font-size="12" fill="#241c00">${new Date(dd+'T00:00:00Z').toLocaleDateString([], {day:'numeric',month:'short',timeZone:'UTC'})}</text>`; });
  g += `<text x="${(L0+W)/2}" y="${H-6}" text-anchor="middle" font-size="14" font-weight="600" fill="#241c00">date</text>`;
  keys.forEach(k => { const pts = days.map((dd,i) => [X(i), Y((map[dd]||{})[k]||0), (map[dd]||{})[k]||0, dd]);
    g += `<polyline fill="none" stroke="${LCOL[k]}" stroke-width="3" stroke-linejoin="round" points="${pts.map(p => p[0]+','+p[1]).join(' ')}"/>`;
    pts.forEach(p => g += `<circle cx="${p[0]}" cy="${p[1]}" r="4.5" fill="${LCOL[k]}" stroke="#0004"><title>${CATS[k]} · ${p[3]}: ${p[2]}</title></circle>`); });
  $('lineChart').innerHTML = keys.length ? `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Line graph of incidents over time">${g}</svg>` : '<div class="empty" style="color:#241c00">Select at least one category above.</div>';
}

/* ------------------------------ alerts / admin pages ------------------------------ */
async function loadAlertsPage(){
  try {
    const al = await api('/api/v/alerts');
    $('alertsTable').innerHTML = al.length ? `<table><tr><th>Published</th><th>Alert</th><th>Severity</th><th>Location</th><th>Expires</th><th>Related</th><th>By</th><th>State</th>${S.me.role==='admin'?'<th></th>':''}</tr>${al.map(a => `<tr><td>${esc(fmtFull(a.created_at))}</td><td><b>${esc(a.title)}</b><br>${esc(a.description)}</td><td>${esc(a.severity)}</td><td>${esc(a.location)}</td><td>${esc(a.expires_at?fmtFull(a.expires_at):'—')}</td><td>${esc(a.related_ref||'—')}</td><td>${esc(a.created_by)}</td><td>${a.removed?'Removed':(a.active?'<span class="stat s-appr">Active</span>':'<span class="stat s-arch">Expired</span>')}</td>${S.me.role==='admin'?`<td>${a.active?`<button class="abtn a-no" onclick="removeAlert(${a.id})">Remove</button>`:''}</td>`:''}</tr>`).join('')}</table>` : '<div class="empty">No alerts have been published yet.</div>';
    const h = await api('/api/v/history');
    $('historyTable').innerHTML = h.length ? `<table><tr><th>When</th><th>Type</th><th>Reference</th><th>Action</th><th>By</th><th>Reason</th></tr>${h.slice(0,80).map(x => `<tr><td>${esc(fmtFull(x.at))}</td><td>${esc(x.type)}</td><td>${esc(x.ref)}</td><td>${esc(x.action)}</td><td>${esc(x.by)}</td><td>${esc(x.reason||'')}</td></tr>`).join('')}</table>` : '<div class="empty">No history yet.</div>';
  } catch(e){ $('alertsTable').textContent = e.message; }
}
async function removeAlert(id){ if(!confirm('Remove this alert from the public site?')) return; try { await api('/api/v/alerts/' + id, {method:'DELETE'}); loadAlertsPage(); } catch(e){ toast('Error', e.message, 'bad'); } }
async function loadAdmin(){
  if(S.me.role !== 'admin') return;
  try {
    const u = await api('/api/v/users'); $('usersTable').innerHTML = `<table><tr><th>Name</th><th>Username</th><th>Role</th></tr>${u.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.username)}</td><td>${esc(x.role)}</td></tr>`).join('')}</table>`;
    const a = await api('/api/v/audit'); $('auditTable').innerHTML = `<table><tr><th>When</th><th>User</th><th>Action</th><th>Detail</th><th>IP</th></tr>${a.map(x => `<tr><td>${esc(fmtFull(x.at))}</td><td>${esc(x.user||'—')}</td><td>${esc(x.action)}</td><td>${esc(x.detail||'')}</td><td>${esc(x.ip||'')}</td></tr>`).join('')}</table>`;
  } catch(e){ toast('Admin', e.message, 'bad'); }
}
async function addUser(e){
  e.preventDefault(); $('userErr').classList.remove('show');
  try { await api('/api/v/users', {json:{name:$('nuName').value, username:$('nuUser').value, password:$('nuPass').value, role:$('nuRole').value}}); e.target.reset(); loadAdmin(); toast('User created', 'They can sign in now.'); }
  catch(ex){ $('userErr').textContent = ex.message; $('userErr').classList.add('show'); }
}

/* ------------------------------ routing ------------------------------ */
const PAGES = ['validator','analytics','alerts','facilities','admin'];
function currentPage(){ const h = location.hash.slice(1); return PAGES.includes(h) ? h : 'validator'; }
function route(){
  if(!S.me) return; const p = currentPage();
  PAGES.forEach(x => $('page-' + x).classList.toggle('hidden', x !== p));
  document.querySelectorAll('.topbar nav a').forEach(a => a.classList.toggle('on', a.dataset.page === p));
  if(p==='validator') setTimeout(() => { renderMap(); vmap && vmap.invalidateSize(); }, 50);
  if(p==='analytics') loadAnalytics(); if(p==='alerts') loadAlertsPage(); if(p==='facilities') loadFacilities(); if(p==='admin') loadAdmin();
}
window.addEventListener('hashchange', route);
['facilitySearch','facilityReadiness','facilityTypeFilter'].forEach(id=>{const el=$(id);if(el){el.addEventListener(id==='facilitySearch'?'input':'change',()=>loadFacilities());}});
document.addEventListener('keydown', e => { if(e.key==='Escape') ['declineOv','alertOv','histOv'].forEach(closeModal); });
document.addEventListener('click', e => { if(!e.target.closest('#feed') && !e.target.closest('#bell')) $('feed').classList.add('hidden'); });
(async () => { try { S.me = await api('/api/auth/me'); boot(); } catch(e){ showLogin(); } })();

/* Community verification: validators can query nearby opted-in public users. */
async function openCommunityValidation(ref){
  const r=S.inc.get(ref); if(!r)return;
  if(!confirm('Send a witness-check request to RoadGuard users within 10 km of '+r.road+', '+r.town+'?'))return;
  try{
    const q=await api('/api/v/verification',{method:'POST',json:{incident_ref:ref,question:'Did you personally witness this reported incident?'}});
    toast('Community query sent', 'Nearby users can now respond. Query expires in 24 hours.');
    S.open.add(ref);renderInc();await loadCommunityValidation(ref);
  }catch(e){toast('Could not send query',e.message,'bad');}
}
async function loadCommunityValidation(ref){
  try{
    const rows=await api('/api/v/verification?ref='+encodeURIComponent(ref));
    const el=document.getElementById('community-'+ref);if(!el)return;
    el.innerHTML='<b>Community verification</b>'+ (rows.length?rows.map(q=>`<div class="community-result"><p>${esc(q.question)}</p><strong>${q.witnessed_yes} said YES — witnessed</strong><br><span>${q.witnessed_no} said NO · ${q.total} total responses</span><br><small>${esc(q.status)} · expires ${esc(fmt(q.expires_at))}</small></div>`).join(''):'<p class="muted">No community queries have been sent for this incident.</p>')+`<button class="abtn a-al" onclick="openCommunityValidation('${esc(ref)}')">Send another nearby query</button>`;
  }catch(e){toast('Could not load witness results',e.message,'bad');}
}

async function queryWitnessesFromAlert(){
  const ref=($('al-ref').value||'').trim();
  if(!ref || ref.startsWith('SOS')){toast('Incident reference required','Community witness checks currently require a road incident report with GPS coordinates.','bad');return;}
  try{
    const q=await api('/api/v/verification',{method:'POST',json:{incident_ref:ref,question:'An alert has been issued for this incident. Did you personally witness it?'}});
    toast('Nearby witness query sent',`Query #${q.id} is open for 24 hours.`);
  }catch(e){$('alertErr').textContent=e.message;$('alertErr').classList.add('show');}
}
