"""End-to-end API tests (TEST 1-9). Starts its own server on a temp DB: python tests/test_api.py"""
import os, sys, json, time, struct, zlib, subprocess, tempfile, threading, requests

PORT = 5077; B = "http://127.0.0.1:%d" % PORT
tmp = tempfile.mkdtemp()
env = dict(os.environ, RG_DB=os.path.join(tmp, "t.db"), RG_UPLOADS=os.path.join(tmp, "up"), PORT=str(PORT),
           RG_ADMIN_PASSWORD="AdminPass-12345", HOST="127.0.0.1")
srv = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "server.py")], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: requests.get(B + "/api/meta", timeout=1); break
    except Exception: time.sleep(0.2)

def png():
    def ch(t, d): c = struct.pack(">I", len(d)) + t + d; return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"\x00\xff\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")

passed = failed = 0
def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print("  PASS", name)
    else: failed += 1; print("  FAIL", name, extra)

def report(cat="accident", road="A5 Harare-Bulawayo", town="Norton", lat="-17.88", lng="30.7", files=True):
    data = dict(category=cat, road=road, town=town, location="5km after Norton", province="Mashonaland West",
                lat=lat, lng=lng, description="Two vehicles collided, one lane blocked.",
                occurred_at="2026-10-30T10:00", reporter_name="Tendai", reporter_phone="+263771234567")
    return requests.post(B + "/api/reports", data=data, files=[("evidence", ("a.png", png(), "image/png"))] if files else None)

try:
    ev = []  # collect validator SSE events
    admin = requests.Session()
    r = admin.post(B + "/api/auth/login", json={"username": "admin", "password": "AdminPass-12345"})
    check("admin login", r.status_code == 200, r.text)

    def listen(sess, url, out):
        try:
            with sess.get(url, stream=True, timeout=30) as resp:
                evn = None
                for line in resp.iter_lines(decode_unicode=True):
                    if line and line.startswith("event:"): evn = line[6:].strip()
                    elif line and line.startswith("data:") and evn: out.append((evn, json.loads(line[5:]))); evn = None
        except Exception: pass
    vev, pev = [], []
    threading.Thread(target=listen, args=(admin, B + "/api/v/stream", vev), daemon=True).start()
    threading.Thread(target=listen, args=(requests.Session(), B + "/api/stream", pev), daemon=True).start()
    time.sleep(1)

    print("TEST 1: report saved as PENDING REVIEW and delivered to validator")
    r = report(); j = r.json(); ref = j.get("ref")
    check("201 + ref", r.status_code == 201 and ref.startswith("RGZ-"), r.text)
    check("UNDER REVIEW message", j["message"].startswith("UNDER REVIEW"))
    lst = admin.get(B + "/api/v/incidents").json()
    check("appears in validator list as PENDING REVIEW", any(x["ref"] == ref and x["status"] == "PENDING REVIEW" and len(x["evidence"]) == 1 for x in lst))
    time.sleep(0.5)
    check("validator got realtime new_report", any(e == "new_report" and d["ref"] == ref for e, d in vev))

    print("TEST 2: not on public map before approval")
    check("map empty of pending", ref not in [x["ref"] for x in requests.get(B + "/api/map/incidents").json()])
    t = requests.get(B + "/api/reports/track/" + ref).json()
    check("tracking says PENDING REVIEW, not public", t["status"] == "PENDING REVIEW" and t["public"] is False)

    print("TEST 3: approve -> public map + realtime")
    r = admin.post(B + "/api/v/incidents/%s/approve" % ref)
    check("approve 200", r.status_code == 200, r.text)
    check("approved on public map", ref in [x["ref"] for x in requests.get(B + "/api/map/incidents").json()])
    pubmap = [x for x in requests.get(B + "/api/map/incidents").json() if x["ref"] == ref][0]
    check("public payload has no reporter data", "reporter_phone" not in pubmap and "reporter_name" not in pubmap)
    time.sleep(0.5)
    check("public got realtime incident_published", any(e == "incident_published" and d["ref"] == ref for e, d in pev))
    h = admin.get(B + "/api/v/history?ref=" + ref).json()
    check("history records validator + time", any(x["action"] == "APPROVED" and x["by"] for x in h))
    check("double decision blocked", admin.post(B + "/api/v/incidents/%s/approve" % ref).status_code == 409)

    print("TEST 4: decline -> hidden + reason to reporter")
    ref2 = report(cat="hazard", town="Gweru").json()["ref"]
    check("decline needs reason", admin.post(B + "/api/v/incidents/%s/decline" % ref2, json={}).status_code == 400)
    admin.post(B + "/api/v/incidents/%s/decline" % ref2, json={"reason": "Duplicate of another report"})
    check("declined hidden from map", ref2 not in [x["ref"] for x in requests.get(B + "/api/map/incidents").json()])
    t = requests.get(B + "/api/reports/track/" + ref2).json()
    check("reporter sees reason", t["status"] == "DECLINED" and "Duplicate" in (t["decision_reason"] or "") and any("Duplicate" in u["message"] for u in t["updates"]))

    print("TEST 5: SOS reaches validator with real data")
    r = requests.post(B + "/api/sos", json=dict(category="medical", lat=-20.15, lng=28.58, accuracy=12, description="Driver unconscious", contact_phone="0771234567", road="A7"))
    sj = r.json(); sref = sj.get("ref")
    check("SOS 201, SOS- ref, CRITICAL", r.status_code == 201 and sref.startswith("SOS-") and sj["priority"] == "CRITICAL", r.text)
    check("SOS says responders NOT contacted", "does NOT contact" in sj["message"])
    s = [x for x in admin.get(B + "/api/v/sos").json() if x["ref"] == sref][0]
    check("SOS data correct", s["category"] == "medical" and s["lat"] == -20.15 and s["lng"] == 28.58 and s["description"] == "Driver unconscious" and s["status"] == "PENDING" and s["gps_granted"])
    time.sleep(0.5)
    check("validator got realtime new_sos", any(e == "new_sos" and d["ref"] == sref for e, d in vev))
    check("SOS not on public map", sref not in json.dumps(requests.get(B + "/api/map/incidents").json()))
    check("SOS without GPS or location rejected", requests.post(B + "/api/sos", json=dict(category="other", description="x")).status_code == 400)
    check("SOS stays pending", requests.get(B + "/api/sos/track/" + sref).json()["status"] == "PENDING")
    admin.post(B + "/api/v/sos/%s/approve" % sref)
    check("SOS approve recorded", requests.get(B + "/api/sos/track/" + sref).json()["status"] == "APPROVED")

    print("TEST 6: alert independent of approval -> public alerts + realtime")
    ref3 = report(cat="hazard", town="Chivhu").json()["ref"]  # still pending
    r = admin.post(B + "/api/v/alerts", json=dict(title="Hazard near Chivhu", description="Slow down, debris on road", location="A5 near Chivhu", category="hazard", severity="warning", expires_in_minutes=120, related_ref=ref3))
    check("alert 201 while report still pending", r.status_code == 201, r.text)
    check("report still PENDING after alert", requests.get(B + "/api/reports/track/" + ref3).json()["status"] == "PENDING REVIEW")
    check("alert in public list", any(a["title"] == "Hazard near Chivhu" for a in requests.get(B + "/api/alerts").json()))
    time.sleep(0.5)
    check("public got realtime alert", any(e == "alert" and d["title"] == "Hazard near Chivhu" for e, d in pev))

    print("TEST 7: multiple reports + analytics")
    for c in ["fire_rescue", "other", "medical"]: report(cat=c)
    a = admin.get(B + "/api/v/analytics").json()["summary"]
    check("totals", a["total_submitted"] == 6 and a["approved"] == 1 and a["declined"] == 1 and a["pending"] == 4, str(a))
    check("sos + alerts counted separately", a["sos_requests"] == 1 and a["active_alerts"] == 1)
    a2 = admin.get(B + "/api/v/analytics?category=medical").json()
    check("category filter", a2["summary"]["total_submitted"] == 1 and a2["by_category"]["medical"] == 1)
    check("status filter", admin.get(B + "/api/v/analytics?status=APPROVED").json()["summary"]["total_submitted"] == 1)

    print("TEST 8: persistence across restart")
    srv.terminate(); srv.wait(); time.sleep(0.5)
    srv = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: requests.get(B + "/api/meta", timeout=1); break
        except Exception: time.sleep(0.2)
    check("approved incident survives", ref in [x["ref"] for x in requests.get(B + "/api/map/incidents").json()])
    check("alert survives", any(a["title"] == "Hazard near Chivhu" for a in requests.get(B + "/api/alerts").json()))
    admin = requests.Session(); admin.post(B + "/api/auth/login", json={"username": "admin", "password": "AdminPass-12345"})
    check("SOS + decline survive", len(admin.get(B + "/api/v/sos").json()) == 1 and requests.get(B + "/api/reports/track/" + ref2).json()["status"] == "DECLINED")

    print("TEST 9: access control & input validation")
    pub = requests.Session()
    for m, p in [("get", "/api/v/incidents"), ("get", "/api/v/sos"), ("post", "/api/v/incidents/%s/approve" % ref3), ("post", "/api/v/alerts"),
                 ("get", "/api/v/analytics"), ("get", "/api/v/stream"), ("get", "/api/v/audit"), ("get", "/api/v/evidence/1")]:
        check("public blocked: %s %s" % (m, p), getattr(pub, m)(B + p).status_code == 401)
    check("fake cookie rejected", requests.get(B + "/api/v/incidents", cookies={"rg_session": "abc"}).status_code == 401)
    admin.post(B + "/api/v/users", json=dict(username="val1", name="Val One", password="ValidatorPass1!", role="validator"))
    v = requests.Session(); v.post(B + "/api/auth/login", json={"username": "val1", "password": "ValidatorPass1!"})
    check("validator can approve", v.post(B + "/api/v/incidents/%s/approve" % ref3).status_code == 200)
    check("validator cannot archive (admin only)", v.post(B + "/api/v/incidents/%s/archive" % ref3).status_code == 403)
    check("validator cannot see audit/users", v.get(B + "/api/v/audit").status_code == 403 and v.get(B + "/api/v/users").status_code == 403)
    li = [x for x in v.get(B + "/api/v/incidents").json() if x["ref"] == ref3][0]
    check("reporter phone hidden from validator role", li["reporter_phone"] is None)
    check("admin sees reporter phone", [x for x in admin.get(B + "/api/v/incidents").json() if x["ref"] == ref3][0]["reporter_phone"] == "+263771234567")
    check("evidence served to validator", v.get(B + "/api/v/evidence/1").headers.get("content-type", "").startswith("image/png"))
    check("bad login", requests.post(B + "/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401)
    check("reject GPS outside Zimbabwe", report(lat="51.5", lng="-0.12").status_code == 400)
    check("reject bad category", requests.post(B + "/api/reports", data=dict(category="x")).status_code == 400)
    check("reject non-image upload", requests.post(B + "/api/reports", data=dict(category="hazard", road="A5 road", town="Norton", lat="-17.8", lng="30.7", description="long enough text", occurred_at="2026-10-30T10:00"), files=[("evidence", ("a.png", b"<script>alert(1)</script>", "image/png"))]).status_code == 400)
    check("archive hides from map (admin)", admin.post(B + "/api/v/incidents/%s/archive" % ref).status_code == 200 and ref not in [x["ref"] for x in requests.get(B + "/api/map/incidents").json()])
    check("admin removes alert", admin.delete(B + "/api/v/alerts/1").status_code == 200 and not requests.get(B + "/api/alerts").json())
finally:
    srv.terminate()
print("\n%d passed, %d failed" % (passed, failed)); sys.exit(1 if failed else 0)
