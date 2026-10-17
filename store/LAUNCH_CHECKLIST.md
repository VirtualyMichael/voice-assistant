# Launch checklist

Items marked **YOU** need your identity, money or consent; I cannot do them for you. Everything else is prepared in this folder.

## 1. Blockers before taking anyone's money
- [ ] **Licences (YOU + a lawyer or careful read).** See `THIRD_PARTY_NOTICES.md` ("Needs a decision before selling"): espeak-ng (GPL-3.0) behind the Kokoro/Piper voices, LGPL `num2words` bundled inside the exe, the unclear Piper voice licence, DuckDuckGo scraping. Fastest safe path: drop the Piper voice from the downloads, replace `num2words` with a small own routine, and get a yes/no on espeak-ng before charging.
- [ ] **Code signing (YOU).** Unsigned installers show "Unknown publisher". Options: Azure Trusted Signing (about $10/month, needs identity verification) or a certificate from a CA (about $100–400 a year). Until then say so on the page.
- [ ] **Clean-PC test (YOU).** Install on a PC (or Windows Sandbox / VM) that never had Python or Visual C++, run the setup guide to the end, ask it three things. Not yet done on a clean machine.
- [ ] **Legal pages.** `EULA.md` and `PRIVACY.md` are templates, not legal advice. Fill in your name/contact and read them.
- [ ] **Support email or Discord (YOU)** you will actually check.

## 2. Accounts (YOU)
- [ ] itch.io account, payout set up (PayPal or Stripe) and your tax details.
- [ ] (Later) pixiv account for BOOTH, and payout method.

## 3. itch.io (do this first)
1. Create the project and fill it in from `ITCH_LISTING.md` (every field is mapped).
2. Upload `dist\installer\VoiceAssistant-Setup-<version>.exe`; tick Windows.
3. Set visibility to **Draft**, buy/download it yourself on a second PC, run it.
4. Switch to **Public**. Post once in r/VRChat tools threads, r/LocalLLaMA and a VRChat tools Discord, following each place's self-promotion rules.

## 4. Watch these numbers for 60 days
Downloads, buyers, comments about setup problems, refund requests. If under 200 downloads and under 10 buyers, keep it free with a tip jar and treat it as a portfolio piece.

## 5. Every release
`powershell -File packaging\build.ps1 -Version X.Y.Z`, then upload the new installer to itch.io (use `butler` later to push updates), and bump the version in `voice_assistant/__init__.py`.

## Rebuild the store images
Close the assistant, then `.venv\Scripts\python store\make_assets.py` (uses a clean profile with a staged demo chat, so nothing personal appears) and `.venv\Scripts\python store\make_notices.py`.
