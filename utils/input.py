from typing import Any, List, Tuple, Callable
from utils.utils import handle_interrupt

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

def get_yes_no_input(prompt: str) -> bool:
    """Get a yes or no input from the user."""
    return get_user_input(
        prompt,
        yes_no_validator,
        "Invalid input. Please enter 'y' for yes or 'n' for no."
    )

def get_user_selection(games: List[Tuple[str, str]]) -> List[int]:
    """Get user selection of games to add."""
    print(f"{len(games) + 1}. Add all games")

    def selection_validator(value: str) -> List[int]:
        if value.strip() == str(len(games) + 1):
            return list(range(len(games)))

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
        "Enter the number(s) of the game(s) you want to add to Sunshine (comma-separated for multiple, or ranges like 2-9): ",
        selection_validator,
        "Invalid selection. Please try again."
    )
