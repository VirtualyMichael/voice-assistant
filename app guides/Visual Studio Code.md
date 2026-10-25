# Visual Studio Code guide

Written for VS Code 1.9x on Windows. On Mac use Cmd for Ctrl.

## Basics

VS Code is a code editor. The **Activity Bar** on the far left switches between Explorer (files), Search, Source Control (Git), Run and Debug, and Extensions. The
**Command Palette** (Ctrl+Shift+P) can run any command by name and is the quickest way to find anything. **Settings** is Ctrl+, and the **terminal** is Ctrl+`.

## Do this

### Open a project
**File, Open Folder**, choose the folder. Trust it if you know where it came from. Reopen recent work with **File, Open Recent**.

### Install a language or tool
Press Ctrl+Shift+X, search (for example "Python" by Microsoft), click **Install**. Reload if asked. Common ones: Python, Pylance, ESLint, Prettier, GitLens.

### Run Python code
1. Install Python from python.org, then the Python extension.
2. Open the file. Press Ctrl+Shift+P, **Python: Select Interpreter**, pick the one you want (a project's `.venv` if it has one).
3. Click the play button at the top right, or open the terminal and type `python filename.py`.

### Format on save
Ctrl+, then search "format on save" and tick **Editor: Format On Save**. Pick a default formatter with **Format Document With...** (right-click in the file).

### Use the built-in terminal
Ctrl+` opens it. The **+** opens another; the arrow beside it chooses the shell (PowerShell, Command Prompt, Git Bash, WSL).

### Search across all files
Ctrl+Shift+F. Use the `.*` button for regular expressions and the files-to-include box to narrow. Replace with the arrow on the left. Ctrl+P jumps to a file by name.

### Use Git
1. Source Control icon (Ctrl+Shift+G). **Initialize Repository** if it is a new folder.
2. Click **+** next to files to stage, type a message in the box, click **Commit** (the tick).
3. **Sync Changes** pushes and pulls. To use a remote, sign in with GitHub when asked.

### Debug
1. Click in the gutter left of a line number to set a breakpoint (red dot).
2. Press F5 and choose the environment. Step with F10 (over) and F11 (into); inspect values in the **Variables** panel.

### Edit many places at once
Alt+Click adds cursors. Ctrl+D selects the next match of the selected word. Ctrl+Shift+L selects every match.

### Rename a variable everywhere
Put the cursor on it and press F2, type the new name, Enter. This follows the code, not just the text.

### Change the theme, font, or zoom
Ctrl+K Ctrl+T picks a colour theme. Ctrl+, then search "font size". Ctrl+Plus and Ctrl+Minus zoom the whole window.

### Keep settings the same on another PC
Click the profile icon bottom left, **Turn on Settings Sync**, sign in with Microsoft or GitHub.

## Shortcuts

| Keys | What it does |
|---|---|
| Ctrl+Shift+P | Command Palette |
| Ctrl+P | Go to file |
| Ctrl+G | Go to line |
| Ctrl+/ | Comment or uncomment the line |
| Alt+Up / Alt+Down | Move the line |
| Shift+Alt+Down | Duplicate the line |
| Ctrl+B | Show or hide the sidebar |
| Ctrl+\ | Split the editor |
| F12 | Go to definition |
| Shift+F12 | Find all references |
| Ctrl+. | Quick fix |
| Ctrl+Z / Ctrl+Y | Undo / redo |

## When it goes wrong

- **Import could not be resolved / red squiggles on installed packages:** the wrong Python interpreter is selected. Ctrl+Shift+P, **Python: Select Interpreter**.
- **"python is not recognized":** Python is not installed or not on PATH; reinstall and tick **Add python.exe to PATH**, then restart VS Code.
- **PowerShell will not run scripts / venv will not activate:** run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell once.
- **IntelliSense is slow or missing:** reload the window (Ctrl+Shift+P, **Developer: Reload Window**); check the extension is enabled.
- **An extension misbehaves:** Ctrl+Shift+P, **Help: Start Extension Bisect**, or disable them one at a time.
- **Git says "unsafe repository" or asks for an identity:** run `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`.
- **The whole editor is slow:** Ctrl+Shift+P, **Developer: Open Process Explorer** shows which window or extension is using the CPU or memory.

## Where to look next

code.visualstudio.com/docs and the Keyboard Shortcuts reference (**File, Preferences, Keyboard Shortcuts**, or Ctrl+K Ctrl+S).
