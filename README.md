# BondScribe

**Turn your voice into better AI prompts.** BondScribe transcribes whatever you
say into clean text on your screen, right next to Claude, ChatGPT, and Gemini.
Speak a messy thought, grab the tidy transcript, and paste it into the AI as a
clear prompt — optionally with a refinement instruction added automatically.

Transcription runs **locally on your machine** with
[faster-whisper] — your audio never leaves your computer.

> **Windows only.** BondScribe captures audio through Windows WASAPI
> (`pyaudiowpatch`), which doesn't exist on macOS or Linux, so it can't run
> there yet. See [Can I run it on macOS?](#can-i-run-it-on-macos) below.

---

## Quick start (one double-click)

1. **Download** this project (green **Code → Download ZIP** button on GitHub),
   then **unzip** it somewhere you can write to — your **Documents** or
   **Desktop** folder is perfect. **Don't** put it inside `Program Files`.
2. Open the unzipped folder and **double-click `RunBondScribe.bat`**.
3. The **first run sets everything up for you** — it installs Python, Node.js,
   the app's dependencies, and downloads the speech model. **This takes a few
   minutes and needs an internet connection.** You'll see *"Loading speech
   model…"* while it downloads — that's normal, just wait.
4. After that first run, BondScribe opens in a few seconds every time.

That's the whole setup. There's nothing to install by hand.

> **"Windows protected your PC" (blue SmartScreen box)?** That appears because
> the launcher isn't code-signed yet — it's safe. Click **More info → Run
> anyway**. Your antivirus might also flag the very first run while it downloads
> dependencies; that's a false positive.

*(Tip: right-click `RunBondScribe.bat` → **Send to → Desktop (create shortcut)**
to launch it from your Desktop.)*

### Alternative: install with `git clone`

Prefer the command line, or want to pull updates later with `git pull`? If you
have [Git](https://git-scm.com/download/win) installed:

1. Open **Command Prompt** (press `Win`, type `cmd`, hit Enter) and move to a
   folder you can write to — for example your Documents:

   ```cmd
   cd %USERPROFILE%\Documents
   ```

2. Clone the repository and enter the folder:

   ```cmd
   git clone https://github.com/GiggleHacks/BondScribe.git
   cd BondScribe
   ```

3. Start it:

   ```cmd
   RunBondScribe.bat
   ```

   (Or just double-click `RunBondScribe.bat` in the new `BondScribe` folder.)
   The first run installs everything and downloads the speech model — see the
   notes above.

4. To update later, pull the newest version and run it again:

   ```cmd
   cd %USERPROFILE%\Documents\BondScribe
   git pull
   RunBondScribe.bat
   ```

---

## Requirements

| | |
|---|---|
| OS | 64-bit Windows 10 or 11 |
| Disk | ~3–4 GB (Python + libraries + speech model) |
| RAM | 8 GB minimum, 16 GB recommended |
| GPU | Optional. An NVIDIA GPU makes transcription much faster; without one it runs on CPU (slower, still works). BondScribe detects this automatically. |
| Internet | Needed on the **first** run only (to install things and download the model). After that it transcribes fully offline. |
| AI accounts | You sign in to Claude / ChatGPT / Gemini yourself in the AI pane, with your own accounts. |

---

## How to use it

- **Transcript** streams into the left pane as you speak (or as your audio
  device plays).
- **Select text** in the transcript and it's copied to your clipboard. If a
  *refinement prompt* is set, that instruction is added in front of your text —
  handy for asks like *"Clean up and rephrase this:"*.
- **Send to AI** pastes the clipboard into the AI pane and submits it.
- **Pause / Resume** listening with the button or `Ctrl+Space`.
- The **folder icon** opens your saved transcripts.

### Microphone vs. system audio

By default BondScribe listens to your **microphone**. To transcribe **system
audio** instead (for example, the other side of a call), set
`AUDIO_MODE=loopback` — see [Configuration](#configuration-optional).

### Speed vs. accuracy

BondScribe starts on the **Fastest** model for the lowest delay. Use the model
picker in the app to switch to **Balanced** or **Most Accurate** any time — the
change applies live.

---

## Please use it responsibly

- BondScribe transcribes locally, but the AI panes are **third-party services**.
  **Anything you send into an AI pane leaves your machine** and is processed by
  that provider. Don't paste passwords, secrets, or anything confidential.
- **Recording or transcribing other people** may be subject to consent and
  recording laws where you live. Make sure you're allowed to before you do it.
- Transcripts are **saved locally** in the `logs/` and `sessions/` folders
  (auto-deleted after 90 days). Delete them yourself any time if you'd rather
  not keep them.
- Speech-to-text makes mistakes. Don't treat a transcript as an exact record.

---

## Configuration (optional)

BondScribe works out of the box. To change defaults, copy `.env.example` to
`.env` and edit it. The common knobs:

```
AUDIO_MODE=mic            # or "loopback" for system audio
# WHISPER_PRESET=fastest  # fastest / balanced / accurate
# WHISPER_DEVICE=cuda     # or "cpu"
```

Device and model are chosen automatically from your hardware unless you override
them.

---

## Troubleshooting

If something doesn't work, see **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**. When
asking for help, include these two log files from the BondScribe folder:

- `bondscribe-setup.log` — what the installer did
- `bondscribe.log` — what the app did

---

## Can I run it on macOS?

Not yet. BondScribe's audio capture is built on Windows-only WASAPI
(`pyaudiowpatch`), so the transcription engine can't run on macOS or Linux. A
double-click launcher for those platforms isn't possible until cross-platform
audio capture is added. For now, BondScribe is **Windows 10/11 only**.

---

## For developers

`RunBondScribe.bat` handles everyone — on a machine that already has Python
3.10–3.13 and Node 18+, it skips straight past the install steps and launches.
The backend is FastAPI (`backend/server.py`, port 48011); the desktop shell is
Electron (`desktop/`). Run the backend alone with:

```
python -m uvicorn backend.server:app --host 127.0.0.1 --port 48011
```

[faster-whisper]: https://github.com/SYSTRAN/faster-whisper
