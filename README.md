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
- **Six deeply-modeled facilities**, wired through a dependency cascade:
  **Riverside Power Plant → Northgate Water Treatment → St. Marin General
  Hospital**; **Fort Marin (military base)** — power, fuel farm, C2/radar,
  perimeter security, with readiness scoring; **Marin Regional Airport** —
  surveillance radar, ILS/navaids, and tower ATC, with airspace status
  (normal/degraded/closed); and **Marin Telecom Central Office** — a shared
  carrier with redundant **fiber + microwave + cellular** transport, a core
  router, and a **GPS timing** source. It carries SCADA/comms for every sector,
  so it is both a single point of failure and a **pivot into every utility**
  (see below).

### Telecom: signal attacks + provider pivot

The telecom carrier models four distinct signal attacks — **fiber cut**,
**microwave jam**, **cellular/RF jam**, and **GPS timing spoof** — plus a
**core routing (BGP) hijack**. Transport survives on any remaining bearer
(degraded), and goes down only when all bearers fail or routing is hijacked.

Because the carrier provides SCADA/comms to every utility, compromising its OT
opens **T1199 Trusted Relationship** — a pivot into *any* subscriber utility.
One telecom intrusion (phish → pivot) becomes OT footholds across power, water,
hospital, base, and airport at once. **Network Segmentation (D3-BDI)** blocks
the pivot; **Traffic Analysis (D3-NTA)** detects it.
- **MITRE ATT&CK console** — a 19-technique library spanning the **Enterprise
  IT chain and the ATT&CK for ICS chain**. Actions follow a realistic kill
  chain: **IT Initial Access → IT→OT Pivot → OT Access → Impact**, gated by
  preconditions, with every event tagged by ATT&CK ID.
- **Assessment / Findings authoring** — set the exact conditions you found on
  the ground (internet-exposed device, default creds, flat network, unpatched;
  plus direct device-state and parameter authoring) so the lab mirrors reality
  and demonstrates the resulting outcome to a client. **Export / import**
  whole scenarios as JSON to prep and replay client-specific engagements.
- **Blue team (MITRE D3FEND)** — deploy defensive countermeasures that
  **block, detect, or mitigate** red actions live, with red-vs-blue scoring
  (prevented / detected / undetected) and automatic **defensive
  recommendations** in the report for every technique left uncovered.
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
`1` Power · `2` Water · `3` Hospital · `4` Fort Marin · `5` Airport ·
`6` Telecom. The dashboard's **OT Protocol Endpoint** card shows the full map
and live examples, e.g.:

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
  engine.py       tick loop, technique application, consequence scoring + report
  town.py         facilities, simulated devices, city layout
  topology.py     the town as a data graph of archetype nodes + typed links
  archetypes.py   reusable node behaviors (generator, grid, pump, comms, …)
  solver.py       generic graph solver (demand pass + topological evaluation)
  catalog.py      archetype catalog + facility templates: load/validate/instantiate
  catalog_view.py read-only HTML view of the device library (builder palette)
  modbus.py       dependency-free simulated Modbus/TCP server + register map
  report.py       standalone printable assessment-report renderer
  data/
    techniques.json       MITRE ATT&CK technique library (data-driven)
    defenses.json         MITRE D3FEND countermeasure library (data-driven)
    archetype_catalog.json archetype metadata (ports, params, device roles, modbus)
    templates.json        facility templates (pre-wired archetype bundles)
    site_marin.json       reference site pack (Marin, built from templates)
tests/
  parity.py           physics regression test vs physics_golden.json
  catalog.py          catalog validation + site-instantiation reproduction test
web/
  index.html      sand-table dashboard
  city.js         sand-table renderer (buildings, roads, responders, zoom/pan)
  app.js          WebSocket client, ATT&CK console, findings authoring
  builder.html    drag-and-drop topology builder
  builder.js      builder canvas (palette, placement, auto-wiring, run)
  *.css
start.bat         one-click Windows launcher
start.sh          Linux/macOS launcher
```

Config via env vars: `ANANKOS_HOST` (default `0.0.0.0`),
`ANANKOS_PORT` (default `47811`), `ANANKOS_TICK_SECONDS` (default `1.0`),
`ANANKOS_MODBUS` (`1`/`0`), `ANANKOS_MODBUS_PORT` (default `15020`).

---

## Blue team (MITRE D3FEND)

Deploy countermeasures from the dashboard's **Blue Team** card and watch them
act on red actions in real time. Each defense maps to the ATT&CK techniques it
addresses and takes one of three effects:

- **block** — prevents the technique (e.g. *Network Segmentation* blocks the
  IT→OT pivot; *Credential Hardening/MFA* blocks default-credential access;
  *Operational Command Authentication* rejects unauthenticated Modbus writes),
- **detect** — the technique lands but an alert is raised and scored
  (*Network/Protocol Traffic Analysis*, *Process Variable Monitoring*),
- **mitigate** — the technique lands but the physical effect is bounded (a
  *Safety Instrumented System* clamps chlorine dose / medical-gas pressure to a
  safe envelope).

A live scoreboard tracks attacks, prevented, detected, and undetected. The
assessment report adds a **D3FEND posture** section (prevention/detection
rates, deployed countermeasures) and a **Defensive Recommendations** table —
for every technique exercised but not covered, the mapped D3FEND countermeasure
and a plain-English recommendation. Run the same scenario with and without
defenses to show a client the before/after.

## Assessment report

Click **📄 Report** in the dashboard (or open `/api/report.html`) to generate a
standalone, printable **assessment report** from the current run: executive
summary (worst severity, peak lives-at-risk, action counts), a critical
service-loss timeline, the full ATT&CK-tagged attack narrative, a MITRE ATT&CK
coverage matrix grouped by tactic, and a facility posture + findings table.
It prints cleanly to PDF from the browser, so a run becomes a client
deliverable. `/api/report` returns the same data as JSON.

## Data-driven topology (Phase 1)

The physical cascade no longer lives in hand-written per-facility code. The town
is a **graph of archetype nodes** (`topology.py`) — generator, grid bus, pump,
pressure tank, treatment, comms backbone, and sector archetypes — wired by
**typed links** (power / water / comms). A **generic solver** (`solver.py`)
sums electrical demand and evaluates nodes in topological order; the
Anankos-specific consequence scoring (lives, severity, milestones) reads the
resolved node states. Archetypes (`archetypes.py`) are reusable behaviors bound
to the same simulated devices the attacker manipulates, so the ATT&CK console,
Modbus endpoint, findings, and blue-team layers are unchanged. This is the
foundation for a future drag-and-drop topology builder (reconstruct a client's
own system topography from the archetype library). `tests/parity.py` proves the
solver reproduces the previous hand-written physics byte-for-byte.

## Device library & templates (Phase 2)

The archetype **catalog** (`data/archetype_catalog.json`) and **facility
templates** (`data/templates.json`) are the data a builder palette draws from.
Each archetype declares its typed ports, parameters, device roles, Modbus
register hints, and — computed at load time — the ATT&CK techniques its devices
expose and the D3FEND countermeasures that address them. Each template is a
pre-wired bundle of archetype nodes + devices (a whole facility) with declared
`consumes`/`provides` ports. A **site pack** (`data/site_marin.json`) places
templates and wires them into a town.

`catalog.py` loads, **validates** (catalog ⇄ runtime consistency), and
**instantiates** a site into the concrete device/node/link structures the engine
runs. `tests/catalog.py` proves the Marin site pack reproduces the live town +
topology byte-for-byte, so the data-driven palette is complete and correct.
Browse it at **`/catalog.html`** (or `/api/catalog`).

## Topology builder (Phase 3)

The **`/builder.html`** canvas lets you compose a client's system topography:
drag facility templates from the library onto the map, position and rename them,
and the power / water / comms links **auto-wire** from each facility's declared
ports. Hit **Run in Simulator** and the engine rebuilds onto that topology — the
sand table, ATT&CK console, Modbus endpoint, blue-team defenses, and report all
operate on the custom site. **Save / Load** a site as JSON (a client "site
pack"), or **Reset to Marin**.

The engine builds every run from a site pack via the catalog, so the default
town and a custom build take the same path. The consequence layer tolerates
absent facilities (a client with no military base or airport still scores and
renders correctly). Endpoints: `GET /api/site` (current spec), `POST /api/build`
(load a composed site). `tests/catalog.py` also verifies a subset site builds
and scores correctly, while `tests/parity.py` keeps the full-Marin physics
locked.

Current builder scope is one of each facility kind with auto-wired links;
multiple-of-a-kind, free-form link drawing, geospatial backgrounds, and Modbus
auto-provisioning for custom builds are the next increments.

## Roadmap (discussed, not yet built)

- **Builder v2** — multiple-of-a-kind facilities, free-form link drawing,
  per-device parameter editing on the canvas, and geospatial/floorplan
  backgrounds.
- **Modbus auto-provisioning** for custom builds (register maps generated from
  archetype hints) and more protocols (DNP3 / IEC-104).
- **Deepen more districts** — promote government and manufacturing to
  fully-modeled facilities (power, water, Fort Marin, airport, telecom done).
- **More protocols** — DNP3 / IEC-104 alongside the Modbus surface.
- **Multi-user exercises** with roles.
