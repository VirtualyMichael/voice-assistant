# More lifelike voices

Today the assistant speaks with **Kokoro** (small, fast, runs on the CPU) and can use **Chatterbox** as an optional separate server (`chatterbox_url` in
the settings), which is already wired in and can clone a voice. Kokoro is good for its size but does not sound like the voices in ChatGPT or Claude.

## What the 2026 write-ups say (not verified by us; licences must be read from each model's own page)

| Model | Why it is on the list | Open question |
|---|---|---|
| **Chatterbox** (Resemble AI) | Natural, can clone a voice from a short sample, reported MIT licence and under 200 ms to first audio. Already supported here. | Try it first: no new code needed. |
| **CosyVoice 2** (0.5B) | Most often named for real-time streaming (about 150 ms to first audio) with the same quality as non-streaming. Reported Apache 2.0. | Needs a small server wrapper like the Chatterbox one. |
| **Fish Audio S2 Pro** | Ranked highest among open-weight models on the Artificial Analysis leaderboard in May 2026. | Licence not confirmed; the fast streaming engine described is for their hosted service. |
| **Orpheus** | Scores very high for naturalness in one comparison. | Slower to first audio (about 0.4 s in that table); licence not confirmed. |
| **Kokoro** (what we use) | 82M parameters, runs anywhere. | No longer leads quality rankings. |

Sources: [MarkTechPost, May 2026](https://www.marktechpost.com/2026/05/30/best-text-to-speech-tts-models-in-2026-a-benchmark-based-comparison/), [BentoML](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models), [presenc.ai](https://presenc.ai/research/best-open-weight-text-to-speech-models-2026), [texttolab](https://texttolab.com/blog/open-source-text-to-speech). Several of these are comparison sites of mixed quality, so treat the numbers as rough.

## Plan

1. **Compare Chatterbox with Kokoro on your own PC** (already supported; the download is a few GB and needs your OK). A 24 GB graphics card runs it alongside the chat model.
2. If it is not lifelike enough, add **CosyVoice 2** the same way (a local server the assistant talks to), since streaming matters most for a voice assistant: speech must start before the
   whole sentence is written.
3. Read the licence file of whichever one is kept, add it to `THIRD_PARTY_NOTICES.md`, and decide whether it can ship in the installer or must be downloaded by the user.
4. Phone: the phone plays what the PC speaks, so a better PC voice improves both. The phone's own offline speech is a separate, later piece.

Nothing here has been downloaded or installed yet.
