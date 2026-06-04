import unittest
from typing import List

from config.types import (
    GameSelection,
)
from utils.utils import (
    dedupe_selected_games_by_name,
    get_source_priority,
    normalize_game_name_for_dedup,
)


LUTRIS_CYBERPUNK: GameSelection = ("1", "Cyberpunk 2077", "Lutris", "Lutris")
STEAM_CYBERPUNK: GameSelection = ("1091500", "Cyberpunk 2077", "Steam", "Steam")
HEROIC_CONTROL: GameSelection = ("abc", "Control", "Heroic", "legendary")
LUTRIS_WITCHER: GameSelection = ("42", "The Witcher 3", "Lutris", "Lutris")
STEAM_WITCHER: GameSelection = ("292030", "The Witcher 3", "Steam", "Steam")


class NormalizeGameNameForDedupTests(unittest.TestCase):
    def test_trims_surrounding_whitespace(self) -> None:
        self.assertEqual(normalize_game_name_for_dedup("  Cyberpunk 2077  "), "cyberpunk 2077")

    def test_collapses_internal_whitespace(self) -> None:
        self.assertEqual(
            normalize_game_name_for_dedup("Cyberpunk   2077\tGOTY"),
            "cyberpunk 2077 goty",
        )

    def test_casefolds_unicode_and_ascii(self) -> None:
        self.assertEqual(normalize_game_name_for_dedup("CYBERPUNK 2077"), "cyberpunk 2077")
        self.assertEqual(normalize_game_name_for_dedup("STREET OF KADASHI"), "street of kadashi")
        self.assertEqual(normalize_game_name_for_dedup("WEIRD  STÜFF"), "weird stüff")

    def test_returns_empty_string_for_whitespace_only(self) -> None:
        self.assertEqual(normalize_game_name_for_dedup("   \t  "), "")


class GetSourcePriorityTests(unittest.TestCase):
    def test_known_sources_have_expected_priority(self) -> None:
        self.assertLess(get_source_priority("Steam"), get_source_priority("Lutris"))
        self.assertLess(get_source_priority("Lutris"), get_source_priority("Heroic"))
        self.assertLess(get_source_priority("Heroic"), get_source_priority("Bottles"))
        self.assertLess(get_source_priority("Bottles"), get_source_priority("Faugus"))
        self.assertLess(get_source_priority("Faugus"), get_source_priority("Ryubing"))
        self.assertLess(get_source_priority("Ryubing"), get_source_priority("RetroArch"))
        self.assertLess(get_source_priority("RetroArch"), get_source_priority("Eden"))

    def test_unknown_source_is_lowest_priority(self) -> None:
        self.assertGreaterEqual(
            get_source_priority("MysteryLauncher"),
            get_source_priority("Eden"),
        )

    def test_unknown_source_is_stable_across_calls(self) -> None:
        self.assertEqual(
            get_source_priority("MysteryLauncher"),
            get_source_priority("MysteryLauncher"),
        )


class DedupeSelectedGamesByNameTests(unittest.TestCase):
    def test_returns_unchanged_list_when_no_duplicates(self) -> None:
        selected = [LUTRIS_CYBERPUNK, HEROIC_CONTROL, LUTRIS_WITCHER]

        deduped, skipped = dedupe_selected_games_by_name(selected)

        self.assertEqual(deduped, selected)
        self.assertEqual(skipped, [])

    def test_dedupes_steam_over_lutris_when_steam_comes_later(self) -> None:
        deduped, skipped = dedupe_selected_games_by_name([LUTRIS_CYBERPUNK, STEAM_CYBERPUNK])

        self.assertEqual(deduped, [STEAM_CYBERPUNK])
        self.assertEqual(skipped, [(LUTRIS_CYBERPUNK, STEAM_CYBERPUNK)])

    def test_dedupes_steam_over_lutris_when_steam_comes_first(self) -> None:
        deduped, skipped = dedupe_selected_games_by_name([STEAM_CYBERPUNK, LUTRIS_CYBERPUNK])

        self.assertEqual(deduped, [STEAM_CYBERPUNK])
        self.assertEqual(skipped, [(LUTRIS_CYBERPUNK, STEAM_CYBERPUNK)])

    def test_dedupes_case_and_whitespace_variants(self) -> None:
        messy_lutris: GameSelection = ("1", "  cyberpunk   2077 ", "Lutris", "Lutris")
        caps_steam: GameSelection = ("1091500", "CYBERPUNK 2077", "Steam", "Steam")

        deduped, skipped = dedupe_selected_games_by_name([messy_lutris, caps_steam])

        self.assertEqual(deduped, [caps_steam])
        self.assertEqual(skipped, [(messy_lutris, caps_steam)])

    def test_keeps_first_when_source_priority_ties(self) -> None:
        first = ("1", "Duplicate", "Lutris", "Lutris")
        second = ("2", "Duplicate", "Lutris", "Lutris")

        deduped, skipped = dedupe_selected_games_by_name([first, second])

        self.assertEqual(deduped, [first])
        self.assertEqual(skipped, [(second, first)])

    def test_preserves_stable_group_order_when_later_duplicate_wins(self) -> None:
        selected = [HEROIC_CONTROL, LUTRIS_WITCHER, STEAM_CYBERPUNK, LUTRIS_CYBERPUNK]

        deduped, skipped = dedupe_selected_games_by_name(selected)

        self.assertEqual(
            deduped,
            [HEROIC_CONTROL, LUTRIS_WITCHER, STEAM_CYBERPUNK],
        )
        self.assertEqual(skipped, [(LUTRIS_CYBERPUNK, STEAM_CYBERPUNK)])

    def test_preserves_stable_group_order_when_earlier_group_is_replaced(self) -> None:
        selected = [LUTRIS_CYBERPUNK, STEAM_CYBERPUNK, HEROIC_CONTROL]

        deduped, skipped = dedupe_selected_games_by_name(selected)

        self.assertEqual(deduped, [STEAM_CYBERPUNK, HEROIC_CONTROL])
        self.assertEqual(skipped, [(LUTRIS_CYBERPUNK, STEAM_CYBERPUNK)])

    def test_does_not_mutate_input(self) -> None:
        selected = [LUTRIS_CYBERPUNK, STEAM_CYBERPUNK, HEROIC_CONTROL]
        original = list(selected)

        dedupe_selected_games_by_name(selected)

        self.assertEqual(selected, original)

    def test_reports_skipped_pair_with_skipped_and_retained(self) -> None:
        deduped, skipped = dedupe_selected_games_by_name([LUTRIS_WITCHER, STEAM_WITCHER])

        self.assertEqual(deduped, [STEAM_WITCHER])
        self.assertEqual(skipped, [(LUTRIS_WITCHER, STEAM_WITCHER)])

    def test_handles_empty_input(self) -> None:
        deduped, skipped = dedupe_selected_games_by_name([])

        self.assertEqual(deduped, [])
        self.assertEqual(skipped, [])

    def test_keeps_single_selected_entry_without_changes(self) -> None:
        deduped, skipped = dedupe_selected_games_by_name([LUTRIS_CYBERPUNK])

        self.assertEqual(deduped, [LUTRIS_CYBERPUNK])
        self.assertEqual(skipped, [])


class SelectedPipelineIntegrationTests(unittest.TestCase):
    def test_pipeline_filters_existing_then_dedupes_to_steam(self) -> None:
        all_games: List[GameSelection] = [LUTRIS_CYBERPUNK, STEAM_CYBERPUNK]
        selected_indices = [0, 1]
        existing_game_names_normalized = set()

        selected_games = [
            all_games[i]
            for i in selected_indices
            if normalize_game_name_for_dedup(all_games[i][1]) not in existing_game_names_normalized
        ]
        selected_games, skipped = dedupe_selected_games_by_name(selected_games)

        self.assertEqual(selected_games, [STEAM_CYBERPUNK])
        self.assertEqual(skipped, [(LUTRIS_CYBERPUNK, STEAM_CYBERPUNK)])

    def test_pipeline_filters_existing_via_normalized_match(self) -> None:
        all_games: List[GameSelection] = [LUTRIS_CYBERPUNK]
        existing_game_names_normalized = {
            normalize_game_name_for_dedup(" cyberpunk   2077 ")
        }

        selected_games = [
            all_games[i]
            for i in [0]
            if normalize_game_name_for_dedup(all_games[i][1]) not in existing_game_names_normalized
        ]
        selected_games, skipped = dedupe_selected_games_by_name(selected_games)

        self.assertEqual(selected_games, [])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
