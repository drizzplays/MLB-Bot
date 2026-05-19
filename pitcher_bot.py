import json
import hashlib
import hmac
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
WEBSITE_NOTIFY_URL = os.getenv("WEBSITE_NOTIFY_URL", "")
WEBSITE_NOTIFY_SECRET = os.getenv("WEBSITE_NOTIFY_SECRET") or os.getenv("SBH_WEBHOOK_SECRET", "")
ENABLE_DISCORD_NOTIFY = os.getenv("ENABLE_DISCORD_NOTIFY", "true").strip().lower() not in {"0", "false", "no", "off"}
ENABLE_WEBSITE_NOTIFY = os.getenv("ENABLE_WEBSITE_NOTIFY", "true").strip().lower() not in {"0", "false", "no", "off"}
STATE_FILE = "pitcher_state.json"
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


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def send_discord_message(content):
    if not ENABLE_DISCORD_NOTIFY:
        print("[notify] Discord disabled by ENABLE_DISCORD_NOTIFY.")
        return False
    if not WEBHOOK_URL:
        raise RuntimeError("Missing Discord webhook. Add DISCORD_WEBHOOK_URL / PITCHER_WEBHOOK_URL secret.")
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()
    print("[notify] Discord pitcher alert sent.")
    return True


def send_website_notification(payload):
    if not ENABLE_WEBSITE_NOTIFY:
        print("[notify] Website notifications disabled by ENABLE_WEBSITE_NOTIFY.")
        return False
    if not WEBSITE_NOTIFY_URL:
        print("[notify] Website notifications disabled: WEBSITE_NOTIFY_URL is not set.")
        return False

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"content-type": "application/json"}
    if WEBSITE_NOTIFY_SECRET:
        signature = hmac.new(WEBSITE_NOTIFY_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["x-sbh-signature"] = f"sha256={signature}"
    else:
        print("[notify] WEBSITE_NOTIFY_SECRET is not set; posting unsigned website notification.")

    r = requests.post(WEBSITE_NOTIFY_URL, data=body, headers=headers, timeout=20)
    if not r.ok:
        body_preview = (r.text or "").replace("\n", " ")[:500]
        raise RuntimeError(f"Website notification HTTP {r.status_code}: {body_preview}")
    print(f"[notify] Website pitcher alert accepted: HTTP {r.status_code}.")
    return True


def deliver_alert(content, payload):
    errors = []
    delivery = {"discord": False, "website": False}
    try:
        delivery["discord"] = bool(send_discord_message(content))
    except Exception as exc:
        errors.append(f"Discord: {exc}")

    try:
        delivery["website"] = bool(send_website_notification(payload))
    except Exception as exc:
        errors.append(f"Website notification: {exc}")

    if errors:
        print("Alert delivery warning: " + "; ".join(errors))
    print(f"[notify] Delivery result: discord={delivery['discord']} website={delivery['website']}")
    if not any(delivery.values()) and errors:
        raise RuntimeError("; ".join(errors))
    return delivery


def get_schedule_for_date(target_date):
    date_str = target_date.strftime("%Y-%m-%d")

    params = {
        "sportId": 1,
        "date": date_str,
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
                game_time_et = format_first_pitch(game_dt)
            except Exception:
                game_time_et = game_date_raw

            key = f"{away_team} @ {home_team} | {game_pk}"
            games[key] = {
                "away_team": away_team,
                "home_team": home_team,
                "away_pitcher": away_pitcher,
                "home_pitcher": home_pitcher,
                "game_time_et": game_time_et,
                "game_pk": game_pk,
            }

    return games


def compare_games(old_games, new_games):
    alerts = []

    for key, new_game in new_games.items():
        old_game = old_games.get(key)
        if not old_game:
            continue

        changes = []

        if old_game.get("away_pitcher") != new_game.get("away_pitcher"):
            changes.append(
                f"{team_label(new_game['away_team'])}: "
                f"{old_game.get('away_pitcher', 'TBD')} -> "
                f"{new_game.get('away_pitcher', 'TBD')}"
            )

        if old_game.get("home_pitcher") != new_game.get("home_pitcher"):
            changes.append(
                f"{team_label(new_game['home_team'])}: "
                f"{old_game.get('home_pitcher', 'TBD')} -> "
                f"{new_game.get('home_pitcher', 'TBD')}"
            )

        if changes:
            msg = (
                f"**Pitcher Update**\n"
                f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
                f"First pitch: {new_game['game_time_et']}\n\n"
                + "\n".join(f"- {x}" for x in changes)
            )
            alerts.append(
                {
                    "message": msg,
                    "payload": {
                        "type": "pitcher_change",
                        "category": "pitching_changes",
                        "notification_category": "pitching_changes",
                        "source": "pitcher_change_bot",
                        "title": "Pitcher Update",
                        "message": msg,
                        "priority": "high",
                        "event_id": f"pitcher-change:{new_game.get('game_pk')}:{','.join(changes)}",
                        "game_pk": new_game.get("game_pk"),
                        "away_team": team_label(new_game["away_team"]),
                        "home_team": team_label(new_game["home_team"]),
                        "matchup": f"{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}",
                        "game_time": new_game.get("game_time_et"),
                        "changes": changes,
                    },
                }
            )

    return alerts


def run_check():
    now = datetime.now(ET).date()
    tomorrow = now + timedelta(days=1)

    state = load_state()

    today_games = get_schedule_for_date(now)
    tomorrow_games = get_schedule_for_date(tomorrow)

    print(f"Today games found: {len(today_games)}")
    print(f"Tomorrow games found: {len(tomorrow_games)}")

    for target_date, new_games in [
        (str(now), today_games),
        (str(tomorrow), tomorrow_games),
    ]:
        old_games = state.get(target_date, {})
        alerts = compare_games(old_games, new_games)

        for alert in alerts:
            try:
                deliver_alert(alert["message"], alert["payload"])
                print("Sent alert:")
                print(alert["message"])
                print("-" * 40)
            except Exception as e:
                print(f"Failed to send Discord alert: {e}")

        state[target_date] = new_games

    save_state(state)
    print("Done.")


if __name__ == "__main__":
    run_check()
