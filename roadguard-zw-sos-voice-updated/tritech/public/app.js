/* RoadGuard ZW — Website 1 (public). All data comes from the shared backend; nothing is simulated. */
const CATS = {
  accident:{label:'Road accident',color:'#c2534a'}, hazard:{label:'Road hazard',color:'#c08a2e'},
  medical:{label:'Medical emergency',color:'#2f7f86'}, fire_rescue:{label:'Fire & rescue',color:'#d9692a'},
  other:{label:'Other incident',color:'#6b7a70'}
};
const ICONS = {
  accident:'<path d="M5 17h14M6 17l1.5-6h9L18 17M7 17v2M17 17v2"/><circle cx="8.5" cy="14" r=".6"/><circle cx="15.5" cy="14" r=".6"/>',
  hazard:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  medical:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  fire_rescue:'<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-1.5-2-1.5-4 1.8 0 3 1.5 3 3 .5-.5 1-1.6 1-3 2 1 4 4 4 6a6 6 0 1 1-9 0z"/>',
  other:'<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>'
};
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = iso => iso ? new Date(iso).toLocaleString([], {day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
const store = {
  get(k, d){ try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch(e){ return d; } },
  set(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} }
};
const API_BASE = 'https://roadguard-backend-jlxp.onrender.com';

async function api(path, opts = {}) {
  const r = await fetch(API_BASE + path, {
    ...opts,
    credentials: 'include'
  });

  let j = {};
  try { j = await r.json(); } catch(e) {}

  if (!r.ok) {
    throw new Error(j.error || ('Request failed (' + r.status + ')'));
  }

  return j;
}

let incidents = [], alerts = [], filter = 'all', selectedRef = null, newAlertIds = new Set(), currentGPS = null;
let myRefs = store.get('rg_refs', []);              // [{ref,type}] — only used to subscribe to MY status updates
let dismissed = new Set(store.get('rg_dismissed', []));
let myStatus = {};                                   // ref -> latest tracking payload
const mapOK = typeof L !== 'undefined';

/* ------------------------------ map ------------------------------ */
let map, markers = {}, baseTileLayer;
function pinIcon(cat, sel){
  const c = sel ? '#152a21' : (CATS[cat]||CATS.other).color;
  return L.divIcon({className:'', iconSize:[30,38], iconAnchor:[15,38], popupAnchor:[0,-34],
    html:`<svg width="30" height="38" viewBox="0 0 30 38"><path d="M15 0C6.7 0 0 6.7 0 15c0 10.5 15 23 15 23s15-12.5 15-23C30 6.7 23.3 0 15 0z" fill="${c}"/><circle cx="15" cy="15" r="6" fill="white"/></svg>`});
}
if(mapOK){
  map = L.map('map', {zoomControl:false}).setView([-19.0154, 29.1549], 6);
  L.control.zoom({position:'topleft'}).addTo(map);
  baseTileLayer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {attribution:'Tiles &copy; Esri', maxZoom:18}).addTo(map);
} else {
  $('map').innerHTML = '<div class="empty" style="padding:30px">The map library could not load (are you offline?). The incident list still works.</div>';
}
function iconSvg(cat){ return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[cat]||ICONS.other}</svg>`; }

function filtered(){
  const q = $('searchInput').value.trim().toLowerCase();
  return incidents.filter(r => (filter==='all'||(filter==='local' ? (currentGPS && r.lat!=null && pointDistanceKm([r.lat,r.lng],[currentGPS.lat,currentGPS.lon])<=50) : r.category===filter)) &&
    (!q || [r.road,r.town,r.location,r.description,r.province,(CATS[r.category]||{}).label].join(' ').toLowerCase().includes(q)));
}
function renderFilters(){
  const items = [['local','Local'],['all','All'],['accident','Accident'],['hazard','Hazard'],['other','Other']];
  $('catFilters').innerHTML = items.map(([k,l])=>`<button class="filter-pill ${filter===k?'active':''}" data-filter="${k}">${esc(l)}</button>`).join('');
  $('catFilters').querySelectorAll('button').forEach(b => b.onclick = () => { filter = b.dataset.filter; renderFilters(); renderIncidents(); });
}
function renderIncidents(){
  const list = filtered();
  $('updatesList').innerHTML = list.map(r => `
    <button class="update-card ${r.ref===selectedRef?'selected':''}" data-ref="${esc(r.ref)}">
      <div class="update-icon ${esc(r.category)}">${iconSvg(r.category)}</div>
      <div class="update-body">
        <div class="update-top"><strong>${esc(r.road)}</strong><span class="update-tag">${esc((CATS[r.category]||{}).label)}</span></div>
        <p>${esc(r.town)}${r.province?', '+esc(r.province):''}</p><p class="desc">${esc(r.description)}</p>
      </div></button>`).join('') ||
    `<p class="empty">${incidents.length ? 'No incidents match your search.' : 'No approved incidents at the moment. Reports appear here once a validator approves them.'}</p>`;
  $('updatesList').querySelectorAll('.update-card').forEach(b => b.onclick = () => selectIncident(b.dataset.ref));
  $('countLabel').textContent = `${list.length} incident${list.length===1?'':'s'} shown`;
  if(mapOK){
    Object.values(markers).forEach(m => map.removeLayer(m)); markers = {};
    list.forEach(r => {
      if(r.lat==null) return;
      const m = L.marker([r.lat, r.lng], {icon:pinIcon(r.category, r.ref===selectedRef)}).addTo(map);
      m.bindPopup(`<div class="popup-title">${esc(r.road)}</div><div class="popup-desc"><b>${esc((CATS[r.category]||{}).label)}</b><br>${esc(r.town)}<br>${esc(r.description)}<br><small>${esc(fmt(r.occurred_at))}</small></div>`);
      m.on('click', () => selectIncident(r.ref, true));
      markers[r.ref] = m;
    });
  }
  renderDetail();
  if(routeCoords.length) renderAhead();
}
function renderDetail(){
  const r = incidents.find(x => x.ref===selectedRef);
  $('detailCard').style.display = r ? '' : 'none';
  if(!r) return;
  $('detailCard').innerHTML = `<div class="detail-left">${iconSvg(r.category)}<div>
    <h4>${esc(r.road)} <span class="pill-tag ${esc(r.category)}">${esc((CATS[r.category]||{}).label)}</span></h4>
    <p>${esc(r.location)} · ${esc(r.town)}${r.province?', '+esc(r.province):''}</p><p style="margin-top:6px;color:var(--ink)">${esc(r.description)}</p></div></div>
    <span class="update-tag">Occurred ${esc(fmt(r.occurred_at))} · Approved ${esc(fmt(r.approved_at))}<br>Ref ${esc(r.ref)}</span>`;
}
function selectIncident(ref, fromMap){
  selectedRef = ref; renderIncidents();
  const r = incidents.find(x => x.ref===ref);
  if(mapOK && r && markers[ref]){ if(!fromMap) map.setView([r.lat, r.lng], Math.max(map.getZoom(), 9)); markers[ref].openPopup(); }
}
function locateMe(){
  if(!mapOK || !navigator.geolocation) return;
  $('locateLabel').textContent = 'Finding you…';
  navigator.geolocation.getCurrentPosition(p => {
    currentGPS={lat:p.coords.latitude,lon:p.coords.longitude};
    map.setView([p.coords.latitude, p.coords.longitude], 11);
    L.circleMarker([p.coords.latitude, p.coords.longitude], {radius:8, color:'#1c3a2d', fillColor:'#e3a73b', fillOpacity:1}).addTo(map).bindPopup('You are here').openPopup();
    $('locateLabel').textContent = 'My location';
  }, () => { $('locateLabel').textContent = 'My location'; }, {timeout:8000});
}

/* ------------------------------ route planning + What's Ahead ------------------------------ */
let routeOrigin=null, routeDestination=null, routeLine=null, routePin=null, gpsWatch=null, journeyActive=false, routeCoords=[], routeLength=0, routeSteps=[], publicFacilities=[], routeOptions=[], routeProgressKm=0, routeDuration=0, destinationConfirmed=false;
const routeLayers=[];
function km(m){return (m/1000).toFixed(m<10000?1:0)+' km'}
function routeStatus(msg){$('routeSummary').textContent=msg}
function geolocateRoute(){
  if(!navigator.geolocation){routeStatus('GPS is not supported by this browser. Search for a starting point manually.');return}
  routeStatus('Getting your current GPS position…');
  navigator.geolocation.getCurrentPosition(async p=>{
    routeOrigin={lat:p.coords.latitude,lon:p.coords.longitude,label:'My current location'}; currentGPS={lat:p.coords.latitude,lon:p.coords.longitude};
    $('routeOrigin').value=`My location (${routeOrigin.lat.toFixed(5)}, ${routeOrigin.lon.toFixed(5)})`;
    if(mapOK) map.setView([routeOrigin.lat,routeOrigin.lon],12);
    routeStatus(`GPS origin set · accuracy ±${Math.round(p.coords.accuracy)} m`); if(filter==='local'){renderFilters();renderIncidents();}
  },e=>routeStatus('Could not access GPS. Allow location permission or enter a starting point manually.'),{enableHighAccuracy:true,timeout:12000,maximumAge:15000});
}
async function geocode(q){
  const u='https://photon.komoot.io/api/?limit=8&lang=en&q='+encodeURIComponent(q);
  const r=await fetch(u,{headers:{'Accept':'application/json'}}); if(!r.ok) throw new Error('Place search is temporarily unavailable.'); const j=await r.json();
  return (j.features||[]).filter(x=>(x.properties?.country||'').toLowerCase().includes('zimbabwe')).map(x=>({lat:x.geometry.coordinates[1],lon:x.geometry.coordinates[0],display_name:[x.properties.name,x.properties.street,x.properties.city||x.properties.town||x.properties.village,x.properties.state,'Zimbabwe'].filter(Boolean).join(', ')}));
}
function initRouteUI(){
  $('useGpsBtn').onclick=geolocateRoute;
  $('routeSearch').addEventListener('input',async()=>{
    const q=$('routeSearch').value.trim(); $('routeSuggestions').innerHTML=''; if(q.length<3)return;
    try{const rows=await geocode(q);$('routeSuggestions').innerHTML=rows.map((x,i)=>`<button type="button" data-i="${i}">${esc(x.display_name)}</button>`).join('');
      $('routeSuggestions').querySelectorAll('button').forEach((b,i)=>b.onclick=()=>{const x=rows[i];setDestination(+x.lat,+x.lon,x.display_name);$('routeSearch').value=x.display_name;$('routeSuggestions').innerHTML='';});
    }catch(e){$('routeSuggestions').textContent=e.message;}
  });
  $('pinModeBtn').onclick=()=>{if(!mapOK)return;routePinMode=true;routeStatus('Tap anywhere on the map to place a destination pin.');map.getContainer().style.cursor='crosshair';};
  $('clearRouteBtn').onclick=clearRoute;
  $('calculateRouteBtn').onclick=calculateRoute;
  $('confirmDestinationBtn').onclick=()=>{if(routeDestination){destinationConfirmed=true;$('confirmDestinationBtn').textContent='Destination confirmed';routeStatus('Destination confirmed. Select a starting point and calculate the route.');}};
  $('routeAlternative').onchange=()=>drawRoute(+$('routeAlternative').value);
  $('startJourneyBtn').onclick=startJourney;
  $('endJourneyBtn').onclick=endJourney;
  $('recalcRouteBtn').onclick=calculateRoute;
  $('lowDataMode').onchange=e=>{document.body.classList.toggle('low-data-mode',e.target.checked);if(mapOK&&baseTileLayer){if(e.target.checked)map.removeLayer(baseTileLayer);else baseTileLayer.addTo(map);}};
}
let routePinMode=false;
function setDestination(lat,lon,label){if(lat < -23 || lat > -15 || lon < 24 || lon > 34){routeStatus('RoadGuard destination pins must be within Zimbabwe.');return;}routeDestination={lat,lon,label};destinationConfirmed=false;$('confirmDestinationBtn').disabled=false;$('confirmDestinationBtn').textContent='Set as destination';$('routeDestination').textContent='Selected pin: '+label+' (confirm below)';if(mapOK){if(routePin)map.removeLayer(routePin);routePin=L.marker([lat,lon],{draggable:true}).addTo(map).bindPopup('Destination').openPopup();routePin.on('dragend',async()=>{const p=routePin.getLatLng();routeDestination.lat=p.lat;routeDestination.lon=p.lng;routeDestination.label=`Pinned location (${p.lat.toFixed(5)}, ${p.lng.toFixed(5)})`;destinationConfirmed=false;$('confirmDestinationBtn').textContent='Set as destination';$('routeDestination').textContent='Adjusted pin: '+routeDestination.label+' (confirm below)';});map.setView([lat,lon],11);}}
if(mapOK){map.on('click',e=>{if(!routePinMode)return;routePinMode=false;map.getContainer().style.cursor='';setDestination(e.latlng.lat,e.latlng.lng,`Pinned location (${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)})`);});}
async function resolveOrigin(){if(routeOrigin)return routeOrigin;const q=$('routeOrigin').value.trim();if(!q)throw new Error('Choose a starting point or use GPS.');const rows=await geocode(q);if(!rows.length)throw new Error('Starting point not found.');routeOrigin={lat:+rows[0].lat,lon:+rows[0].lon,label:rows[0].display_name};return routeOrigin;}
async function calculateRoute(){
  if(!routeDestination||!destinationConfirmed){routeStatus('Choose a destination, then press Set as destination to confirm it.');return;}
  try{
    const o=await resolveOrigin(),d=routeDestination;routeStatus('Calculating a real road route…');
    const url=`https://router.project-osrm.org/route/v1/driving/${o.lon},${o.lat};${d.lon},${d.lat}?overview=full&geometries=geojson&steps=true&alternatives=true`;
    const r=await fetch(url);if(!r.ok)throw new Error('Road routing service is unavailable. Try again later.');const j=await r.json();if(j.code!=='Ok'||!j.routes?.length)throw new Error('No drivable road route was returned for these points. Try another pin near an accessible road.');if((j.waypoints||[]).some(w=>w.distance>1000))throw new Error('One of the selected points is more than 1 km from the routable road network. Move the pin closer to an accessible road.');
    routeOptions=j.routes; $('routeAlternative').innerHTML=j.routes.map((r,i)=>`<option value="${i}">${i===0?'Fastest':'Alternative '+i} · ${km(r.distance)} · ${Math.round(r.duration/60)} min</option>`).join(''); drawRoute(0); const best=j.routes[0];
    $('routeSummary').innerHTML=$('routeSummary').innerHTML + (routeSteps.length ? '<br><br><b>Next road instructions</b><br>' + routeSteps.slice(0,5).map(st=>esc([st.maneuver?.type,st.maneuver?.modifier,st.name].filter(Boolean).join(' · ')||'Continue')).join('<br>') : '');
    $('startJourneyBtn').disabled=false;renderAhead();
  }catch(e){routeStatus(e.message);$('startJourneyBtn').disabled=true;}
}
function drawRoute(index){const best=routeOptions[index];if(!best)return;routeCoords=best.geometry.coordinates.map(c=>[c[1],c[0]]);routeLength=best.distance;routeDuration=best.duration;routeSteps=(best.legs||[]).flatMap(l=>l.steps||[]);if(routeLine)map.removeLayer(routeLine);routeLine=L.polyline(routeCoords,{color:index?'#d18a22':'#087e70',weight:6,opacity:.9}).addTo(map);map.fitBounds(routeLine.getBounds(),{padding:[35,35]});$('routeSummary').innerHTML=`<strong>YOUR JOURNEY</strong><br>From: ${esc(routeOrigin.label)}<br>To: ${esc(routeDestination.label)}<br>Distance: ${km(best.distance)}<br>Estimated time: ${Math.round(best.duration/60)} min<br>Route option: ${index===0?'Fastest route':'Alternative '+index}<br>Road-network route calculated by OSRM`;if(routeCoords.length)renderAhead();}
function pointDistanceKm(a,b){const R=6371,rad=x=>x*Math.PI/180,dl=rad(b[0]-a[0]),dn=rad(b[1]-a[1]),x=Math.sin(dl/2)**2+Math.cos(rad(a[0]))*Math.cos(rad(b[0]))*Math.sin(dn/2)**2;return 2*R*Math.asin(Math.sqrt(x));}
function nearestRouteProgress(pt){let best=Infinity,along=0,walk=0;for(let i=1;i<routeCoords.length;i++){const a=routeCoords[i-1],b=routeCoords[i],seg=pointDistanceKm(a,b),n=Math.max(1,Math.ceil(seg*10));for(let k=0;k<=n;k++){const t=k/n,p=[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t],d=pointDistanceKm(pt,p);if(d<best){best=d;along=walk+seg*t;}}walk+=seg;}return {distance:best,along};}
function renderAhead(){
  if(!routeCoords.length){$('aheadList').innerHTML='';return;}
  const rows=incidents.filter(x=>x.lat!=null&&x.lng!=null).map(x=>({x,p:nearestRouteProgress([x.lat,x.lng])})).filter(z=>z.p.distance<=1.5 && z.p.along>=routeProgressKm).sort((a,b)=>a.p.along-b.p.along);
  $('aheadDisclaimer').textContent='Approved reports within 1.5 km of the calculated road route. Nearby does not necessarily mean the road is blocked.';
  $('aheadList').innerHTML=rows.map(z=>`<div class="ahead-item"><b>${esc((CATS[z.x.category]||CATS.other).label)} · ${esc(z.x.road)}</b>${esc(z.x.location||z.x.town)} · ${km(Math.max(0,z.p.along-routeProgressKm)*1000)} ahead<br><small>Nearby — route impact not confirmed · ${esc(z.x.ref)}</small></div>`).join('')||'<div class="ahead-item">No approved incidents are currently recorded near this route. This does not guarantee that the road is clear or safe.</div>';
  const near=publicFacilities.filter(f=>f.lat!=null&&f.lng!=null).map(f=>({f,d:nearestRouteProgress([f.lat,f.lng]).distance})).filter(x=>x.d<=15).sort((a,b)=>a.d-b.d).slice(0,8);
  $('nearbyFacilities').innerHTML=near.map(x=>`<div class="ahead-item"><b>${esc(x.f.name)} · ${esc(x.f.facility_type)}</b>${esc(x.f.address)}, ${esc(x.f.town)} · ${x.d.toFixed(1)} km from route<br><small>${esc(x.f.readiness)} · ${esc(x.f.main_phone||x.f.emergency_phone||'No verified phone listed')}</small></div>`).join('')||'No verified facilities with coordinates are currently recorded near this route.';
}
function clearRoute(){if(routeLine&&map)map.removeLayer(routeLine);if(routePin&&map)map.removeLayer(routePin);routeLine=routePin=null;routeOrigin=routeDestination=null;destinationConfirmed=false;$('confirmDestinationBtn').disabled=true;$('confirmDestinationBtn').textContent='Set as destination';routeCoords=[];routeLength=0;routeSteps=[];routeOptions=[];routeProgressKm=0;$('routeOrigin').value='';$('routeSearch').value='';$('routeDestination').textContent='No destination selected.';$('routeSummary').textContent='Select a start and destination to calculate a route.';$('aheadList').innerHTML='';$('startJourneyBtn').disabled=true;endJourney();}
function startJourney(){if(!routeCoords.length)return;journeyActive=true;$('startJourneyBtn').classList.add('hidden');$('endJourneyBtn').classList.remove('hidden');if(navigator.geolocation){gpsWatch=navigator.geolocation.watchPosition(p=>{const pt=[p.coords.latitude,p.coords.longitude],pr=nearestRouteProgress(pt);routeProgressKm=pr.along;currentGPS={lat:p.coords.latitude,lon:p.coords.longitude};const remaining=Math.max(0,routeLength/1000-pr.along), mins=routeLength?Math.round((routeDuration/60)*(remaining/(routeLength/1000))):0;routeStatus(`Journey active · ${remaining.toFixed(1)} km remaining · approximately ${mins} min · GPS accuracy ±${Math.round(p.coords.accuracy)} m`);renderAhead();if(pr.distance>0.8){routeOrigin={lat:p.coords.latitude,lon:p.coords.longitude,label:'Current GPS position'};routeStatus(`You appear ${pr.distance.toFixed(1)} km from the planned route. Check your position and recalculate from your current location.`);$('recalcRouteBtn').classList.remove('hidden');}else $('recalcRouteBtn').classList.add('hidden');},()=>{}, {enableHighAccuracy:true,maximumAge:5000,timeout:15000});}}
function endJourney(){journeyActive=false;if(gpsWatch!==null&&navigator.geolocation)navigator.geolocation.clearWatch(gpsWatch);gpsWatch=null;$('endJourneyBtn').classList.add('hidden');$('recalcRouteBtn').classList.add('hidden');$('startJourneyBtn').classList.remove('hidden');}

/* ------------------------------ alerts ------------------------------ */
function sevLabel(s){ return {info:'Information',warning:'Warning',critical:'Critical'}[s] || s; }
function alertCard(a){
  return `<article class="alert-card sev-${esc(a.severity)} ${newAlertIds.has(a.id)?'new':''}">
    <span class="sev ${esc(a.severity)}">${esc(sevLabel(a.severity))}</span> <span class="pill-tag ${esc(a.category)}">${esc((CATS[a.category]||{}).label)}</span>
    <h4>${esc(a.title)}</h4><p>${esc(a.description)}</p>
    <div class="alert-meta"><span>📍 ${esc(a.location)}</span><span>🕒 ${esc(fmt(a.created_at))}</span>${a.expires_at?`<span>Expires ${esc(fmt(a.expires_at))}</span>`:''}</div></article>`;
}
function renderAlerts(){
  alerts = alerts.filter(a => !a.expires_at || new Date(a.expires_at) > new Date());
  $('alertList').innerHTML = alerts.map(alertCard).join('') || '<p class="empty">No active alerts right now. When a validator publishes an alert it appears here instantly.</p>';
  renderBell();
}
function unreadReportNotes(){
  const out = [];
  Object.values(myStatus).forEach(t => (t.updates||[]).forEach((u,i) => { const id = t.ref+':'+u.kind; if(u.kind!=='submitted' && !dismissed.has(id)) out.push({id, ref:t.ref, ...u}); }));
  return out;
}
function renderBell(){
  const n = alerts.filter(a => !dismissed.has('a'+a.id)).length + unreadReportNotes().length;
  $('bellBadge').textContent = n; $('bellBadge').classList.toggle('hidden', n===0);
}
function dismissNote(id){ dismissed.add(id); store.set('rg_dismissed', [...dismissed]); renderBell(); renderDrawer(); }
function openDrawer(){ renderDrawer(); $('drawerBg').classList.add('open'); }
function closeDrawer(){ $('drawerBg').classList.remove('open'); }
function renderDrawer(){
  const al = alerts.filter(a => !dismissed.has('a'+a.id));
  $('drawerAlerts').innerHTML = al.map(a => `<div class="note-item"><button class="x" aria-label="Dismiss" onclick="dismissNote('a${a.id}')">✕</button>
    <span class="sev ${esc(a.severity)}">${esc(sevLabel(a.severity))}</span><b>${esc(a.title)}</b>${esc(a.description)}<br><small>📍 ${esc(a.location)} · ${esc(fmt(a.created_at))}</small></div>`).join('') || '<p class="empty">No new alerts.</p>';
  const rn = unreadReportNotes();
  const pend = Object.values(myStatus).filter(t => !(t.updates||[]).some(u => u.kind!=='submitted'));
  $('drawerReports').innerHTML = rn.map(n => `<div class="note-item"><button class="x" aria-label="Dismiss" onclick="dismissNote('${esc(n.id)}')">✕</button><b>${esc(n.ref)}</b>${esc(n.message)}<br><small>${esc(fmt(n.created_at))}</small></div>`).join('')
    + pend.map(t => `<div class="note-item"><b>${esc(t.ref)}</b>Still under review.</div>`).join('')
    || '<p class="empty">Nothing yet. Reports and SOS requests you submit on this device are tracked here.</p>';
}
function toast({cls='', label='', title='', text='', actions=[], ttl=0}){
  const el = document.createElement('div'); el.className = 'toast ' + cls; el.setAttribute('role','alert');
  el.innerHTML = `${label}<h5>${esc(title)}</h5><p>${esc(text)}</p><div class="row"></div>`;
  const row = el.querySelector('.row');
  actions.concat([{text:'Dismiss', fn:()=>{}}]).forEach((a,i,arr) => { const b = document.createElement('button'); b.textContent = a.text; if(i===0 && arr.length>1) b.className='pri';
    b.onclick = () => { a.fn(); el.remove(); }; row.appendChild(b); });
  $('toasts').prepend(el); if(ttl) setTimeout(() => el.remove(), ttl);
}
function onNewAlert(a, live){
  const i = alerts.findIndex(x => x.id===a.id); if(i>=0) alerts[i] = a; else alerts.unshift(a);
  if(live){ newAlertIds.add(a.id); setTimeout(() => { newAlertIds.delete(a.id); renderAlerts(); }, 6000);
    toast({cls:'sev-'+a.severity, label:`<span class="sev ${esc(a.severity)}">🔔 RoadGuard alert · ${esc(sevLabel(a.severity))}</span>`, title:a.title, text:`${a.description} — ${a.location}`,
      actions:[{text:'Open alert', fn:() => { location.hash = '#alerts-section'; $('alertList').scrollIntoView({behavior:'smooth'}); }}]});
    if('Notification' in window && Notification.permission==='granted' && document.hidden) new Notification('RoadGuard ZW: ' + a.title, {body:a.description + ' — ' + a.location});
  }
  renderAlerts();
}
function enableDeviceNotifs(){
  if(!('Notification' in window)){ toast({cls:'bad', title:'Not supported', text:'This browser does not support device notifications. In-page pop-ups still work.', ttl:6000}); return; }
  Notification.requestPermission().then(p => { $('devNotifBtn').textContent = p==='granted' ? 'Device notifications on' : 'Device notifications blocked'; });
}

/* ------------------------------ realtime ------------------------------ */
let es = null;
function connect(){
  if(es) es.close();
  const refs = myRefs.map(r => r.ref).join(',');
  es = new EventSource(API_BASE + '/api/stream' + (refs ? '?refs=' + encodeURIComponent(refs) : ''), {withCredentials: true});
  es.onopen = () => { $('livePill').classList.add('on'); $('liveLabel').textContent = 'Live'; loadAll(); };
  es.onerror = () => { $('livePill').classList.remove('on'); $('liveLabel').textContent = 'Reconnecting…'; };
  es.addEventListener('incident_published', e => { const r = JSON.parse(e.data); const i = incidents.findIndex(x => x.ref===r.ref); if(i>=0) incidents[i]=r; else incidents.unshift(r); renderIncidents(); });
  es.addEventListener('incident_removed', e => { const {ref} = JSON.parse(e.data); incidents = incidents.filter(x => x.ref!==ref); if(selectedRef===ref) selectedRef=null; renderIncidents(); });
  es.addEventListener('alert', e => onNewAlert(JSON.parse(e.data), true));
  es.addEventListener('alert_removed', e => { const {id} = JSON.parse(e.data); alerts = alerts.filter(a => a.id!==id); renderAlerts(); });
  es.addEventListener('status', async e => {
    const s = JSON.parse(e.data); await refreshRef(s.ref);
    const ok = s.status==='APPROVED';
    toast({cls: ok?'ok':'bad', title: `${s.ref}: ${s.status}`, ttl:15000,
      text: ok ? 'A validator approved your submission.' : (s.status==='DECLINED' ? 'Declined. Reason: ' + (s.reason||'not given') : 'Status changed to ' + s.status),
      actions:[{text:'Track', fn:() => { $('trackInput').value = s.ref; trackRef(); location.hash = '#track-section'; }}]});
  });
}
async function loadAll(){
  try { incidents = await api('/api/map/incidents'); renderIncidents(); } catch(e){}
  try { alerts = await api('/api/alerts'); renderAlerts(); } catch(e){}
  myRefs.forEach(r => refreshRef(r.ref));
}
async function refreshRef(ref){
  try { myStatus[ref] = await api('/api/' + (ref.startsWith('SOS')?'sos':'reports') + '/track/' + encodeURIComponent(ref)); renderBell(); } catch(e){}
}
function rememberRef(ref, type){
  if(!myRefs.some(r => r.ref===ref)){ myRefs.push({ref, type}); myRefs = myRefs.slice(-30); store.set('rg_refs', myRefs); }
  connect();       // re-subscribe so status updates for the new ref reach this browser
  refreshRef(ref);
}

/* ------------------------------ tracking ------------------------------ */
async function trackRef(e){
  if(e) e.preventDefault();
  const ref = $('trackInput').value.trim().toUpperCase(); if(!ref) return;
  const box = $('trackResult'); box.innerHTML = '<div class="track-result">Looking up…</div>';
  try {
    const t = await api('/api/' + (ref.startsWith('SOS')?'sos':'reports') + '/track/' + encodeURIComponent(ref));
    const cls = {'PENDING REVIEW':'st-pending','PENDING':'st-pending','APPROVED':'st-approved','DECLINED':'st-declined','ARCHIVED':'st-archived'}[t.status] || 'st-pending';
    box.innerHTML = `<div class="track-result"><div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center"><b style="font-size:20px;color:var(--green-900)">${esc(t.ref)}</b><span class="status-badge ${cls}">${esc(t.status)}</span></div>
      <p style="margin:8px 0;color:var(--ink-soft)">${esc((CATS[t.category]||{}).label)}${t.road?' · '+esc(t.road):''}${t.town?' · '+esc(t.town):''}</p>
      ${t.status==='PENDING REVIEW'?'<div class="under-review"><b>UNDER REVIEW</b> — Your report has been successfully submitted and is awaiting verification by RoadGuard ZW validators.</div>':''}
      ${t.status==='PENDING'?'<div class="under-review">Your SOS request is with the validator desk and awaiting action.</div>':''}
      ${t.decision_reason?`<div class="limit-note"><b>Reason given:</b> ${esc(t.decision_reason)}</div>`:''}
      ${t.type==='report'?`<p style="font-size:13px">${t.public?'✅ Visible on the public map.':'Not visible on the public map.'}</p>`:''}
      <ul class="timeline">${(t.updates||[]).map(u => `<li>${esc(u.message)}<small>${esc(fmt(u.created_at))}</small></li>`).join('')}</ul></div>`;
  } catch(err){ box.innerHTML = `<div class="track-result" style="color:var(--crit)">${esc(err.message)}</div>`; }
}

/* ------------------------------ report form ------------------------------ */
let pickMap, pickMarker, lastRef = null, reporterPosition = null;
const bounds = {lat:[-23, -15], lng:[24, 34]};
function localNow(){ const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0,16); }
function setPick(lat, lng, pan){
  $('f-lat').value = (+lat).toFixed(6); $('f-lng').value = (+lng).toFixed(6);
  $('gpsStatus').textContent = 'Location set: ' + (+lat).toFixed(5) + ', ' + (+lng).toFixed(5);
  if(pickMap){ if(pickMarker) pickMarker.setLatLng([lat,lng]); else pickMarker = L.marker([lat,lng]).addTo(pickMap); if(pan) pickMap.setView([lat,lng], 12); }
}
function openReport(){
  $('repForm').classList.remove('hidden'); $('repDone').classList.add('hidden'); $('repErr').classList.remove('show');
  $('f-when').value = localNow(); $('modalOverlay').classList.add('open');
  if(mapOK){
    if(!pickMap){
      pickMap = L.map('pickMap').setView([-19.0154, 29.1549], 5);
      L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {attribution:'Tiles &copy; Esri', maxZoom:18}).addTo(pickMap);
    }
    setTimeout(() => pickMap.invalidateSize(), 80);
  } else $('pickMap').innerHTML = '<div class="empty">Map unavailable — enter coordinates manually.</div>';
}
function closeReport(){ $('modalOverlay').classList.remove('open'); }
function useMyLocation(){
  if(!navigator.geolocation){ $('gpsStatus').textContent = 'Geolocation is not supported — tap the map instead.'; return; }
  $('gpsStatus').textContent = 'Requesting location…';
  navigator.geolocation.getCurrentPosition(p => { reporterPosition={lat:p.coords.latitude,lng:p.coords.longitude,accuracy:p.coords.accuracy}; setPick(p.coords.latitude, p.coords.longitude, true); },
    () => { $('gpsStatus').textContent = 'Location permission denied — tap the map to drop a pin.'; }, {enableHighAccuracy:true, timeout:12000});
}
$('f-files').addEventListener('change', e => {
  const box = $('previews'); box.innerHTML = '';
  [...e.target.files].slice(0,4).forEach(f => { const im = document.createElement('img'); im.src = URL.createObjectURL(f); im.alt = f.name; box.appendChild(im); });
});
async function submitReport(e){
  e.preventDefault(); const err = $('repErr'); err.classList.remove('show');
  const fail = m => { err.textContent = m; err.classList.add('show'); err.scrollIntoView({block:'nearest'}); };
  const lat = parseFloat($('f-lat').value), lng = parseFloat($('f-lng').value);
  if(!$('f-cat').value) return fail('Choose an incident category.');
  if($('f-road').value.trim().length < 2) return fail('Enter the road or highway name.');
  if($('f-town').value.trim().length < 2) return fail('Enter the nearest town.');
  if(!$('f-prov').value) return fail('Select the province.');
  if(isNaN(lat) || isNaN(lng)) return fail('Set the GPS location: use "Use my location" or tap the map.');
  if(lat<bounds.lat[0]||lat>bounds.lat[1]||lng<bounds.lng[0]||lng>bounds.lng[1]) return fail('Those coordinates are outside Zimbabwe.');
  if($('f-desc').value.trim().length < 10) return fail('Describe the incident (at least 10 characters).');
  const files = [...$('f-files').files];
  if(files.length > 4) return fail('Attach at most 4 photos.');
  if(files.some(f => f.size > 5*1024*1024)) return fail('Each photo must be under 5 MB.');
  const fd = new FormData();
  fd.set('category', $('f-cat').value); fd.set('road', $('f-road').value); fd.set('town', $('f-town').value); fd.set('province', $('f-prov').value);
  fd.set('location', $('f-loc').value); fd.set('lat', lat); fd.set('lng', lng); fd.set('description', $('f-desc').value);
  if(reporterPosition){fd.set('reporter_lat',reporterPosition.lat);fd.set('reporter_lng',reporterPosition.lng);fd.set('gps_accuracy',reporterPosition.accuracy);}
  fd.set('occurred_at', new Date($('f-when').value).toISOString());
  fd.set('reporter_name', $('f-name').value); fd.set('reporter_phone', $('f-phone').value); fd.set('reporter_email', $('f-email').value);
  files.forEach(f => fd.append('evidence', f));
  const btn = $('repSubmit'); btn.disabled = true; btn.textContent = 'Submitting…';
  try {
    const j = await api('/api/reports', {method:'POST', body:fd});
    lastRef = j.ref; $('repRef').textContent = j.ref; $('repMsg').innerHTML = '<b>' + esc(j.message.split(' — ')[0]) + '</b> — ' + esc(j.message.split(' — ').slice(1).join(' — '));
    $('repForm').classList.add('hidden'); $('repDone').classList.remove('hidden'); $('reportForm').reset(); $('previews').innerHTML = '';
    if(pickMarker){ pickMap.removeLayer(pickMarker); pickMarker = null; } reporterPosition=null; $('gpsStatus').textContent = 'or tap the map to drop a pin';
    rememberRef(j.ref, 'report');
  } catch(ex){ fail(ex.message); }
  finally { btn.disabled = false; btn.textContent = 'Submit report'; }
}
function trackFromDone(){ closeReport(); $('trackInput').value = lastRef; trackRef(); location.hash = '#track-section'; $('track-section').scrollIntoView(); }

/* ------------------------------ VOICE-GUIDED SOS ------------------------------ */
let sosCat='other', sosPos=null, sosTimer=null, sosRecognition=null, sosQuestion=0, sosAnswers={description:'',injuries:'',hazards:'',location:''}, sosBusy=false;
const SOS_QUESTIONS=[
  {key:'description',ask:'Tell me briefly what happened. For example, an accident, medical emergency or fire.'},
  {key:'injuries',ask:'Is anyone injured or trapped? If yes, how many people need help? You can say none or unsure.'},
  {key:'hazards',ask:'Is there fire, smoke, leaking fuel or another immediate danger? You can say none or unsure.'},
  {key:'location',ask:'GPS is being checked. If you know the road, nearest town or landmark, tell me.'}
];
function sosShow(n){[1,2,3,4].forEach(i=>$("sosStep"+i).classList.toggle('hidden',i!==n));}
function sosBubble(who,msg){const el=document.createElement('div');el.className=who;el.textContent=msg;$('sosChat').appendChild(el);$('sosChat').scrollTop=$('sosChat').scrollHeight;}
function sosSpeak(msg){try{if(!('speechSynthesis'in window))return;window.speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(msg);u.lang='en-ZW';u.rate=.94;window.speechSynthesis.speak(u);}catch(e){}}
function stopSOSVoice(){try{if(sosRecognition)sosRecognition.abort();}catch(e){}sosRecognition=null;try{if('speechSynthesis'in window)window.speechSynthesis.cancel();}catch(e){}}
function openSOS(){resetSOS();$('sosOverlay').classList.add('open');}
function closeSOS(){clearInterval(sosTimer);stopSOSVoice();$('sosOverlay').classList.remove('open');}
function resetSOS(){clearInterval(sosTimer);stopSOSVoice();sosShow(1);sosPos=null;sosQuestion=0;sosAnswers={description:'',injuries:'',hazards:'',location:''};sosBusy=false;}
function startSOSAssistant(){sosShow(2);$('sosTitle').textContent='RoadGuard SOS Assistant';$('sosChat').innerHTML='';$('sos-answer').value='';$('sosLoc').textContent='Requesting GPS location…';$('sosLoc').className='loc-flag';sosBubble('agent','RoadGuard SOS assistant is ready. I will ask a few short questions. If you are in immediate danger, call emergency services now.');sosSpeak('RoadGuard SOS assistant. Tell me briefly what happened.');
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>{sosPos={lat:p.coords.latitude,lng:p.coords.longitude,accuracy:p.coords.accuracy};$('sosLoc').className='loc-flag ok';$('sosLoc').textContent=`GPS captured: ${sosPos.lat.toFixed(5)}, ${sosPos.lng.toFixed(5)} (±${Math.round(sosPos.accuracy)} m)`;},()=>{$('sosLoc').className='loc-flag warn';$('sosLoc').textContent='GPS unavailable. Please provide a road, town or landmark if known.';},{enableHighAccuracy:true,timeout:10000,maximumAge:15000});}else{$('sosLoc').textContent='GPS is not supported. Please describe your location.';}
  sosAskCurrent();}
function sosAskCurrent(){const q=SOS_QUESTIONS[sosQuestion];if(!q){confirmSOS();return;}sosBubble('agent',q.ask);$('sosVoiceStatus').textContent=`Question ${sosQuestion+1} of ${SOS_QUESTIONS.length}. Speak or type your answer.`;sosSpeak(q.ask);}
function listenSOSAnswer(){const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){$('sosVoiceStatus').textContent='Speech recognition is not available in this browser. Please type your answer.';return;}stopSOSVoice();const rec=new SR();sosRecognition=rec;rec.lang='en-US';rec.interimResults=false;rec.maxAlternatives=1;rec.onstart=()=>$('sosVoiceStatus').textContent='Listening… speak now.';rec.onresult=e=>{$('sos-answer').value=e.results[0][0].transcript; $('sosVoiceStatus').textContent='Speech captured. Select Send answer to continue.';};rec.onerror=()=>$('sosVoiceStatus').textContent='Could not capture speech. Please try again or type your answer.';rec.onend=()=>{sosRecognition=null;};try{rec.start();}catch(e){$('sosVoiceStatus').textContent='Microphone unavailable. Please type your answer.';}}
function submitSOSAnswer(){const val=$('sos-answer').value.trim();if(!val){$('sosVoiceStatus').textContent='Please speak, type an answer or select Skip.';return;}const q=SOS_QUESTIONS[sosQuestion];sosAnswers[q.key]=val;sosBubble('caller',val);$('sos-answer').value='';sosQuestion++;if(sosQuestion>=SOS_QUESTIONS.length){$('sosVoiceStatus').textContent='Details collected. Review and send your SOS request.';sosBubble('agent','Thank you. You can send the emergency request now.');sosSpeak('Thank you. You can send the emergency request now.');}else sosAskCurrent();}
function skipSOSQuestion(){const q=SOS_QUESTIONS[sosQuestion];if(q){sosAnswers[q.key]='Not provided';sosBubble('caller','Skipped');sosQuestion++;}if(sosQuestion>=SOS_QUESTIONS.length){sosBubble('agent','You can send the emergency request now.');$('sosVoiceStatus').textContent='Questions complete. Send when ready.';}else sosAskCurrent();}
function inferSOSCategory(text){const t=text.toLowerCase();if(/fire|burn|smoke|flame/.test(t))return 'fire_rescue';if(/medical|collapsed|unconscious|not breathing|ill|sick|heart|labour|bleed/.test(t))return 'medical';if(/crash|accident|collision|overturn|vehicle hit|kombi hit/.test(t))return 'accident';return 'other';}
function confirmSOS(){if(sosBusy)return;const desc=[sosAnswers.description,sosAnswers.injuries!=='Not provided'?'Injuries/trapped: '+sosAnswers.injuries:'',sosAnswers.hazards!=='Not provided'?'Hazards: '+sosAnswers.hazards:''].filter(Boolean).join('. ').slice(0,1000);if(!desc){$('sosVoiceStatus').textContent='Please answer what happened before sending, or type a short description.';return;}if(!sosPos&&!$('sos-loc').value.trim()&&!sosAnswers.location){$('sosVoiceStatus').textContent='GPS is unavailable. Please enter or say a road, town or landmark before sending.';return;}sosAnswers.description=desc;sosShow(3);let n=3;$('sosNum').textContent=n;$('sosCount').textContent='Preparing secure SOS…';sosTimer=setInterval(()=>{n--;if(n<=0){clearInterval(sosTimer);sendSOS();}else $('sosNum').textContent=n;},1000);}
function cancelSOS(){clearInterval(sosTimer);sosShow(2);}
async function postCompactSOS(body){const raw=new TextEncoder().encode(JSON.stringify(body));if('CompressionStream'in window){try{const cs=new CompressionStream('gzip');const writer=cs.writable.getWriter();writer.write(raw);writer.close();const compressed=new Uint8Array(await new Response(cs.readable).arrayBuffer());if(compressed.length+40<raw.length){const r=await fetch(API_BASE + '/api/sos',{method:'POST',credentials:'include',headers:{'Content-Type':'application/vnd.roadguard.sos+gzip','X-RG-Codec':'gzip-json-v1'},body:compressed});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||`Request failed (${r.status})`);return j;}}catch(e){if(e.message&&/Request failed|Too many|Invalid|Location/.test(e.message))throw e;}}
 return api('/api/sos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
async function sendSOS(){if(sosBusy)return;sosBusy=true;$('sosCount').textContent='Sending to validator desk…';const description=[sosAnswers.description,sosAnswers.injuries&&sosAnswers.injuries!=='Not provided'?'Injuries/trapped: '+sosAnswers.injuries:'',sosAnswers.hazards&&sosAnswers.hazards!=='Not provided'?'Hazards: '+sosAnswers.hazards:''].filter(Boolean).join('. ').slice(0,1000);const body={category:inferSOSCategory(description),description,location:$('sos-loc').value.trim()||sosAnswers.location||'',contact_name:$('sos-name').value.trim(),contact_phone:$('sos-phone').value.trim(),source:'voice_assistant',voice_transcript:true};if(sosPos)Object.assign(body,sosPos);try{const j=await postCompactSOS(body);$('sosRef').textContent=j.ref;$('sosDoneMsg').textContent='Your request has reached the RoadGuard ZW validator desk.'+ (j.gps?' GPS location included.':' Location is based on the description you provided.');$('sosLimit').textContent=j.message||'This request does not directly contact or dispatch emergency services. Contact local emergency services if immediate help is needed.';sosShow(4);rememberRef(j.ref,'sos');sosSpeak('Your request has been sent to the RoadGuard validator desk. Keep your reference number.');}catch(ex){sosShow(2);$('sosVoiceStatus').textContent=ex.message+' Please retry when connected. If you are in immediate danger, contact emergency services directly.';}finally{sosBusy=false;}}

/* ------------------------------ init ------------------------------ */
(async function init(){
  renderFilters(); renderIncidents(); renderAlerts(); initRouteUI();
  api('/api/facilities').then(x=>{publicFacilities=x; if(routeCoords.length)renderAhead();}).catch(()=>{});
  try { const m = await api('/api/meta'); $('f-prov').innerHTML = '<option value="">Select…</option>' + m.provinces.map(p => `<option>${esc(p)}</option>`).join(''); } catch(e){}
  connect(); loadAll();
  if(mapOK && navigator.geolocation) locateMe();
  document.addEventListener('keydown', e => { if(e.key==='Escape'){ closeReport(); closeSOS(); closeDrawer(); } });
})();
