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
                game_time = game_dt.strftime("%Y-%m-%d %I:%M %p ET")
                game_iso = game_dt.isoformat()
            except Exception:
                game_time = game_date_raw
                game_iso = None

            key = f"{away_team} @ {home_team} | {game_pk}"
            games[key] = {
                "away_team": away_team,
                "home_team": home_team,
                "away_pitcher": away_pitcher,
                "home_pitcher": home_pitcher,
                "game_time": game_time,
                "game_iso": game_iso,
                "game_pk": game_pk,
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
                f"🆕 {team_label(new_game['away_team'])}: pitcher posted — {new_away}"
            )
        else:
            changes.append(
                f"🔄 {team_label(new_game['away_team'])}: {old_away} → {new_away}"
            )

    if old_home != new_home and new_home not in ("", "TBD"):
        if old_home in ("", "TBD"):
            changes.append(
                f"🆕 {team_label(new_game['home_team'])}: pitcher posted — {new_home}"
            )
        else:
            changes.append(
                f"🔄 {team_label(new_game['home_team'])}: {old_home} → {new_home}"
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
        f"🚨 **PITCHER UPDATED**\n\n"
        f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
        f"**First pitch:** {new_game['game_time']}\n\n"
        + "\n".join(f"- {x}" for x in changes)
        + "\n\n⚾ DRIZZPLAYS"
    )


def run():
    state = load_state()
    today = datetime.now(ET).date()
    dates_to_check = [today]

    if CHECK_TOMORROW:
        dates_to_check.append(today + timedelta(days=1))

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

    print("About to save state...")
    preview = json.dumps(state, indent=2)
    print(preview[:2000])
    save_state(state)
    print("State saved.")
    print(f"Done. Total alerts sent: {total_alerts}")


if __name__ == "__main__":
    run()
