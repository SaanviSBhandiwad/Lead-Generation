# PromptIQ Growth Agent — Custom UI

A small web UI that wraps your existing `hermes chat -q` setup: four mode
buttons (Investors / Clients / Workspaces / Partners), a results table, and
a Leaflet map panel — all driven by your `promptiq-growth` Hermes skill.

## How it works
- The frontend (`templates/index.html`) is a single-page UI with buttons,
  a table, and a map.
- The backend (`app.py`) is a small Flask server. When you click a button,
  it builds a mode-specific prompt, appends a strict "respond with ONLY
  this JSON shape" instruction, and runs it via:
      hermes chat -q "<prompt>"
  as a subprocess. It then extracts the JSON block from the output
  (using delimiter markers, so it's robust to any log/progress text
  Hermes prints around the actual answer) and returns it to the frontend.

## Setup (on your Ubuntu/WSL machine, where `hermes` is installed)

1. Copy this whole `promptiq_ui/` folder onto your Ubuntu/WSL machine
   (e.g. `~/promptiq_ui/`).

2. Install Flask if you don't already have it:
   ```bash
   pip install flask --break-system-packages
   ```

3. Confirm `hermes` is on your PATH:
   ```bash
   hermes --version
   ```
   If that fails, this backend won't be able to find it either — fix your
   PATH first (or edit the `["hermes", "chat", "-q", prompt]` line in
   app.py to use the full path to the `hermes` binary).

4. Run the backend:
   ```bash
   cd ~/promptiq_ui
   python3 app.py
   ```
   You should see:
   ```
   PromptIQ Growth UI backend running at http://127.0.0.1:5050
   ```

5. Open a browser to **http://127.0.0.1:5050**

## Testing without waiting on real Hermes calls
Real queries can take 30-120+ seconds (multi-step search + Maps
verification + scoring). To test the UI itself instantly with canned
sample data instead of calling the real agent:

```bash
MOCK=1 python3 app.py
```

## Using it for real
With MOCK unset (just `python3 app.py`), clicking a button will actually
invoke your `promptiq-growth` skill through Hermes — same as typing the
equivalent prompt into your terminal or Telegram bot, just from a nicer UI.

You can add extra targeting instructions in the "Optional extra
instructions" text box (e.g. "focus on fintech" or "exclude government
enterprises") — these get appended to the prompt sent to Hermes.

## Known limitations
- **Latency**: each click is a fresh `hermes chat -q` subprocess call —
  there's no session reuse, so every click re-does full research from
  scratch. Fine for on-demand use, not for anything needing instant
  responses.
- **JSON reliability**: if the underlying model occasionally ignores the
  "respond with ONLY JSON" instruction (more likely on weaker/free-tier
  models), the backend will return a clear error with the raw output
  included so you can see what went wrong, rather than silently failing.
- **Timeout**: defaults to 240 seconds per query (see
  `HERMES_TIMEOUT_SECONDS` in app.py) — increase this if your searches
  routinely need more time, e.g. for larger `count` values.
- This assumes `hermes` runs correctly as a plain subprocess call from
  wherever you launch `python3 app.py` — if you normally invoke it through
  a wrapper, venv activation, or systemd service context, you may need to
  adjust the subprocess command in `app.py` accordingly.
