# Using the assistant from your phone

Talk to the assistant from your Android phone, show it things with the phone's camera, and change its settings, from anywhere. Your PC does
the work; the phone is its microphone, speaker and camera. Everything travels between your own devices over Tailscale, a private network
that connects only the devices you sign in with the same account.

## What you need

- This PC, with the assistant running.
- An Android phone with the Voice Assistant app.
- A free [Tailscale](https://tailscale.com/download) account (you sign in with Google, Microsoft or Apple; nothing to pay).

## Set it up (about 5 minutes, once)

1. **Install Tailscale on the PC and on the phone**, and sign in to the *same* account on both. The phone's Tailscale app must show
   "Connected".
2. **On the PC**, open the assistant, then *Settings* > *Phone*, and press **Turn on the phone link**. Three lines appear; the first two
   should have a green tick. (If Tailscale has a red cross, it is not running on this PC yet.)
3. **On the phone**, open the Voice Assistant app. It walks you through the rest and checks each step for you.
4. **Pair the phone**: on the PC press **Pair a new phone**. A picture (a QR code) appears. On the phone tap **Scan the code** and point the
   camera at the PC screen. The phone says "Connected", and the PC window closes the picture by itself.
5. **Try it**: hold the big Talk button and ask something, or say your wake phrase (set up in the app) and ask hands-free.

The pairing code works once and expires after 5 minutes. If it expires, press **Show a new code**.

## If something does not work

The app has a **Test the connection** button that finds the most likely cause. These are the usual ones:

| What you see | What to do |
|---|---|
| The PC says Tailscale is not running | Install Tailscale on the PC and sign in. Then reopen the Phone page. |
| The phone says it cannot reach the PC | Check that Tailscale is on in both places, that the PC is awake, and that the assistant is running with the phone link on. |
| "That code expired" | Press **Show a new code** on the PC and scan again. |
| It does not hear you | Allow the microphone for the app. In a noisy room, hold the Talk button instead of using the wake phrase. |
| Hands-free stops when the phone sleeps for a long time | Open the app once. Android only lets an app start listening while it is on screen. |
| A setting is locked with a padlock | Safety settings (shell commands, web access, the "ask me first" questions...) can only be changed from the phone if you tick **Let my phone change safety settings** on the PC's Phone page. The phone then asks for your fingerprint or PIN each time. |

## Removing a phone

On the PC: *Settings* > *Phone*, select the phone, press **Remove the selected phone**. On the phone: **Forget PC** in the app's settings
(this removes the phone from the PC's list too).

## Privacy

- Your voice goes from the phone to your own PC. With a local model it never leaves your devices; with a cloud model the conversation goes to
  that company, as it does when you use the assistant at the PC.
- Photos you take with the assistant's camera are sent to your PC, described by your model and kept in memory only for ten minutes (so you can
  ask a follow-up). They are not saved.
- The PC accepts only phones you paired. The phone link listens only on your Tailscale address, not on your home network or the internet.
