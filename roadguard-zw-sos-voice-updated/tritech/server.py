"""
RoadGuard ZW - shared backend for BOTH websites.

  Website 1 (public)    -> served at  /
  Website 2 (validator) -> served at  /validator/
  One API, one SQLite database (data/roadguard.db), one real-time channel (SSE).

Run:  pip install -r requirements.txt && python server.py
"""
import os, re, json, time, uuid, queue, secrets, sqlite3, threading, hashlib
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, Response, send_from_directory,
                   abort, g, stream_with_context, redirect)
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from roadguard_sos import IncidentClassifier
    SOS_CLASSIFIER = IncidentClassifier()
except Exception:
    SOS_CLASSIFIER = None

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RG_DB", os.path.join(BASE, "data", "roadguard.db"))
UPLOAD_DIR = os.environ.get("RG_UPLOADS", os.path.join(BASE, "uploads"))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

CATEGORIES = ["accident", "hazard", "medical", "fire_rescue", "other"]
SOS_CATEGORIES = ["accident", "medical", "fire_rescue", "other"]
SEVERITIES = ["info", "warning", "critical"]
PROVINCES = ["Bulawayo", "Harare", "Manicaland", "Mashonaland Central", "Mashonaland East",
             "Mashonaland West", "Masvingo", "Matabeleland North", "Matabeleland South", "Midlands"]
ST_PENDING, ST_APPROVED, ST_DECLINED, ST_ARCHIVED = "PENDING REVIEW", "APPROVED", "DECLINED", "ARCHIVED"
MAX_FILES, MAX_FILE_BYTES = 4, 5 * 1024 * 1024
SESSION_HOURS = 12

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILES * MAX_FILE_BYTES + 512 * 1024

# --------------------------------------------------------------------------- helpers
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS roles(name TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL REFERENCES roles(name), active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
  road TEXT NOT NULL, location TEXT NOT NULL, town TEXT NOT NULL, province TEXT,
  lat REAL, lng REAL, description TEXT NOT NULL, occurred_at TEXT NOT NULL,
  reporter_name TEXT, reporter_phone TEXT, reporter_email TEXT,
  reporter_lat REAL, reporter_lng REAL, gps_accuracy REAL,
  status TEXT NOT NULL DEFAULT 'PENDING REVIEW',
  decided_by INTEGER REFERENCES users(id), decided_at TEXT, decision_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_inc_status ON incidents(status);
CREATE TABLE IF NOT EXISTS emergency_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
  road TEXT, location TEXT, province TEXT, lat REAL, lng REAL, accuracy_m REAL, gps_granted INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL, contact_name TEXT, contact_phone TEXT,
  priority TEXT NOT NULL DEFAULT 'CRITICAL', status TEXT NOT NULL DEFAULT 'PENDING',
  decided_by INTEGER REFERENCES users(id), decided_at TEXT, decision_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, original_name TEXT, mime TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS public_alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL, location TEXT NOT NULL,
  category TEXT NOT NULL, severity TEXT NOT NULL, expires_at TEXT, related_ref TEXT,
  created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, removed_at TEXT);
CREATE TABLE IF NOT EXISTS facilities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, facility_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, facility_type TEXT NOT NULL,
  address TEXT NOT NULL, town TEXT NOT NULL, province TEXT, road TEXT, lat REAL, lng REAL, readiness TEXT NOT NULL DEFAULT 'OFF STANDBY',
  equipment TEXT, main_phone TEXT, emergency_phone TEXT, ambulance_phone TEXT, alternative_phone TEXT,
  verified INTEGER NOT NULL DEFAULT 0, verified_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sos_assignments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sos_ref TEXT NOT NULL REFERENCES emergency_requests(ref), facility_id INTEGER NOT NULL REFERENCES facilities(id),
  instructions TEXT, assigned_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_notif_ref ON notifications(ref);
CREATE TABLE IF NOT EXISTS verification_queries(
 id INTEGER PRIMARY KEY AUTOINCREMENT, incident_ref TEXT NOT NULL REFERENCES incidents(ref),
 question TEXT NOT NULL, radius_km REAL NOT NULL DEFAULT 10, status TEXT NOT NULL DEFAULT 'OPEN',
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS verification_votes(
 id INTEGER PRIMARY KEY AUTOINCREMENT, query_id INTEGER NOT NULL REFERENCES verification_queries(id) ON DELETE CASCADE,
 voter_key TEXT NOT NULL, witnessed INTEGER NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(query_id,voter_key));

CREATE TABLE IF NOT EXISTS validation_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, subject_type TEXT NOT NULL, subject_ref TEXT NOT NULL, action TEXT NOT NULL,
  from_status TEXT, to_status TEXT, reason TEXT, user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT NOT NULL, detail TEXT, ip TEXT, created_at TEXT NOT NULL);
"""

def init_db():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    # Additive migrations for an existing RoadGuard database; preserve all rows.
    alert_cols = {r[1] for r in c.execute("PRAGMA table_info(public_alerts)")}
    if "source" not in alert_cols: c.execute("ALTER TABLE public_alerts ADD COLUMN source TEXT NOT NULL DEFAULT 'RoadGuard validator'")
    existing = {r[1] for r in c.execute("PRAGMA table_info(incidents)")}
    for col, typ in (("reporter_lat", "REAL"), ("reporter_lng", "REAL"), ("gps_accuracy", "REAL")):
        if col not in existing: c.execute("ALTER TABLE incidents ADD COLUMN %s %s" % (col, typ))
    c.executemany("INSERT OR IGNORE INTO roles VALUES(?,?)", [
        ("validator", "Reviews reports, approves/declines, sends alerts"),
        ("admin", "Everything a validator can do + archive, remove alerts, manage validators")])
    if not c.execute("SELECT 1 FROM users").fetchone():
        pw = os.environ.get("RG_ADMIN_PASSWORD")
        generated = False
        if not pw:
            pw, generated = secrets.token_urlsafe(9), True
        c.execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                  ("admin", "RoadGuard Administrator", generate_password_hash(pw), "admin", now()))
        c.commit()
        print("=" * 64)
        print(" First start: created admin account")
        print("   username: admin")
        print("   password: %s %s" % (pw, "(generated - change it / set RG_ADMIN_PASSWORD)" if generated else ""))
        print("=" * 64)
    c.commit(); c.close()

# --------------------------------------------------------------------------- realtime (SSE)
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = []
        self.event_id = 0
        self.history = []
    def _allowed(self, scope, refs, event_scope, ref):
        return ((event_scope == "public" and scope == "public") or
                (event_scope == "validator" and scope == "validator") or
                (event_scope == "ref" and scope == "public" and ref in refs))
    def subscribe(self, scope, refs=None, last_id=0):
        q = queue.Queue(maxsize=200); refs = refs or set()
        with self.lock:
            for eid, ev, data, event_scope, ref in self.history:
                if eid > last_id and self._allowed(scope, refs, event_scope, ref):
                    try: q.put_nowait((eid, ev, data))
                    except queue.Full: break
            self.subs.append((q, scope, refs))
        return q
    def unsubscribe(self, q):
        with self.lock: self.subs = [x for x in self.subs if x[0] is not q]
    def publish(self, scope, event, data, ref=None):
        with self.lock:
            self.event_id += 1
            msg = (self.event_id, event, json.dumps(data), scope, ref)
            self.history.append(msg)
            if len(self.history) > 500: self.history = self.history[-500:]
            for q, sc, refs in self.subs:
                if self._allowed(sc, refs, scope, ref):
                    try: q.put_nowait(msg[:3])
                    except queue.Full: pass

hub = Hub()

def sse_response(q):
    @stream_with_context
    def gen():
        yield "retry: 2000\n\n"
        try:
            while True:
                try:
                    eid, ev, data = q.get(timeout=15)
                    yield "id: %s\nevent: %s\ndata: %s\n\n" % (eid, ev, data)
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(q)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# --------------------------------------------------------------------------- auth
def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()

def audit(user_id, action, detail=""):
    db().execute("INSERT INTO audit_logs(user_id,action,detail,ip,created_at) VALUES(?,?,?,?,?)",
                 (user_id, action, detail[:500], client_ip(), now()))

def hash_token(t): return hashlib.sha256(t.encode()).hexdigest()

def current_user():
    tok = request.cookies.get("rg_session") or ""
    if not tok: return None
    r = db().execute("SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id "
                     "WHERE s.token_hash=? AND u.active=1", (hash_token(tok),)).fetchone()
    if not r or r["expires_at"] < now(): return None
    return r

def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            u = current_user()
            if not u: return jsonify(error="Authentication required"), 401
            if u["role"] not in roles: return jsonify(error="Insufficient permissions"), 403
            g.user = u
            return fn(*a, **k)
        return w
    return deco
validator_only = require_role("validator", "admin")
admin_only = require_role("admin")

# simple in-memory rate limiter
_rl = {}
def rate_limit(key, limit, window):
    t = time.time(); hits = [x for x in _rl.get(key, []) if t - x < window]
    if len(hits) >= limit: _rl[key] = hits; return False
    hits.append(t); _rl[key] = hits; return True

@app.after_request
def secure_headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    allowed_origin = "https://road-guard-safety.vercel.app"
    if request.headers.get("Origin") == allowed_origin:
        r.headers["Access-Control-Allow-Origin"] = allowed_origin
        r.headers["Access-Control-Allow-Credentials"] = "true"
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, X-RG-Codec, Last-Event-ID"
        r.headers["Vary"] = "Origin"
    return r

# --------------------------------------------------------------------------- validation helpers
def clean(s, n):
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", (s or "").strip())[:n]

def parse_coords(form):
    lat, lng = form.get("lat", "").strip(), form.get("lng", "").strip()
    if not lat and not lng: return None, None
    try: la, lo = float(lat), float(lng)
    except ValueError: raise ValueError("GPS coordinates must be numbers")
    if not (-91 < la < 91 and -181 < lo < 181): raise ValueError("GPS coordinates out of range")
    if not (-23 <= la <= -15 and 24 <= lo <= 34):
        raise ValueError("GPS coordinates are outside Zimbabwe")
    return la, lo

def new_ref(prefix, table):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        ref = "%s-%s-%s" % (prefix, datetime.now().year, "".join(secrets.choice(alphabet) for _ in range(6)))
        if not db().execute("SELECT 1 FROM %s WHERE ref=?" % table, (ref,)).fetchone(): return ref

SIGS = [(b"\xff\xd8\xff", "image/jpeg", "jpg"), (b"\x89PNG\r\n\x1a\n", "image/png", "png")]
def sniff_image(data):
    for sig, mime, ext in SIGS:
        if data.startswith(sig): return mime, ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP": return "image/webp", "webp"
    return None, None

def pub_incident(r):
    return {"ref": r["ref"], "category": r["category"], "road": r["road"], "location": r["location"],
            "town": r["town"], "province": r["province"], "lat": r["lat"], "lng": r["lng"],
            "description": r["description"], "occurred_at": r["occurred_at"], "approved_at": r["decided_at"]}

def val_incident(r, role):
    ev = db().execute("SELECT id, original_name, mime, size FROM evidence WHERE incident_id=?", (r["id"],)).fetchall()
    d = {k: r[k] for k in ["ref", "category", "road", "location", "town", "province", "lat", "lng",
         "description", "occurred_at", "status", "decided_at", "decision_reason", "created_at", "reporter_name", "reporter_lat", "reporter_lng", "gps_accuracy"]}
    d["reporter_phone"] = r["reporter_phone"] if role == "admin" else None   # contact details: admin only
    d["reporter_email"] = r["reporter_email"] if role == "admin" else None
    d["decided_by"] = _uname(r["decided_by"])
    d["evidence"] = [{"id": e["id"], "name": e["original_name"], "mime": e["mime"], "size": e["size"],
                      "url": "/api/v/evidence/%d" % e["id"]} for e in ev]
    return d

def _uname(uid):
    if not uid: return None
    r = db().execute("SELECT full_name FROM users WHERE id=?", (uid,)).fetchone()
    return r["full_name"] if r else None

def val_sos(r):
    d = {k: r[k] for k in ["ref", "category", "road", "location", "province", "lat", "lng", "accuracy_m",
         "description", "contact_name", "contact_phone", "priority", "status", "decided_at",
         "decision_reason", "created_at"]}
    d["gps_granted"] = bool(r["gps_granted"]); d["decided_by"] = _uname(r["decided_by"])
    d["assessment"] = assess_sos(r["category"], r["description"])
    a = db().execute("SELECT f.facility_id,f.name,f.address,f.main_phone,f.emergency_phone,sa.instructions,sa.created_at FROM sos_assignments sa JOIN facilities f ON f.id=sa.facility_id WHERE sa.sos_ref=? ORDER BY sa.id DESC LIMIT 1", (r["ref"],)).fetchone()
    d["assignment"] = dict(a) if a else None
    return d

def pub_alert(r):
    return {"id": r["id"], "title": r["title"], "description": r["description"], "location": r["location"],
            "category": r["category"], "severity": r["severity"], "expires_at": r["expires_at"],
            "created_at": r["created_at"], "source": (r["source"] if "source" in r.keys() else "RoadGuard validator")}

def history(subject_type, ref, action, frm, to, reason, uid):
    db().execute("INSERT INTO validation_history(subject_type,subject_ref,action,from_status,to_status,reason,user_id,created_at)"
                 " VALUES(?,?,?,?,?,?,?,?)", (subject_type, ref, action, frm, to, reason, uid, now()))

def notify(ref, kind, message):
    db().execute("INSERT INTO notifications(ref,kind,message,created_at) VALUES(?,?,?,?)", (ref, kind, message, now()))

# =========================================================================== PUBLIC API (Website 1)
@app.post("/api/reports")
def submit_report():
    if not rate_limit("rep:" + client_ip(), 10, 600): return jsonify(error="Too many submissions. Try again later."), 429
    f = request.form
    category = f.get("category", "")
    if category not in CATEGORIES: return jsonify(error="Choose a valid incident category"), 400
    road, town = clean(f.get("road"), 120), clean(f.get("town"), 120)
    location, desc = clean(f.get("location"), 200), clean(f.get("description"), 2000)
    province = f.get("province", "")
    if province and province not in PROVINCES: return jsonify(error="Invalid province"), 400
    if len(road) < 2: return jsonify(error="Road or highway name is required"), 400
    if len(town) < 2: return jsonify(error="Nearest town is required"), 400
    if len(desc) < 10: return jsonify(error="Please describe the incident (at least 10 characters)"), 400
    try: lat, lng = parse_coords(f)
    except ValueError as e: return jsonify(error=str(e)), 400
    if lat is None: return jsonify(error="GPS coordinates are required. Use 'Use my location' or tap the map."), 400
    rep_lat = rep_lng = rep_acc = None
    try:
        if f.get("reporter_lat") and f.get("reporter_lng"):
            rep_lat, rep_lng = float(f.get("reporter_lat")), float(f.get("reporter_lng"))
            if not (-23 <= rep_lat <= -15 and 24 <= rep_lng <= 34): raise ValueError()
        if f.get("gps_accuracy"): rep_acc = max(0, min(100000, float(f.get("gps_accuracy"))))
    except (TypeError, ValueError): return jsonify(error="Reporter GPS details are invalid."), 400
    try:
        occ = datetime.fromisoformat(f.get("occurred_at", ""))
        if occ.tzinfo is None: occ = occ.replace(tzinfo=timezone.utc)
    except ValueError: return jsonify(error="Enter the date and time of the incident"), 400
    if occ > datetime.now(timezone.utc) + timedelta(hours=14): return jsonify(error="Incident time cannot be in the future"), 400
    email = clean(f.get("reporter_email"), 120)
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email): return jsonify(error="Invalid email address"), 400
    phone = clean(f.get("reporter_phone"), 30)
    if phone and not re.match(r"^[0-9+()\-\s]{7,30}$", phone): return jsonify(error="Invalid phone number"), 400

    files = [x for x in request.files.getlist("evidence") if x and x.filename]
    if len(files) > MAX_FILES: return jsonify(error="At most %d photos" % MAX_FILES), 400
    saved = []
    for x in files:
        data = x.read()
        if len(data) > MAX_FILE_BYTES: return jsonify(error="Each photo must be under 5 MB"), 400
        mime, ext = sniff_image(data)
        if not mime: return jsonify(error="Only JPEG, PNG or WebP photos are accepted"), 400
        saved.append((data, mime, ext, clean(x.filename, 120)))

    d = db(); ref = new_ref("RGZ", "incidents"); ts = now()
    written = []
    try:
        with d:  # single transaction: report + evidence + history
            cur = d.execute("INSERT INTO incidents(ref,category,road,location,town,province,lat,lng,description,occurred_at,"
                            "reporter_name,reporter_phone,reporter_email,reporter_lat,reporter_lng,gps_accuracy,status,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (ref, category, road, location or town, town, province or None, lat, lng, desc, occ.isoformat(),
                             clean(f.get("reporter_name"), 80), phone, email,
                             rep_lat, rep_lng, rep_acc, ST_PENDING, ts, ts))
            iid = cur.lastrowid
            for data, mime, ext, orig in saved:
                fn = "%s.%s" % (uuid.uuid4().hex, ext)
                with open(os.path.join(UPLOAD_DIR, fn), "wb") as fh: fh.write(data)
                written.append(fn)
                d.execute("INSERT INTO evidence(incident_id,filename,original_name,mime,size,created_at) VALUES(?,?,?,?,?,?)",
                          (iid, fn, orig, mime, len(data), ts))
            history("incident", ref, "SUBMITTED", None, ST_PENDING, None, None)
            notify(ref, "submitted", "Report received and awaiting verification by RoadGuard ZW validators.")
    except Exception:
        for fn in written:
            try: os.remove(os.path.join(UPLOAD_DIR, fn))
            except OSError: pass
        raise
    row = d.execute("SELECT * FROM incidents WHERE ref=?", (ref,)).fetchone()
    hub.publish("validator", "new_report", val_incident(row, "validator"))
    hub.publish("validator", "stats", {})
    return jsonify(ref=ref, status=ST_PENDING,
                   message="UNDER REVIEW — Your report has been successfully submitted and is awaiting verification by RoadGuard ZW validators."), 201

@app.get("/api/reports/track/<ref>")
def track_report(ref):
    r = db().execute("SELECT * FROM incidents WHERE ref=?", (ref.upper().strip(),)).fetchone()
    if not r: return jsonify(error="No report found with that reference number"), 404
    notes = db().execute("SELECT kind,message,created_at FROM notifications WHERE ref=? ORDER BY id", (r["ref"],)).fetchall()
    return jsonify(ref=r["ref"], type="report", category=r["category"], road=r["road"], town=r["town"], status=r["status"],
                   public=r["status"] == ST_APPROVED, decision_reason=r["decision_reason"] if r["status"] == ST_DECLINED else None,
                   submitted_at=r["created_at"], decided_at=r["decided_at"], updates=[dict(n) for n in notes])

@app.get("/api/map/incidents")
def map_incidents():
    # ONLY approved incidents are ever returned to the public.
    rows = db().execute("SELECT * FROM incidents WHERE status=? AND category IN ('accident','hazard','other') ORDER BY decided_at DESC LIMIT 1000", (ST_APPROVED,)).fetchall()
    return jsonify([pub_incident(r) for r in rows])

@app.post("/api/sos")
def submit_sos():
    if not rate_limit("sos:" + client_ip(), 6, 600): return jsonify(error="Too many SOS requests from this connection."), 429
    # Low-bandwidth SOS transport: gzip-compressed JSON is decoded here into
    # the same canonical object used by the existing validator workflow.
    if request.mimetype == "application/vnd.roadguard.sos+gzip":
        import gzip
        try:
            raw = gzip.decompress(request.get_data(cache=False))
            if len(raw) > 16_384:
                return jsonify(error="SOS payload is too large."), 413
            j = json.loads(raw.decode("utf-8"))
            if not isinstance(j, dict):
                return jsonify(error="Invalid SOS payload."), 400
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return jsonify(error="Could not decode compressed SOS request."), 400
    else:
        j = request.get_json(silent=True) or {}
    cat = j.get("category")
    if cat not in SOS_CATEGORIES: return jsonify(error="Invalid emergency category"), 400
    desc = clean(j.get("description"), 1000) or "No further details provided."
    # Lightweight Naive Bayes assistant classification. This is advisory; the
    # validator still reviews every SOS and the citizen's transcript is retained.
    ai_prediction = None
    if SOS_CLASSIFIER is not None:
        predicted, confidence = SOS_CLASSIFIER.predict(desc)
        ai_prediction = {"label": predicted, "confidence": round(float(confidence), 3)}
        mapped = {"crash": "accident", "fire": "fire_rescue", "medical": "medical"}.get(predicted)
        if cat == "other" and mapped and confidence >= 0.62:
            cat = mapped
    lat = lng = acc = None
    granted = 0
    if j.get("lat") is not None and j.get("lng") is not None:
        try: lat, lng = float(j["lat"]), float(j["lng"])
        except (TypeError, ValueError): return jsonify(error="Invalid GPS coordinates"), 400
        if not (-23 <= lat <= -15 and 24 <= lng <= 34): return jsonify(error="GPS coordinates are outside Zimbabwe"), 400
        granted = 1
        try: acc = float(j["accuracy"]) if j.get("accuracy") is not None else None
        except (TypeError, ValueError): acc = None
    location = clean(j.get("location"), 200)
    phone = clean(j.get("contact_phone"), 30)
    if phone and not re.match(r"^[0-9+()\-\s]{7,30}$", phone): return jsonify(error="Invalid phone number"), 400
    if not granted and not location: return jsonify(error="Location permission was not granted — please describe where you are."), 400
    prov = j.get("province") if j.get("province") in PROVINCES else None
    d = db(); ref = new_ref("SOS", "emergency_requests"); ts = now()
    with d:
        d.execute("INSERT INTO emergency_requests(ref,category,road,location,province,lat,lng,accuracy_m,gps_granted,description,"
                  "contact_name,contact_phone,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ref, cat, clean(j.get("road"), 120), location, prov, lat, lng, acc, granted, desc,
                   clean(j.get("contact_name"), 80), phone, "CRITICAL", "PENDING", ts, ts))
        history("sos", ref, "SUBMITTED", None, "PENDING", None, None)
        notify(ref, "submitted", "SOS request received by the RoadGuard ZW validator desk and awaiting action.")
    row = d.execute("SELECT * FROM emergency_requests WHERE ref=?", (ref,)).fetchone()
    hub.publish("validator", "new_sos", val_sos(row)); hub.publish("validator", "stats", {})
    return jsonify(ref=ref, status="PENDING", priority="CRITICAL", assessment=assess_sos(cat,desc),
                   ai_classification=ai_prediction, gps=bool(granted),
                   message="Your SOS request was submitted to the RoadGuard ZW validator desk. "
                           "This does NOT contact ambulance, police or fire services — if you are in danger call 112, 995 or 994 now."), 201

@app.get("/api/sos/track/<ref>")
def track_sos(ref):
    r = db().execute("SELECT * FROM emergency_requests WHERE ref=?", (ref.upper().strip(),)).fetchone()
    if not r: return jsonify(error="No SOS request found with that reference"), 404
    notes = db().execute("SELECT kind,message,created_at FROM notifications WHERE ref=? ORDER BY id", (r["ref"],)).fetchall()
    return jsonify(ref=r["ref"], type="sos", category=r["category"], status=r["status"], priority=r["priority"],
                   decision_reason=r["decision_reason"] if r["status"] == "DECLINED" else None,
                   submitted_at=r["created_at"], decided_at=r["decided_at"], updates=[dict(n) for n in notes])

def active_alerts_rows():
    return db().execute("SELECT * FROM public_alerts WHERE removed_at IS NULL AND (expires_at IS NULL OR expires_at>?) "
                        "ORDER BY id DESC LIMIT 100", (now(),)).fetchall()

@app.get("/api/alerts")
def list_alerts():
    return jsonify([pub_alert(r) for r in active_alerts_rows()])

@app.get("/api/stream")
def public_stream():
    refs = set(x.upper() for x in request.args.get("refs", "").split(",") if x)
    try: last_id = int(request.headers.get("Last-Event-ID")) if request.headers.get("Last-Event-ID") else hub.event_id
    except ValueError: last_id = hub.event_id
    return sse_response(hub.subscribe("public", refs, last_id))

@app.get("/api/meta")
def meta():
    return jsonify(provinces=PROVINCES, categories=CATEGORIES)

# =========================================================================== AUTH
@app.post("/api/auth/login")
def login():
    if not rate_limit("login:" + client_ip(), 8, 300): return jsonify(error="Too many attempts. Wait a few minutes."), 429
    j = request.get_json(silent=True) or {}
    u = db().execute("SELECT * FROM users WHERE username=? AND active=1", (clean(j.get("username"), 60).lower(),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], j.get("password") or ""):
        audit(None, "LOGIN_FAILED", clean(j.get("username"), 60)); db().commit()
        return jsonify(error="Invalid username or password"), 401
    tok = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    with db():
        db().execute("DELETE FROM sessions WHERE expires_at<?", (now(),))
        db().execute("INSERT INTO sessions VALUES(?,?,?)", (hash_token(tok), u["id"], exp))
        audit(u["id"], "LOGIN", "")
    resp = jsonify(username=u["username"], name=u["full_name"], role=u["role"])
    resp.set_cookie(
        "rg_session",
        tok,
        httponly=True,
        samesite="None",
        secure=True,
        max_age=SESSION_HOURS * 3600
    )
    return resp

@app.post("/api/auth/logout")
def logout():
    tok = request.cookies.get("rg_session")
    if tok:
        with db(): db().execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(tok),))
    resp = jsonify(ok=True)
    resp.delete_cookie(
        "rg_session",
        path="/",
        samesite="None",
        secure=True
    )
    return resp

@app.get("/api/auth/me")
def me():
    u = current_user()
    if not u: return jsonify(error="Not signed in"), 401
    return jsonify(username=u["username"], name=u["full_name"], role=u["role"])

# =========================================================================== VALIDATOR API (Website 2)
@app.get("/api/v/stream")
@validator_only
def v_stream():
    try: last_id = int(request.headers.get("Last-Event-ID")) if request.headers.get("Last-Event-ID") else hub.event_id
    except ValueError: last_id = hub.event_id
    return sse_response(hub.subscribe("validator", last_id=last_id))

@app.get("/api/v/incidents")
@validator_only
def v_incidents():
    st = request.args.get("status")
    q, a = "SELECT * FROM incidents", []
    if st: q += " WHERE status=?"; a.append(st)
    rows = db().execute(q + " ORDER BY id DESC LIMIT 1000", a).fetchall()
    return jsonify([val_incident(r, g.user["role"]) for r in rows])

@app.get("/api/v/sos")
@validator_only
def v_sos():
    rows = db().execute("SELECT * FROM emergency_requests ORDER BY (status='PENDING') DESC, id DESC LIMIT 500").fetchall()
    return jsonify([val_sos(r) for r in rows])

@app.get("/api/v/evidence/<int:eid>")
@validator_only
def v_evidence(eid):
    e = db().execute("SELECT * FROM evidence WHERE id=?", (eid,)).fetchone()
    if not e: abort(404)
    return send_from_directory(UPLOAD_DIR, e["filename"], mimetype=e["mime"])

def decide(table, subtype, ref, to_status, reason, allowed_from):
    d = db(); u = g.user; ts = now()
    with d:
        row = d.execute("SELECT * FROM %s WHERE ref=?" % table, (ref,)).fetchone()
        if not row: return None, ("Not found", 404)
        if row["status"] not in allowed_from:
            return None, ("Already %s — cannot change to %s" % (row["status"], to_status), 409)
        cur = d.execute("UPDATE %s SET status=?,decided_by=?,decided_at=?,decision_reason=?,updated_at=? "
                        "WHERE ref=? AND status=?" % table, (to_status, u["id"], ts, reason, ts, ref, row["status"]))
        if cur.rowcount != 1: return None, ("Record changed by another validator", 409)
        history(subtype, ref, to_status, row["status"], to_status, reason, u["id"])
        audit(u["id"], "%s_%s" % (subtype.upper(), to_status.replace(" ", "_")), ref)
    return d.execute("SELECT * FROM %s WHERE ref=?" % table, (ref,)).fetchone(), None

def broadcast_incident_change(row, was_public):
    pub = row["status"] == ST_APPROVED and row["category"] in ("accident", "hazard", "other")
    hub.publish("validator", "incident_updated", val_incident(row, "validator"))
    hub.publish("validator", "stats", {})
    if pub: hub.publish("public", "incident_published", pub_incident(row))
    elif was_public: hub.publish("public", "incident_removed", {"ref": row["ref"]})
    hub.publish("ref", "status", {"ref": row["ref"], "status": row["status"],
                                  "reason": row["decision_reason"] if row["status"] == ST_DECLINED else None}, ref=row["ref"])

@app.post("/api/v/incidents/<ref>/approve")
@validator_only
def v_approve(ref):
    row, err = decide("incidents", "incident", ref, ST_APPROVED, None, (ST_PENDING, ST_DECLINED, ST_ARCHIVED))
    if err: return jsonify(error=err[0]), err[1]
    with db(): notify(ref, "approved", "Your report was approved by a validator and is now visible on the public map.")
    broadcast_incident_change(row, False)
    return jsonify(val_incident(row, g.user["role"]))

@app.post("/api/v/incidents/<ref>/decline")
@validator_only
def v_decline(ref):
    reason = clean((request.get_json(silent=True) or {}).get("reason"), 500)
    if len(reason) < 3: return jsonify(error="A reason is required to decline"), 400
    prev = db().execute("SELECT status FROM incidents WHERE ref=?", (ref,)).fetchone()
    row, err = decide("incidents", "incident", ref, ST_DECLINED, reason, (ST_PENDING, ST_APPROVED, ST_ARCHIVED))
    if err: return jsonify(error=err[0]), err[1]
    with db(): notify(ref, "declined", "Your report was declined. Reason: " + reason)
    broadcast_incident_change(row, prev and prev["status"] == ST_APPROVED)
    return jsonify(val_incident(row, g.user["role"]))

@app.post("/api/v/incidents/<ref>/archive")
@admin_only
def v_archive(ref):
    prev = db().execute("SELECT status FROM incidents WHERE ref=?", (ref,)).fetchone()
    row, err = decide("incidents", "incident", ref, ST_ARCHIVED, "Archived by administrator", (ST_APPROVED, ST_DECLINED, ST_PENDING))
    if err: return jsonify(error=err[0]), err[1]
    with db(): notify(ref, "archived", "This report has been archived and is no longer public.")
    broadcast_incident_change(row, prev and prev["status"] == ST_APPROVED)
    return jsonify(val_incident(row, g.user["role"]))

@app.post("/api/v/sos/<ref>/approve")
@validator_only
def v_sos_approve(ref):
    row, err = decide("emergency_requests", "sos", ref, "APPROVED", None, ("PENDING",))
    if err: return jsonify(error=err[0]), err[1]
    with db(): notify(ref, "approved", "Your SOS request was validated by a RoadGuard ZW validator.")
    hub.publish("validator", "sos_updated", val_sos(row)); hub.publish("validator", "stats", {})
    hub.publish("ref", "status", {"ref": ref, "status": "APPROVED", "reason": None}, ref=ref)
    return jsonify(val_sos(row))

@app.post("/api/v/sos/<ref>/decline")
@validator_only
def v_sos_decline(ref):
    reason = clean((request.get_json(silent=True) or {}).get("reason"), 500)
    if len(reason) < 3: return jsonify(error="A reason is required to decline"), 400
    row, err = decide("emergency_requests", "sos", ref, "DECLINED", reason, ("PENDING",))
    if err: return jsonify(error=err[0]), err[1]
    with db(): notify(ref, "declined", "Your SOS request was declined. Reason: " + reason)
    hub.publish("validator", "sos_updated", val_sos(row)); hub.publish("validator", "stats", {})
    hub.publish("ref", "status", {"ref": ref, "status": "DECLINED", "reason": reason}, ref=ref)
    return jsonify(val_sos(row))
@app.post("/api/v/sos/<ref>/archive")
@admin_only
def v_sos_archive(ref):
    row, err = decide("emergency_requests", "sos", ref, "ARCHIVED", "Archived by administrator", ("PENDING", "APPROVED", "DECLINED"))
    if err: return jsonify(error=err[0]), err[1]
    with db(): notify(ref, "archived", "This SOS request has been archived.")
    hub.publish("validator", "sos_updated", val_sos(row)); hub.publish("validator", "stats", {})
    hub.publish("ref", "status", {"ref": ref, "status": "ARCHIVED", "reason": None}, ref=ref)
    return jsonify(val_sos(row))
@app.post("/api/v/alerts")
@validator_only
def v_create_alert():
    """Works independently of approval: may reference a report/SOS or stand alone."""
    j = request.get_json(silent=True) or {}
    title, desc, loc = clean(j.get("title"), 140), clean(j.get("description"), 1000), clean(j.get("location"), 200)
    cat, sev = j.get("category", "other"), j.get("severity")
    if len(title) < 4: return jsonify(error="Alert title is required"), 400
    if len(desc) < 5: return jsonify(error="Alert description is required"), 400
    if len(loc) < 2: return jsonify(error="Affected road or location is required"), 400
    if cat not in CATEGORIES: return jsonify(error="Invalid category"), 400
    if sev not in SEVERITIES: return jsonify(error="Invalid severity"), 400
    exp = None
    if j.get("expires_in_minutes"):
        try: m = int(j["expires_in_minutes"]); assert 5 <= m <= 60 * 24 * 14
        except Exception: return jsonify(error="Expiry must be between 5 minutes and 14 days"), 400
        exp = (datetime.now(timezone.utc) + timedelta(minutes=m)).isoformat(timespec="seconds")
    rel = clean(j.get("related_ref"), 40).upper() or None
    d = db()
    with d:
        cur = d.execute("INSERT INTO public_alerts(title,description,location,category,severity,expires_at,related_ref,created_by,created_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?)", (title, desc, loc, cat, sev, exp, rel, g.user["id"], now()))
        history("alert", rel or "-", "ALERT_PUBLISHED", None, None, title, g.user["id"])
        audit(g.user["id"], "ALERT_PUBLISHED", "%s | %s" % (title, rel or "standalone"))
    row = d.execute("SELECT * FROM public_alerts WHERE id=?", (cur.lastrowid,)).fetchone()
    hub.publish("public", "alert", pub_alert(row))
    hub.publish("validator", "alert", dict(pub_alert(row), related_ref=rel)); hub.publish("validator", "stats", {})
    return jsonify(pub_alert(row)), 201

@app.get("/api/v/alerts")
@validator_only
def v_alerts():
    rows = db().execute("SELECT a.*, u.full_name AS by FROM public_alerts a JOIN users u ON u.id=a.created_by ORDER BY a.id DESC LIMIT 200").fetchall()
    out = []
    for r in rows:
        x = pub_alert(r); x.update(related_ref=r["related_ref"], created_by=r["by"], removed=bool(r["removed_at"]))
        x["active"] = not r["removed_at"] and (not r["expires_at"] or r["expires_at"] > now()); out.append(x)
    return jsonify(out)

@app.delete("/api/v/alerts/<int:aid>")
@admin_only
def v_remove_alert(aid):
    with db():
        c = db().execute("UPDATE public_alerts SET removed_at=? WHERE id=? AND removed_at IS NULL", (now(), aid))
        audit(g.user["id"], "ALERT_REMOVED", str(aid))
    if not c.rowcount: return jsonify(error="Not found"), 404
    hub.publish("public", "alert_removed", {"id": aid}); hub.publish("validator", "stats", {})
    return jsonify(ok=True)

@app.get("/api/v/history")
@validator_only
def v_history():
    ref = request.args.get("ref")
    q = ("SELECT h.*, u.full_name AS who FROM validation_history h LEFT JOIN users u ON u.id=h.user_id "
         + ("WHERE h.subject_ref=? " if ref else "") + "ORDER BY h.id DESC LIMIT 200")
    rows = db().execute(q, (ref,) if ref else ()).fetchall()
    return jsonify([{"type": r["subject_type"], "ref": r["subject_ref"], "action": r["action"], "from": r["from_status"],
                     "to": r["to_status"], "reason": r["reason"], "by": r["who"] or "Public user", "at": r["created_at"]} for r in rows])

@app.get("/api/v/audit")
@admin_only
def v_audit():
    rows = db().execute("SELECT a.*, u.username FROM audit_logs a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300").fetchall()
    return jsonify([{"user": r["username"], "action": r["action"], "detail": r["detail"], "ip": r["ip"], "at": r["created_at"]} for r in rows])

@app.get("/api/v/users")
@admin_only
def v_users():
    return jsonify([{"id": r["id"], "username": r["username"], "name": r["full_name"], "role": r["role"], "active": bool(r["active"])}
                    for r in db().execute("SELECT * FROM users ORDER BY id")])

@app.post("/api/v/users")
@admin_only
def v_add_user():
    j = request.get_json(silent=True) or {}
    un, nm, pw, role = clean(j.get("username"), 40).lower(), clean(j.get("name"), 80), j.get("password") or "", j.get("role", "validator")
    if not re.match(r"^[a-z0-9._-]{3,40}$", un): return jsonify(error="Username: 3-40 letters/digits . _ -"), 400
    if len(pw) < 10: return jsonify(error="Password must be at least 10 characters"), 400
    if role not in ("validator", "admin") or not nm: return jsonify(error="Name and valid role required"), 400
    try:
        with db():
            db().execute("INSERT INTO users(username,full_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                         (un, nm, generate_password_hash(pw), role, now()))
            audit(g.user["id"], "USER_CREATED", "%s (%s)" % (un, role))
    except sqlite3.IntegrityError: return jsonify(error="Username already exists"), 409
    return jsonify(ok=True), 201

# --------------------------------------------------------------- facilities + SOS assignment
@app.get("/api/facilities")
def public_facilities():
    rows=db().execute("SELECT facility_id,name,facility_type,address,town,province,road,lat,lng,readiness,equipment,main_phone,emergency_phone,ambulance_phone,alternative_phone,verified,verified_at FROM facilities WHERE verified=1 ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/v/facilities")
@validator_only
def validator_facilities():
    rows=db().execute("SELECT * FROM facilities ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/v/facilities")
@admin_only
def create_facility():
    j=request.get_json(silent=True) or {}
    name,typ,address,town=clean(j.get('name'),140),j.get('facility_type'),clean(j.get('address'),220),clean(j.get('town'),100)
    if not name or typ not in ('hospital','fire_rescue','zrp_patrol','civil_protection') or not address or not town:
        return jsonify(error='Name, valid facility type, address and town are required.'),400
    readiness=j.get('readiness','OFF STANDBY')
    if readiness not in ('ACTIVE STANDBY','OFF STANDBY'): return jsonify(error='Readiness must be ACTIVE STANDBY or OFF STANDBY.'),400
    lat=lng=None
    try:
        if (j.get('lat') in (None,'')) != (j.get('lng') in (None,'')): raise ValueError()
        if j.get('lat') not in (None,'') and j.get('lng') not in (None,''):
            lat,lng=float(j['lat']),float(j['lng'])
            if not (-23<=lat<=-15 and 24<=lng<=34): raise ValueError()
    except Exception:return jsonify(error='Coordinates must be valid Zimbabwe latitude/longitude.'),400
    fid=clean(j.get('facility_id'),40) or ('FAC-'+uuid.uuid4().hex[:8].upper())
    ts=now()
    try:
        with db():
            cur=db().execute("INSERT INTO facilities(facility_id,name,facility_type,address,town,province,road,lat,lng,readiness,equipment,main_phone,emergency_phone,ambulance_phone,alternative_phone,verified,verified_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (fid,name,typ,address,town,clean(j.get('province'),80),clean(j.get('road'),120),lat,lng,j.get('readiness','OFF STANDBY'),clean(j.get('equipment'),500),clean(j.get('main_phone'),30),clean(j.get('emergency_phone'),30),clean(j.get('ambulance_phone'),30),clean(j.get('alternative_phone'),30),1 if j.get('verified') else 0,ts if j.get('verified') else None,ts,ts))
            audit(g.user['id'],'FACILITY_CREATED',fid)
    except sqlite3.IntegrityError:return jsonify(error='Facility ID already exists.'),409
    row=db().execute('SELECT * FROM facilities WHERE id=?',(cur.lastrowid,)).fetchone()
    hub.publish('validator','facility_updated',dict(row)); hub.publish('public','facility_updated',{'facility_id':fid})
    return jsonify(dict(row)),201

@app.patch("/api/v/facilities/<int:fid>")
@admin_only
def update_facility(fid):
    j=request.get_json(silent=True) or {}; row=db().execute('SELECT * FROM facilities WHERE id=?',(fid,)).fetchone()
    if not row:return jsonify(error='Facility not found.'),404
    allowed={'readiness','verified','equipment','main_phone','emergency_phone','ambulance_phone','alternative_phone','address','road'}
    sets=[]; vals=[]
    for k in allowed:
        if k in j:
            sets.append(k+'=?'); vals.append(1 if k=='verified' and j[k] else 0 if k=='verified' else clean(str(j[k]),500))
    if 'verified' in j:sets.append('verified_at=?');vals.append(now() if j['verified'] else None)
    sets.append('updated_at=?');vals.append(now());vals.append(fid)
    with db():db().execute('UPDATE facilities SET '+','.join(sets)+' WHERE id=?',vals);audit(g.user['id'],'FACILITY_UPDATED',row['facility_id'])
    updated=db().execute('SELECT * FROM facilities WHERE id=?',(fid,)).fetchone();hub.publish('validator','facility_updated',dict(updated));hub.publish('public','facility_updated',{'facility_id':row['facility_id']})
    return jsonify(dict(updated))

@app.post("/api/v/sos/<ref>/assign")
@validator_only
def assign_sos_facility(ref):
    j=request.get_json(silent=True) or {}; fac=db().execute('SELECT * FROM facilities WHERE facility_id=? OR CAST(id AS TEXT)=?',(clean(j.get('facility_id'),40),clean(j.get('facility_id'),40))).fetchone()
    sos=db().execute('SELECT * FROM emergency_requests WHERE ref=?',(ref.upper(),)).fetchone()
    if not sos:return jsonify(error='SOS request not found.'),404
    if sos['status'] in ('DECLINED','ARCHIVED','RESOLVED'): return jsonify(error='This SOS is closed and cannot be assigned.'),409
    if not fac:return jsonify(error='Facility not found in the directory.'),404
    if not fac['verified']:return jsonify(error='Only verified facilities can be assigned.'),400
    instructions=clean(j.get('instructions'),500)
    ts=now()
    with db():
        db().execute('INSERT INTO sos_assignments(sos_ref,facility_id,instructions,assigned_by,created_at) VALUES(?,?,?,?,?)',(ref,fac['id'],instructions,g.user['id'],ts))
        db().execute("UPDATE emergency_requests SET status='FACILITY ASSIGNED',decided_by=?,decided_at=?,updated_at=? WHERE ref=?",(g.user['id'],ts,ts,ref))
        notify(ref,'facility_assigned',f"Facility assigned: {fac['name']} · {fac['address']}. Contact: {fac['main_phone'] or fac['emergency_phone'] or 'No verified contact listed'}. {instructions}")
        history('sos',ref,'FACILITY_ASSIGNED',sos['status'],'FACILITY ASSIGNED',fac['facility_id'],g.user['id'])
        audit(g.user['id'],'SOS_FACILITY_ASSIGNED',ref+' -> '+fac['facility_id'])
    updated=db().execute('SELECT * FROM emergency_requests WHERE ref=?',(ref,)).fetchone()
    hub.publish('validator','sos_updated',val_sos(updated));hub.publish('ref','status',{'ref':ref,'status':'FACILITY ASSIGNED','reason':None},ref=ref)
    return jsonify(val_sos(updated))


# ----------------------------------------------------------- community verification
def geo_km(a,b,c,d):
    from math import radians, sin, cos, asin, sqrt
    x,y=radians(a),radians(c); dx=radians(c-a); dy=radians(d-b)
    h=sin(dx/2)**2+cos(x)*cos(y)*sin(dy/2)**2
    return 6371*2*asin(sqrt(h))

def verification_summary(qid):
    row=db().execute("SELECT COUNT(*) total, COALESCE(SUM(witnessed=1),0) yes, COALESCE(SUM(witnessed=0),0) no FROM verification_votes WHERE query_id=?",(qid,)).fetchone()
    return dict(total=row["total"], witnessed_yes=row["yes"], witnessed_no=row["no"])

@app.post("/api/v/verification")
@validator_only
def create_verification_query():
    j=request.get_json(silent=True) or {}; ref=clean(j.get("incident_ref"),40).upper()
    inc=db().execute("SELECT * FROM incidents WHERE ref=?",(ref,)).fetchone()
    if not inc:return jsonify(error="Incident not found"),404
    if inc["lat"] is None or inc["lng"] is None:return jsonify(error="This incident has no GPS coordinates; community proximity checks cannot be sent."),400
    question=clean(j.get("question"),240) or "Did you witness this reported incident?"
    ts=now(); expiry=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(timespec="seconds")
    cur=db().execute("INSERT INTO verification_queries(incident_ref,question,radius_km,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?)",(ref,question,10,g.user["id"],ts,expiry))
    audit(g.user["id"],"COMMUNITY_QUERY_CREATED",ref); db().commit()
    hub.publish("public","community_query",{"id":cur.lastrowid,"incident_ref":ref})
    return jsonify(id=cur.lastrowid,incident_ref=ref,question=question,expires_at=expiry,**verification_summary(cur.lastrowid)),201

@app.get("/api/v/verification")
@validator_only
def list_verification_queries():
    ref=request.args.get("ref","").upper()
    rows=db().execute("SELECT * FROM verification_queries WHERE incident_ref=? ORDER BY id DESC",(ref,)).fetchall() if ref else db().execute("SELECT * FROM verification_queries ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify([dict(id=r["id"],incident_ref=r["incident_ref"],question=r["question"],status=r["status"],created_at=r["created_at"],expires_at=r["expires_at"],**verification_summary(r["id"])) for r in rows])

@app.get("/api/community/queries")
def community_queries():
    try: lat,lng=float(request.args["lat"]),float(request.args["lng"])
    except (KeyError,ValueError,TypeError): return jsonify(error="GPS coordinates are required"),400
    if not (-23<=lat<=-15 and 24<=lng<=34):return jsonify(error="Coordinates outside Zimbabwe"),400
    rows=db().execute("SELECT q.*,i.road,i.town,i.category,i.lat,i.lng FROM verification_queries q JOIN incidents i ON i.ref=q.incident_ref WHERE q.status='OPEN' AND q.expires_at>? ORDER BY q.id DESC LIMIT 100",(now(),)).fetchall()
    out=[]
    for r in rows:
        distance=geo_km(lat,lng,r["lat"],r["lng"])
        if distance<=r["radius_km"]:
            out.append(dict(id=r["id"],incident_ref=r["incident_ref"],question=r["question"],road=r["road"],town=r["town"],category=r["category"],distance_km=round(distance,1)))
    return jsonify(out)

@app.post("/api/community/vote")
def community_vote():
    j=request.get_json(silent=True) or {}
    try: qid=int(j.get("query_id")); lat=float(j.get("lat")); lng=float(j.get("lng"))
    except (TypeError,ValueError):return jsonify(error="Query and GPS location are required"),400
    if not (-23<=lat<=-15 and 24<=lng<=34):return jsonify(error="Coordinates outside Zimbabwe"),400
    witnessed=j.get("witnessed")
    if witnessed not in (True,False,0,1):return jsonify(error="Choose yes or no"),400
    q=db().execute("SELECT q.*,i.lat,i.lng FROM verification_queries q JOIN incidents i ON i.ref=q.incident_ref WHERE q.id=? AND q.status='OPEN' AND q.expires_at>?",(qid,now())).fetchone()
    if not q:return jsonify(error="This verification query is closed or expired"),404
    if geo_km(lat,lng,q["lat"],q["lng"])>q["radius_km"]:return jsonify(error="You are outside the verification area"),403
    voter=request.cookies.get("rg_voter") or secrets.token_urlsafe(24)
    voter=hashlib.sha256(voter.encode()).hexdigest()
    if not rate_limit("community-vote:"+voter,20,3600):return jsonify(error="Too many verification responses"),429
    try:
        db().execute("INSERT INTO verification_votes(query_id,voter_key,witnessed,lat,lng,created_at) VALUES(?,?,?,?,?,?)",(qid,voter,int(bool(witnessed)),lat,lng,now()));db().commit()
    except sqlite3.IntegrityError:return jsonify(error="A response from this device has already been recorded"),409
    summary=verification_summary(qid);hub.publish("validator","verification_updated",dict(query_id=qid,**summary))
    resp=jsonify(ok=True,**summary)
    resp.set_cookie("rg_voter",request.cookies.get("rg_voter") or secrets.token_urlsafe(24),httponly=True,samesite="Lax",max_age=31536000,secure=request.is_secure)
    return resp

# ----------------------------------------------------------- AI-assisted SOS triage
def assess_sos(category, description):
    t=(description or "").lower()
    critical_words=("trapped","unconscious","not breathing","multiple","fire","bleeding","child","pregnant","overturned","explosion")
    high_words=("injured","injury","collision","smoke","burn","stuck","severe","ambulance")
    critical=any(w in t for w in critical_words)
    high=any(w in t for w in high_words)
    score=95 if critical else 78 if category in ("medical","fire_rescue") or high else 65 if category=="accident" else 45
    return {"score":score,"level":"CRITICAL" if score>=85 else "HIGH" if score>=65 else "MODERATE",
            "reasons":["Emergency category: "+category]+(["Description contains a high-risk indicator"] if critical or high else []),
            "disclaimer":"Automated triage assistance only; a human dispatcher/validator must assess the situation."}

# ----------------------------------------------------------- USSD gateway adapter
@app.post("/api/integrations/ussd")
def ussd_gateway():
    expected=os.environ.get("RG_USSD_TOKEN")
    if expected and request.headers.get("X-USSD-Token")!=expected:return jsonify(error="Unauthorized gateway"),401
    j=request.get_json(silent=True) or request.form.to_dict()
    text_in=clean(j.get("text"),1000); parts=text_in.split("*") if text_in else []
    if not parts:return jsonify(response="CON RoadGuard ZW Emergency\n1. Road accident\n2. Medical\n3. Fire/rescue\n4. Other")
    cats={"1":"accident","2":"medical","3":"fire_rescue","4":"other"}
    if len(parts)==1 and parts[0] in cats:return jsonify(response="CON Enter location (town/road):")
    if len(parts)==2 and parts[0] in cats:return jsonify(response="CON Briefly describe the emergency:")
    if len(parts)>=3 and parts[0] in cats:
        cat=cats[parts[0]];loc=clean(parts[1],200);desc=clean("*".join(parts[2:]),1000) or "SOS submitted through USSD"
        ref=new_ref("SOS","emergency_requests");ts=now()
        with db():
            db().execute("INSERT INTO emergency_requests(ref,category,road,location,description,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,'CRITICAL','PENDING',?,?)",(ref,cat,loc,loc,desc,ts,ts))
            history("sos",ref,"SUBMITTED",None,"PENDING","USSD",None);notify(ref,"submitted","USSD emergency received; location is user-described and requires confirmation.")
        row=db().execute("SELECT * FROM emergency_requests WHERE ref=?",(ref,)).fetchone()
        hub.publish("validator","new_sos",val_sos(row));hub.publish("validator","stats",{})
        return jsonify(response="END SOS received. Ref "+ref+". This gateway does not dispatch responders; call official emergency services if in immediate danger.")
    return jsonify(response="END Invalid selection. Dial again.")

# ----------------------------------------------------------- official authority alert webhook
@app.post("/api/integrations/authority/alerts")
def authority_alert_webhook():
    token=os.environ.get("RG_AUTHORITY_TOKEN")
    if not token or request.headers.get("X-Authority-Token")!=token:return jsonify(error="Authority integration is not configured or token is invalid"),401
    j=request.get_json(silent=True) or {}
    title=clean(j.get("title"),140);desc=clean(j.get("description"),1000);loc=clean(j.get("location"),200)
    if not title or not desc or not loc:return jsonify(error="title, description and location are required"),400
    severity=j.get("severity","warning")
    if severity not in SEVERITIES:severity="warning"
    admin=db().execute("SELECT id FROM users WHERE role='admin' AND active=1 ORDER BY id LIMIT 1").fetchone()
    if not admin:return jsonify(error="No active administrator account"),503
    exp=None
    if j.get("expires_in_minutes"):
        try:exp=(datetime.now(timezone.utc)+timedelta(minutes=max(1,min(10080,int(j["expires_in_minutes"]))))).isoformat(timespec="seconds")
        except (ValueError,TypeError):return jsonify(error="Invalid expiry"),400
    cur=db().execute("INSERT INTO public_alerts(title,description,location,category,severity,expires_at,related_ref,created_by,created_at,source) VALUES(?,?,?,?,?,?,?,?,?,?)",(title,desc,loc,j.get("category") if j.get("category") in CATEGORIES else "other",severity,exp,clean(j.get("related_ref"),40) or None,admin["id"],now(),"Official authority feed"))
    db().commit();row=db().execute("SELECT * FROM public_alerts WHERE id=?",(cur.lastrowid,)).fetchone()
    payload=pub_alert(row);payload["source"]="Official authority feed";hub.publish("public","alert",payload);hub.publish("validator","alert",payload)
    return jsonify(payload),201

# ----------------------------------------------------------------------- analytics
@app.get("/api/v/analytics")
@validator_only
def v_analytics():
    a = request.args
    where, args = ["1=1"], []
    if a.get("from"): where.append("date(created_at)>=date(?)"); args.append(a["from"])
    if a.get("to"): where.append("date(created_at)<=date(?)"); args.append(a["to"])
    if a.get("town"): where.append("lower(town) LIKE ?"); args.append("%" + a["town"].lower() + "%")
    if a.get("province"): where.append("province=?"); args.append(a["province"])
    if a.get("category") in CATEGORIES: where.append("category=?"); args.append(a["category"])
    if a.get("status") in (ST_PENDING, ST_APPROVED, ST_DECLINED, ST_ARCHIVED): where.append("status=?"); args.append(a["status"])
    w = " AND ".join(where); d = db()
    inc = d.execute("SELECT category, status, date(created_at) AS day FROM incidents WHERE " + w, args).fetchall()
    by_cat = {c: 0 for c in CATEGORIES}; per_day = {}
    counts = {ST_PENDING: 0, ST_APPROVED: 0, ST_DECLINED: 0, ST_ARCHIVED: 0}
    for r in inc:
        by_cat[r["category"]] += 1; counts[r["status"]] += 1
        per_day.setdefault(r["day"], {c: 0 for c in CATEGORIES})[r["category"]] += 1
    # SOS honours the same date/province/category filters (town/status are report concepts)
    sw, sa = ["1=1"], []
    if a.get("from"): sw.append("date(created_at)>=date(?)"); sa.append(a["from"])
    if a.get("to"): sw.append("date(created_at)<=date(?)"); sa.append(a["to"])
    if a.get("province"): sw.append("province=?"); sa.append(a["province"])
    if a.get("category") in SOS_CATEGORIES: sw.append("category=?"); sa.append(a["category"])
    sos = d.execute("SELECT COUNT(*) c, SUM(status='PENDING') p FROM emergency_requests WHERE " + " AND ".join(sw), sa).fetchone()
    active = len(active_alerts_rows())
    series = [dict(day=day, **v) for day, v in sorted(per_day.items())]
    return jsonify(
        summary=dict(total_submitted=len(inc), approved=counts[ST_APPROVED], pending=counts[ST_PENDING],
                     declined=counts[ST_DECLINED], archived=counts[ST_ARCHIVED],
                     sos_requests=sos["c"] or 0, sos_pending=sos["p"] or 0, active_alerts=active),
        by_category=by_cat, series=series)

# =========================================================================== static sites
@app.get("/validator")
def v_redirect(): return redirect("/validator/")

@app.get("/validator/")
def v_index(): return send_from_directory(os.path.join(BASE, "validator"), "index.html")

@app.get("/validator/<path:p>")
def v_static(p): return send_from_directory(os.path.join(BASE, "validator"), p)

@app.get("/")
def index(): return send_from_directory(os.path.join(BASE, "public"), "index.html")

@app.get("/<path:p>")
def pub_static(p):
    if p.startswith("api/"): abort(404)
    return send_from_directory(os.path.join(BASE, "public"), p)

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5002"))
    print("RoadGuard ZW running:  Public http://localhost:%d/   Validator http://localhost:%d/validator/" % (port, port))
    host = os.environ.get("HOST", "0.0.0.0")
    try:   # production-grade server; each live (SSE) browser tab holds one thread
        from waitress import serve
        serve(app, host=host, port=port, threads=int(os.environ.get("THREADS", "128")), channel_timeout=120)
    except ImportError:
        app.run(host=host, port=port, threaded=True)
