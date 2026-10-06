<p align="center">
  <img src="assets/ishti_logo_dark.png#gh-dark-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
  <img src="assets/ishti_logo.png#gh-light-mode-only" alt="ISHTI: Personal AI Assistant" width="460" />
</p>

# ISHTI: your private assistant on your own PC

ISHTI is a personal assistant that lives on your Windows PC. You can type to it or talk to it. It answers questions,
keeps your reminders and notes, finds files, opens apps, writes and reads documents, and translates to and from
Urdu and Pashto. It is **100% private**: the AI runs on your own graphics card, and everything it remembers stays in
a folder on your PC. Nothing is sent to a company's servers.

## What you need

- Windows 10 or Windows 11
- An NVIDIA graphics card. 8 GB of video memory is recommended; 16 GB gives the best results.
- About 25 GB of free disk space
- Internet, only for the first install (to download the programs and AI models once)

## Install (3 steps)

1. **Get the project.** On the project's GitHub page, click the green **Code** button, then **Download ZIP**.
   Open the ZIP and drag the folder to a short place such as `C:\ISHTI`. (If you know Git, `git clone` works too.)
2. **Double-click `install.bat`.** A black window opens and tells you what it is doing. It may ask a few Yes/No
   questions: press **Enter** to accept the suggestion. The first time takes 20 to 40 minutes, mostly downloading.
   You can leave it alone. If it stops, just double-click `install.bat` again; it carries on where it stopped.
3. **Double-click the ISHTI icon on your desktop.** The installer put it there. That is all.

The installer needs [Ollama](https://ollama.com/download) (a free program that runs AI models). If you do not have
it, the installer tells you where to get it and waits for you.

## First use

- The ISHTI window opens in a couple of seconds.
- A **Starting up** panel shows ISHTI getting ready. This takes a short while the first time.
- When it says ISHTI is ready, type a message and press Enter. Or say something, if you have switched the
  microphone on (see Settings below).

Try asking:

1. "What is the weather today?"
2. "Remind me to call Ali tomorrow at 5 pm."
3. "Find my files named invoice."
4. "Translate 'good morning, how are you?' into Urdu."
5. "Write a short, polite email asking for a day off."

ISHTI asks you before it does anything that cannot be undone, such as deleting files or sending a message.

## Settings, explained simply

Open **Settings** inside the ISHTI window.

- **Language:** English is always on. You can switch Urdu and Pashto on if you want them.
- **Models & voices:** choose which AI model ISHTI uses, download new ones, and delete the ones you do not need
  (this frees disk space). You can also pick the voice ISHTI speaks with.
- **Privacy:** see and control everything that leaves your PC (more below).

The microphone and voice control are **off** until you turn them on yourself.

## Our privacy promise

- Nothing is sent anywhere unless you ask a web skill to look something up (for example, the weather or a web search).
- There is no tracking and no telemetry.
- In **Settings -> Privacy** you can switch the internet off completely. ISHTI then works only with what is on your PC.
- The same page lists every outside connection, so you can see for yourself.

## If something goes wrong

**The window does not open.** Wait 20 seconds, then double-click ISHTI again. Still nothing? Double-click `stop.bat`
in the ISHTI folder, then start ISHTI again. If it keeps happening, run `install.bat` again; it repairs what is missing.

**"Ollama not found."** Install Ollama from <https://ollama.com/download>, then double-click `install.bat` again.

**The AI model is too big for my graphics card.** Open **Settings -> Models & voices** and choose a smaller model.
Smaller models are faster and use less memory.

**ISHTI does not speak, or I hear no sound.** Check that your speakers or headphones are the Windows default and are
not muted. In **Settings -> Models & voices**, make sure a voice is downloaded and selected. For the microphone, turn
it on first.

**ISHTI answers very slowly.** Close games and other programs that use the graphics card. A smaller model also helps.

**I want to see what went wrong.** The log files are in `%LOCALAPPDATA%\ISHTI\logs`. Paste that into the address bar
of a File Explorer window and press Enter. The installer's own log is `install.log` in the ISHTI folder.

**How do I check my installation?** Open a Command Prompt in the ISHTI folder and run
`venv\Scripts\python tools\install.py --check`. It lists what is in place and what is missing.

**How do I shut ISHTI down properly?** Use the **Shut down** button in the app, or close the window. Both stop ISHTI
cleanly and give your graphics card back.

**How do I uninstall?** Delete the ISHTI folder and the ISHTI icon on your desktop. The AI models are stored by Ollama, not
in this folder. To remove them too, uninstall Ollama from Windows Settings -> Apps.

## Learn more

| Read this | For |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Install details, running, testing, troubleshooting |
| [docs/SAFETY.md](docs/SAFETY.md) | How ISHTI asks permission, limits itself, and keeps a record of what it did |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How ISHTI is built, for developers |
| [docs/SKILLS.md](docs/SKILLS.md) | Everything ISHTI can do, and how to add a new ability |
| [docs/PC_VERIFY.md](docs/PC_VERIFY.md) | What was just checked on this PC after the latest update (every safety change re-tested, live proofs, results) |

Latest update (October 2026): every safety change from the code review was re-tested on this PC — 125/125 skills load,
the AI stays fully on the graphics card, asking permission before deleting anything works, and the full offline test
suite passes. Details and evidence in [docs/PC_VERIFY.md](docs/PC_VERIFY.md).

For developers: `start.bat doctor` checks the system, `start.bat test` runs the offline tests, and
`start.bat server` runs only the backend. Third-party software and licences are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
