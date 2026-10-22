# VRChat control by voice

You can talk to VRChat through the assistant: type in the chatbox, flip avatar toggles (a hat, wings, an outfit piece), pick an emote, mute your microphone or jump.

It works through **OSC**, VRChat's own feature for letting other programs on your PC control the game. The assistant sends tiny messages to VRChat on your own PC (`127.0.0.1`, port 9000). Nothing goes to the internet.

> **Status:** checked against a live VRChat session on Windows: avatar toggles (switching a parameter off and on again, in game) and reading the worn avatar and its values. The chatbox, microphone mute and jump use the documented OSC messages but have not been checked in a live session yet.

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
| "Turn off all the Stylization Clothing toggles" | Switches **every on/off switch in a folder** at once, then checks each one with VRChat. "Toggle all..." flips each switch from its own state. |
| "What can I toggle?" | Lists the avatar parameters it found. |
| "Mute me in VRChat" | Presses VRChat's mute toggle (it flips your mic; it cannot tell you which state you are in now). |
| "Jump" | Presses the jump button (works only in worlds that allow jumping). |

You say the name roughly as you would speak it. "hat toggle", "HatToggle" and "hat_toggle" are all the same to it, and small speech slips ("capee" for "Cape") are tolerated. If a name could mean several parameters or none, it lists the closest ones and asks instead of guessing.

## 3. How avatar switches work (the part that is not obvious)

An avatar's toggles, outfits and emotes are **parameters**: named values like `Hat` (on/off), `WingSize` (0 to 1) or `Outfit` (a number). VRChat lets an OSC program set any of them by name.

**It asks VRChat which avatar you are wearing.** VRChat runs a small local web page called OSCQuery (you can see it advertised in VRChat's log as "OSCQuery on <port>"). It says which avatar is worn right now, which parameters that avatar really has, which of them can be switched, and what each one's value is at this moment. The assistant reads that every time, so:

- It only touches parameters **your current avatar actually has**. A name that exists only on one of your other avatars is refused instead of being sent into the void.
- It **checks the result**. After sending, it asks VRChat what the value is now and tells you whether it really changed. If VRChat did not change it, it says so instead of claiming success.
- "Toggle" uses the **real current value**, so it is right even if you changed the item by hand in the menu.
- It sends messages to the port VRChat says it is listening on, so it still works if VRChat is not on 9000.

You can see the same list by asking "what can I toggle?" It shows every switch on your current avatar with its current value.

**If VRChat cannot be asked** (VRChat is not running, or OSC is not enabled), it falls back to the avatar files VRChat saved on your PC (`%USERPROFILE%\AppData\LocalLow\VRChat\VRChat\OSC\<your user id>\Avatars\<avatar id>.json`). Those cover every avatar you have worn with OSC on, not only the current one, so in that mode a name may belong to another avatar. The assistant says so and marks the result "Not verified".

**Folders of switches.** Avatar tools put switches in folders by naming them like `Stylization/Clothing/Shirt`. You can change a whole folder in one request: "turn off all the Stylization Clothing toggles" or "toggle all the clothing toggles". The folder is matched from the start of the name (so `Stylization/Clothing` means everything named `Stylization/Clothing/...`; only if nothing starts that way does it look for a folder of that name elsewhere). Only on/off switches are changed (sliders and numbers in the folder are left alone), and the reply says how many VRChat confirmed and which, if any, did not change. If your avatar has no such folder, the reply lists the folders it does have. "Toggle" flips each switch from its own current state; "on" or "off" sets them all the same.

**Loose requests ("change all the clothing colors to white").** You do not have to name a folder. The assistant searches your avatar's switches with the words you said (any word can match anywhere in a switch's name, so `shirtColor`, `shortsColor` and `Stylization/Clothing/Shirt` all fit "clothing color"), reads the folder and switch names with their current values, decides which switches you mean, changes them in one go, and tells you exactly which ones it changed.

**White versus black.** A switch is only on or off; which of those is white or black depends on how the avatar was built, and the names often do not say (`shirtColor` is just on/off). So the assistant makes its best guess, changes the switches, and, if screen reading is available and you allow the screenshot, **looks at VRChat to see the result** and flips them if the clothes ended up the wrong colour. Without screen reading it tells you which way it set them and offers to flip them ("toggle all the clothing colors" flips each from its own state). If it guessed wrong once, say "the other one" and it flips them.

**Names with slashes** such as `Clothing/Shirt` or `Stylization/Clothing/Shirt` are normal: avatar tools build them from menu folders. Say the words ("clothing shirt") and the assistant matches them.

**Built-in parameters are skipped.** VRChat's own values (`Voice`, `Viseme`, `GestureLeft`, `VelocityX`, `MuteSelf` and so on) are read-only readouts, not switches.

## 4. Things it cannot do or cannot know

- **Chatbox messages cannot be confirmed.** VRChat does not report them, so the assistant says what it sent, not that it appeared. Avatar changes and the mute state *are* checked (see above).
- **Parameters the avatar drives itself** (an animation that immediately flips a value back) can show "did not take"; that is the avatar, not the connection.
- **Chatbox limits:** 144 characters and VRChat throttles fast messages, so the assistant shortens long text and waits between messages. It uses the send-immediately form with no notification sound.
- **Mute is a toggle,** not "mute" and "unmute" separately, because VRChat only exposes a toggle for it. The assistant reads your mute state before and after and tells you which state you are in; it only works with VRChat's "Toggle Voice" setting on.
- **Public text:** `vrchat_say` posts text other players read. The assistant is told to say only what you asked it to; check what it heard before you rely on it in a crowded room.

## 5. Troubleshooting

| Problem | Usual cause and fix |
| --- | --- |
| Nothing happens at all | OSC is not enabled in VRChat (Action Menu > Options > OSC > Enabled), or the assistant's **Control VRChat by voice** box is off. The assistant now says "did not take" when VRChat does not react, instead of claiming success. |
| It worked, then stopped after VRChat updated or after you switched avatar | Action Menu > Options > OSC > **Reset Config**, then switch avatars once. |
| "has no switch that matches" | The worn avatar has no parameter with that name. The reply lists the closest ones; say one of those, or ask "what can I toggle?". |
| "VRChat could not be asked" | VRChat is not running, or OSC is off. Start VRChat, enable OSC, and try again (results are "Not verified" until then). |
| Wrong item toggles | Two parameters have similar names. Say the full name, or ask "what can I toggle?" and use the exact wording. |
| The chatbox ignores messages for a while | VRChat rate-limits fast chatbox messages. Wait a moment. |
| You use another OSC tool at the same time | Several programs can send to VRChat on port 9000 together. Programs that *listen* on port 9001 can clash with each other; this assistant does not listen, so it does not take that port. |

## 6. Safety

The assistant only sends messages to your own PC. It can change your avatar's parameters, type in the chatbox, toggle your microphone and press jump. It cannot move you around the world, and it does not touch VRChat's safety controls. You can switch the whole feature off with the tick box, or turn OSC off in VRChat, at any time.
