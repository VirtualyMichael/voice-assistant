# Discord guide

Written for the Discord desktop app, 2025 layout. Settings names change from time to time.

## Basics

Discord is organised into **servers** (the round icons down the left side). Each server has **text channels** and **voice channels** on the left, and a member list on the
right. **Direct messages** (DMs) are under the Discord logo at the top left. **User Settings** is the gear next to your name at the bottom left.

## Do this

### Join a server
1. Click the **+** at the bottom of the server list, then **Join a Server**.
2. Paste the invite link or code and click **Join Server**.

### Set up your voice and microphone
1. **User Settings, Voice & Video**.
2. Choose your **Input Device** and **Output Device**, then click **Let's Check** to see your mic level.
3. For **Input Mode**, pick **Voice Activity** (open mic with a sensitivity bar) or **Push to Talk** (set the key under **Keybinds**).
4. Turn **Automatically determine input sensitivity** off and drag the slider yourself if it cuts you off or picks up noise.

### Reduce background noise
**User Settings, Voice & Video, Noise Suppression**, choose **Krisp** (stronger) or **Standard**. Turn **Echo Cancellation** on if you use speakers.

### Mute yourself or deafen
Microphone icon next to your name at the bottom left mutes you; the headphone icon deafens (you also stop hearing). Right-click a person in voice to change their volume only for you.

### Share your screen or a game
1. Join a voice channel.
2. Click **Share Your Screen** at the bottom of the channel list.
3. Choose **Applications** (one window) or **Screens**, pick the quality, and click **Go Live**.
If the other side hears no audio, choose the window under Applications; sound from a whole screen can need extra setup.

### Change your nickname or profile picture
Per server: right-click the server icon, **Edit Server Profile**. Everywhere: **User Settings, My Account** (change **Display Name**), then **Profiles**.

### Mute a server or channel and control notifications
Right-click the server icon or channel, **Mute Server / Mute Channel**, or **Notification Settings** to choose **All Messages**, **Only @mentions** or **Nothing**.

### Set your status or Do Not Disturb
Click your name at the bottom left and choose **Online, Idle, Do Not Disturb** (no sounds or popups) or **Invisible**.

### Create a server
**+** at the bottom of the server list, **Create My Own**, choose a template. Open the server name at the top for **Server Settings** (roles, channels, invites).

### Roles and permissions (server owner or admin)
**Server Settings, Roles, Create Role**, name it, set permissions, then give it to a member with right-click on them in the member list, **Roles**. Channel-specific rules are in each channel's **Edit Channel, Permissions**.

### Block or report someone
Right-click the person, **Block** (they cannot DM you). To report a message, hover it, click the three dots, **Report Message**.

### Use Discord overlay or while gaming
**User Settings, Game Overlay**. If the overlay does not show, run Discord as administrator and make sure the game is added under **Registered Games**.

## Shortcuts

| Keys | What it does |
|---|---|
| Ctrl+K | Quick switcher (search servers, channels, DMs) |
| Ctrl+Shift+M | Mute / unmute |
| Ctrl+Shift+D | Deafen / undeafen |
| Alt+Up / Alt+Down | Previous / next channel |
| Esc | Mark the channel as read |
| Ctrl+/ | List of shortcuts |
| Ctrl+, | User Settings |

## When it goes wrong

- **People cannot hear me:** check the Input Device in Voice & Video, check Windows' microphone privacy (Settings, Privacy and security, Microphone, allow desktop apps), and that you are not server-muted (red slash on your icon).
- **I cannot hear anyone:** check the Output Device; right-click the person to see their volume is not 0; check the Windows volume mixer.
- **"Stuck on RTC connecting":** a firewall, VPN or a regional server problem; turn the VPN off, change the voice channel's **Region Override** (Edit Channel, Overview), restart Discord.
- **Discord is stuck on a grey screen or update loop:** quit from the tray icon, then end Discord in Task Manager; delete the contents of `%appdata%\discord\Cache`; reopen.
- **Screen share is black:** run Discord as administrator, or turn hardware acceleration off in **User Settings, Advanced**.
- **Messages will not send:** you may be rate limited, muted by a moderator, or the channel is read-only.

## Where to look next

Discord Support (support.discord.com) and the Discord Safety Center for reporting and safety settings.
