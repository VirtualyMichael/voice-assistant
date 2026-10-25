# Windows 11 guide

Written for Windows 11 version 23H2 and 24H2. Menus differ a little on other builds.

## Basics

Windows 11 has a **Start menu** (the Windows key), a **taskbar** along the bottom, and **Settings** (Win+I) for almost every option. Quick toggles
for Wi-Fi, volume, Bluetooth and brightness live in **Quick Settings**: click the Wi-Fi, speaker and battery icons together at the right of the taskbar
(or press Win+A). Notifications and the calendar are in the clock area (Win+N).

## Do this

### Connect to Wi-Fi
1. Press Win+A to open Quick Settings.
2. Click the arrow next to the Wi-Fi tile.
3. Choose the network, tick **Connect automatically** if you want, click **Connect**, and type the password.

### Change the default sound output (speakers, headphones)
1. Press Win+A, then click the arrow next to the volume slider.
2. Choose the device under **Sound output**.
For per-app output: **Settings, System, Sound, Volume mixer**.

### Take a screenshot
1. Press Win+Shift+S.
2. Choose rectangle, window or full screen at the top of the screen, then drag if needed.
3. The picture is copied to the clipboard and a notification appears; click it to open Snipping Tool and save. Paste with Ctrl+V anywhere.
Print Screen alone copies the whole screen. Win+PrtScn saves the whole screen to **Pictures, Screenshots**.

### Uninstall a program
1. **Settings, Apps, Installed apps**.
2. Click the three dots next to the program, then **Uninstall**.

### Free up disk space
1. **Settings, System, Storage**.
2. Open **Temporary files**, tick what you can lose (not Downloads unless you checked it), click **Remove files**.
3. Turn on **Storage Sense** on the same page to do it automatically.

### Update Windows
**Settings, Windows Update, Check for updates**. Set **Active hours** under **Advanced options** so restarts do not interrupt you.

### Use several desktops
Win+Tab opens Task View; click **New desktop**. Switch with Ctrl+Win+Left or Right.

### Snap windows side by side
Drag a window to the left or right edge, or press Win+Left or Win+Right. Hover the maximise button to pick a layout.

### Set a default app (browser, PDF reader)
**Settings, Apps, Default apps**, choose the app, then set each file type or link type to it.

### Back up files
Sign in with a Microsoft account and turn on **OneDrive** folder backup: click the OneDrive cloud in the taskbar, **Settings, Sync and backup, Manage backup**.
For a full copy, use an external drive and **File History** (search "File History" in Start).

### Remove a stuck program
Press Ctrl+Shift+Esc to open Task Manager, select the program in **Processes**, click **End task**.

## Shortcuts

| Keys | What it does |
|---|---|
| Win | Start menu |
| Win+E | File Explorer |
| Win+I | Settings |
| Win+V | Clipboard history (turn it on the first time) |
| Win+. | Emoji picker |
| Win+L | Lock the PC |
| Win+D | Show the desktop |
| Win+Shift+S | Screenshot tool |
| Alt+Tab | Switch between windows |
| Ctrl+Shift+Esc | Task Manager |
| Win+Ctrl+Shift+B | Restart a frozen display driver (the screen flashes) |

## When it goes wrong

- **No sound:** right-click the speaker icon, **Sound settings**, check the right output is selected and not muted; try the Volume mixer; restart the audio service by
  restarting the PC if the device vanished.
- **Wi-Fi connected but no internet:** forget the network (Settings, Network and internet, Wi-Fi, Manage known networks) and join again; restart the router; in a
  terminal run `ipconfig /release` then `ipconfig /renew`.
- **PC is slow:** Task Manager, **Startup apps** tab, disable what you do not need; check the **Processes** tab for something using the disk or CPU constantly; keep
  15% of the drive free.
- **Windows Update fails:** Settings, System, Troubleshoot, Other troubleshooters, **Windows Update**, then restart and try again.
- **A program will not start:** right-click it, **Run as administrator**; reinstall it; check Settings, Windows Update for pending restarts.

## Where to look next

Microsoft Support (support.microsoft.com) and the Tips app on the Start menu. Search by the exact error text you see.
