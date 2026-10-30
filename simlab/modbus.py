"""A compact, dependency-free Modbus/TCP server that exposes the simulated
devices so trainees can drive the sandbox with real OT tooling (pymodbus,
modpoll, Metasploit modbus modules, etc.) instead of only the dashboard.

SIMULATION ONLY. This server speaks Modbus framing but every register is
backed by an in-memory model in SimEngine. It mirrors a deliberately insecure
reality: Modbus has no authentication, so writes are accepted as-is and tagged
in the event log as unauthenticated protocol writes. Nothing real is touched.

Register map (16-bit; floats are scaled x10). Unit IDs select the facility:

  Unit 1  Power Plant
    HR0  turbine setpoint MW (x10, rw)      C0  main breaker closed (rw)
    IR0  generation MW (x10)   IR1 demand MW (x10)   IR2 grid ok
  Unit 2  Water Treatment
    HR0  pump speed % (x10, rw)   HR1 chlorine dose ppm (x10, rw)
    C0   pump running (rw)
    IR0  pressure psi (x10)   IR1 water quality (0 safe/1 chem/2 untreated)
  Unit 3  Hospital
    HR0  O2 psi (x10, rw)   HR1 HVAC temp F (x10, rw)   HR2 gen fuel min (rw)
    C0   transfer switch AUTO (rw; write 0 = lock to utility)
    IR0  hospital powered   IR1 on generator   IR2 gen fuel min
  Unit 4  Fort Marin
    HR0  fuel farm level % (x10, rw)
    C0   perimeter gates locked (rw; write 0 = unlock/breach)
    C1   C2/radar link up (rw; write 0 = comms down)
    IR0  base powered   IR1 comms up   IR2 perimeter secure   IR3 readiness(0-3)
"""
from __future__ import annotations

import asyncio
import struct

UNIT_FACILITY = {1: "power", 2: "water", 3: "hospital", 4: "base"}
_READINESS = {"full": 0, "degraded": 1, "compromised": 2, "dark": 3}


def _u16(v):
    try:
        return max(0, min(65535, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


class RegisterMap:
    """Translates Modbus register access to/from the SimEngine."""

    def __init__(self, engine):
        self.e = engine

    # ---- reads -------------------------------------------------------------
    def read_holding(self, unit, addr):
        d = self.e.town["devices"]
        m = {
            (1, 0): _u16((d["pp_gov"]["state"]["setpoint_mw"]) * 10),
            (2, 0): _u16(d["wt_pump"]["state"]["speed_pct"] * 10),
            (2, 1): _u16(d["wt_dose"]["state"]["dose_ppm"] * 10),
            (3, 0): _u16(d["hp_o2"]["state"]["o2_psi"] * 10),
            (3, 1): _u16(d["hp_bms"]["state"]["temp_f"] * 10),
            (3, 2): _u16(d["hp_ats"]["state"]["gen_fuel_min"]),
            (4, 0): _u16(d["fb_fuel"]["state"]["level_pct"] * 10),
        }
        return m.get((unit, addr))

    def read_input(self, unit, addr):
        g = self.e.derived
        b = g.get("base", {})
        m = {
            (1, 0): _u16(g["generation_mw"] * 10), (1, 1): _u16(g["demand_mw"] * 10),
            (1, 2): 1 if g["grid_ok"] else 0,
            (2, 0): _u16(g["water_pressure"] * 10),
            (2, 1): {"safe": 0, "unsafe_chemical": 1, "unsafe_untreated": 2}.get(g["water_quality"], 0),
            (3, 0): 1 if g["hospital_powered"] else 0, (3, 1): 1 if g["hospital_on_generator"] else 0,
            (3, 2): _u16(g["hospital_gen_fuel_min"]),
            (4, 0): 1 if b.get("powered") else 0, (4, 1): 1 if b.get("comms") else 0,
            (4, 2): 1 if b.get("perimeter_secure") else 0, (4, 3): _READINESS.get(b.get("readiness"), 0),
        }
        return m.get((unit, addr))

    def read_coil(self, unit, addr):
        d = self.e.town["devices"]
        m = {
            (1, 0): d["pp_bkr"]["state"]["closed"] in (1, 1.0, True),
            (2, 0): (d["wt_pump"]["state"]["speed_pct"] or 0) > 0,
            (3, 0): d["hp_ats"]["state"]["mode"] == "auto",
            (4, 0): d["fb_perim"]["state"]["gates_locked"] in (1, 1.0, True),
            (4, 1): d["fb_c2"]["state"]["link_up"] in (1, 1.0, True),
        }
        return m.get((unit, addr))

    # ---- writes ------------------------------------------------------------
    def write_holding(self, unit, addr, raw):
        d = self.e.town["devices"]
        table = {
            (1, 0): ("pp_gov", "setpoint_mw", 10.0),
            (2, 0): ("wt_pump", "speed_pct", 10.0),
            (2, 1): ("wt_dose", "dose_ppm", 10.0),
            (3, 0): ("hp_o2", "o2_psi", 10.0),
            (3, 1): ("hp_bms", "temp_f", 10.0),
            (3, 2): ("hp_ats", "gen_fuel_min", 1.0),
            (4, 0): ("fb_fuel", "level_pct", 10.0),
        }
        if (unit, addr) not in table:
            return False
        did, param, scale = table[(unit, addr)]
        val = raw / scale
        d[did]["state"][param] = val
        self._log(f"wrote {did}.{param} = {val} (HR{addr})")
        return True

    def write_coil(self, unit, addr, on):
        d = self.e.town["devices"]
        key = (unit, addr)
        if key == (1, 0):
            d["pp_bkr"]["state"]["closed"] = 1 if on else 0
            self._log(f"main breaker {'CLOSED' if on else 'OPEN'} (coil)")
        elif key == (2, 0):
            d["wt_pump"]["state"]["speed_pct"] = 85.0 if on else 0.0
            self._log(f"pump {'STARTED' if on else 'STOPPED'} (coil)")
        elif key == (3, 0):
            d["hp_ats"]["state"]["mode"] = "auto" if on else "locked_utility"
            self._log(f"hospital ATS {'AUTO' if on else 'LOCKED to utility'} (coil)")
        elif key == (4, 0):
            d["fb_perim"]["state"]["gates_locked"] = 1 if on else 0
            self._log(f"perimeter gates {'LOCKED' if on else 'UNLOCKED'} (coil)")
        elif key == (4, 1):
            d["fb_c2"]["state"]["link_up"] = 1 if on else 0
            self._log(f"base C2/radar link {'UP' if on else 'DOWN'} (coil)")
        else:
            return False
        return True

    def _log(self, msg):
        self.e._log("protocol", "MODBUS", f"Unauthenticated Modbus write: {msg}.", "attack")

    def describe(self):
        return {
            "units": [
                {"unit": 1, "facility": "Power Plant",
                 "holding": {"HR0": "turbine setpoint MW (x10)"},
                 "coils": {"C0": "main breaker closed"},
                 "input": {"IR0": "generation MW (x10)", "IR1": "demand MW (x10)", "IR2": "grid ok"}},
                {"unit": 2, "facility": "Water Treatment",
                 "holding": {"HR0": "pump speed % (x10)", "HR1": "chlorine dose ppm (x10)"},
                 "coils": {"C0": "pump running"},
                 "input": {"IR0": "pressure psi (x10)", "IR1": "quality 0/1/2"}},
                {"unit": 3, "facility": "Hospital",
                 "holding": {"HR0": "O2 psi (x10)", "HR1": "HVAC temp F (x10)", "HR2": "gen fuel min"},
                 "coils": {"C0": "transfer switch AUTO"},
                 "input": {"IR0": "powered", "IR1": "on generator", "IR2": "gen fuel min"}},
                {"unit": 4, "facility": "Fort Marin",
                 "holding": {"HR0": "fuel farm level % (x10)"},
                 "coils": {"C0": "perimeter gates locked", "C1": "C2/radar link up"},
                 "input": {"IR0": "powered", "IR1": "comms up", "IR2": "perimeter secure", "IR3": "readiness 0-3"}},
            ],
        }


# --- exceptions -------------------------------------------------------------
ILLEGAL_FUNCTION = 1
ILLEGAL_ADDRESS = 2


class ModbusServer:
    def __init__(self, engine, host, port, on_change=None):
        self.regs = RegisterMap(engine)
        self.host, self.port = host, port
        self.on_change = on_change  # async callable invoked after a write
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle, self.host, self.port)

    async def _handle(self, reader, writer):
        try:
            while True:
                header = await reader.readexactly(6)
                tid, pid, length = struct.unpack(">HHH", header)
                rest = await reader.readexactly(length)
                unit = rest[0]
                pdu = rest[1:]
                resp_pdu, changed = self._dispatch(unit, pdu)
                out = struct.pack(">HHHB", tid, 0, len(resp_pdu) + 1, unit) + resp_pdu
                writer.write(out)
                await writer.drain()
                if changed and self.on_change:
                    await self.on_change()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def _exc(self, fc, code):
        return struct.pack(">BB", fc | 0x80, code)

    def _protocol_gate(self, unit):
        """Returns (allowed, detect_defense). Scores the attempt; logs/blocks."""
        e = self.regs.e
        fac = UNIT_FACILITY[unit]
        block, detect = e.eval_protocol_defense(fac)
        e.score["attempts"] += 1
        if block:
            e.score["blocked"] += 1
            e._log("defense", block["id"],
                   f"Unauthenticated Modbus write to {fac} BLOCKED by {block['name']} ({block['id']}).",
                   "defense")
            return False, detect
        return True, detect

    def _protocol_result(self, unit, detect):
        e = self.regs.e
        fac = UNIT_FACILITY[unit]
        if detect:
            e.score["detected"] += 1
            e._log("defense", detect["id"],
                   f"Unauthenticated Modbus write to {fac} DETECTED by {detect['name']} ({detect['id']}).",
                   "detect")
        else:
            e.score["undetected"] += 1

    def _dispatch(self, unit, pdu):
        if not pdu:
            return self._exc(0, ILLEGAL_FUNCTION), False
        fc = pdu[0]
        if unit not in UNIT_FACILITY:
            return self._exc(fc, ILLEGAL_ADDRESS), False
        try:
            if fc in (1, 2):  # read coils / discrete inputs
                addr, qty = struct.unpack(">HH", pdu[1:5])
                bits = []
                for a in range(addr, addr + qty):
                    v = self.regs.read_coil(unit, a)
                    if v is None:
                        return self._exc(fc, ILLEGAL_ADDRESS), False
                    bits.append(1 if v else 0)
                nbytes = (qty + 7) // 8
                packed = bytearray(nbytes)
                for i, bit in enumerate(bits):
                    if bit:
                        packed[i // 8] |= (1 << (i % 8))
                return struct.pack(">BB", fc, nbytes) + bytes(packed), False
            if fc in (3, 4):  # read holding / input registers
                addr, qty = struct.unpack(">HH", pdu[1:5])
                regs = []
                for a in range(addr, addr + qty):
                    v = self.regs.read_holding(unit, a) if fc == 3 else self.regs.read_input(unit, a)
                    if v is None:
                        return self._exc(fc, ILLEGAL_ADDRESS), False
                    regs.append(v)
                return struct.pack(">BB", fc, qty * 2) + b"".join(struct.pack(">H", r) for r in regs), False
            if fc == 5:  # write single coil
                addr, val = struct.unpack(">HH", pdu[1:5])
                allowed, detect = self._protocol_gate(unit)
                if not allowed:
                    self.regs.e._recompute()
                    return self._exc(fc, 0x04), True  # SLAVE DEVICE FAILURE: write rejected
                ok = self.regs.write_coil(unit, addr, val == 0xFF00)
                if not ok:
                    return self._exc(fc, ILLEGAL_ADDRESS), False
                self._protocol_result(unit, detect)
                self.regs.e._recompute()
                return pdu[:5], True
            if fc == 6:  # write single register
                addr, val = struct.unpack(">HH", pdu[1:5])
                allowed, detect = self._protocol_gate(unit)
                if not allowed:
                    self.regs.e._recompute()
                    return self._exc(fc, 0x04), True
                ok = self.regs.write_holding(unit, addr, val)
                if not ok:
                    return self._exc(fc, ILLEGAL_ADDRESS), False
                self._protocol_result(unit, detect)
                self.regs.e._recompute()
                return pdu[:5], True
            if fc == 16:  # write multiple registers
                addr, qty = struct.unpack(">HH", pdu[1:5])
                vals = struct.unpack(">" + "H" * qty, pdu[6:6 + qty * 2])
                allowed, detect = self._protocol_gate(unit)
                if not allowed:
                    self.regs.e._recompute()
                    return self._exc(fc, 0x04), True
                for i, v in enumerate(vals):
                    if not self.regs.write_holding(unit, addr + i, v):
                        return self._exc(fc, ILLEGAL_ADDRESS), False
                self._protocol_result(unit, detect)
                self.regs.e._recompute()
                return struct.pack(">BHH", fc, addr, qty), True
            return self._exc(fc, ILLEGAL_FUNCTION), False
        except struct.error:
            return self._exc(fc, ILLEGAL_ADDRESS), False
