# App guides

Short, practical guides for common apps, in the `app guides` folder next to the program. The assistant searches them (the reference library, collection
**app guides**) when you ask how to do something in one of these apps, and answers from them instead of from memory, naming the guide it used.

Included: Windows 11, Google Chrome, Discord, OBS Studio, Microsoft Excel, Visual Studio Code, Steam, YouTube Music.

Each guide is a plain Markdown file, one per app, laid out the same way:

- **Basics**: what the app is for and how it is organised.
- **Do this**: numbered steps for the jobs people ask about most.
- **Shortcuts**: the keys worth knowing.
- **When it goes wrong**: the usual problems and the fix that works most often.
- **Where to look next**: the official help, for anything not covered.

## Being honest about them

These were written from general knowledge of the apps and are a good first answer, not the last word. Menus move between versions, and each guide says
which version it was written for. They have not been checked screen by screen against every version. When a step does not match what you see, the
assistant should say so and point to the official help rather than guess.

To make an app's answers authoritative, add its official manual (a PDF or saved web pages) to a folder in **Settings, Skills, Add library folder**; those
documents are searched together with these guides.

## Adding an app

Copy any guide, keep the same headings, and save it in the `app guides` folder as `<app name>.md`. Press **Rescan** under the reference library in
**Settings, Skills** (or restart). Keep each step to one action, name buttons exactly as they appear, and put the shortcut in the step where it helps.

Turn the guides off with **Include the built-in app guides** in the same place if you only want your own documents searched.

## Still to do

- More apps: Microsoft Word and Outlook, Gmail, Google Docs and Sheets, Photoshop, Blender, Zoom and Teams, Spotify, WhatsApp, Android settings, VRChat.
- Check each guide against a current install and record the version and date it was checked.
- Per-app collections so a question about Excel never returns Chrome passages (the assistant can already limit a search to one collection by name).
