"""Browser end-to-end test: public site (citizen) <-> validator site, two separate browser contexts.
Leaflet is stubbed ONLY because this sandbox has no internet; the app code under test is unchanged."""
import os, sys, time, struct, zlib, subprocess, tempfile, requests, json
from playwright.sync_api import sync_playwright, expect

PORT = 5078; B = "http://127.0.0.1:%d" % PORT
OUT = os.environ.get("SHOTS", "/home/claude/shots"); os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
env = dict(os.environ, RG_DB=tmp + "/t.db", RG_UPLOADS=tmp + "/up", PORT=str(PORT), RG_ADMIN_PASSWORD="AdminPass-12345", HOST="127.0.0.1")
srv = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(60):
    try: requests.get(B + "/api/meta", timeout=1); break
    except Exception: time.sleep(.2)

def png():
    def ch(t, d): c = struct.pack(">I", len(d)) + t + d; return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + ch(b"IEND", b"")
open(tmp + "/ev.png", "wb").write(png())

LEAFLET_STUB = """
(function(){
  const maps={};
  function Layer(){this.layers=new Set();}
  Layer.prototype.addLayer=function(l){this.layers.add(l);return this};
  Layer.prototype.removeLayer=function(l){this.layers.delete(l);return this};
  Layer.prototype.clearLayers=function(){this.layers.clear();return this};
  Layer.prototype.addTo=function(c){c.layers.add(this);return this};
  function mk(){return new Proxy({}, {get:(t,k)=> k in t ? t[k] : ()=>mk()})}
  function Marker(ll){this.ll=ll;this.popup=null}
  Marker.prototype={addTo(c){c.layers.add(this);this.c=c;return this},bindPopup(h){this.popup=h;return this},on(){return this},openPopup(){return this},
    setLatLng(l){this.ll=l;return this}};
  window.L={
    map(id){const m=new Layer();m.id=id;m.setView=()=>m;m.getZoom=()=>6;m.invalidateSize=()=>m;m.on=(ev,fn)=>{m['on_'+ev]=fn;return m};m.getCenter=()=>({lat:-19,lng:29});maps[id]=m;return m},
    control:{zoom:()=>({addTo:()=>{}})}, tileLayer:()=>({addTo:()=>{}}), layerGroup:()=>{const g=new Layer();return g},
    divIcon:o=>o, marker:(ll,o)=>new Marker(ll), circleMarker:(ll)=>new Marker(ll), __count:(id)=>{const m=maps[id];if(!m)return -1;let n=0;m.layers.forEach(l=>{if(l instanceof Marker)n++;else if(l.layers)n+=[...l.layers].filter(x=>x instanceof Marker).length});return n}, __map:id=>maps[id]
  };
})();
"""
import re
def strip_sri(route):
    resp = route.fetch(); body = re.sub(r' integrity="[^"]*"', '', resp.text())
    route.fulfill(response=resp, body=body)
def stub(ctx):
    ctx.route(re.compile(r"^http://127\.0\.0\.1:%d/(validator/)?(index\.html)?$" % PORT), strip_sri)
    ctx.route("**/unpkg.com/**leaflet.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=LEAFLET_STUB))
    ctx.route("**/unpkg.com/**leaflet.css", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    ctx.route("**/*fonts.g*", lambda r: r.abort()); ctx.route("**/tile.openstreetmap.org/**", lambda r: r.abort())

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond: passed += 1; print("  PASS", name)
    else: failed += 1; print("  FAIL", name)
def markers(page, id): return page.evaluate("id => L.__count(id)", id)

try:
  with sync_playwright() as p:
    br = p.chromium.launch()
    citizen_ctx = br.new_context(viewport={"width": 1366, "height": 900}, geolocation={"latitude": -20.1500, "longitude": 28.5800, "accuracy": 15}, permissions=["geolocation"])
    val_ctx = br.new_context(viewport={"width": 1440, "height": 1000})
    stub(citizen_ctx); stub(val_ctx)
    pub = citizen_ctx.new_page(); val = val_ctx.new_page()
    errs = []
    for pg, n in ((pub, "public"), (val, "validator")): pg.on("pageerror", lambda e, n=n: errs.append((n, str(e))))

    print("Validator login screen + protection")
    val.goto(B + "/validator/"); expect(val.locator("#login")).to_be_visible()
    check("login shown when signed out", val.locator("#app").is_hidden())
    val.fill("#lu", "admin"); val.fill("#lp", "wrong"); val.click(".btn-main"); expect(val.locator("#loginErr")).to_be_visible()
    val.fill("#lp", "AdminPass-12345"); val.click(".btn-main"); expect(val.locator("#app")).to_be_visible()
    expect(val.locator("#liveLbl")).to_have_text("Live")
    pub.goto(B + "/"); expect(pub.locator("#liveLabel")).to_have_text("Live")
    check("public page has no DEMO/EXAMPLE/SAMPLE labels", not any(w in pub.inner_text("body").upper() for w in ["DEMO", "EXAMPLE", "SAMPLE"]))

    print("TEST 1/2: submit report on Website 1 -> appears on Website 2; hidden from public map")
    pub.click("header .btn-primary"); expect(pub.locator("#repForm")).to_be_visible()
    pub.select_option("#f-cat", "accident"); pub.fill("#f-road", "A5 · Harare–Bulawayo"); pub.fill("#f-town", "Norton"); pub.select_option("#f-prov", "Mashonaland West")
    pub.fill("#f-loc", "5 km after Norton turn-off"); pub.fill("#f-lat", "-17.8833"); pub.fill("#f-lng", "30.6167")
    pub.fill("#f-desc", "Stationary truck blocking the left lane after a collision."); pub.set_input_files("#f-files", tmp + "/ev.png")
    pub.fill("#f-name", "Tendai"); pub.fill("#f-phone", "+263771234567")
    pub.click("#repSubmit"); expect(pub.locator("#repDone")).to_be_visible()
    ref1 = pub.inner_text("#repRef").strip(); check("reference generated", ref1.startswith("RGZ-"))
    check("UNDER REVIEW message shown", "UNDER REVIEW" in pub.inner_text("#repMsg"))
    expect(val.locator(".inc", has_text=ref1)).to_be_visible(timeout=5000)   # no reload on validator page
    check("validator sees it live as PENDING REVIEW", "PENDING REVIEW" in val.locator(".inc", has_text=ref1).inner_text())
    check("evidence thumbnail loads for validator", val.locator(".inc", has_text=ref1).locator("img").count() == 1)
    pub.keyboard.press("Escape"); time.sleep(.3)
    check("public map has NO marker before approval", markers(pub, "map") == 0)
    check("public list empty before approval", "No approved incidents" in pub.inner_text("#updatesList"))

    print("TEST 3: approve -> live on public map")
    val.locator(".inc", has_text=ref1).get_by_role("button", name="Approve").click()
    expect(pub.locator("#updatesList .update-card")).to_have_count(1, timeout=5000)
    check("public map has marker after approval", markers(pub, "map") == 1)
    check("approval toast shown to reporter", "APPROVED" in pub.locator("#toasts").inner_text())
    pub.screenshot(path=OUT + "/public_approved.png")

    print("TEST 4: decline -> hidden + reason delivered")
    pub.click("header .btn-primary"); pub.select_option("#f-cat", "hazard"); pub.fill("#f-road", "A3 Harare-Mutare"); pub.fill("#f-town", "Marondera"); pub.select_option("#f-prov", "Mashonaland East")
    pub.fill("#f-lat", "-18.18"); pub.fill("#f-lng", "31.55"); pub.fill("#f-desc", "Pothole cluster near the bridge."); pub.click("#repSubmit"); expect(pub.locator("#repDone")).to_be_visible()
    ref2 = pub.inner_text("#repRef").strip(); pub.keyboard.press("Escape")
    expect(val.locator(".inc", has_text=ref2)).to_be_visible(timeout=5000)
    val.locator(".inc", has_text=ref2).get_by_role("button", name="Decline").click()
    val.click(".m-actions .btn-red"); expect(val.locator("#declineErr")).to_be_visible()     # reason required
    val.fill("#declineReason", "Duplicate of an existing report"); val.click(".m-actions .btn-red")
    expect(pub.locator("#toasts .toast", has_text="DECLINED")).to_be_visible(timeout=5000)
    check("decline reason shown to reporter", "Duplicate of an existing report" in pub.locator("#toasts").inner_text())
    check("declined report not on public map", markers(pub, "map") == 1)

    print("TEST 5: SOS with GPS -> validator Emergency SOS")
    pub.click(".sos-fab", force=True); pub.click("text=🚑 Medical"); expect(pub.locator("#sosLoc.ok")).to_be_visible(timeout=5000)
    check("GPS coordinates captured", "-20.15000" in pub.inner_text("#sosLoc"))
    pub.fill("#sos-desc", "Driver unconscious after collision"); pub.fill("#sos-phone", "0771234567")
    pub.screenshot(path=OUT + "/public_sos_form.png")
    pub.click("text=Send SOS request"); expect(pub.locator("#sosStep3")).to_be_visible()
    pub.click("#sosStep3 .btn-submit"); expect(pub.locator("#sosStep2")).to_be_visible()      # cancel works
    pub.click("text=Send SOS request"); expect(pub.locator("#sosStep4")).to_be_visible(timeout=8000)
    sref = pub.inner_text("#sosRef").strip(); check("SOS reference", sref.startswith("SOS-"))
    check("honest limitation shown", "NOT" in pub.inner_text("#sosLimit").upper() and "112" in pub.inner_text("#sosStep4"))
    row = val.locator(".sos-row", has_text=sref); expect(row).to_be_visible(timeout=5000)
    t = row.inner_text()
    check("SOS has category, GPS, description, CRITICAL, PENDING", "Medical" in t and "-20.15000, 28.58000" in t and "Driver unconscious" in t and "CRITICAL" in t and "PENDING" in t)
    check("SOS shown on validator map, NOT on public map", markers(val, "vmap") >= 1 and markers(pub, "map") == 1)
    val.screenshot(path=OUT + "/validator_main.png", full_page=True)
    val.click(".sos-filters button:has-text('Road accident')"); check("SOS category filter hides medical", sref not in val.inner_text("#sosList"))
    val.click(".sos-filters button.all")

    print("TEST 6: alert from validator -> pop-up + Alerts section on Website 1")
    refp = requests.post(B + "/api/reports", data=dict(category="hazard", road="A5 Harare-Bulawayo", town="Norton", province="Mashonaland West", lat="-17.9", lng="30.7", description="Oil spill across both lanes near Norton.", occurred_at="2026-10-30T09:00")).json()["ref"]
    expect(val.locator(".inc", has_text=refp)).to_be_visible(timeout=5000)
    val.locator(".inc", has_text=refp).get_by_role("button", name="Alert").click()
    val.fill("#al-title", "Accident ahead on A5 Harare–Bulawayo"); val.fill("#al-desc", "Please exercise caution near Norton."); val.click("#alertSend")
    time.sleep(.5); check("alert works while report is still PENDING (independent of approval)", "PENDING REVIEW" in val.locator(".inc", has_text=refp).inner_text())
    expect(pub.locator("#toasts .toast", has_text="Accident ahead")).to_be_visible(timeout=5000)
    check("pop-up appeared on public site without reload", True)
    check("alert saved in public Alerts section", "Accident ahead on A5" in pub.inner_text("#alertList"))
    check("bell badge counts alert", pub.inner_text("#bellBadge").strip() != "0")
    pub.screenshot(path=OUT + "/public_alert_popup.png")
    pub.click("#toasts .toast button:has-text('Dismiss')"); pub.click("#sosStep4 .btn-submit"); pub.click("#bellBtn"); pub.click("#drawerAlerts .x")
    pub.keyboard.press("Escape")
    check("dismissing notification keeps the alert", "Accident ahead on A5" in pub.inner_text("#alertList"))

    print("TEST 7: analytics reflect database")
    ref_extra = []
    for c in ("medical", "fire_rescue", "other"):
        r = requests.post(B + "/api/reports", data=dict(category=c, road="A1 Gweru", town="Gweru", province="Midlands", lat="-19.45", lng="29.8", description="Multiple reports test entry.", occurred_at="2026-10-30T09:00"))
        ref_extra.append(r.json()["ref"])
    val.goto(B + "/validator/#analytics"); time.sleep(1)
    cards = val.inner_text("#sumCards"); 
    check("cards: 6 submitted, 1 approved, 4 pending, 1 declined, 1 SOS, 1 alert", all(x.lower() in cards.replace("\n", " ").lower() for x in ["Total submitted reports 6", "Approved reports 1", "Pending reports 4", "Declined reports 1", "Emergency SOS requests 1", "Active alerts 1"]))
    check("bar graph has 5 bars", val.locator("#barChart rect").count() == 5)
    check("line graph drawn", val.locator("#lineChart polyline").count() == 5)
    val.select_option("#aCat", "medical"); time.sleep(.8)
    check("category filter applies", "total submitted reports 1" in val.inner_text("#sumCards").replace("\n", " ").lower())
    val.select_option("#aCat", ""); 
    val.screenshot(path=OUT + "/validator_analytics.png", full_page=True)
    r = requests.post(B + "/api/reports", data=dict(category="accident", road="A7", town="Bulawayo", province="Bulawayo", lat="-20.1", lng="28.6", description="Live analytics update test.", occurred_at="2026-10-30T09:00")); time.sleep(1.2)
    check("analytics update live when a record arrives", "total submitted reports 7" in val.inner_text("#sumCards").replace("\n", " ").lower())

    print("TEST 8: reload both -> all data persists")
    pub.reload(); val.goto(B + "/validator/#validator"); time.sleep(1.2)
    expect(pub.locator("#updatesList .update-card")).to_have_count(1); check("approved incident persists on public", True)
    check("alert persists on public", "Accident ahead on A5" in pub.inner_text("#alertList"))
    val.click("#incTabs button:has-text('Approved')"); check("validator keeps session; approved report persists", ref1 in val.inner_text("#incList"))
    val.click("#incTabs button:has-text('Declined')"); check("declined persists", ref2 in val.inner_text("#incList"))
    check("SOS persists", sref in val.inner_text("#sosList"))
    pub.fill("#trackInput", ref2); pub.click("#track-section button[type=submit]"); expect(pub.locator("#trackResult")).to_contain_text("DECLINED"); check("tracking by reference works", "Duplicate" in pub.inner_text("#trackResult"))

    print("TEST 9: public cannot access validator functions")
    anon = br.new_context(); a = anon.request
    codes = [a.get(B + "/api/v/incidents").status, a.get(B + "/api/v/sos").status, a.post(B + "/api/v/incidents/%s/approve" % ref_extra[0]).status, a.post(B + "/api/v/alerts", data={}).status]
    check("anonymous gets 401 on validator API %s" % codes, codes == [401] * 4)
    check("public page ships no validator controls", "Approve" not in pub.content().replace("Approved", ""))

    print("TEST 10: responsive layouts")
    for name, w, h in (("mobile", 390, 844), ("tablet", 820, 1100)):
        for who, ctx, url in (("public", citizen_ctx, "/"), ("validator", val_ctx, "/validator/")):
            pg = ctx.new_page(); pg.set_viewport_size({"width": w, "height": h}); pg.goto(B + url); time.sleep(.8)
            over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check("%s %s: no horizontal overflow (%dpx)" % (who, name, over), over <= 1)
            pg.screenshot(path="%s/%s_%s.png" % (OUT, who, name), full_page=(name == "mobile" and who == "public")); pg.close()
    check("no JavaScript errors on either site: %s" % errs[:3], not errs)
    br.close()
finally:
    srv.terminate()
print("\n%d passed, %d failed" % (passed, failed)); sys.exit(1 if failed else 0)
