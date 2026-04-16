import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "lineup_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def send_discord_message(content):
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()


def get_schedule_for_date(target_date):
    date_str = target_date.strftime("%Y-%m-%d")

    params = {
        "sportId": 1,
        "date": date_str,
    }

    r = requests.get(MLB_SCHEDULE_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    games = {}

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})

            away_team = away.get("team", {}).get("name", "Away")
            home_team = home.get("team", {}).get("name", "Home")

            game_pk = str(game.get("gamePk", ""))
            game_date_raw = game.get("gameDate", "")

            try:
                game_dt = datetime.fromisoformat(
                    game_date_raw.replace("Z", "+00:00")
                ).astimezone(ET)
                game_time_et = game_dt.strftime("%Y-%m-%d %I:%M %p ET")
            except Exception:
                game_time_et = game_date_raw

            key = f"{away_team} @ {home_team} | {game_pk}"
            games[key] = {
                "away_team": away_team,
                "home_team": home_team,
                "game_time_et": game_time_et,
                "game_pk": game_pk,
            }

    return games


def extract_lineup(team_data):
    players = team_data.get("players", {})
    batting_order = team_data.get("battingOrder", [])

    lineup = []
    for player_id in batting_order:
        player = players.get(f"ID{player_id}", {})
        name = player.get("person", {}).get("fullName", "Unknown")
        lineup.append(name)

    return lineup


def get_lineups_for_games(games):
    lineups = {}

    for key, game in games.items():
        game_pk = game["game_pk"]
        url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"

        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"Failed to load lineup for {key}: {e}")
            continue

        teams = data.get("teams", {})
        away_team_data = teams.get("away", {})
        home_team_data = teams.get("home", {})

        away_lineup = extract_lineup(away_team_data)
        home_lineup = extract_lineup(home_team_data)

        lineups[key] = {
            "away_team": game["away_team"],
            "home_team": game["home_team"],
            "game_time_et": game["game_time_et"],
            "away_lineup": away_lineup,
            "home_lineup": home_lineup,
        }

    return lineups


def lineup_to_text(title, lineup):
    if not lineup:
        return f"**{title}**\nNot posted yet"

    return f"**{title}**\n" + "\n".join(
        f"{i + 1}. {player}" for i, player in enumerate(lineup)
    )


def get_lineup_differences(old_lineup, new_lineup):
    changes = []

    max_len = max(len(old_lineup), len(new_lineup))

    for i in range(max_len):
        old_player = old_lineup[i] if i < len(old_lineup) else None
        new_player = new_lineup[i] if i < len(new_lineup) else None

        if old_player != new_player:
            if old_player and new_player:
                changes.append(f"{i + 1}. {old_player} -> {new_player}")
            elif old_player and not new_player:
                changes.append(f"{i + 1}. {old_player} removed")
            elif not old_player and new_player:
                changes.append(f"{i + 1}. {new_player} added")

    return changes


def compare_lineups(old_lineups, new_lineups):
    alerts = []

    for key, new_game in new_lineups.items():
        old_game = old_lineups.get(key)
        if not old_game:
            continue

        away_old = old_game.get("away_lineup", [])
        away_new = new_game.get("away_lineup", [])
        home_old = old_game.get("home_lineup", [])
        home_new = new_game.get("home_lineup", [])

        away_changes = get_lineup_differences(away_old, away_new)
        home_changes = get_lineup_differences(home_old, home_new)

        if away_changes or home_changes:
            sections = []

            if away_changes:
                sections.append(
                    f"**{new_game['away_team']} Changes**\n"
                    + "\n".join(f"- {change}" for change in away_changes)
                )
                sections.append(
                    lineup_to_text(f"{new_game['away_team']} Current Lineup", away_new)
                )

            if home_changes:
                sections.append(
                    f"**{new_game['home_team']} Changes**\n"
                    + "\n".join(f"- {change}" for change in home_changes)
                )
                sections.append(
                    lineup_to_text(f"{new_game['home_team']} Current Lineup", home_new)
                )

            msg = (
                f"📋 **Lineup Update**\n\n"
                f"**{new_game['away_team']} @ {new_game['home_team']}**\n"
                f"**First pitch:** {new_game['game_time_et']}\n\n"
                + "\n\n".join(sections)
                + "\n\n🔥 **DRIZZPLAYS**"
            )

            # Discord hard limit safety
            if len(msg) > 1900:
                msg = (
                    f"📋 **Lineup Update**\n\n"
                    f"**{new_game['away_team']} @ {new_game['home_team']}**\n"
                    f"**First pitch:** {new_game['game_time_et']}\n\n"
                    f"Too many lineup changes to fit in one message.\n\n"
                    + lineup_to_text(f"{new_game['away_team']} Current Lineup", away_new)
                    + "\n\n"
                    + lineup_to_text(f"{new_game['home_team']} Current Lineup", home_new)
                    + "\n\n🔥 **DRIZZPLAYS**"
                )

            alerts.append(msg)

    return alerts


def run_check():
    now = datetime.now(ET).date()
    tomorrow = now + timedelta(days=1)

    state = load_state()

    today_games = get_schedule_for_date(now)
    tomorrow_games = get_schedule_for_date(tomorrow)

    today_lineups = get_lineups_for_games(today_games)
    tomorrow_lineups = get_lineups_for_games(tomorrow_games)

    print(f"Today lineups found: {len(today_lineups)}")
    print(f"Tomorrow lineups found: {len(tomorrow_lineups)}")

    for target_date, new_lineups in [
        (str(now), today_lineups),
        (str(tomorrow), tomorrow_lineups),
    ]:
        old_lineups = state.get(target_date, {})
        alerts = compare_lineups(old_lineups, new_lineups)

        for alert in alerts:
            try:
                send_discord_message(alert)
                print("Sent alert:")
                print(alert)
                print("-" * 40)
            except Exception as e:
                print(f"Failed to send Discord alert: {e}")

        state[target_date] = new_lineups

    save_state(state)
    print("Done.")


if __name__ == "__main__":
    run_check()
