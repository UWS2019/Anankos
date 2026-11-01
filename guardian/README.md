# Anankos Guardian — Home Network Sentinel

A one-click desktop app that audits the security of **your own home network**. It
finds every device connected to your router, checks each one for risky or
exposed services, inspects your Wi-Fi encryption, then explains anything it
finds in plain language and walks you through fixing it.

Built for two kinds of people:

- **Simple mode** — one big *Scan My Network* button. No jargon. You get a
  Network Health score and a short list of "here's what to fix", each with
  numbered steps and a button that opens your router's settings page.
- **Expert mode** — custom port ranges, single-target scans, tunable timeouts,
  full device/port/service detail, and report exports.

It also includes:

- **Report export** — save any scan as a polished, self-contained **HTML** or
  **PDF** report (brand-styled, printable, includes the device list and the AI
  debrief prompt), or the raw **JSON** for your own tooling.
- **AI debrief prompt** — one click builds a ready-to-paste prompt containing
  your findings. Drop it into ChatGPT, Claude, Gemini, or a local model and it
  explains everything in plain language and tells you what to do first. The app
  sends nothing itself — you choose where to paste it — and it warns that the
  text contains your local device addresses.
- **Operator Console (expert)** — schedule automatic **re-scans** on an interval
  (15 min → 24 h), with a live activity log, a persistent **scan history** trend
  table, and CSV export. The schedule is saved and resumes when the app reopens.
- **Harden this PC** — optional one-click fixes for **this computer only** (never
  other devices). v1 detects whether the built-in OS firewall is on and can turn
  it on for you; if the change needs administrator rights, it hands you the exact
  command instead. Safe and reversible.
- **Send debrief to your own AI (optional)** — instead of copy-pasting, point the
  app at any **OpenAI-compatible** endpoint (OpenAI, OpenRouter, LM Studio,
  Ollama) or **Anthropic**, and it returns the plain-language explanation in-app.
  Your endpoint, model and key are stored **only on this computer** and used only
  when you press *Send*. Requests go **only** to the address you entered.
- **Live Monitor dashboard** — a premium, real-time view: colored stat tiles
  (devices, online, open services, alerts, health, last scan), live **CPU and RAM
  gauges** for this machine with per-core bars and a CPU sparkline, and a
  **device-presence grid** showing every device with an online/at-risk status
  dot. The local metrics update every couple of seconds (`src/scanner/sysmon.js`,
  read-only from the OS); the device grid comes from your latest scan and a
  one-click *Refresh devices*.

---

## Scope & intent (please read)

Guardian is a **defensive auditing tool for networks you own or are authorized
to assess.** By design it only ever looks at devices on the **local subnet of
the machine it runs on** — i.e. the things behind your own home router. It:

- never scans the internet or any address outside your local network;
- never attempts passwords, logins, or exploitation — it **observes only**;
- never changes a setting on its own. Every fix is a *recommendation* with
  step-by-step instructions that **you** carry out.

Do not point it at networks you do not own or have written permission to test.

---

## How it works (no magic, no external tools)

Everything runs locally in Node — there are **no bundled scanners, no nmap, no
raw sockets, and no admin privileges required.** The techniques are the same
ordinary operations any networked app performs:

| Step | What it does | How |
|------|--------------|-----|
| Detect network | Finds your IP, subnet and router | `os.networkInterfaces()` + default route |
| Discover devices | Lists what's online | TCP "ping" sweep + the OS `arp` table (gives MACs → vendor) |
| Check services | Finds open/risky ports | Plain TCP connect scan of a curated port list |
| Check Wi-Fi | Reads your encryption type | Built-in OS command (`netsh` / `nmcli` / `airport`) |
| Analyze | Turns results into advice | A rules engine → findings + a 0–100 health score |

---

## Project layout

```
guardian/
├── package.json            app manifest + electron-builder config
├── build/icon.(svg|png)    app icon
└── src/
    ├── main.js             Electron main process (runs the engine)
    ├── preload.js          secure IPC bridge (window.guardian)
    ├── renderer/           the UI (branded HTML/CSS/JS, Simple + Expert)
    └── scanner/            the scan engine (pure Node, UI-independent)
        ├── netinfo.js        local network + subnet detection
        ├── discovery.js      host discovery (sweep + ARP)
        ├── ports.js          TCP connect port scan + catalog
        ├── wifi.js           Wi-Fi security read
        ├── oui.js            MAC → vendor lookup
        ├── checks.js         rules engine (findings + remediation + score)
        ├── debrief.js        builds the paste-into-any-LLM debrief prompt
        ├── report.js         self-contained HTML report (used for HTML + PDF)
        ├── history.js        scan-history + schedule persistence (JSON/CSV)
        ├── scheduler.js      operator scheduled re-scans
        ├── harden.js         optional one-click hardening of THIS pc (firewall)
        ├── ai.js             optional direct send to a user-configured LLM
        ├── sysmon.js         live local CPU/RAM/uptime metrics (Live Monitor)
        ├── index.js          orchestrator (emits progress)
        └── cli.js            headless runner (test without the GUI)
```

Scan history, the last full report, and the saved schedule live in Electron's
per-user `userData` directory (e.g. `%APPDATA%/anankos-guardian` on Windows,
`~/Library/Application Support/anankos-guardian` on macOS,
`~/.config/anankos-guardian` on Linux). Nothing is uploaded anywhere.

The scan engine is deliberately decoupled from Electron, so the exact same code
powers both the GUI and the CLI.

---

## Run it in development

```bash
cd guardian
npm install        # installs electron + electron-builder (needs internet)
npm start          # launches the desktop app
```

Try the engine headless, no GUI:

```bash
npm run scan:cli                         # simple scan, readable summary
node src/scanner/cli.js --json           # full JSON report
node src/scanner/cli.js --expert --ports 1-1024
node src/scanner/cli.js --target 192.168.1.1 --expert --ports 1-1024
```

> Note: a live scan talks to your LAN, so run it on your real home machine —
> not inside a locked-down CI/sandbox, where network probing is blocked.

---

## Build the downloadable installers

electron-builder produces a real click-to-install app per platform. Build each
target **on that OS** (or use a CI matrix):

```bash
npm run dist:win     # Windows  -> release/Anankos Guardian Setup x.y.z.exe
npm run dist:mac     # macOS    -> release/Anankos Guardian-x.y.z.dmg
npm run dist:linux   # Linux    -> release/Anankos Guardian-x.y.z.AppImage
```

Output lands in `release/`. The Windows build is a one-click NSIS installer that
adds desktop + Start-menu shortcuts; macOS is a drag-to-Applications DMG; Linux
is a portable AppImage you mark executable and double-click.

### A note on antivirus / SmartScreen

Any tool that enumerates network devices can trip heuristic antivirus or
Windows SmartScreen, especially before the installer is **code-signed**. For a
public release you'll want a code-signing certificate (Windows) and Apple
notarization (macOS). For personal/internal use the unsigned build runs fine
once you approve the first-run prompt.

---

## Extending it

- **More device names:** drop a `src/scanner/oui-extra.json` of `"aa:bb:cc":
  "Vendor"` entries; it's merged automatically.
- **More / different checks:** add rules in `src/scanner/checks.js` — each finding
  is just `{ id, severity, title, plain, why, steps[], routerLink }`.
- **Expert nmap upgrade:** `ports.js` is isolated, so swapping in an
  nmap-backed scanner for expert mode is a contained change.

---

_Part of the Anankos toolset. For auditing networks you own or are authorized to assess._
