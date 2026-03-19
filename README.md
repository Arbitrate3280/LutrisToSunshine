
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

### Virtual Display (Headless Streaming)

**What is it?**

The Virtual Display feature creates a separate, invisible desktop environment that runs in the background on your system. This lets you stream games without interrupting your actual desktop work - perfect for headless systems or when you want to keep your main desktop undisturbed while gaming.

**Why use it?**

- **Uninterrupted desktop work**: Stream games in the background while your actual desktop stays usable for other tasks
- **Headless-friendly**: No monitor required - the virtual display runs completely without a physical display
- **Smart controller handling**: Use controllers from your streaming device or pass through physical controllers from your host PC without interfering with your desktop
- **Flatpak compatibility**: Games from Flatpak launchers (Lutris, Heroic, etc.) automatically target the virtual environment without messing up your host desktop settings

**How it works:**

Behind the scenes, this feature sets up a headless Sway compositor (a Wayland display server) that Sunshine can capture from. When you launch a game, it gets directed to this invisible display instead of your main desktop. The system also handles the complex plumbing needed for Flatpak games and ensures your controllers work properly in the streaming session.
**Two types of game inputs:**

This feature supports two different ways to control your streamed games:

**1. Client inputs (automatic)**

Controllers, keyboards, and mice from your streaming device (like the Moonlight app on your phone or another PC) work automatically. These inputs go directly to the streamed game and never affect your host desktop - you can be working on your host PC while someone else controls the game from their device.
The managed setup installs a udev rule so Sunshine's virtual keyboard, mouse, touch, and pen devices stay accessible to the headless Sway session.

**2. Host controller pass-through (optional)**

If you have physical controllers connected to your host PC (USB, Bluetooth, etc.) and want to use them in your streamed games, the `inputs` command lets you select which controllers should be "grabbed" from the host and re-exposed only to the streaming session. This means:
- Your host PC won't see or respond to those controllers while you're streaming
- The controllers only work in the streamed game, not on your host desktop
- Perfect for keeping your desktop clean while gaming, or for setups where you want dedicated gaming controllers
- The original controller identity is preserved (including rumble support when available)

**Commands:**

Use the `virtualdisplay` command group to manage the headless streaming environment:

```bash
python3 lutristosunshine.py virtualdisplay setup
python3 lutristosunshine.py virtualdisplay inputs
python3 lutristosunshine.py virtualdisplay status
python3 lutristosunshine.py virtualdisplay test-rumble
python3 lutristosunshine.py virtualdisplay sync-apps
python3 lutristosunshine.py virtualdisplay logs
python3 lutristosunshine.py virtualdisplay stop
python3 lutristosunshine.py virtualdisplay disable
```

**Command details:**

- `setup` - Installs user services, helper scripts, input-isolation rules, starts the managed Sunshine stack, and updates existing Sunshine app entries to work with the virtual display
- `inputs` - Select physical controllers connected to your host PC for exclusive use in streamed games (see "Two types of game inputs" above for details)
- `status` - Shows whether the virtual display is running, which controllers are configured, and system information
- `test-rumble` - Sends test rumble signals to your controllers to verify force feedback is working through the virtual device path
- `sync-apps` - Reapplies virtual display settings to existing Sunshine app entries
- `logs` - Shows system logs for debugging virtual display issues
- `stop` - Stops the virtual display stack
- `disable` - Removes the virtual display setup and restores Sunshine to normal operation

**Technical notes:**

- Imported games automatically receive the virtual-display launch wrapper and resolution commands when this mode is enabled
- Flatpak launchers use transient portal handoff during launch, so they target the headless session without permanently affecting your host desktop
- Sunshine capture settings remain user-managed (this tool doesn't change your `capture` configuration)
- After upgrading LutrisToSunshine, rerun `python3 lutristosunshine.py virtualdisplay setup` once so the managed udev rule is regenerated with the latest input permissions

### Virtual Display Status

The virtual display feature is currently a **work-in-progress** and has been tested only on:
- **Aurora** (specific environment)
- **AMD GPU** configuration

Other environments, GPU configurations, and distributions may encounter issues. Feedback and testing reports from different setups are welcome.

3. Follow the prompts to list games, select games to add, and optionally download images from SteamGridDB.

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
