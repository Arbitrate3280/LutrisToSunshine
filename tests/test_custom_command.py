import unittest
from unittest.mock import patch

from utils.input import get_user_selection, CUSTOM_COMMAND_SELECTION, get_required_input
from sunshine.catalog import submit_command
from sunshine.connection import CONNECTION


class GetUserSelectionCustomSentinelTests(unittest.TestCase):
    """Test that get_user_selection returns CUSTOM_COMMAND_SELECTION for the custom sentinel."""

    @patch("builtins.input", return_value="4")
    def test_returns_custom_command_sentinel_for_custom_option(self, mock_input):
        games = [("id1", "Game 1"), ("id2", "Game 2")]
        result = get_user_selection(games)
        self.assertEqual(result, CUSTOM_COMMAND_SELECTION)

    @patch("builtins.input", return_value="1")
    def test_returns_indices_for_game_selection(self, mock_input):
        games = [("id1", "Game 1"), ("id2", "Game 2")]
        result = get_user_selection(games)
        self.assertEqual(result, [0])

    @patch("builtins.input", return_value="1,2")
    def test_returns_multiple_indices(self, mock_input):
        games = [("id1", "Game 1"), ("id2", "Game 2")]
        result = get_user_selection(games)
        self.assertEqual(sorted(result), [0, 1])

    @patch("builtins.input", return_value="3")
    def test_all_games_sentinel_for_two_games(self, mock_input):
        games = [("id1", "Game 1"), ("id2", "Game 2")]
        result = get_user_selection(games)
        # N+1 = 3 is "Add all games", N+2 = 4 is "Add custom command"
        # So 3 should return all indices, not CUSTOM_COMMAND_SELECTION
        self.assertEqual(result, [0, 1])


class GetRequiredInputTests(unittest.TestCase):
    """Test get_required_input validation."""

    @patch("builtins.input", return_value="valid input")
    def test_returns_stripped_input(self, mock_input):
        result = get_required_input("Enter: ")
        self.assertEqual(result, "valid input")

    @patch("builtins.input", return_value="  spaces  ")
    def test_strips_whitespace(self, mock_input):
        result = get_required_input("Enter: ")
        self.assertEqual(result, "spaces")

    @patch("builtins.input", side_effect=["", "  ", "valid"])
    def test_retries_on_empty_input(self, mock_input):
        result = get_required_input("Enter: ")
        self.assertEqual(result, "valid")
        self.assertEqual(mock_input.call_count, 3)


class SubmitCustomCommandTests(unittest.TestCase):
    """Test submit_command passes custom commands through to the API."""

    @patch.object(CONNECTION, "installation_type", "native")
    @patch.object(CONNECTION, "server_name", "sunshine")
    @patch("sunshine.catalog.display_enabled", return_value=False)
    @patch("sunshine.catalog.add_game_to_sunshine_api")
    def test_calls_api_add_with_correct_args(self, mock_api, mock_display):
        submit_command(
            "My Game",
            "flatpak run com.example.game",
            "/path/to/image.png",
            display_enabled_fn=mock_display,
            api_add_fn=mock_api,
        )
        mock_api.assert_called_once_with(
            "My Game",
            "flatpak run com.example.game",
            "/path/to/image.png",
            prep_cmd=[],
            detached=[],
        )

    @patch.object(CONNECTION, "installation_type", "native")
    @patch.object(CONNECTION, "server_name", "sunshine")
    @patch("sunshine.catalog.display_enabled", return_value=False)
    @patch("sunshine.catalog.add_game_to_sunshine_api")
    def test_calls_api_add_with_empty_command(self, mock_api, mock_display):
        submit_command(
            "Test",
            "",
            "/path/to/image.png",
            display_enabled_fn=mock_display,
            api_add_fn=mock_api,
        )
        mock_api.assert_called_once_with(
            "Test",
            "",
            "/path/to/image.png",
            prep_cmd=[],
            detached=[],
        )


class SubmitCommandToSunshineTests(unittest.TestCase):
    """Test submit_command applies wrapping correctly."""

    @patch.object(CONNECTION, "installation_type", "flatpak")
    @patch.object(CONNECTION, "server_name", "sunshine")
    @patch("sunshine.catalog.display_enabled", return_value=False)
    @patch("sunshine.catalog.add_game_to_sunshine_api")
    def test_flatpak_prefix_applied(self, mock_api, mock_display):
        submit_command(
            "Test Game",
            "mycommand",
            "/img.png",
            display_enabled_fn=mock_display,
            api_add_fn=mock_api,
        )
        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][0], "Test Game")
        self.assertIn("flatpak-spawn --host mycommand", call_args[0][1])

    @patch.object(CONNECTION, "installation_type", "native")
    @patch.object(CONNECTION, "server_name", "sunshine")
    @patch("sunshine.catalog.display_enabled", return_value=True)
    @patch("sunshine.catalog.get_app_prep_commands", return_value=[{"do": "/usr/bin/prep.sh", "undo": "/usr/bin/cleanup.sh"}])
    @patch("sunshine.catalog.wrap_command", return_value="/usr/bin/wrapped mycommand")
    @patch("sunshine.catalog.add_game_to_sunshine_api")
    def test_display_enabled_applies_wrap_and_prep(self, mock_api, mock_wrap, mock_prep, mock_display):
        submit_command(
            "Test Game",
            "mycommand",
            "/img.png",
            display_enabled_fn=mock_display,
            wrap_fn=mock_wrap,
            prep_fn=mock_prep,
            api_add_fn=mock_api,
        )
        mock_wrap.assert_called_once_with("mycommand", "cmd")
        mock_prep.assert_called_once()
        mock_api.assert_called_once_with(
            "Test Game",
            "/usr/bin/wrapped mycommand",
            "/img.png",
            prep_cmd=[{"do": "/usr/bin/prep.sh", "undo": "/usr/bin/cleanup.sh"}],
            detached=[],
        )

if __name__ == "__main__":
    unittest.main()
