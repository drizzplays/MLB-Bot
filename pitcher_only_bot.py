import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "pitcher_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

PITCHER_ALERT_WINDOW_HOURS = 6
PITCHER_ALERT_START_HOUR_ET = 8
CHECK_TOMORROW = os.getenv("PITCHER_CHECK_TOMORROW", "").strip().lower() in {
    "1",
    "true",
    "yes",
}

VALID_PREGAME_STATUS_CODES = {
    "S",  # Scheduled
    "P",  # Pre-Game
}

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


def game_status_code(game):
    return str(game.get("status", {}).get("codedGameState") or "").upper()


def is_real_pregame_game(game):
    game_pk = str(game.get("gamePk") or "").strip()
    if not game_pk:
        return False

    status_code = game_status_code(game)
    if status_code and status_code not in VALID_PREGAME_STATUS_CODES:
        return False

    return True


def state_key_for_game(game_data):
    game_pk = str(game_data.get("game_pk") or "").strip()
    if game_pk:
        return game_pk
    return f"{game_data.get('away_team', 'Away')} @ {game_data.get('home_team', 'Home')}"


def old_game_candidates(old_games, game_key, game_data):
    game_pk = str(game_data.get("game_pk") or "").strip()
    if game_pk and game_pk in old_games:
        return old_games.get(game_pk)
    if game_key in old_games:
        return old_games.get(game_key)
    legacy_key = f"{game_data.get('away_team')} @ {game_data.get('home_team')} | {game_pk}"
    return old_games.get(legacy_key)


def is_pregame(game_iso):
    if not game_iso:
        return True
    try:
        game_dt = datetime.fromisoformat(game_iso)
        return datetime.now(ET) < game_dt
    except Exception:
        return True


def is_after_pitcher_alert_start(hour=PITCHER_ALERT_START_HOUR_ET):
    now = datetime.now(ET)
    return now.hour >= hour


def is_within_pitcher_alert_window(game_iso, hours=PITCHER_ALERT_WINDOW_HOURS):
    if not game_iso:
        return False
    try:
        game_dt = datetime.fromisoformat(game_iso)
        now = datetime.now(ET)
        if now >= game_dt:
            return False
        return (game_dt - now).total_seconds() <= hours * 3600
    except Exception:
        return False


def get_games(target_date):
    params = {
        "sportId": 1,
        "date": target_date.strftime("%Y-%m-%d"),
        "hydrate": "probablePitcher(note)",
    }

    r = requests.get(MLB_SCHEDULE_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    games = {}

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            if not is_real_pregame_game(game):
                continue

            away = game.get("teams", {}).get("away", {})
            home = game.get("teams", {}).get("home", {})

            away_team = away.get("team", {}).get("name", "Away")
            home_team = home.get("team", {}).get("name", "Home")

            away_pitcher = away.get("probablePitcher", {}).get("fullName", "TBD")
            home_pitcher = home.get("probablePitcher", {}).get("fullName", "TBD")

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

            key = game_pk
            games[key] = {
                "away_team": away_team,
                "home_team": home_team,
                "away_pitcher": away_pitcher,
                "home_pitcher": home_pitcher,
                "game_time": game_time,
                "game_iso": game_iso,
                "game_pk": game_pk,
                "status_code": game_status_code(game),
            }

    return games


def pitcher_changes(old_game, new_game):
    changes = []

    old_away = old_game.get("away_pitcher", "TBD") if old_game else "TBD"
    new_away = new_game.get("away_pitcher", "TBD")
    old_home = old_game.get("home_pitcher", "TBD") if old_game else "TBD"
    new_home = new_game.get("home_pitcher", "TBD")

    if old_away != new_away and new_away not in ("", "TBD"):
        if old_away in ("", "TBD"):
            changes.append(
                f"{team_label(new_game['away_team'])}: pitcher posted - {new_away}"
            )
        else:
            changes.append(
                f"{team_label(new_game['away_team'])}: {old_away} -> {new_away}"
            )

    if old_home != new_home and new_home not in ("", "TBD"):
        if old_home in ("", "TBD"):
            changes.append(
                f"{team_label(new_game['home_team'])}: pitcher posted - {new_home}"
            )
        else:
            changes.append(
                f"{team_label(new_game['home_team'])}: {old_home} -> {new_home}"
            )

    return changes


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        return None

    if not is_after_pitcher_alert_start():
        return None

    if not is_within_pitcher_alert_window(new_game.get("game_iso")):
        return None

    changes = pitcher_changes(old_game, new_game)
    if not changes:
        return None

    return (
        f"**Pitcher Update**\n"
        f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
        f"First pitch: {new_game['game_time']}\n\n"
        + "\n".join(f"- {x}" for x in changes)
    )


def run():
    state = load_state()
    today = datetime.now(ET).date()
    dates_to_check = [today]

    if CHECK_TOMORROW:
        dates_to_check.append(today + timedelta(days=1))

    print(f"Loaded state keys: {list(state.keys())}")
    print(f"Tomorrow check enabled: {CHECK_TOMORROW}")

    total_alerts = 0
    next_state = {}

    for target_date in dates_to_check:
        date_key = str(target_date)
        print(f"Checking {date_key}...")

        new_games = get_games(target_date)
        print(f"Games pulled for {date_key}: {len(new_games)}")

        old_games = state.get(date_key, {})
        print(f"Old games for {date_key}: {len(old_games)}")

        for game_key, game_data in new_games.items():
            old_game = old_game_candidates(old_games, game_key, game_data) or {}
            if old_game and str(old_game.get("game_pk") or "") != str(game_data.get("game_pk") or ""):
                print(
                    "Skipping mismatched stale state entry: "
                    f"old_game_pk={old_game.get('game_pk')} new_game_pk={game_data.get('game_pk')}"
                )
                old_game = {}

            alert = build(old_game, game_data)
            if alert:
                send(alert)
                total_alerts += 1
                print(f"Sent alert for: {game_key}")

        next_state[date_key] = new_games

    print("About to save state...")
    preview = json.dumps(next_state, indent=2)
    print(preview[:2000])
    save_state(next_state)
    print("State saved.")
    print(f"Done. Total alerts sent: {total_alerts}")


if __name__ == "__main__":
    run()
