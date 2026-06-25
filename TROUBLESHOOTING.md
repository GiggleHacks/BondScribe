# BondScribe — Troubleshooting

Before anything else, grab the two log files in your BondScribe folder — they
answer most questions:

- **`bondscribe-setup.log`** — the installer (Python/Node/deps).
- **`bondscribe.log`** — the running app (backend + model loading).

---

## "Windows protected your PC" / SmartScreen blue box

The launcher isn't code-signed yet. Click **More info → Run anyway**. This is
expected and safe; it only appears because the file is new/unsigned.

## Antivirus blocks or quarantines it

First-run setup downloads Python, Node, and the speech model, which some AV
tools flag. Allow `RunBondScribe.bat` / the BondScribe folder, then re-run.

## "Could not find or install Python / Node"

The auto-installer uses Windows `winget`. If it's missing or blocked (common on
locked-down/corporate machines):

- Install **Python 3.10–3.13** from <https://www.python.org/downloads/> and
  **check "Add python.exe to PATH"** during setup.
- Install **Node.js LTS** from <https://nodejs.org>.
- Re-run `RunBondScribe.bat`.

## Setup fails downloading dependencies (corporate proxy / firewall)

`pip install` and the model download need internet. On a proxy, set the proxy
environment variables before running, or run it on an unrestricted network once
(after the first successful run it works offline).

## The window is stuck on "Loading speech model…"

On the **first** run the model (hundreds of MB to a few GB) downloads — give it
several minutes on a normal connection. If it never finishes, check
`bondscribe.log` for download errors (usually no internet or a blocked proxy).

## "Port 48011 already in use"

Another copy of BondScribe (or something else on that port) is running. Close it
and press **F5** in the BondScribe window, or reboot.

## Transcription is very slow

You're likely on **CPU** (no NVIDIA GPU). BondScribe already picks a smaller
model on CPU, but it's still slower than on a GPU. To trade accuracy for speed,
set a smaller model in `.env` (e.g. `WHISPER_MODEL=base`).

## No transcript appears / the level meter doesn't move

- Make sure the right input device is the Windows **default**, or pin one with
  `AUDIO_DEVICE_INDEX` in `.env` (list devices: `python -m backend.audio --list`).
- Check Windows **microphone privacy** permissions.
- To capture **system audio** instead of the mic, set `AUDIO_MODE=loopback`.

## "Send to AI" doesn't paste into the assistant

You must be **signed in** to the assistant (Claude/ChatGPT/Gemini) in the AI
pane. If the site changed its layout, the auto-paste may fail — copy the text
and paste it manually.

## Still stuck?

Open an issue and attach **`bondscribe-setup.log`** and **`bondscribe.log`**,
plus your Windows version and whether you have an NVIDIA GPU.
