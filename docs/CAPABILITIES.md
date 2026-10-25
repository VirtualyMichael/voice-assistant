# Closing the gap with cloud assistants

The aim: do what Google Home and the cloud assistants do, on your own hardware, and automate most of daily life on the PC first and the phone second.
This is where things stand and the order to build the rest. "Done" means built and tested; "partly" means it works in a narrow way.

## Why a small local model can still do the work

A cloud model is larger, but most of what makes it useful is not size. It is tools, memory, search and checking its own work. A small model with good
tools beats a big model with none. So the plan is to move capability out of the model and into code:

| Cloud strength | How we get it locally | Status |
|---|---|---|
| Knows a lot | The reference library (passages from your documents, with sources) and the app guides | Done; more documents to add |
| Looks things up | Web search, research, and deep research that checks each claim against the page | Done |
| Does things | Tools for files, apps, the screen, the phone, music, smart home | Done for the PC; growing on the phone |
| Reasons step by step | Thinking is now shown (folded) so you can see it; tool loops allow many steps | Done |
| Remembers you | Profile and memory | Done |
| Understands screens and photos | Vision model for the screen, camera and attachments | Done |
| Runs long jobs | Scheduled tasks, delegating to Claude Code, Codex or Odysseus | Done |
| Fast, natural voice | Streaming recognition, interruptible speech, wake phrase | Done; wake phrase tuning is last |

## Where Google Home still wins, and what to build

1. **Instant, reliable basics** (timers, alarms, lights, "what time is it"). Cloud assistants answer these without a model. We should answer them the same way:
   a small set of fast routes that skip the model for the most common requests (the routing and answer-card code are the start). *Next.*
2. **Knowing the house.** Rooms, device groups ("all the lights downstairs"), scenes, and "turn off everything when I leave". Needs a rooms and groups model on top of the smart-home code. *Next.*
3. **Presence and context.** "At home / away", time of day, what is playing, what is on the screen, which room the voice came from. Routines that start from these instead of only from words. *Soon.*
4. **Phone control.** Android lets apps do much more than iOS. Done: alarms, timers, calendar, texts and calls, notifications, sharing in, and now music and video
   control. Next: open an app or a setting by name, flashlight, do-not-disturb, Wi-Fi and Bluetooth shortcuts (some need the user to tap, which Android enforces), and
   reading what is on the phone's screen through an accessibility service the user turns on.
5. **Proactive help.** A morning summary, reminders that notice you did not do it, "your flight changed" from notifications. Needs the scheduler plus notification access. *Soon.*
6. **Many apps.** Cloud assistants have thousands of integrations. We have MCP servers, the browser, and computer control; the app guides cover "how do I" questions.
   The pattern for each new service is a small MCP server or a script the assistant can call.

## Where we can be better than Google Home

- Everything stays on your machines, and you can read the code.
- You can add your own documents, skills and tools.
- It can use the PC's screen, files and programs, which a smart speaker never can.
- It shows its work: sources on answers, the thinking block, and the verified quote behind each research claim.

## Honest limits

- A small local model makes more mistakes on long, open-ended reasoning than a large cloud one. The mitigation is checking (research verification, tests, asking before acting)
  rather than trusting a single answer. A bigger model on a 24 GB graphics card helps and is worth trying (see `docs/1.0.md`).
- Android decides what an app may do in the background. Starting another app from the background needs a tap on a notification; sending texts and calls needs
  permissions Google restricts for apps sold on Google Play. Sideloaded builds can do more.
- iPhone is far more closed; nothing here plans for it.

## Order of work

1. Fast routes for the most common requests (no model needed), with tests for each.
2. Rooms, groups and scenes for the smart home.
3. Context and presence for routines.
4. More phone abilities (open apps and settings, flashlight, do-not-disturb), each behind its own switch.
5. Proactive summaries.
6. Wake phrase and voice recognition tuning (last, as agreed).
