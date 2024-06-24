
# LutrisToSunshine

This script lists games from Lutris, adds them to Sunshine, and optionally downloads game covers from SteamGridDB.



## Features

- Lists all games from Lutris (supports both Flatpak and native installations)
- Adds selected games to Sunshine
- Option to add all listed games at once
- Downloads game covers from SteamGridDB (optional)
- Avoids duplicate entries in Sunshine

## Main Goal
This tool is designed to work with Sunshine installed on the host and Lutris. I'm sharing it in case others find it useful. It was created with the help of AI, as I'm not a developer. Please note, this is a personal tool and not intended as a formal project.

## Installation

1. Clone the repository

```bash
  git clone https://github.com/yourusername/LutrisToSunshine.git
  cd LutrisToSunshine
```
2. Install required Python libraries:

```bash
  pip install requests Pillow
```
## Usage

1. Ensure that Lutris is closed before running the script.

2. Run the script:

```sh
python3 lutristosunshine.py
```

3. Follow the prompts to list games, select games to add, and optionally download images from SteamGridDB.
## License

[MIT](https://choosealicense.com/licenses/mit/)


## Acknowledgements

 - [Lutris](https://lutris.net/)
 - [Sunshine](https://app.lizardbyte.dev/Sunshine/)
 - [SteamGridDB](https://www.steamgriddb.com/)

