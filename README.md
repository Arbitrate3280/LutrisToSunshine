
# LutrisToSunshine

This script lists games from Lutris, Heroic, Bottles, Steam, Faugus, Ryubing, RetroArch, and Eden, adds them to Sunshine, and optionally downloads game covers from SteamGridDB.

<img width="1065" height="444" alt="lutristosunshine" src="https://github.com/user-attachments/assets/e4b02abd-1797-44ec-a965-856ba00e7112" />

## Features

- Supports:
  - Lutris (Native and Flatpak)
  - Heroic (Native and Flatpak)
  - Bottles (Flatpak)
  - Steam (Native and Flatpak)
  - Faugus (Flatpak)
  - Ryubing (Flatpak)
  - RetroArch (Native and Flatpak)
  - Eden (AppImage and native binary)
- Adds selected games to Sunshine
- Option to add all listed games at once
- Downloads game covers from SteamGridDB (optional)
- Avoids duplicate entries in Sunshine

## Main Goal
This tool is designed to work with Sunshine (native or flatpak) along with Lutris, Heroic, Bottles, Steam, Faugus, Ryubing, RetroArch, or Eden. I'm sharing it in case others find it useful. It was created with the help of AI, as I'm not a developer. Please note, this is a personal tool and not intended as a formal project.

## Installation

1. Clone the repository

```bash
  git clone https://github.com/Arbitrate3280/LutrisToSunshine.git
  cd LutrisToSunshine
```
2. Install required Python libraries:

```bash
  pip install -r requirements.txt
```
## Usage

1. Ensure that Lutris is closed before running the script (other launchers should be fine).

2. Run the script:

```bash
python3 lutristosunshine.py
```

### Command Line Arguments

- `--cover`: Automatically download covers from SteamGridDB for all added games
- `--all`: Automatically add all listed games (skips the selection prompt)

### Examples

- Interactive mode (select games manually, optionally download covers):
  ```bash
  python3 lutristosunshine.py
  ```

- Add all games with automatic cover downloads:
  ```bash
  python3 lutristosunshine.py --all --cover
  ```

- Add all games without cover downloads:
  ```bash
  python3 lutristosunshine.py --all
  ```

- Interactive mode with automatic cover downloads:
  ```bash
  python3 lutristosunshine.py --cover
  ```

3. Follow the prompts to list games, select games to add, and optionally download images from SteamGridDB.

### Launcher Notes

- Faugus support is currently Flatpak-only.
- Faugus games are discovered from `~/.var/app/io.github.Faugus.faugus-launcher/config/faugus-launcher/games.json`.
- Faugus entries are launched through the Faugus Flatpak runtime so bundled tools such as `mangohud` work the same way they do inside Faugus itself.
- Per-game Faugus options such as `mangohud`, `disable_hidraw`, and `prevent_sleep` are carried over when generating the Sunshine command.

Alternatively, you can download the binary available in the "Releases" section of the GitHub repository. Download the binary from the latest release, make it executable, and run it:

```bash
chmod +x lutristosunshine
./lutristosunshine
```

The same command line arguments work with the binary:
```bash
./lutristosunshine --all --cover
```

## License

[MIT](https://choosealicense.com/licenses/mit/)


## Acknowledgements

 - [Lutris](https://lutris.net/)
 - [Heroic Games Launcher](https://heroicgameslauncher.com/)
 - [Bottles](https://usebottles.com/)
 - [Faugus Launcher](https://github.com/Faugus/faugus-launcher)
 - [Ryubing](https://ryujinx.app/)
 - [RetroArch](https://www.retroarch.com/)
 - Eden
 - [Sunshine](https://app.lizardbyte.dev/Sunshine/)
 - [SteamGridDB](https://www.steamgriddb.com/)
