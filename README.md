# LutrisToSunshine

Imports games from supported launchers into Sunshine with optional SteamGridDB cover art, plus a guided virtual-display mode for headless streaming.

<img width="1065" height="444" alt="lutristosunshine" src="https://github.com/user-attachments/assets/e4b02abd-1797-44ec-a965-856ba00e7112" />
<img width="1648" height="439" alt="Captura_de_tela_20260324_203642" src="https://github.com/user-attachments/assets/bec9a1e3-9318-492f-bdf5-242429dc6607" />

## Supported Launchers

- Lutris, Heroic, Steam, Faugus: native and Flatpak
- Bottles, Ryubing: Flatpak
- RetroArch: native and Flatpak
- Eden: AppImage and native binary

## Quick Start

```bash
git clone https://github.com/Arbitrate3280/LutrisToSunshine.git
cd LutrisToSunshine
pip install -r requirements.txt
```

Make sure Sunshine is running, close Lutris before scanning (other launchers can stay open), then:

```bash
python3 lutristosunshine.py
```

### Options

| Flag | What it does |
|------|-------------|
| `--all` | Import all detected games without prompts |
| `--cover` | Download SteamGridDB covers for imported games |
| `--sunshine-host` | Override the Sunshine/Apollo web UI host |
| `--sunshine-port` | Override the Sunshine web UI port (usually `47990`) |

## Virtual Display

Run `python3 lutristosunshine.py display` to open an interactive menu for managing headless streaming on a separate desktop — your main session stays usable while streaming.

The menu includes:

- **Setup/update** — installs the Sway+Sunshine stack and reconciles your game apps
- **Display sync** — match the client's resolution and FPS, set a custom mode, or lock a fixed resolution
- **GPU selection** — pick which GPU drives the virtual display (for multi-GPU setups)
- **Renderer** — toggle between GLES2 (stable, broad support) and Vulkan (HDR capable)
- **Host controllers** — make controllers connected to your PC available inside the virtual display
- **MangoHud FPS limit** — dynamically cap FPS to the client's refresh rate
- **Service controls** — start, stop, or restart Sunshine for the virtual display
- **Status dashboard** — health overview, input isolation state, controller info, and more
- **Advanced tools** — logs, rumble test, and full teardown

Requires `sway`, `swaybg`, and `xdg-desktop-portal-wlr`.

## Binary Release

```bash
chmod +x lutristosunshine
./lutristosunshine --all --cover
```

## License

[MIT](https://choosealicense.com/licenses/mit-license.php)
