# Anankos Sim Lab — Quickstart

A self-contained critical-infrastructure consequence simulator. Everything runs
locally; nothing touches real systems.

## 1. Run it

**Windows:** double-click **`start.bat`**
**Mac / Linux:** `./start.sh`

First run creates a Python virtual environment and installs two packages
(FastAPI + Uvicorn), opens your browser, and starts the server on
**http://127.0.0.1:47811**. (Requires Python 3.10+ installed and on PATH.)

> Reach it from your phone: install Tailscale on this machine and your phone,
> `tailscale up` on both, then open the Tailscale URL the launcher prints.
> Keep it on your private tailnet — don't port-forward to the internet.

## 2. A 5-minute test drive

1. **Describe a scenario** — in the **Scenario Console** at the top, click a
   preset chip (e.g. *Telecom supply-chain compromise*) or type your own in
   plain English, then press **▶ Run Scenario**. The lab compiles it into a
   MITRE-tagged kill chain and **auto-plays it** on the sand table — watch the
   narration strip and the city react to **CATASTROPHIC**. (⌘/Ctrl-Enter runs it.)
2. **Try your own words** — e.g. *"An attacker finds the water plant's dosing
   controller exposed to the internet and drives the chlorine dose unsafe,"* or
   *"Cut the fiber, jam the microwave and cellular links, and spoof GPS."* The
   parser works fully offline; set `ANANKOS_API_KEY` for richer AI parsing.
3. **Report** — click **📄 Report** (top bar) for a printable assessment with
   the attack narrative, ATT&CK coverage, and D3FEND recommendations.
4. **Go manual (optional)** — click **⚙ Advanced** to open the drawer with the
   ATT&CK console, Blue-Team D3FEND defenses, Findings authoring, and the OT
   Protocol/Modbus card. Deploy defenses, then re-run a scenario to see the
   before/after (e.g. segmentation blocks the telecom pivot).
5. **Build a client site** — click **🧩 Builder**. Add/remove facilities, drag
   to position, rename, then **Run in Simulator**. **Save** it as a JSON "site
   pack" to reload later.

## 3. Drive it with real OT tooling (optional)

A simulated Modbus/TCP endpoint runs on **port 15020**. The dashboard's
*OT Protocol Endpoint* card shows the register map and copy-paste examples, e.g.
with [pymodbus](https://pymodbus.readthedocs.io):

```bash
pip install pymodbus
# poison the water: unit 2 (water), holding reg 1, value 150 = 15.0 ppm
python -c "from pymodbus.client import ModbusTcpClient as C; c=C('127.0.0.1',port=15020); c.write_register(1,150,slave=2)"
```

Writes land in the event log tagged `MODBUS` and drive the city — unless you've
deployed `D3-OAM`, which rejects them.

## 4. Run the tests

```bash
# from the project folder, with the venv active (start.* creates .venv)
python tests/parity.py    # physics regression (locks the cascade behavior)
python tests/catalog.py   # catalog validation + site-build reproduction
```

## 5. Where to tinker

- **Add/adjust techniques:** `simlab/data/techniques.json`
- **Add/adjust defenses:** `simlab/data/defenses.json`
- **Facility templates & device defaults:** `simlab/data/templates.json`
- **Archetype behaviors (physics):** `simlab/archetypes.py`
- **Consequence scoring / severity:** `simlab/engine.py`
- **Sand-table visuals:** `web/city.js`
- **The Marin reference site:** `simlab/data/site_marin.json`

See **README.md** for the full architecture. If you change physics, run
`tests/parity.py` — it will tell you exactly what moved.

---
*Simulation only. Every device is an in-memory model; nothing here speaks a real
control protocol or touches real equipment.*
