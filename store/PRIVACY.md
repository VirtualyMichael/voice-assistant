# Privacy notice: Voice Assistant (template, not legal advice)

Last updated: [DATE]. Contact: [YOUR NAME / EMAIL].

**The short version:** Voice Assistant runs on your PC. I do not run any servers for it, and it has no analytics or tracking. I never receive your audio, text or files.

## What stays on your PC
- Microphone audio is processed in memory to recognize speech and is not saved.
- Your settings (`%APPDATA%\VoiceAssistant\config.json`), your conversation (`conversation.json`: only your messages and the assistant's replies, never file contents or tool output), a PDF text cache, and the speech models. Use **New chat** to erase the conversation; uninstalling offers to delete everything.
- API keys you enter are stored in Windows Credential Manager, not in the settings file.

## When data leaves your PC (only because you chose a feature)
- **Cloud AI (Claude or OpenAI):** if you select one, what you say, the assistant's earlier replies, and any file, web or clipboard text the assistant reads for you are sent to that provider under your own account and their privacy terms.
- **Web search and web pages:** when you enable them, search words and page requests go to DuckDuckGo and the sites involved.
- **Model downloads:** the setup guide downloads speech models from GitHub (k2-fsa/sherpa-onnx releases) only after you tick them; GitHub sees a normal download request.
- **Tool servers (MCP) and skills you add** can do whatever those programs do; the app asks before actions that change things unless you mark a server as trusted.
- **Local AI (LM Studio, Ollama, llama.cpp):** requests go to the server on your own PC.

## Your control
Everything above is off or opt-in unless noted. Delete `%APPDATA%\VoiceAssistant` to remove all local data.
