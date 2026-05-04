import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://example.invalid/webhook")

import pitcher_only_bot as bot


def game(hours_from_now=9, home_pitcher="Tarik Skubal", away_pitcher="Payton Tolle"):
    game_dt = datetime.now(bot.ET) + timedelta(hours=hours_from_now)
    return {
        "away_team": "Boston Red Sox",
        "home_team": "Detroit Tigers",
        "away_pitcher": away_pitcher,
        "home_pitcher": home_pitcher,
        "game_time": "May 4, 6:40 PM ET",
        "game_iso": game_dt.isoformat(),
        "game_pk": "824283",
    }


class PitcherChangeTests(unittest.TestCase):
    def test_named_pitcher_to_tbd_is_scratch_alert_even_outside_normal_window(self):
        old_game = game(hours_from_now=9, home_pitcher="Tarik Skubal")
        new_game = game(hours_from_now=9, home_pitcher="TBD")

        changes = bot.pitcher_changes(old_game, new_game)
        self.assertEqual(
            changes,
            [
                {
                    "type": "scratch",
                    "text": "🚨 DET: Tarik Skubal removed / scratched -> TBD",
                }
            ],
        )
        self.assertTrue(bot.should_send_pitcher_alert(changes, new_game["game_iso"]))

    def test_tbd_to_named_pitcher_remains_window_gated(self):
        old_game = game(hours_from_now=9, home_pitcher="TBD")
        new_game = game(hours_from_now=9, home_pitcher="Tarik Skubal")

        changes = bot.pitcher_changes(old_game, new_game)
        self.assertEqual(changes[0]["type"], "posted")
        self.assertFalse(bot.should_send_pitcher_alert(changes, new_game["game_iso"]))

    def test_named_pitcher_swap_is_urgent(self):
        old_game = game(hours_from_now=9, home_pitcher="Tarik Skubal")
        new_game = game(hours_from_now=9, home_pitcher="Casey Mize")

        changes = bot.pitcher_changes(old_game, new_game)
        self.assertEqual(
            changes,
            [
                {
                    "type": "swap",
                    "text": "🚨 DET: Tarik Skubal -> Casey Mize",
                }
            ],
        )
        self.assertTrue(bot.should_send_pitcher_alert(changes, new_game["game_iso"]))


if __name__ == "__main__":
    unittest.main()
