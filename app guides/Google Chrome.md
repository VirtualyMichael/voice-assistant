# Google Chrome guide

Written for Chrome 130 and newer on Windows. Menus are the same on Mac and Linux apart from the shortcut keys (use Cmd for Ctrl on Mac).

## Basics

The **address bar** (also called the Omnibox) takes web addresses and searches. The **three dots** at the top right open the main menu. **Profiles** keep
bookmarks, passwords and history separate for each person or purpose; the profile picture is next to the three dots.

## Do this

### Open settings
Click the three dots, then **Settings** (or type `chrome://settings` in the address bar).

### Reopen a tab you closed
Press Ctrl+Shift+T. Repeat to go further back.

### Bookmark a page
1. Press Ctrl+D (or click the star in the address bar).
2. Choose a folder (the Bookmarks bar is the row under the address bar), click **Done**.
Show the bar with Ctrl+Shift+B. Manage everything at `chrome://bookmarks`.

### Clear history, cookies and the cache
1. Press Ctrl+Shift+Delete.
2. Pick a **Time range** (use **All time** to clear everything).
3. Tick **Browsing history**, **Cookies and other site data**, and **Cached images and files** as needed. Clearing cookies signs you out of sites.
4. Click **Delete data**.

### Use incognito
Press Ctrl+Shift+N. Nothing is saved to history; your employer, school and the sites you visit can still see activity.

### Save and fill passwords
Chrome offers to save a password after you sign in. Manage them at `chrome://password-manager` or **Settings, Autofill and passwords, Google Password Manager**.
You can see what is saved only after entering your computer's password.

### Install or remove an extension
Open the Chrome Web Store, search for the extension, click **Add to Chrome**. To remove it: three dots, **Extensions, Manage extensions**, **Remove**.
Only install extensions you need; they can read the pages you visit.

### Block pop-ups or allow one site
**Settings, Privacy and security, Site settings, Pop-ups and redirects**. Add the site under **Allowed to send pop-ups** if needed.

### Change the search engine
**Settings, Search engine, Manage search engines and site search**.

### Stop Chrome opening many tabs at start
**Settings, On startup**, choose **Open the New Tab page** or **Continue where you left off**.

### Pin tabs and group them
Right-click a tab, **Pin** (small tab at the left that survives restarts) or **Add tab to new group** (colour and name it).

### Cast or print a page
Print: Ctrl+P, choose the printer or **Save as PDF**. Cast: three dots, **Cast, save and share, Cast**.

### Download location
**Settings, Downloads, Location, Change**. Turn on **Ask where to save each file** if you want to choose every time.

## Shortcuts

| Keys | What it does |
|---|---|
| Ctrl+T / Ctrl+W | New tab / close tab |
| Ctrl+Shift+T | Reopen closed tab |
| Ctrl+L | Select the address bar |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous tab |
| Ctrl+1 to 8 | Jump to that tab number; Ctrl+9 goes to the last |
| Ctrl+F | Find on the page |
| Ctrl+Shift+Delete | Clear browsing data |
| Ctrl+H | History |
| Ctrl+J | Downloads |
| F12 or Ctrl+Shift+I | Developer tools |
| Ctrl+Plus / Minus / 0 | Zoom in / out / reset |
| Shift+Esc | Chrome's own Task Manager (find a tab using too much memory) |

## When it goes wrong

- **A site will not load or looks broken:** reload with Ctrl+Shift+R (bypasses the cache); try incognito (if it works there, an extension or cookie is the cause); clear that site's
  data by clicking the icon left of the address, **Cookies and site data, Manage on-device site data**.
- **Chrome is slow or uses lots of memory:** Shift+Esc to see which tab; **Settings, Performance** turns on Memory Saver; remove unused extensions.
- **"Your connection is not private":** the date and time on your computer are wrong, or the site's certificate is broken. Check the clock first. Do not proceed on banking or shopping sites.
- **Sync is not working:** the profile picture, then **Turn on sync**, or sign out and in; check `chrome://sync-internals` only if advised.
- **Search keeps going to another site:** an extension or a changed search engine; check **Settings, Search engine** and remove unknown extensions. If it keeps returning, run **Settings, Reset settings**.
- **Video stutters:** **Settings, System**, turn **Use graphics acceleration when available** off and on again, then restart Chrome.

## Where to look next

Chrome Help (support.google.com/chrome). Internal pages are listed at `chrome://chrome-urls`.
