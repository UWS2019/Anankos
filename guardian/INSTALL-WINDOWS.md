# Installing Anankos Guardian on Windows

The download is the app's source code, so there is a quick one-time setup before
you get a normal double-click program. It takes about 10 minutes, most of it
waiting.

## Step 1 — Install Node.js (one time, free)

Node.js is the free engine the app runs on.

1. Go to **https://nodejs.org**
2. Click the big button labelled **"LTS"** (the recommended version).
3. Run the downloaded installer and click **Next → Next → Install** (the defaults
   are fine).

You only ever do this once.

## Step 2 — Unzip the download

1. Right-click `anankos-guardian.zip` → **Extract All… → Extract**.
2. Open the folder it creates. You will see files including **`RUN-WINDOWS.bat`**
   and **`BUILD-WINDOWS.bat`**.

## Step 3 — Pick one

### To just try it right now
Double-click **`RUN-WINDOWS.bat`**.
- The first time, a black window appears and downloads components for a few
  minutes. Leave it open.
- Then the Guardian app window opens. (Keep the black window open while you use
  it; closing it closes the app.)

### To make a proper installed app (Start-menu icon, double-click to open)
Double-click **`BUILD-WINDOWS.bat`**.
- It downloads components, then builds your installer (a few minutes).
- When it finishes it opens a **`release`** folder.
- Double-click the file named **`Anankos Guardian Setup 1.0.0.exe`** to install it
  like any normal program. After that it lives in your Start menu and opens with a
  click — the black window is no longer needed.

## Things you may see (all normal)

- **"Windows protected your PC" (blue SmartScreen box):** this appears because the
  app is not code-signed yet. Click **More info → Run anyway**. (Code signing is a
  paid certificate we can add before any public release.)
- **A warning from your antivirus:** security tools sometimes flag *any* program
  that lists network devices. Guardian only looks at your own network and changes
  nothing on its own; you can allow it.
- **Windows Firewall asks to allow it:** allow it on **Private** networks so it can
  see your home devices.

## Tips

- Run it on the computer that is actually on your **home Wi-Fi/Ethernet** — not a
  work VPN — so it sees your real devices.
- The **"Harden this PC"** firewall button works best if you right-click the app
  and choose **Run as administrator**; otherwise it will just show you the exact
  command to run.

Stuck on any step? Send me the text from the black window and I'll sort it out.
