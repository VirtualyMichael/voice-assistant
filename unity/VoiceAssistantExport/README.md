# Voice Assistant avatar export (Unity)

A read-only Unity editor script for the Voice Assistant app's VRChat control. It reads an avatar's expression menu, parameters and FX layer from Unity itself, so it works even when the menu and layers are kept in a generated asset file (VRCFury, Modular Avatar and similar).

- **Unity:** 2022.3 with the VRChat Avatars SDK 3. Checked to compile against Unity 2022.3.22 and a current SDK.
- **Install:** copy this folder into your project's `Assets` folder.
- **Use:** select the avatar, then `Tools > Voice Assistant > Export selected avatar`. For generated avatars, do it in Play mode with the Avatars 3.0 Emulator (or after Build & Test).
- **Output:** `%APPDATA%\VoiceAssistant\unity-exports\<avatar name>.json`. Then wear the avatar in VRChat and tell the assistant "import this avatar".

It writes only that one file. It never changes the project, never uploads anything and needs no network.
