import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "lineup_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

TEAM_EMOJIS = {
    "Arizona Diamondbacks": "⚾",
    "Atlanta Braves": "<:braves:1319500374482358333>",
    "Baltimore Orioles": "⚾",
    "Boston Red Sox": "⚾",
    "Chicago Cubs": "<:cubs:1319495233037275176>",
    "Chicago White Sox": "⚾",
    "Cincinnati Reds": "⚾",
    "Cleveland Guardians": "<:guardians:1376110439431143464>",
    "Colorado Rockies": "⚾",
    "Detroit Tigers": "<:tigers:1375047163888795728>",
    "Houston Astros": "⚾",
    "Kansas City Royals": "⚾",
    "Los Angeles Angels": "⚾",
    "Los Angeles Dodgers": "<:ladodgers:1319496737743704094>",
    "Miami Marlins": "⚾",
    "Milwaukee Brewers": "⚾",
    "Minnesota Twins": "<:twins:1383372555255283782>",
    "New York Mets": "<:mets:1316263476171378790>",
    "New York Yankees": "<:584d4b6e0a44bd1070d5d493:1319507500495667210>",
    "Athletics": "⚾",
    "Philadelphia Phillies": "<:phillies:1375046480959770665>",
    "Pittsburgh Pirates": "<:pirates:1319496010237612103>",
    "San Diego Padres": "<:padres:1375529423796965547>",
    "San Francisco Giants": "⚾",
    "Seattle Mariners": "⚾",
    "St. Louis Cardinals": "⚾",
    "Tampa Bay Rays": "<:tbrays:1319497631172661278>",
    "Texas Rangers": "⚾",
    "Toronto Blue Jays": "<:bluejays:1319500116360560724>",
    "Washington Nationals": "⚾",
}

CHECK_TOMORROW = True


def team_label(team_name):
    emoji = TEAM_EMOJIS.get(team_name, "⚾")
    return f"{emoji} {team_name}"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        print("State file is invalid JSON. Resetting to empty state.")
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def send(content):
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()


def load_batters(filename="batters.txt"):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return {
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            }
    except FileNotFoundError:
        print(f"Watchlist file not found: {filename}")
        return set()


WATCHED_BATTERS = load_batters()


def extract_lineup(team_data):
    players = team_data.get("players", {})
    batting_order = team_data.get("battingOrder", [])

    lineup = []
    for player_id in batting_order:
        player = players.get(f"ID{player_id}", {})
        name = player.get("person", {}).get("fullName", "Unknown")
        lineup.append(name)

    return lineup


def is_pregame(game_iso):
    if not game_iso:
        return True

    try:
        game_dt = datetime.fromisoformat(game_iso)
        return datetime.now(ET) < game_dt
    except Exception:
        return True


def get_games(target_date):
    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
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
                game_time = game_dt.strftime("%Y-%m-%d %I:%M %p ET")
                game_iso = game_dt.isoformat()
            except Exception:
                game_time = game_date_raw
                game_iso = None

            away_lineup = []
            home_lineup = []

            try:
                box = requests.get(
                    f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore",
                    timeout=20,
                )
                box.raise_for_status()
                box_data = box.json()

                teams = box_data.get("teams", {})
                away_lineup = extract_lineup(teams.get("away", {}))
                home_lineup = extract_lineup(teams.get("home", {}))
            except Exception as e:
                print(f"Failed to load boxscore for {away_team} @ {home_team}: {e}")

            key = f"{away_team} @ {home_team} | {game_pk}"
            games[key] = {
                "away_team": away_team,
                "home_team": home_team,
                "away_lineup": away_lineup,
                "home_lineup": home_lineup,
                "game_time": game_time,
                "game_iso": game_iso,
                "game_pk": game_pk,
            }

    return games


def get_player_team(player_name):
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"sportId": 1, "name": player_name},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        people = data.get("people", [])
        if not people:
            return None

        current_team = people[0].get("currentTeam", {})
        return current_team.get("name")
    except Exception as e:
        print(f"Player search failed for {player_name}: {e}")
        return None


def get_missing_watched_batters(new_game):
    away_team = new_game["away_team"]
    home_team = new_game["home_team"]
    away_lineup = set(new_game.get("away_lineup", []))
    home_lineup = set(new_game.get("home_lineup", []))

    missing = []

    for batter in WATCHED_BATTERS:
        batter_team = get_player_team(batter)
        if not batter_team:
            continue

        if batter_team == away_team and batter not in away_lineup and new_game.get("away_lineup"):
            missing.append((batter, away_team))

        if batter_team == home_team and batter not in home_lineup and new_game.get("home_lineup"):
            missing.append((batter, home_team))

    return sorted(missing)


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        return None

    old_away_lineup = old_game.get("away_lineup", [])
    old_home_lineup = old_game.get("home_lineup", [])

    # No new alert unless lineup changed
    if (
        old_away_lineup == new_game.get("away_lineup", [])
        and old_home_lineup == new_game.get("home_lineup", [])
    ):
        return None

    missing = get_missing_watched_batters(new_game)
    if not missing:
        return None

    lines = [f"- ❌ {batter} not in {team_label(team)} lineup" for batter, team in missing]

    msg = (
        f"🚨 **WATCHLIST BATTER MISSING**\n\n"
        f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
        f"**First pitch:** {new_game['game_time']}\n\n"
        + "\n".join(lines)
        + "\n\n⚾ DRIZZPLAYS"
    )

    return msg


def run():
    state = load_state()
    today = datetime.now(ET).date()
    dates_to_check = [today]

    if CHECK_TOMORROW:
        dates_to_check.append(today + timedelta(days=1))

    print(f"Loaded watched batters: {len(WATCHED_BATTERS)}")
    print(f"Loaded state keys: {list(state.keys())}")

    total_alerts = 0

    for target_date in dates_to_check:
        date_key = str(target_date)
        print(f"Checking {date_key}...")

        new_games = get_games(target_date)
        print(f"Games pulled for {date_key}: {len(new_games)}")

        old_games = state.get(date_key, {})
        print(f"Old games for {date_key}: {len(old_games)}")

        for game_key, game_data in new_games.items():
            alert = build(old_games.get(game_key, {}), game_data)
            if alert:
                send(alert)
                total_alerts += 1
                print(f"Sent alert for: {game_key}")

        state[date_key] = new_games

    print("About to save lineup state...")
    preview = json.dumps(state, indent=2)
    print(preview[:3000])

    save_state(state)

    print("Lineup state saved.")
    print(f"Done. Total alerts sent: {total_alerts}")


if __name__ == "__main__":
    run()
