# Anankos Sim Lab

A **civilian critical-infrastructure consequence simulator** — a 2D "sand table"
of a fictional town whose IT/OT systems you can manipulate to study how a
failure or intrusion cascades into real-world civic outcomes. Built for
training, tabletop exercises, and resilience assessment.

> **Simulation only.** Every "device" in this lab is an in-memory software
> model. Nothing here speaks a real control protocol, touches a real network,
> or controls real equipment. It cannot be pointed at anything outside itself.

---

## What's in v1

- **Sand-table city** — a live 2D map of **Marin**. Buildings light up or go
  dark, power lines and water pipes flow / dry out / turn red, and the hospital
  pulses when lives are at risk. The wider city (Downtown/Government, Airport,
  Sports Arena, Industrial Park, **Fort Marin military base**, Residential)
  reacts to grid and water status.
- **Three deeply-modeled facilities**, wired through a dependency cascade:
  **Riverside Power Plant → Northgate Water Treatment → St. Marin General Hospital**.
- **MITRE ATT&CK for ICS** console — a focused 10-technique library. Each
  action runs a mini kill chain: **Initial Access → Impact**, and every event
  is tagged with its ATT&CK ID.
- **Consequence engine** — a tick-based model (1 tick = 1 simulated minute)
  that scores lives-at-risk, grid balance, water pressure/quality, hospital
  power and backup-generator fuel, and a town severity level
  (nominal → degraded → severe → catastrophic).

### Technique library (v1)

| ID | Technique | Tactic | Effect in sim |
|----|-----------|--------|---------------|
| T0812 | Default Credentials | Initial Access | Compromise a facility (unlocks impact) |
| T0866 | Exploitation of Remote Services | Initial Access | Compromise a facility |
| T0836 | Modify Parameter | Impair Process Control | Change a setpoint (e.g. chlorine dose, turbine MW) |
| T0855 | Unauthorized Command Message | Impair Process Control | Open breaker / stop pump / force transfer switch |
| T0831 | Manipulation of Control | Impair Process Control | Override an actuator setpoint |
| T0835 | Manipulate I/O Image | Impair Process Control | Falsify a controller's I/O value |
| T0856 | Spoof Reporting Message | Impair Process Control | HMI shows normal while reality diverges |
| T0814 | Denial of Service | Inhibit Response Function | Make a device unresponsive |
| T0816 | Device Restart/Shutdown | Inhibit Response Function | Force a device offline |
| T0880 | Loss of Safety | Impact | Defeat a safety function |

---

## Run it

### Windows — one click
Double-click **`start.bat`**. On first run it creates a virtual environment,
installs dependencies, opens your browser, and starts the internal server on
**port 47811** (an uncommon high port), bound to all interfaces.

### Linux / macOS
```bash
./start.sh
```

Then open **http://127.0.0.1:47811**.

## Reach it from your phone (Tailscale)

The server binds to `0.0.0.0`, so any device on your tailnet can reach it —
nothing is exposed to the public internet.

1. Install Tailscale on the host and on your phone; sign both into the same
   tailnet and run `tailscale up`.
2. The launcher prints the host's Tailscale URL
   (`http://100.x.y.z:47811`). Open that on your phone.

> Keep this on a private tailnet. Do not port-forward 47811 to the internet.

---

## How a scenario works

1. Press **Start** (the clock begins advancing).
2. In the **ATT&CK Console**, pick an **Initial Access** technique (T0812 /
   T0866) and a target device to compromise a facility.
3. Pick an **Impact** technique against a device in that facility — e.g.
   *T0855 Unauthorized Command Message → open the power plant's main breaker*,
   or *T0836 Modify Parameter → drive the chlorine dose to 15 ppm*.
4. Watch the sand table and the stat row react, and read the tagged
   consequences in the **Event Log**.
5. **Restore** a device (or Reset) to recover and watch the town stabilize.

Example cascade: open the main breaker → grid collapses → hospital transfers to
its backup generator → generator fuel drains → hospital goes dark and water
pressure bleeds off → town status reaches **catastrophic**.

---

## Architecture

```
simlab/
  app.py          FastAPI: REST + WebSocket + static hosting
  engine.py       tick engine, technique application, cascade + scoring
  town.py         facilities, simulated devices, city layout
  data/
    techniques.json   MITRE ATT&CK for ICS technique library (data-driven)
web/
  index.html      sand-table dashboard
  app.js          WebSocket client + data-driven SVG city
  styles.css
start.bat         one-click Windows launcher
start.sh          Linux/macOS launcher
```

Config via env vars: `ANANKOS_HOST` (default `0.0.0.0`),
`ANANKOS_PORT` (default `47811`), `ANANKOS_TICK_SECONDS` (default `1.0`).

---

## Roadmap (discussed, not yet built)

- **Blue team / MITRE D3FEND** — detections, mitigations, and red/blue scoring.
- **Deepen the city** — promote ambient districts (military base, airport,
  government, manufacturing) to fully-modeled facilities with their own devices.
- **Simulated protocol endpoints** — let trainees drive the sandbox with
  external tooling instead of only the UI.
- **Scenario save/replay** and multi-user exercises with roles.
