# A watch for the assistant: how to build it

Short version: **build a Wear OS app that talks only to the phone app**; the phone keeps doing what it does now (talk to the PC over Tailscale). The watch never needs to reach the PC. This also decides the other question, where speech is processed: see "Where speech is processed".

## Which watch

| Watch | Can it work with an Android phone? | Verdict |
|---|---|---|
| **Wear OS** (Pixel Watch, Galaxy Watch 4 and later, others) | Yes. Google's Data Layer API links a watch app to a phone app. | **Build this.** |
| **Apple Watch** (watchOS) | Only pairs with an iPhone. No link to an Android phone. | Not possible with this phone. Would need an iPhone app first. |
| Other (Garmin, Fitbit OS, Amazfit) | Closed or limited app platforms, no general audio streaming. | Skip. |

Everything below is Wear OS. If "watch OS" meant Apple Watch, say so: it is a different project that starts with an iPhone app.

## Why watch → phone → PC (and not watch → PC)

- A watch cannot run Tailscale, so it cannot reach the PC by itself. The phone already can.
- The watch is paired to the phone over Bluetooth (and Wi-Fi when both are on the same network), a link built for exactly this.
- One place talks to the PC (the phone), so pairing, safety settings, the offline helper, texts and calls, notifications and sharing all stay in one app. The watch is a remote microphone, speaker and screen for the phone.

```
Watch (mic, speaker, small screen)
   │  Bluetooth / Wi-Fi: Data Layer (MessageClient for small messages, ChannelClient for audio streams)
Phone app (the existing app + a watch service)
   │  Tailscale WebSocket, exactly as today
PC assistant
```

## What the watch app does

1. **Ask**: a Tile and a complication open it with one tap; the screen has a big mic button. Hold or tap to talk, release to send.
2. **Hear**: the reply is spoken on the watch speaker (or the phone speaker or earbuds, a setting), with the reply text, answer card sources and settings shortcuts shown as short cards.
3. **Type**: dictation or a few quick replies ("Yes", "No", "Stop"). Yes/No for the assistant's questions ("Set an alarm for 06:30?") is the best use of a watch: one tap on the wrist instead of taking out the phone.
4. **Show**: timers and alarm confirmations, the last answer, connection state ("PC reachable / offline helper").
5. **No always-listening wake word on the watch**, at least at first: continuous listening drains a watch battery in hours. Tap, Tile, complication, or the watch's side-button shortcut start it. (The phone's own hands-free wake phrase keeps working while the phone is nearby.)

## Where speech is processed

The phone is the right place for the *plumbing* and the PC is the right place for the *accuracy*.

| Step | Where | Why |
|---|---|---|
| Capture audio, voice-activity hint | **Watch** | It has the microphone. Send 16 kHz 16-bit mono PCM (32 KB/s), which is small for Bluetooth. |
| Relay, end-of-speech detection, buffering, reconnect | **Phone** | It already does this for its own microphone: the same code path, a different audio source. |
| Accurate speech recognition | **PC** (as now) | Parakeet on the PC is far more accurate than anything that fits in a phone. |
| Quick recognition when the PC is away | **Phone** (sherpa-onnx streaming zipformer, already the same engine family as the wake word) | Feeds the offline helper. Lower accuracy, only used offline. |
| Speaking the reply | **PC** makes the audio, **phone** forwards it, **watch** plays it | Keeps one voice everywhere. When offline, the phone's own text-to-speech speaks. |

So the idea "the phone does the speech processing" is right for relaying and for the offline path, and the PC stays the accurate recogniser online. Nothing leaves your devices either way.

## Protocol between watch and phone (sketch)

Data Layer, with the phone app as the only owner of the PC link:

- `/va/talk/start` (message, watch → phone), then an **audio channel** (ChannelClient, watch → phone, PCM stream), then `/va/talk/end`.
- `/va/state` (message, phone → watch): idle, listening, thinking, speaking; connected or offline.
- `/va/text` (message, phone → watch): reply text as it streams, plus a short card (sources, a settings shortcut).
- `/va/reply_audio` (channel, phone → watch): reply audio to play on the watch, when chosen; `/va/reply_audio/cancel` (message) stops it at once.
- `/va/ask` (message, phone → watch) and `/va/answer` (watch → phone): a Yes/No question, answered from the wrist. The same safety rules apply: nothing sensitive happens on a tap without the phone's own settings allowing it.
- `/va/quick` (watch → phone): a typed or dictated message.

The phone app gains a `WearableListenerService` that turns these into calls on the existing `Controller` (press talk, feed audio frames, send text, answer approval).

## Pieces to build

| Piece | Size | Notes |
|---|---|---|
| Wear OS app module in the phone repository (Compose for Wear OS, Tile, complication, microphone foreground service) | Medium | Same Kotlin and Compose skills as the phone app. |
| Phone: watch service + routing audio and state | Small to medium | Most logic exists in `Controller`; the watch is another input and output. |
| Settings: "Use my watch", where replies play | Small | Under "This phone". |
| Pairing | None | The watch is already paired to the phone by Android; the apps recognise each other by package name and signature. |
| Testing | Needs a real watch, or the Wear OS emulator paired with a phone emulator | Audio timing is the thing to test: capture start, end-of-speech, interrupting a reply. |

## Risks and decisions

- **Google Play services**: the Data Layer is part of Play services (a proprietary library, free to use). It is on every Pixel and Galaxy Watch. If the app ever ships through F-Droid, the watch link is the one non-free piece.
- **Battery**: no continuous listening; microphone only while the button is held. Streaming PCM over Bluetooth for a few seconds is cheap.
- **Latency**: expect about the same as the phone alone plus a fraction of a second for Bluetooth. Reply audio forwarded to the watch speaker adds a little; playing on the phone or earbuds avoids it.
- **Standalone use**: a watch with LTE and no phone cannot reach the PC. A later option is a small cloud-free relay, but "local only" rules it out for now.
- **Safety**: Yes/No answers from the wrist are the same as answering on the phone; texts and calls keep their own "Send texts and place calls myself" switch on the phone.

## Order of work

1. Phone: a `WatchLink` interface in `Controller` (talk, state, text, approvals) so the watch is just another front end. Unit-testable now with no watch.
2. Wear OS app: a screen with the mic button, state and reply text; push-to-talk streaming to the phone.
3. Yes/No questions and the Tile.
4. Reply audio on the watch, complications, quick replies.
5. Offline phone speech recognition (also useful without a watch).

Step 1 is worth doing before owning a watch; steps 2 to 4 need a Wear OS watch or the emulator to test properly.

## Status (first slice, built but not yet tried on a watch)

Built in the phone repository:

- `watchlink/` holds `WatchProtocol.kt`, the one file both apps compile, with unit tests (round trips, garbage input, cutting a stream into 30 ms frames).
- **Phone app**: `WatchService` receives the watch's messages and the audio channel and hands them to the same `Controller` the phone screen uses; `WatchLink` sends the watch the state, what it heard, the growing reply (at most four times a second) and Yes/No questions. Switched on in **Settings, This phone, "Let my watch talk to the assistant"** (off by default; ignored completely until then). Questions that need a choice of person (texts, calls) are answered on the phone, not the watch.
- **Watch app** (`wear/`, Compose for Wear OS): a Talk button (press to start, press again to finish), the state in words, what it heard, the reply, Yes/No when asked, Stop, New chat. It records 16 kHz audio only while Talk is on (at most 30 seconds, and never after the screen goes away) and streams it to the phone.
- The watch app has the same application id and the same release key as the phone app; that is how Wear OS pairs the two.

What has been checked: both apps compile, the protocol tests pass (8), the phone's 76 unit tests pass. What has **not** been checked: any real watch or the Wear OS emulator (no watch system image is installed; it is a download of about a gigabyte), the actual audio over Bluetooth, battery use, or the Tile and reply audio on the watch.

To try it with a Wear OS watch: on the watch turn on Developer options and Wireless debugging, then `adb connect <watch address:port>` and `adb -s <that> install wear-debug.apk` (built by `gradlew :wear:assembleDebug`); on the phone switch the watch setting on; open the app on the watch.

Added in 0.17 (built, run on a Wear OS 4 emulator without a paired phone; the phone link itself is still untested): the **Ask tile** (swipe to it and tap once; it opens the app already listening), an **Ask complication** for the watch face, six **quick-reply chips** (Yes, No, Thanks, Continue, Say that again, Shorter please) that need no microphone, a buzz when the microphone opens and closes, and the screen staying on while it listens. What the emulator check showed: the app starts without errors, shows "Phone not found" when no phone is paired, the quick replies display, and the tile and complication are registered. What it did not show: the audio and messages between a real watch and phone, the tile on a watch face, or the complication in a face's slot.

Added after 0.17 (built and unit-tested; **not yet tried on any watch, real or emulated**):

- **Reply audio on the watch.** A new phone setting under "Let my watch talk to the assistant", shown only while that is on: **"Where replies play when I ask from my watch"**, Watch or Phone (**Phone by default**, so nothing changes until you pick Watch). With Watch, the PC's spoken reply (16-bit mono, at the PC's voice sample rate, sent at playback speed a little ahead) is forwarded by the phone to the watch over a Data Layer channel and played there with `AudioTrack`, starting as soon as the first sound arrives. Only replies to questions asked on the watch (talking or the chips) go there; anything started on the phone, by the wake phrase, a share or the camera still plays on the phone. It stops at once on the watch's Stop, on a new Talk press, a new chip or dictated reply, and when the phone says the reply was cut off (Stop on the phone, a new question, the PC interrupting). A watch without a speaker shows the reply as text only, and the phone then plays nothing (you chose not to hear replies on the phone). How the audio is cut into pieces and read back (including rejecting anything that is not in that format) lives in `WatchProtocol.kt` with its own tests.
- **Dictated replies.** "Say or type a reply" next to the quick-reply chips opens the watch's own input screen (speak, type or draw letters; plain speech recognition on a watch without it) and sends the text the same way as a chip.
- **Clearer problems on the watch** instead of one "Phone not found": "Phone not found" (no phone with the app in reach), "Your phone isn't answering. Open Voice Assistant on your phone." (the phone is there but its app does not answer within 6 seconds), "Switched off on your phone…" (the phone app answered, but "Let my watch talk to the assistant" is off), and "Phone can't reach your PC". Tapping Talk while one of the first three shows checks again instead of recording into nothing.
  - To tell "switched off" apart, the phone now answers the watch's hello while the setting is off, but only with a fixed "off": no state, nothing about the PC or the conversation, and only to the watch that asked. Everything else the watch sends is still ignored, and a watch message no longer even starts the assistant (and its link to the PC) while the setting is off (before, it did start it, then ignored the message).
- **No more waiting forever.** While a question is under way (listening, thinking, speaking) and the phone has said nothing for 15 seconds, the watch checks on it; no answer within 6 seconds shows "Lost touch with your phone. Tap Talk to try again.", closes the microphone and stops any reply audio. A long answer from the PC does not trip this, because the phone answers the check. On the phone, if the watch's audio stream ends without the watch saying "that was all" (it went out of reach mid-question), the question is finished after 1.5 seconds instead of the PC listening on.

What was checked: both apps build (`gradlew --offline :app:testDebugUnitTest :app:assembleDebug :wear:assembleDebug`), and all 88 phone unit tests pass, 17 of them the watch protocol (9 new: hello and cancel messages, reply-audio round trips, long audio cut into pieces, a piece arriving one byte at a time, partial pieces, garbage, nothing read after the end, a sample-rate change mid-reply). What was **not** checked: any of it on a real watch or the Wear OS emulator, the reply audio over real Bluetooth (whether 0.3 s of lead is enough to avoid gaps, how much delay it adds, battery), a watch without a speaker, the watch's input screen for dictation, the "isn't answering" and "lost touch" timings with a real phone going out of reach, and the new phone setting on a real phone with a watch.

Still to do from the plan: offline phone speech recognition.
