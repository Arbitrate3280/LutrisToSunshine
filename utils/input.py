from typing import Any, List, Tuple, Callable, Optional, Union
from utils.utils import handle_interrupt

CUSTOM_COMMAND_SELECTION = "__custom_command__"
def get_user_input(prompt: str, validator: Callable[[str], Any], error_message: str) -> Any:
    """Get and validate user input."""
    while True:
        try:
            user_input = input(prompt)
            return validator(user_input)
        except ValueError:
            print(error_message)
        except (KeyboardInterrupt, EOFError):
            handle_interrupt()

def yes_no_validator(value: str) -> bool:
    """Validate yes/no input."""
    value = value.strip().lower()
    if value in ['y', 'yes']:
        return True
    elif value in ['n', 'no']:
        return False
    raise ValueError()

def get_yes_no_input(prompt: str, default: Optional[bool] = None) -> bool:
    """Get a yes or no input from the user."""
    if default is True:
        prompt = f"{prompt.rstrip()} [Y/n]: "
    elif default is False:
        prompt = f"{prompt.rstrip()} [y/N]: "

    def validator(value: str) -> bool:
        stripped = value.strip()
        if stripped == "" and default is not None:
            return default
        return yes_no_validator(stripped)

    return get_user_input(
        prompt,
        validator,
        "Invalid input. Please enter 'y' for yes or 'n' for no."
    )

def get_menu_choice(prompt: str, valid_choices: List[str]) -> str:
    """Get a menu choice from a known set of values."""
    normalized_choices = {choice.strip().lower() for choice in valid_choices}

    def validator(value: str) -> str:
        selected = value.strip().lower()
        if selected in normalized_choices:
            return selected
        raise ValueError()

    return get_user_input(
        prompt,
        validator,
        f"Invalid selection. Choose one of: {', '.join(valid_choices)}."
    )

def _non_empty_validator(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError()
    return stripped


def get_required_input(prompt: str, error_message: str = "Input cannot be empty.") -> str:
    """Prompt for a non-empty trimmed string."""
    return get_user_input(prompt, _non_empty_validator, error_message)

def get_user_selection(games: List[Tuple[str, str]]) -> Union[List[int], str]:
    """Get user selection of games to add."""
    print(f"{len(games) + 1}. Add all games")
    print(f"{len(games) + 2}. Add custom command")

    def selection_validator(value: str) -> Union[List[int], str]:
        if value.strip() == str(len(games) + 1):
            return list(range(len(games)))
        if value.strip() == str(len(games) + 2):
            return CUSTOM_COMMAND_SELECTION

        indices = []
        for part in value.split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                indices.extend(range(start - 1, end))
            else:
                indices.append(int(part) - 1)

        if all(0 <= i < len(games) for i in indices):
            return list(set(indices))  # Remove duplicates
        raise ValueError()

    return get_user_input(
        "Select games to add (comma-separated or ranges like 2-9): ",
        selection_validator,
        "Invalid selection. Please try again."
    )
