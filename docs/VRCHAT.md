# VRChat control by voice

You can talk to VRChat through the assistant: type in the chatbox, flip avatar toggles (a hat, wings, an outfit piece), pick an emote, mute your microphone or jump.

It works through **OSC**, VRChat's own feature for letting other programs on your PC control the game. The assistant sends tiny messages to VRChat on your own PC (`127.0.0.1`, port 9000). Nothing goes to the internet.

> **Honest status:** the messages are built to VRChat's documentation and are tested byte for byte, but this has not yet been tried in a live VRChat session. If something does not work for you, the troubleshooting section below lists the usual causes, and please report it.

## 1. Turn it on (two switches)

1. **In VRChat:** open the Action Menu, go to **Options > OSC** and choose **Enabled**. (If you ever get odd behaviour, **Reset Config** in the same menu clears VRChat's OSC files; they are rebuilt.)
2. **In the assistant:** Settings > VR > tick **Control VRChat by voice**.

That is all. VRChat listens on port 9000 by default and the assistant sends to it; you do not need to change anything unless you started VRChat with a custom `--osc=` option, in which case change `vrchat_port` / `vrchat_host` in the assistant's settings file.

## 2. What you can say

| Say something like | What happens |
| --- | --- |
| "Tell VRChat I'll be right back" | Types "I'll be right back" in the chatbox (other players see it). |
| "Put 'nice to meet you' in the chatbox" | Same. |
| "Turn my hat on" / "Take my hat off" | Switches the avatar parameter called Hat on or off. |
| "Toggle my wings" | Flips it (see the note on toggles below). |
| "Set wing size to half" | Sets a slider parameter (0 to 1). |
| "Set my outfit to 2" | Sets a number parameter. |
| "What can I toggle?" | Lists the avatar parameters it found. |
| "Mute me in VRChat" | Presses VRChat's mute toggle (it flips your mic; it cannot tell you which state you are in now). |
| "Jump" | Presses the jump button (works only in worlds that allow jumping). |

You say the name roughly as you would speak it. "hat toggle", "HatToggle" and "hat_toggle" are all the same to it, and small speech slips ("capee" for "Cape") are tolerated. If a name could mean several parameters or none, it lists the closest ones and asks instead of guessing.

## 3. How avatar switches work (the part that is not obvious)

An avatar's toggles, outfits and emotes are **parameters**: named values like `Hat` (on/off), `WingSize` (0 to 1) or `Outfit` (a number). VRChat lets an OSC program set any of them by name. The assistant sets them by sending the name and value.

**Where the names come from.** When you switch to a published avatar with OSC enabled, VRChat saves a small file listing that avatar's parameters:

`%USERPROFILE%\AppData\LocalLow\VRChat\VRChat\OSC\<your user id>\Avatars\<avatar id>.json`

The assistant reads all of these files, so it knows the parameter names of every avatar you have worn since turning OSC on. Avatars you have never worn with OSC enabled have no file yet. Two things follow:

- The assistant does **not know which avatar you are wearing right now** (VRChat reports that on a different channel). It sends to the parameter name; an avatar without that name simply ignores it. So "turn my hat on" works for whichever avatar has a `Hat`.
- Avatar files appear only for **published** avatars (VRChat does not write them for Build & Test).

If no files exist yet, switch to your avatar once with OSC on, then ask "what can I toggle?". You can also name a parameter exactly even without a file; the assistant will say that it could not check the name.

**Finding the names yourself.** If you made the avatar, the parameter names are the ones in its VRChat Expressions Parameters asset in Unity. For someone else's avatar, open its JSON file above in any text editor and look at the `"name"` entries.

**Built-in parameters are skipped.** VRChat's own values (`Voice`, `Viseme`, `GestureLeft`, `VelocityX`, `Grounded` and so on) are left out of the list because they are read-only readouts, not switches.

## 4. Things it cannot do or cannot know

- **VRChat never confirms.** OSC is one-way for this purpose, so the assistant says what it *sent*, not that the avatar changed. If nothing changes, see troubleshooting.
- **Toggles flip from what the assistant last set.** It cannot read the current state, so "toggle my hat" turns it on the first time, off the second time. If you changed it by hand in the menu, say "turn my hat on" or "off" instead.
- **Chatbox limits:** 144 characters and VRChat throttles fast messages, so the assistant shortens long text and waits between messages. It uses the send-immediately form with no notification sound.
- **Mute is a toggle,** not "mute" and "unmute" separately, because VRChat only exposes a toggle for it.
- **Public text:** `vrchat_say` posts text other players read. The assistant is told to say only what you asked it to; check what it heard before you rely on it in a crowded room.

## 5. Troubleshooting

| Problem | Usual cause and fix |
| --- | --- |
| Nothing happens at all | OSC is not enabled in VRChat (Action Menu > Options > OSC > Enabled), or the assistant's **Control VRChat by voice** box is off. |
| It worked, then stopped after VRChat updated or after you switched avatar | Action Menu > Options > OSC > **Reset Config**, then switch avatars once. |
| "No avatar parameters found" | You have not worn a published avatar with OSC enabled yet. Switch to it once, or name the parameter exactly. |
| Wrong item toggles | Two parameters have similar names. Say the full name, or ask "what can I toggle?" and use the exact wording. |
| The chatbox ignores messages for a while | VRChat rate-limits fast chatbox messages. Wait a moment. |
| You use another OSC tool at the same time | Several programs can send to VRChat on port 9000 together. Programs that *listen* on port 9001 can clash with each other; this assistant does not listen, so it does not take that port. |

## 6. Safety

The assistant only sends messages to your own PC. It can change your avatar's parameters, type in the chatbox, toggle your microphone and press jump. It cannot move you around the world, and it does not touch VRChat's safety controls. You can switch the whole feature off with the tick box, or turn OSC off in VRChat, at any time.
