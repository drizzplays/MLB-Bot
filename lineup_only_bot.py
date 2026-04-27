import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "lineup_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

TEAM_ABBR = {
    "Arizona Diamondbacks": "ARI",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Athletics": "ATH",
    "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

CHECK_TOMORROW = True
DEBUG = True


def debug(msg):
    if DEBUG:
        print(msg)


def team_label(team_name):
    return TEAM_ABBR.get(team_name, team_name)


def format_first_pitch(game_dt):
    time_text = game_dt.strftime("%I:%M %p").lstrip("0")
    return f"{game_dt.strftime('%b')} {game_dt.day}, {time_text} ET"


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
            return [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
    except FileNotFoundError:
        print(f"Watchlist file not found: {filename}")
        return []


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


def extract_roster(team_data):
    players = team_data.get("players", {})
    roster = []

    for player in players.values():
        name = player.get("person", {}).get("fullName")
        if name:
            roster.append(name)

    return roster


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
                game_time = format_first_pitch(game_dt)
                game_iso = game_dt.isoformat()
            except Exception:
                game_time = game_date_raw
                game_iso = None

            away_lineup = []
            home_lineup = []
            away_roster = []
            home_roster = []

            try:
                box = requests.get(
                    f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore",
                    timeout=20,
                )
                box.raise_for_status()
                box_data = box.json()

                teams = box_data.get("teams", {})
                away_team_data = teams.get("away", {})
                home_team_data = teams.get("home", {})

                away_lineup = extract_lineup(away_team_data)
                home_lineup = extract_lineup(home_team_data)

                away_roster = extract_roster(away_team_data)
                home_roster = extract_roster(home_team_data)

            except Exception as e:
                print(f"Failed to load boxscore for {away_team} @ {home_team}: {e}")

            key = f"{away_team} @ {home_team} | {game_pk}"
            games[key] = {
                "away_team": away_team,
                "home_team": home_team,
                "away_lineup": away_lineup,
                "home_lineup": home_lineup,
                "away_roster": away_roster,
                "home_roster": home_roster,
                "game_time": game_time,
                "game_iso": game_iso,
                "game_pk": game_pk,
            }

    return games


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        debug("[BUILD] Skipped: game already started")
        return None

    away_team = new_game["away_team"]
    home_team = new_game["home_team"]

    old_away_lineup = set(old_game.get("away_lineup", []))
    old_home_lineup = set(old_game.get("home_lineup", []))
    new_away_lineup = set(new_game.get("away_lineup", []))
    new_home_lineup = set(new_game.get("home_lineup", []))
    away_lineup_was_posted = bool(old_game.get("away_lineup"))
    home_lineup_was_posted = bool(old_game.get("home_lineup"))

    if old_away_lineup == new_away_lineup and old_home_lineup == new_home_lineup:
        debug(f"[BUILD] No lineup change for {away_team} @ {home_team}")
        return None

    away_roster = set(new_game.get("away_roster", []))
    home_roster = set(new_game.get("home_roster", []))

    missing_lines = []
    active_lines = []

    for batter in WATCHED_BATTERS:
        if batter in away_roster and new_game.get("away_lineup"):
            was_in = batter in old_away_lineup
            is_in = batter in new_away_lineup
            debug(f"[WATCH] {batter} | team={away_team} | was_in={was_in} | is_in={is_in}")

            if not is_in:
                missing_lines.append(f"- {batter} ({team_label(away_team)})")
            elif away_lineup_was_posted and not was_in and is_in:
                active_lines.append(f"- {batter} ({team_label(away_team)})")

        elif batter in home_roster and new_game.get("home_lineup"):
            was_in = batter in old_home_lineup
            is_in = batter in new_home_lineup
            debug(f"[WATCH] {batter} | team={home_team} | was_in={was_in} | is_in={is_in}")

            if not is_in:
                missing_lines.append(f"- {batter} ({team_label(home_team)})")
            elif home_lineup_was_posted and not was_in and is_in:
                active_lines.append(f"- {batter} ({team_label(home_team)})")

        else:
            debug(f"[WATCH] Skipped {batter}: not on either game roster")

    missing_lines = sorted(set(missing_lines))
    active_lines = sorted(set(active_lines))

    if not missing_lines and not active_lines:
        debug(f"[BUILD] No watched batter alert for {away_team} @ {home_team}")
        return None

    sections = []

    if missing_lines:
        sections.append("**Not in lineup**\n" + "\n".join(missing_lines))

    if active_lines:
        sections.append("**Added after lineup posted**\n" + "\n".join(active_lines))

    msg = (
        f"**Lineup Watchlist**\n"
        f"**{team_label(away_team)} @ {team_label(home_team)}**\n"
        f"First pitch: {new_game['game_time']}\n\n"
        + "\n\n".join(sections)
    )

    return msg


def run():
    old_state = load_state()
    today = datetime.now(ET).date()
    dates_to_check = [today]

    if CHECK_TOMORROW:
        dates_to_check.append(today + timedelta(days=1))

    print(f"Loaded watched batters: {len(WATCHED_BATTERS)}")
    print(f"Loaded old state keys: {list(old_state.keys())}")

    total_alerts = 0
    new_state = {}

    for target_date in dates_to_check:
        date_key = str(target_date)
        print(f"Checking {date_key}...")

        new_games = get_games(target_date)
        print(f"Games pulled for {date_key}: {len(new_games)}")

        old_games = old_state.get(date_key, {})
        print(f"Old games for {date_key}: {len(old_games)}")

        for game_key, game_data in new_games.items():
            alert = build(old_games.get(game_key, {}), game_data)
            if alert:
                send(alert)
                total_alerts += 1
                print(f"Sent alert for: {game_key}")

        new_state[date_key] = new_games

    print("About to save lineup state...")
    preview = json.dumps(new_state, indent=2)
    print(preview[:3000])

    save_state(new_state)

    print("Lineup state saved.")
    print(f"Done. Total alerts sent: {total_alerts}")


if __name__ == "__main__":
    run()
