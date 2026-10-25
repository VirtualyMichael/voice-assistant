# Steam guide

Written for the Steam client on Windows, 2025 layout.

## Basics

Steam is a store, a **Library** of the games you own, and a friends and chat system. Open the **Library** from the top menu. **Steam, Settings** (top left) holds
downloads, storage, controller and in-game options.

## Do this

### Install a game
1. **Library**, click the game in the list, then **Install**.
2. Choose the drive when asked. A drive for games is set under **Steam, Settings, Storage**.

### Move a game to another drive
1. **Steam, Settings, Storage**, add the new drive with **Add Drive** (the + in the drop-down).
2. In the Library, right-click the game, **Properties, Installed Files, Move install folder**, choose the drive.

### Make downloads faster or limit them
**Steam, Settings, Downloads**: set **Download Region** closest to you, and **Limit bandwidth** if it slows the rest of your network. Turn on **Allow downloads during gameplay** only if you want that.

### Use a launch option (for example to set the frame rate or use a specific API)
Right-click the game, **Properties, General, Launch Options**, and type the option, such as `-novid` (skips intro videos) or `-windowed`. Options differ per game.

### Verify a game's files (fixes crashes and missing files)
Right-click the game, **Properties, Installed Files, Verify integrity of game files**. Wait until it finishes.

### Add a game that is not from Steam
**Games** (bottom left) or the **+** in the Library, **Add a Non-Steam Game**, pick the program. Then you can use the Steam overlay and controller settings with it.

### Refund a game
Go to **Help, Steam Support**, choose the purchase, **I would like a refund**. Usually allowed within 14 days of buying and under 2 hours of play.

### Share games with family
**Steam, Settings, Family**, set up **Steam Family** and invite people so you can share libraries.

### Use a controller
**Steam, Settings, Controller** turns on support for Xbox, PlayStation, Switch Pro and generic controllers. In a game, press the Steam button (or Shift+Tab for the overlay) for **Controller Settings**.

### Change the in-game overlay
**Steam, Settings, In Game** to turn the overlay on or off and change its shortcut (default Shift+Tab). **Properties, General** turns it on or off per game.

### Back up or restore a game
Use **Steam, Back Up and Restore Games** for a local copy. Cloud saves are automatic for games that support them (the cloud icon in the game's page).

### Play offline
**Steam, Go Offline**. You have to have logged in once with **Remember my password** ticked first.

## When it goes wrong

- **A game will not start or crashes:** verify files (above); update graphics drivers; right-click, **Properties, Compatibility** for older games; try running Steam as administrator.
- **Download is stuck at 0 or very slow:** **Steam, Settings, Downloads, Clear Download Cache**; change the download region; restart Steam; check your disk is not full.
- **"Disk write error":** the drive is full, locked by antivirus, or damaged. Verify files, add an exclusion for the Steam folder in your antivirus, or run `chkdsk` on the drive.
- **Steam is slow to open:** **Settings, Interface**, turn **Enable GPU accelerated rendering in web views** off; or clear the web browser cache under **Settings, Web Browser**.
- **No sound or voice chat:** **Settings, Voice** to choose the microphone; check Windows privacy settings for the microphone.
- **Cannot log in:** Steam Guard code from the mobile app or email; if you lost access, use **Help, Steam Support** and account recovery. Never share your Steam Guard codes.

## Where to look next

Steam Support (help.steampowered.com) and the game's own Community Hub (Discussions tab) for per-game problems.
