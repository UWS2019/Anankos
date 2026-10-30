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
- **Four deeply-modeled facilities**, wired through a dependency cascade:
  **Riverside Power Plant → Northgate Water Treatment → St. Marin General
  Hospital**, and **Fort Marin (military base)** — power, fuel farm, C2/radar,
  and perimeter security, with readiness scoring and a knock-on to airport
  radar/ATC.
- **MITRE ATT&CK console** — a 19-technique library spanning the **Enterprise
  IT chain and the ATT&CK for ICS chain**. Actions follow a realistic kill
  chain: **IT Initial Access → IT→OT Pivot → OT Access → Impact**, gated by
  preconditions, with every event tagged by ATT&CK ID.
- **Assessment / Findings authoring** — set the exact conditions you found on
  the ground (internet-exposed device, default creds, flat network, unpatched;
  plus direct device-state and parameter authoring) so the lab mirrors reality
  and demonstrates the resulting outcome to a client. **Export / import**
  whole scenarios as JSON to prep and replay client-specific engagements.
- **Consequence engine** — a tick-based model (1 tick = 1 simulated minute)
  that scores lives-at-risk, grid balance, water pressure/quality, hospital
  power and backup-generator fuel, and a town severity level
  (nominal → degraded → severe → catastrophic).

### Technique library

The kill chain is gated: **IT access → pivot → OT access → impact**. OT access
needs either an IT foothold (pivot) or an `internet exposed` finding on the
target.

**Enterprise (IT):** T1190 Exploit Public-Facing Application · T1566 Phishing ·
T1133 External Remote Services · T1078 Valid Accounts (Initial Access) ·
T1210 Exploitation of Remote Services (IT→OT pivot).

**ICS (OT) — access:** T0883 Internet Accessible Device · T0812 Default
Credentials · T0866 Exploitation of Remote Services.

**ICS (OT) — impact:** T0836 Modify Parameter · T0855 Unauthorized Command
Message · T0831 Manipulation of Control · T0835 Manipulate I/O Image · T0856
Spoof Reporting Message · T0813 Denial of View · T0814 Denial of Service ·
T0816 Device Restart/Shutdown · T0826 Loss of Availability · T0827 Loss of
Control · T0880 Loss of Safety.

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

## Drive it with real OT tooling (simulated Modbus/TCP)

The lab also exposes a **simulated, dependency-free Modbus/TCP endpoint** on
**port 15020**, backed by the same device models. Trainees can point standard
OT tooling (pymodbus, modpoll, Metasploit `auxiliary/scanner/scada/modbusclient`)
at the sandbox and watch the city react live. Modbus has no authentication, so
writes are accepted as-is and logged as `MODBUS` events — mirroring the real
risk of an exposed control network. **It never touches real equipment.**

Register map (16-bit; floats scaled ×10). Unit ID selects the facility:
`1` Power · `2` Water · `3` Hospital · `4` Fort Marin. The dashboard's
**OT Protocol Endpoint** card shows the full map and live examples, e.g.:

```bash
# poison the water (chlorine dose 15.0 ppm): unit 2, holding reg 1, value 150
python -c "from pymodbus.client import ModbusTcpClient as C; \
  c=C('127.0.0.1',port=15020); c.write_register(1,150,slave=2)"

# open the power plant main breaker: unit 1, coil 0 OFF
python -c "from pymodbus.client import ModbusTcpClient as C; \
  c=C('127.0.0.1',port=15020); c.write_coil(0,False,slave=1)"

# read Fort Marin status (unit 4 input registers)
modpoll -m tcp -t 3 -r 1 -c 4 -0 127.0.0.1:15020 -a 4
```

Set `ANANKOS_MODBUS=0` to disable the endpoint, or `ANANKOS_MODBUS_PORT` to
change the port.

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
  app.py          FastAPI: REST + WebSocket + static hosting + Modbus startup
  engine.py       tick engine, technique application, cascade + scoring + report data
  town.py         facilities, simulated devices, city layout
  modbus.py       dependency-free simulated Modbus/TCP server + register map
  report.py       standalone printable assessment-report renderer
  data/
    techniques.json   MITRE ATT&CK technique library (data-driven)
web/
  index.html      sand-table dashboard
  city.js         sand-table renderer (buildings, roads, responders, zoom/pan)
  app.js          WebSocket client, ATT&CK console, findings authoring
  styles.css
start.bat         one-click Windows launcher
start.sh          Linux/macOS launcher
```

Config via env vars: `ANANKOS_HOST` (default `0.0.0.0`),
`ANANKOS_PORT` (default `47811`), `ANANKOS_TICK_SECONDS` (default `1.0`),
`ANANKOS_MODBUS` (`1`/`0`), `ANANKOS_MODBUS_PORT` (default `15020`).

---

## Assessment report

Click **📄 Report** in the dashboard (or open `/api/report.html`) to generate a
standalone, printable **assessment report** from the current run: executive
summary (worst severity, peak lives-at-risk, action counts), a critical
service-loss timeline, the full ATT&CK-tagged attack narrative, a MITRE ATT&CK
coverage matrix grouped by tactic, and a facility posture + findings table.
It prints cleanly to PDF from the browser, so a run becomes a client
deliverable. `/api/report` returns the same data as JSON.

## Roadmap (discussed, not yet built)

- **Blue team / MITRE D3FEND** — detections, mitigations, and red/blue scoring.
- **Deepen more districts** — promote airport, government, and manufacturing to
  fully-modeled facilities with their own devices (Fort Marin already done).
- **More protocols** — DNP3 / IEC-104 alongside the Modbus surface.
- **Multi-user exercises** with roles.
