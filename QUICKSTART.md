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

1. **Press ▶ Start** (top bar). The clock advances; T+ counts simulated minutes.
2. **Run an attack** — in the ATT&CK Console (right), the kill chain is gated:
   - `T1566 Phishing` on *Plant Business Network* → IT foothold.
   - `T1210 Exploitation of Remote Services` on the same → pivot to OT.
   - `T0855 Unauthorized Command Message` on *Main Breaker RTU* → command
     `open_breaker`. Watch the grid go red and the hospital fall to its
     generator; let it run and the hospital goes dark → **CATASTROPHIC**.
3. **Mirror a finding** — in *Assessment / Findings*, pick **Water**, tick
   **internet exposed**. Now `T0883 Internet Accessible Device` works directly
   on *Chlorine Dosing PLC*; then `T0836 Modify Parameter` → `dose_ppm` = `15`.
   The water plant shows a red chemical plume.
4. **Defend** — in *Blue Team*, **deploy** `D3-SIS` (Safety Instrumented System)
   and `D3-OAM` (Command Authentication), then repeat the chlorine attack: it's
   clamped back to safe and the scoreboard logs it. Run the same scenario with
   defenses off vs on to see the before/after.
5. **Report** — click **📄 Report** (top bar) for a printable assessment with
   the attack narrative, ATT&CK coverage, and D3FEND recommendations. Print to
   PDF from the browser.
6. **Build a client site** — click **🧩 Builder**. Remove facilities you don't
   need, drag to reposition, rename, then **Run in Simulator**. Open the
   dashboard to see the sim running on your custom topology. **Save** it as a
   JSON "site pack" to reload later.

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
