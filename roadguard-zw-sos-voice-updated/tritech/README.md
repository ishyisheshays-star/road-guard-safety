# RoadGuard ZW — connected road-safety platform

This is an extension of the supplied RoadGuard ZW application. It preserves the existing Flask + SQLite + SSE architecture and the two connected websites:

- Public citizen website: `http://localhost:5002/`
- Validator portal: `http://localhost:5002/validator/`
- Shared API/database: `server.py` and `data/roadguard.db`

## 1. Install and run

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set an administrator password before the first run:

**Windows PowerShell**
```powershell
$env:RG_ADMIN_PASSWORD = 'Choose-A-Strong-Password'
python server.py
```

**macOS/Linux**
```bash
export RG_ADMIN_PASSWORD='Choose-A-Strong-Password'
python server.py
```

Sign in at `/validator/` with username `admin` and the password configured above. If no password is supplied on first startup, the application generates one and prints it in the server console. Add validator accounts from the Admin page.

Optional environment settings:

| Variable | Purpose | Default |
|---|---|---|
| `PORT` | HTTP port | `5002` |
| `HOST` | Bind address | `0.0.0.0` |
| `THREADS` | Waitress worker threads | `128` |
| `RG_DB` | SQLite database path | `data/roadguard.db` |
| `RG_UPLOADS` | Evidence upload directory | `uploads/` |

## 2. Navigation and location services

The public journey planner uses live external services:

- **Photon / OpenStreetMap** for Zimbabwe place search.
- **OSRM public demo routing service** for actual road-network route geometry, distance, estimated duration, alternatives and route steps.
- Browser Geolocation API for GPS. GPS requires HTTPS or localhost and explicit visitor permission.

The public OSRM demo service is community infrastructure, not a guaranteed production SLA. It can rate-limit or become unavailable. For production, deploy a self-hosted OSRM instance or configure a contracted routing provider. Follow Nominatim's usage policy; for sustained production traffic, use a hosted geocoder or self-host Nominatim. The application reports service errors instead of drawing fake driving routes.

What's Ahead matches approved incident coordinates against the calculated route corridor and sorts them by route progress. The current UI labels corridor matches as **Nearby — route impact not confirmed**; it does not assert that a nearby incident blocks the road. It currently calculates distance ahead along the route geometry. The current public dataset does not include validator-confirmed lane/closure impact metadata, so definitive **ON YOUR ROUTE** classifications are not asserted.

## 3. Emergency facilities and SOS

Administrators can add facilities in Validator → Emergency facilities. Only facilities explicitly marked verified appear in the public facilities endpoint and can be assigned to an SOS. Add only independently verified facility addresses, coordinates and contact numbers. No hospital, police, fire or ambulance contact has been fabricated or preloaded.

SOS submissions are delivered to RoadGuard validators and may be assigned to a verified directory entry. This platform does **not** dispatch emergency responders or contact emergency services. It must not be treated as a replacement for calling official emergency numbers.

## 4. Real-time updates

Both websites use the shared Flask SSE hub. The hub now emits event IDs and retains a bounded in-memory replay buffer for reconnecting clients within the current server process. EventSource automatically reconnects. A full server restart clears that in-memory replay buffer; the SQLite records remain persistent and clients refresh their current data on connection. For durable replay across server restarts, move the event log into SQLite or a message broker before production.

## 5. Database and migrations

The existing SQLite file is retained. New tables (`facilities` and `sos_assignments`) are created using additive `CREATE TABLE IF NOT EXISTS` schema initialization; existing records are not dropped. Back up `data/roadguard.db` before deployment. Keep the database and uploads directory on persistent storage.

## 6. Tests and verification

Run:

```bash
python tests/test_api.py
python tests/test_ui.py
```

`test_ui.py` requires Playwright and Chromium (`pip install playwright` then `playwright install chromium`). The supplied environment used to prepare this update did not have Flask installed and had no package-network access, so live Flask/API and browser tests could not be executed here. Python syntax, JavaScript syntax, and SQLite schema creation were checked. Run the test commands in the project environment before public deployment.

## 7. Current implementation boundaries

- Routing and geocoding depend on external OpenStreetMap community services and internet access.
- GPS needs HTTPS on phones; plain LAN HTTP origins generally cannot access geolocation.
- Route alternatives are displayed when OSRM returns them.
- Route deviation is detected from GPS-to-route distance and reported to the driver; the driver must choose to recalculate.
- What's Ahead currently uses approved incident reports with coordinates. Public alerts currently have descriptive locations but no required coordinate geometry, so they are not falsely placed on the route.
- Facilities are empty until an administrator enters and verifies real data.
- SMS, official ambulance/police/fire dispatch, official road authority feeds, and responder dispatch confirmation require external agreements/integrations and are not claimed as live dispatch services.
- SQLite is suitable for a pilot. For concurrent production deployment, use PostgreSQL, persistent event storage, HTTPS, backups, and production routing/geocoding services.

## 8. TryCloudflare

Start Flask first, then in a second terminal run:

```bash
cloudflared tunnel --url http://localhost:5002
```

Open the HTTPS URL printed by cloudflared. Both the public site and `/validator/` share the same origin. Keep the Flask process running. A temporary TryCloudflare URL changes when the tunnel is restarted; use a named tunnel for a stable production address.

## 9. Added emergency-access and verification capabilities

- Public and validator portals include English/Shona/Ndebele language selection hooks and browser speech read-aloud. Browser voice availability and quality for Shona/Ndebele depend on installed device voices; untranslated content remains in English where a translation is not supplied.
- Validators can send a 10 km community witness query from an incident card. Public visitors opt in by sharing GPS and can answer YES/NO. The validation portal displays YES witness count and NO count. One response per browser cookie is accepted per query; this is a lightweight community signal, not proof of an incident.
- SOS triage includes a transparent rules-based risk indicator. It is not a medical diagnosis or a substitute for a human dispatcher.
- USSD gateway adapter: `POST /api/integrations/ussd`. Configure `RG_USSD_TOKEN` and have the Zimbabwe mobile-network/USSD aggregator send `X-USSD-Token`. Gateway sequence accepts `text` such as `1*location*description`; map your aggregator's callback contract as needed. USSD SOS entries have descriptive (not GPS-confirmed) locations.
- Official authority alert webhook: `POST /api/integrations/authority/alerts`, authenticated by `X-Authority-Token` matching `RG_AUTHORITY_TOKEN`. This endpoint accepts alerts from an authority system; it does not create an official connection by itself. Obtain written authorization, a feed/API agreement and credentials from the relevant authority before enabling it.
- Set `PORT=5002` to override the default. Provider credentials are not included in the ZIP.


## Voice-guided, low-bandwidth SOS

The public SOS button now opens a guided voice conversation. It uses the browser Speech Recognition API where available and browser speech synthesis for spoken prompts; typed answers remain available as a fallback. GPS is requested with permission. The conversation is submitted to the existing `/api/sos` endpoint and therefore continues to appear in the existing validator SOS queue.

For supported browsers, the client sends gzip-compressed JSON using `application/vnd.roadguard.sos+gzip`; the Flask endpoint decompresses it and passes the same canonical fields to the existing database/validator workflow. If compression is unavailable or does not reduce the payload, the client sends ordinary JSON. Voice recognition requires a supported browser, microphone permission and a secure context (HTTPS or localhost). This SOS feature notifies the RoadGuard validator desk; it does not itself contact or dispatch emergency services.
