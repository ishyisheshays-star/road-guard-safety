"""
RoadGuard SOS - independent emergency call-taking agent (Zimbabwe-adapted).

Zero third-party dependencies (stdlib only). Plug into any app:

    agent = RoadGuardAgent(send=my_transport_fn)      # my_transport_fn(bytes) -> bool
    agent.set_gps(-17.83, 31.05, accuracy_m=25)       # from the host app, if available
    agent.set_caller(phone="+263771234567")           # optional
    print(agent.greeting())                           # "RoadGuard SOS. What's your emergency?"
    reply = agent.handle("there's been an accident near Kadoma, two people hurt")

Speech-to-text / text-to-speech are NOT included on purpose: pass any ASR/TTS
you already use (see VoiceSession). The agent works on text turns.

Design (call-taking protocol, same order a trained 911/999 call-taker uses):
  1. WHAT is happening  2. WHERE (most critical)  3. WHO is hurt / trapped
  4. HAZARDS (fire, fuel, traffic)  5. CALLBACK number  6. Pre-arrival advice
Life-threatening incidents send an early "flash" report the moment type +
location are known, then a final revised report (same id, rev+1).
"""
from __future__ import annotations

import base64
import collections
import json
import math
import os
import re
import time
import uuid
import zlib
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 1. ZIMBABWE CONFIG  (edit freely - verify numbers/locations with your ops team)
# --------------------------------------------------------------------------
TOWNS = ["harare", "bulawayo", "gweru", "kwekwe", "kadoma", "chegutu", "norton",
         "masvingo", "mutare", "beitbridge", "victoria falls", "hwange", "chinhoyi",
         "karoi", "kariba", "rusape", "marondera", "chitungwiza", "plumtree",
         "gwanda", "zvishavane", "shurugwi", "bindura", "mvurwi", "chivhu",
         "lupane", "nyanga", "chiredzi", "ruwa", "epworth", "chirundu", "birchenough"]

# Dispatch hints per incident type (VERIFY numbers before production use).
DISPATCH = {
    "crash":    ["ambulance", "police"],
    "fire":     ["fire", "ambulance"],
    "medical":  ["ambulance"],
    "security": ["police"],
    "breakdown": ["roadside"],
    "animal":   ["police"],
    "flood":    ["police", "civil_protection"],
    "other":    ["operator"],
}

PROMPTS = {  # Shona/Ndebele lines: have a native speaker review before launch.
    "greet":    {"en": "RoadGuard SOS. What's your emergency?",
                 "sn": "RoadGuard SOS. Chii chakaitika?",
                 "nd": "RoadGuard SOS. Kwenzakalani?"},
    "type":     {"en": "Tell me what happened.",
                 "sn": "Ndiudzei kuti chii chakaitika.",
                 "nd": "Ngitshele ukuthi kwenzakalani."},
    "location": {"en": "Where are you? Give me the road, nearest town, a landmark or a kilometre mark.",
                 "sn": "Muri kupi? Ndiudzei mugwagwa, taundi riri pedyo kana chimwe chiratidzo.",
                 "nd": "Ukuphi? Ngitshele indlela, idolobha eliseduze kumbe uphawu oluseduze."},
    "injured":  {"en": "Is anyone injured? How many?",
                 "sn": "Pane vakakuvara here? Vangani?",
                 "nd": "Kukhona abalimeleyo yini? Bangaki?"},
    "trapped":  {"en": "Is anyone trapped inside a vehicle?",
                 "sn": "Pane vakasungwa mumota here?",
                 "nd": "Kukhona obotshelweyo emotweni yini?"},
    "hazard":   {"en": "Do you see fire, smoke or fuel leaking?",
                 "sn": "Munoona moto, utsi kana mafuta ari kuyerera here?",
                 "nd": "Ubona umlilo, intuthu kumbe uphethroli ophuphumayo yini?"},
    "callback": {"en": "What phone number can we call you back on?",
                 "sn": "Nhamba yenhare ingakufonerai ndeipi?",
                 "nd": "Yiphi inombolo esingakufonela kuyo?"},
    "clarify":  {"en": "Sorry, I did not catch that. Please say it again, short and clear.",
                 "sn": "Pamusoroi, handina kunzwa. Dzokororai zvakare.",
                 "nd": "Ngiyaxolisa, angizwanga. Phinda futhi."},
    "close":    {"en": "Help is being alerted. Stay safe and keep your phone on.",
                 "sn": "Alert yatumirwa. Garai muchinzvenga njodzi uye murege kudzima foni.",
                 "nd": "Usizo luyatshelwa. Hlala usindisekile ugcine ifoni ivuliwe."},
}

ADVICE = {  # short pre-arrival instructions, spoken in the chosen language where defined
    "fire":    "Move at least 30 metres away from the vehicle. Do not go back for belongings.",
    "bleeding": "Press firmly on the wound with a clean cloth and keep pressing.",
    "crash":   "Switch off engines. Do not move injured people unless there is fire. Warn oncoming traffic.",
    "security": "Do not resist or argue. Your safety comes first. Stay on the line if you can.",
    "flood":   "Never drive or walk through moving water. Move to higher ground.",
    "animal":  "Slow down, switch on hazard lights and keep clear of the animals.",
}

SEED = [  # (text, label) bootstrap training data; extend via train_from_csv()
    ("accident crash collision overturned rolled hit head on", "crash"),
    ("kombi hit a pedestrian", "crash"), ("truck and car collided", "crash"),
    ("ngozi yemota vakakuvara", "crash"), ("ingozi yemoto abantu balimele", "crash"),
    ("car on fire burning flames smoke tanker fuel", "fire"), ("moto mumota", "fire"),
    ("umlilo emotweni", "fire"), ("bus caught fire", "fire"),
    ("someone collapsed unconscious not breathing heart attack chest pain", "medical"),
    ("woman in labour pregnant", "medical"), ("snake bite", "medical"),
    ("hijack hijacked robbery armed gun thieves robbers attacked", "security"),
    ("they stopped us with weapons", "security"), ("hatshi abaphangi", "security"),
    ("broke down breakdown flat tyre no fuel stuck engine", "breakdown"),
    ("car will not start", "breakdown"), ("mota yaramba kutanga", "breakdown"),
    ("cattle cows donkey goats elephant on the road", "animal"),
    ("animal crossing blocking road", "animal"),
    ("flood river overflowing bridge washed away low level bridge water", "flood"),
]

NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10}
YES = {"yes", "yeah", "yep", "yebo", "ehe", "hongu", "ok", "okay"}
NO = {"no", "nope", "aiwa", "hapana", "kwete", "hatshi", "cha"}
SN_WORDS = {"ngozi", "ndiri", "hapana", "hongu", "vakakuvara", "mota", "ndapota", "moto", "kwete"}
ND_WORDS = {"ingozi", "ngikhona", "yebo", "umlilo", "balimele", "siyabonga", "hatshi", "moto"}

# --------------------------------------------------------------------------
# 2. LEARNING: tiny Naive Bayes incident classifier (retrain on real data)
# --------------------------------------------------------------------------
def _tok(s: str) -> List[str]:
    return re.findall(r"[a-z']+", s.lower())


class IncidentClassifier:
    def __init__(self):
        self.wc: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.dc: collections.Counter = collections.Counter()
        self.vocab: set = set()
        self.train(SEED)

    def train(self, samples: List[Tuple[str, str]]):
        for text, label in samples:
            self.dc[label] += 1
            for w in _tok(text):
                self.wc[label][w] += 1
                self.vocab.add(w)

    def train_from_csv(self, path: str):
        """CSV with header text,label - e.g. anonymised, labelled local call transcripts."""
        import csv
        with open(path, newline="", encoding="utf-8") as f:
            self.train([(r["text"], r["label"]) for r in csv.DictReader(f)])

    def predict(self, text: str) -> Tuple[str, float]:
        toks = [w for w in _tok(text) if w in self.vocab]
        if not toks:
            return "other", 0.0
        total = sum(self.dc.values())
        scores = {}
        for lab in self.dc:
            n = sum(self.wc[lab].values())
            s = math.log(self.dc[lab] / total)
            for w in toks:
                s += math.log((self.wc[lab][w] + 1) / (n + len(self.vocab)))
            scores[lab] = s
        m = max(scores.values())
        exp = {k: math.exp(v - m) for k, v in scores.items()}
        z = sum(exp.values())
        best = max(exp, key=exp.get)
        return best, exp[best] / z

    def save(self, path):
        json.dump({"wc": self.wc, "dc": self.dc}, open(path, "w"))

    def load(self, path):
        d = json.load(open(path))
        self.wc = collections.defaultdict(collections.Counter, {k: collections.Counter(v) for k, v in d["wc"].items()})
        self.dc = collections.Counter(d["dc"])
        self.vocab = {w for c in self.wc.values() for w in c}


# --------------------------------------------------------------------------
# 3. REPORT + FORMAT  (match your server's existing text format here)
# --------------------------------------------------------------------------
# !! Replace TEMPLATE with the exact layout your server already parses. !!
TEMPLATE = ("SOS|{id}|{rev}|{ts}|{type}|P{prio}|{lat},{lon}|{acc}|{loc}|"
            "inj={inj}|trap={trap}|haz={haz}|veh={veh}|cb={cb}|lang={lang}|n={notes}")


@dataclass
class Report:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    rev: int = 0
    type: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    acc: Optional[int] = None
    loc_parts: List[str] = field(default_factory=list)
    injured: Optional[int] = None
    critical: bool = False
    bleeding: bool = False
    trapped: Optional[bool] = None
    hazards: List[str] = field(default_factory=list)
    vehicles: List[str] = field(default_factory=list)
    callback: Optional[str] = None
    lang: str = "en"
    notes: List[str] = field(default_factory=list)

    @property
    def has_location(self):
        return self.lat is not None or bool(self.loc_parts)

    @property
    def priority(self) -> int:
        """1 = life-threatening ... 4 = non-urgent."""
        t = self.type or "other"
        if (self.trapped or self.critical or "fire" in self.hazards or "fuel" in self.hazards
                or self.bleeding or (self.injured or 0) >= 3 or (t == "security" and "armed" in self.notes)):
            return 1
        if t in ("crash", "fire", "medical", "security") or (self.injured or 0) > 0:
            return 2
        if t in ("animal", "flood", "other"):
            return 3
        return 4

    def to_text(self, template: str = TEMPLATE) -> str:
        clean = lambda s: re.sub(r"[|\r\n]+", " ", str(s))[:80]
        return template.format(
            id=self.id, rev=self.rev, ts=int(time.time()), type=self.type or "other",
            prio=self.priority,
            lat="" if self.lat is None else f"{self.lat:.5f}",
            lon="" if self.lon is None else f"{self.lon:.5f}",
            acc="" if self.acc is None else self.acc,
            loc=clean("; ".join(self.loc_parts)),
            inj="" if self.injured is None else self.injured,
            trap="" if self.trapped is None else int(self.trapped),
            haz=clean(",".join(self.hazards)), veh=clean(",".join(self.vehicles)),
            cb=self.callback or "", lang=self.lang, notes=clean(";".join(self.notes)))


# --------------------------------------------------------------------------
# 4. BANDWIDTH-FRIENDLY CODEC  (text report -> small bytes -> back to SAME text)
# --------------------------------------------------------------------------
_DICT = (b"|inj=|trap=|haz=|veh=|cb=+263|lang=en|n=|P1|P2|P3|P4|unconscious|bleeding|"
         b"fire|fuel|smoke|kombi|truck|bus|car|tanker|armed|breakdown|animal|flood|medical|security|"
         b"crash|road|km|from|near|past|tollgate|bridge|Harare|Bulawayo|Gweru|Kwekwe|Kadoma|Chegutu|"
         b"Masvingo|Mutare|Beitbridge|Victoria Falls|Chinhoyi|A1|A3|A4|A5|A8|SOS|")
_VER = b"\x01"


def compress_report(text: str, armor: Optional[str] = None):
    """Raw-deflate with preset dictionary: ~35-50% smaller on short reports, no header overhead.
    armor=None -> bytes (HTTP/socket); 'b64' -> SMS/USSD-safe text; 'b85' -> densest text."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, _DICT)
    blob = _VER + c.compress(text.encode("utf-8")) + c.flush()
    raw = text.encode("utf-8")
    if len(blob) >= len(raw) + 1:                       # never make it bigger
        blob = b"\x00" + raw
    if armor == "b64":
        return base64.urlsafe_b64encode(blob).decode().rstrip("=")
    if armor == "b85":
        return base64.b85encode(blob).decode()
    return blob


def decompress_report(blob, armor: Optional[str] = None) -> str:
    """Server/gateway side: returns the ORIGINAL text so your existing parser is untouched."""
    if armor == "b64":
        blob = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    elif armor == "b85":
        blob = base64.b85decode(blob)
    if blob[:1] == b"\x00":
        return blob[1:].decode("utf-8")
    d = zlib.decompressobj(-15, _DICT)
    return (d.decompress(blob[1:]) + d.flush()).decode("utf-8")


class Outbox:
    """Store-and-forward: survives dead zones. Persists to disk if path given."""

    def __init__(self, send: Callable[[bytes], bool], path: Optional[str] = None):
        self.send, self.path, self.q = send, path, []
        if path and os.path.exists(path):
            self.q = [base64.b64decode(x) for x in json.load(open(path))]

    def _persist(self):
        if self.path:
            json.dump([base64.b64encode(x).decode() for x in self.q], open(self.path, "w"))

    def enqueue(self, blob: bytes):
        self.q.append(blob)
        self._persist()
        self.flush()

    def flush(self):
        while self.q:
            try:
                ok = self.send(self.q[0])
            except Exception:
                ok = False
            if not ok:
                break               # call flush() again on connectivity change / timer
            self.q.pop(0)
        self._persist()


# --------------------------------------------------------------------------
# 5. THE AGENT
# --------------------------------------------------------------------------
class RoadGuardAgent:
    SLOT_ORDER = ["type", "location", "injured", "trapped", "hazard", "callback"]

    def __init__(self, send: Optional[Callable[[bytes], bool]] = None,
                 classifier: Optional[IncidentClassifier] = None,
                 outbox_path: Optional[str] = None, template: str = TEMPLATE,
                 armor: Optional[str] = None, feedback_log: str = "roadguard_feedback.jsonl"):
        self.clf = classifier or IncidentClassifier()
        self.outbox = Outbox(send or (lambda b: True), outbox_path)
        self.template, self.armor, self.feedback_log = template, armor, feedback_log
        self.reset()

    # ---- host-app hooks
    def reset(self):
        self.r = Report()
        self.pending: Optional[str] = None
        self.turns = 0
        self.silences = 0
        self.flash_sent = False
        self.done = False
        self.asked: set = set()

    def set_gps(self, lat, lon, accuracy_m=None):
        self.r.lat, self.r.lon = lat, lon
        self.r.acc = None if accuracy_m is None else int(accuracy_m)

    def set_caller(self, phone=None, lang=None):
        if phone:
            self.r.callback = phone
        if lang in ("en", "sn", "nd"):
            self.r.lang = lang

    # ---- dialogue
    def _say(self, key):
        return PROMPTS[key].get(self.r.lang, PROMPTS[key]["en"])

    def greeting(self) -> str:
        return PROMPTS["greet"]["en"]

    def handle(self, text: str) -> str:
        if self.done:
            return self._say("close")
        self.turns += 1
        self.silences = 0
        self._detect_lang(text)
        self._extract(text)
        if self.pending:
            self._answer_pending(text)
        self._maybe_flash()
        nxt = self._next_slot()
        if nxt is None or self.turns >= 8:
            return self._finish()
        self.pending = nxt
        if nxt in self.asked and nxt == "type":
            return self._say("clarify")
        self.asked.add(nxt)
        return self._say(nxt)

    def on_silence(self) -> Optional[str]:
        """Call when caller says nothing for ~6s. Sends what we have if life is at risk."""
        self.silences += 1
        if self.silences >= 2:
            self.r.notes.append("caller_unresponsive")
            if self.r.has_location and not self.done:
                return self._finish()
        return self._say("clarify")

    def _next_slot(self) -> Optional[str]:
        r = self.r
        for s in self.SLOT_ORDER:
            if s == "type" and r.type is None:
                return s
            if s == "location" and not r.has_location:
                return s
            if s == "injured" and r.injured is None and r.type in ("crash", "medical", "security", "fire", None, "other"):
                return s
            if s == "trapped" and r.trapped is None and r.type == "crash" and (r.injured or 0) > 0:
                return s
            if s == "hazard" and not r.hazards and "hazard" not in self.asked and r.type in ("crash", "fire", "breakdown"):
                return s
            if s == "callback" and not r.callback:
                return s
        return None

    # ---- understanding
    def _detect_lang(self, text):
        w = set(_tok(text))
        sn, nd = len(w & SN_WORDS), len(w & ND_WORDS)
        if sn > nd and sn:
            self.r.lang = "sn"
        elif nd > sn and nd:
            self.r.lang = "nd"

    def _num(self, t: str) -> Optional[int]:
        m = re.search(r"\b(\d{1,2})\b", t)
        if m:
            return int(m.group(1))
        for w in _tok(t):
            if w in NUMWORDS:
                return NUMWORDS[w]
        return None

    def _extract(self, text: str):
        r, t = self.r, text.lower()
        if r.type is None:
            lab, conf = self.clf.predict(t)
            if conf >= 0.5 and lab != "other":
                r.type = lab
        m = re.search(r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
                      r"(people|persons|person|injured|hurt|passengers|casualt)", t)
        if m:
            r.injured = int(m.group(1)) if m.group(1).isdigit() else NUMWORDS[m.group(1)]
        if re.search(r"injured|hurt|bleeding|blood|vakakuvara|balimele|unconscious|dead|died", t):
            r.injured = max(r.injured or 0, 1)
        if re.search(r"unconscious|not breathing|not moving|dead|died|passed out|collapsed", t):
            r.critical = True
        if re.search(r"bleeding|blood", t):
            r.bleeding = True
        if re.search(r"trapped|stuck inside|cannot get out|can't get out", t):
            r.trapped = True
        for kw, tag in (("fire", "fire"), ("burning", "fire"), ("flames", "fire"), ("moto", "fire"),
                        ("umlilo", "fire"), ("smoke", "smoke"), ("fuel", "fuel"), ("petrol", "fuel"),
                        ("diesel", "fuel"), ("leak", "fuel"), ("chemical", "chemical")):
            if kw in t and tag not in r.hazards:
                r.hazards.append(tag)
        for v in ("kombi", "bus", "truck", "lorry", "tanker", "haulage", "motorbike", "car", "pedestrian"):
            if re.search(rf"\b{v}\b", t) and v not in r.vehicles:
                r.vehicles.append(v)
        if re.search(r"\b(gun|armed|weapon|knife)s?\b", t) and "armed" not in r.notes:
            r.notes.append("armed")
        # location clues
        for town in TOWNS:
            if re.search(rf"\b{town}\b", t) and town.title() not in r.loc_parts:
                r.loc_parts.append(town.title())
        for road in re.findall(r"\b([ar]\s?\d{1,2})\b", t):
            tag = road.replace(" ", "").upper()
            if tag not in r.loc_parts:
                r.loc_parts.append(tag)
        for km in re.findall(r"(\d{1,3}\s?km\s+(?:from|past|after|before|near)\s+[a-z ]{3,20})", t):
            if km not in r.loc_parts:
                r.loc_parts.append(km.strip())
        pm = re.search(r"(\+?\d[\d\s-]{8,14}\d)", text)
        if pm and not r.callback:
            r.callback = re.sub(r"[\s-]", "", pm.group(1))

    def _answer_pending(self, text: str):
        r, t, p = self.r, text.lower(), self.pending
        w = set(_tok(t))
        yes, no = bool(w & YES), bool(w & NO)
        self.pending = None
        if p == "location" and not r.has_location and len(t.split()) >= 1:
            r.loc_parts.append(text.strip()[:60])        # free text landmark
        elif p == "injured":
            n = self._num(t)
            if n is not None:
                r.injured = n
            elif no and not yes and not r.critical:
                r.injured = 0
            elif yes:
                r.injured = max(r.injured or 0, 1)
        elif p == "trapped":
            r.trapped = True if yes else (False if no else r.trapped)
        elif p == "hazard":
            self.asked.add("hazard")
            if no and not r.hazards:
                r.hazards.append("none")
        elif p == "callback" and not r.callback:
            if re.search(r"same number|this number|this phone", t):
                r.callback = r.callback or "same"
            elif not re.search(r"\d{9}", t):
                self.asked.add("callback")
                r.callback = r.callback or "declined"
        elif p == "type" and r.type is None:
            r.type = "other"
            r.notes.append(text.strip()[:50])
        if p == "callback":
            self.asked.add("callback")

    # ---- output
    def _maybe_flash(self):
        if not self.flash_sent and self.r.type and self.r.has_location and self.r.priority <= 2:
            self._transmit()
            self.flash_sent = True

    def _transmit(self):
        self.r.rev += 1
        blob = compress_report(self.r.to_text(self.template), self.armor)
        self.outbox.enqueue(blob.encode() if isinstance(blob, str) else blob)

    def _finish(self) -> str:
        self.done = True
        if self.r.callback in ("same", "declined"):
            self.r.notes.append(f"cb_{self.r.callback}")
            self.r.callback = None if self.r.callback == "declined" else self.r.callback
        self._transmit()
        parts = [self._say("close")]
        t, haz = self.r.type, self.r.hazards
        if "fire" in haz or t == "fire":
            parts.append(ADVICE["fire"])
        elif self.r.bleeding:
            parts.append(ADVICE["bleeding"])
        elif t in ADVICE:
            parts.append(ADVICE[t])
        return " ".join(parts)

    # ---- learning loop: operators correct, you retrain nightly
    def record_outcome(self, text: str, true_type: str, true_priority: Optional[int] = None):
        with open(self.feedback_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "label": true_type, "prio": true_priority}) + "\n")
        self.clf.train([(text, true_type)])


class VoiceSession:
    """Glue for any speech stack: asr() -> str|None (None = silence), tts(str) -> None."""

    def __init__(self, agent: RoadGuardAgent, asr: Callable[[], Optional[str]], tts: Callable[[str], None]):
        self.a, self.asr, self.tts = agent, asr, tts

    def run(self):
        self.tts(self.a.greeting())
        while not self.a.done:
            heard = self.asr()
            reply = self.a.on_silence() if heard is None else self.a.handle(heard)
            if reply:
                self.tts(reply)


# --------------------------------------------------------------------------
if __name__ == "__main__":
    sent = []
    ag = RoadGuardAgent(send=lambda b: sent.append(b) or True, armor=None)
    ag.set_gps(-18.3329, 29.9147, 30)
    print("AGENT:", ag.greeting())
    for line in ["kombi and a truck collided past Kadoma on the A5, there is smoke",
                 "yes three people, one is bleeding", "yes one is trapped",
                 "yes smoke from the engine", "0771234567"]:
        print("CALLER:", line)
        print("AGENT:", ag.handle(line))
        if ag.done:
            break
    txt = decompress_report(sent[-1])
    raw = len(txt.encode())
    print(f"\nReports sent: {len(sent)} (flash + final)")
    print("Final report text:", txt)
    print(f"raw {raw} B -> compressed {len(sent[-1])} B; "
          f"b85 {len(compress_report(txt, 'b85'))} chars; b64 {len(compress_report(txt, 'b64'))} chars")
    assert decompress_report(compress_report(txt, "b64"), "b64") == txt
