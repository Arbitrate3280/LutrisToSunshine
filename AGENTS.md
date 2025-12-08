# Repository Guidelines

## Project Structure & Module Organization
- `lutristosunshine.py`: CLI entrypoint that orchestrates launcher discovery, Sunshine checks, user prompts, and game import.
- `config/`: constants such as API defaults and color codes.
- `launchers/`: per-launcher integrations (`lutris.py`, `heroic.py`, `bottles.py`, `steam.py`, `ryubing.py`, `retroarch.py`) that list games and expose launch commands.
- `sunshine/`: Sunshine API helpers for installation detection, token management, and app creation.
- `utils/`: shared helpers for input handling, command execution, parsing, and SteamGridDB downloads.
- `requirements.txt`: Python runtime deps (`requests`, `Pillow`). No bundled tests yet.

## Build, Test, and Development Commands
- Install deps: `pip install -r requirements.txt` (use venv if possible).
- Run tool: `python3 lutristosunshine.py` (ensure Sunshine is running; Lutris must be closed).
- Optional binary: use released `./lutristosunshine` after `chmod +x` if available.
- No automated test suite; validate changes by exercising the CLI against at least one launcher and confirming Sunshine receives new apps.

## Coding Style & Naming Conventions
- Python 3, 4-space indents, prefer PEP 8 casing (`snake_case` for functions/vars, `CamelCase` for classes if added).
- Keep functions small and launcher-specific logic inside the corresponding module; reuse helpers from `utils/`.
- Type hints are present in several helpers—extend them when adding new functions.
- User prompts and errors should be concise and actionable; prefer `print` over logging for CLI output.

## Testing Guidelines
- Manual checks: run against supported launchers (Lutris, Heroic, Bottles, Steam, Ryubing, RetroArch) and verify Sunshine shows new entries.
- When touching SteamGridDB flows, confirm API key handling still writes to the expected config path.
- If adding tests, place them under `tests/` and document the runner; aim for coverage of parsing and command-building functions.

## Security & Configuration Tips
- Sunshine auth tokens and SteamGridDB API keys are stored under `~/.config/sunshine/` (or Flatpak-equivalent `.var/app/...`). Do not commit these files.
- Prefer `flatpak-spawn --host` when Sunshine is Flatpak-installed; follow existing patterns in `sunshine.py`.
- Avoid hardcoding personal paths; use `os.path.expanduser` consistently.

## Commit & Pull Request Guidelines
- Commit history follows Conventional Commits (`feat:`, `fix:`, `chore:`). Continue using imperative, scoped messages.
- PRs should describe behavior changes, manual test steps (launchers exercised), and any config impacts (paths, tokens).
- Include screenshots or brief logs when modifying prompts or output formatting, especially around game listings or error handling.
