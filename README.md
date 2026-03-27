# LutrisToSunshine

LutrisToSunshine imports games from supported launchers into Sunshine and can optionally download cover art from SteamGridDB. It also includes a guided virtual-display mode for headless streaming, so streamed games can run on a separate desktop without disturbing your main session.

<img width="1065" height="444" alt="lutristosunshine" src="https://github.com/user-attachments/assets/e4b02abd-1797-44ec-a965-856ba00e7112" />
<img width="1648" height="439" alt="Captura_de_tela_20260324_203642" src="https://github.com/user-attachments/assets/bec9a1e3-9318-492f-bdf5-242429dc6607" />

## Table of Contents

- [Why Use It](#why-use-it)
- [Supported Launchers](#supported-launchers)
- [Quick Start](#quick-start)
- [Common Usage](#common-usage)
- [What The Main Flow Does](#what-the-main-flow-does)
- [Virtual Display](#virtual-display)
- [Headless Prep Commands](#headless-prep-commands)
- [Troubleshooting](#troubleshooting)
- [Binary Release](#binary-release)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## Why Use It

- Import games from multiple launchers into Sunshine from one CLI
- Avoid duplicate Sunshine entries
- Optionally fetch game covers from SteamGridDB
- Run streamed games in a virtual display that dynamically matches resolution/refresh rate of the client with optional host-controller passthrough

## Supported Launchers

- Lutris: native and Flatpak
- Heroic: native and Flatpak
- Bottles: Flatpak
- Steam: native and Flatpak
- Faugus: Flatpak
- Ryubing: Flatpak
- RetroArch: native and Flatpak
- Eden: AppImage and native binary

## Quick Start

1. Clone the repository:

```bash
git clone https://github.com/Arbitrate3280/LutrisToSunshine.git
cd LutrisToSunshine
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Make sure Sunshine is running.

4. Close Lutris before scanning. Other launchers can stay open.

5. Run the importer:

```bash
python3 lutristosunshine.py
```

## Common Usage

Interactive import:

```bash
python3 lutristosunshine.py
```

Import all detected games:

```bash
python3 lutristosunshine.py --all
```

Import all detected games and download covers:

```bash
python3 lutristosunshine.py --all --cover
```

Interactive import with automatic cover downloads:

```bash
python3 lutristosunshine.py --cover
```

Import using a custom Sunshine web UI port:

```bash
python3 lutristosunshine.py --sunshine-port 10000
```

### Main Flags

- `--all`: add all detected games without the selection prompt
- `--cover`: download SteamGridDB covers for added games
- `--sunshine-host`: override the Sunshine or Apollo web UI host for auth and API requests
- `--sunshine-port`: override the Sunshine or Apollo web UI port for auth and API requests; this is usually `47990`

## What The Main Flow Does

When you run `python3 lutristosunshine.py`, the tool:

1. Detects Sunshine or Apollo
2. Finds supported launchers installed on your system
3. Lists the games it can import
4. Lets you choose which games to add
5. Optionally downloads covers
6. Adds the selected games to Sunshine

## Virtual Display

### What It Is

Virtual display creates a separate headless desktop for streamed games. Sunshine captures that desktop instead of your main one, so you can keep using your normal desktop while a stream is running.

### Why It Is Useful

- Keep your main desktop usable while streaming
- Run Sunshine on headless systems
- Keep client input isolated from the host desktop
- Dynamically matches resolution/refresh rate of the client
- Route streamed-game audio separately from normal desktop audio
- Host controller passthrough: optional. Use the `controllers` command only if you want physical controllers connected to the host PC to be reserved for streamed games and hidden from the host desktop while streaming.

### Virtual Display Requirements

Install these packages first:

- `sway`
- `swaybg`
- `xdg-desktop-portal-wlr`

Fedora / RHEL:

```bash
sudo dnf install sway swaybg xdg-desktop-portal-wlr
```

Ubuntu / Debian:

```bash
sudo apt install sway swaybg xdg-desktop-portal-wlr
```

Arch:

```bash
sudo pacman -S sway swaybg xdg-desktop-portal-wlr
```

### Virtual Display Quick Start

Open the guided hub:

```bash
python3 lutristosunshine.py virtualdisplay
```

Set up, start, and sync the full virtual-display flow:

```bash
python3 lutristosunshine.py virtualdisplay enable
```

Configure optional host-controller passthrough:

```bash
python3 lutristosunshine.py virtualdisplay controllers
```

Inspect problems and suggested fixes:

```bash
python3 lutristosunshine.py virtualdisplay doctor
```

Show current virtual-display state:

```bash
python3 lutristosunshine.py virtualdisplay status
```

Enable the dynamic MangoHud FPS limit so it dynamically sets it to the client's refresh rate:

```bash
python3 lutristosunshine.py virtualdisplay mangohud-fps-limit enable
```

Test controller rumble through the bridged path:

```bash
python3 lutristosunshine.py virtualdisplay rumble
```

Stop the running virtual-display stack without removing setup:

```bash
python3 lutristosunshine.py virtualdisplay stop
```

Undo virtual display changes, restore Sunshine to original state, and remove the managed setup:

```bash
python3 lutristosunshine.py virtualdisplay reset
```

Show recent virtual-display logs:

```bash
python3 lutristosunshine.py virtualdisplay logs
```

## Headless Prep Commands

Use `headless:` on `prep-cmd.do` and `prep-cmd.undo` when a companion command should run inside the Sway virtual display instead of on the host session.


Example:

```json
"prep-cmd": [
  {
    "do": "headless:/home/vitor/bash-scripts/antimicrox-toggle.sh do",
    "undo": "headless:/home/vitor/bash-scripts/antimicrox-toggle.sh undo"
  }
]
```

After editing the app entry, run:

```bash
python3 lutristosunshine.py virtualdisplay enable
```

That reconciles the Sunshine app list and rewrites `headless:` prep commands to the managed helper that launches them with the virtual-display environment and Flatpak portal handoff when needed.

## Troubleshooting

### Input isolation not working on KDE Plasma

If you are on KDE Plasma and input isolation is not working (e.g., keyboard or mouse events leak between the virtual display and the host desktop), try adding your user to the `input` group:

```bash
sudo usermod -aG input $USER
```

Log out and back in (or start a new session) for the change to take effect.

## Limitations And Compatibility

Virtual display is still a work in progress. It has currently been tested only on:

- Aurora (Ublue)
- AMD GPU

Other environments, distributions, or GPU setups may need extra troubleshooting.

Dynamic MangoHud FPS limit only works if you're already using MangoHud, it doesn't set it up for you. It sets `MANGOHUD_CONFIG=read_cfg,fps_limit=$SUNSHINE_CLIENT_FPS` for those launches and does not enable MangoHud by itself.
Still need to disable input on host for bridged controllers. 

## Binary Release

If you prefer the standalone binary from the Releases page:

```bash
chmod +x lutristosunshine
./lutristosunshine
```

The same flags still apply:

```bash
./lutristosunshine --all --cover
```

## License

[MIT](https://choosealicense.com/licenses/mit-license.php)

## Acknowledgements

- [Lutris](https://lutris.net/)
- [Heroic Games Launcher](https://heroicgameslauncher.com/)
- [Bottles](https://usebottles.com/)
- [Faugus Launcher](https://github.com/Faugus/faugus-launcher)
- [Ryubing](https://ryujinx.app/)
- [RetroArch](https://www.retroarch.com/)
- [Eden](https://eden-emu.dev/)
- [Sunshine](https://app.lizardbyte.dev/Sunshine/)
- [SteamGridDB](https://www.steamgriddb.com/)
- [Sunshine Headless Sway](https://github.com/daaaaan/sunshine-headless-sway)
