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
TEST_NOTIFICATION = os.getenv("TEST_NOTIFICATION", "").strip().lower() in {"1", "true", "yes", "on"}
STATE_FILE = "pitcher_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

PITCHER_ALERT_WINDOW_HOURS = 6
PITCHER_ALERT_START_HOUR_ET = 8
UNKNOWN_PITCHER_VALUES = {"", "TBD", "TBA", "NONE", "NULL", "N/A"}

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


def send_discord(content):
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
        delivery["discord"] = bool(send_discord(content))
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


def send_test_notification():
    now = datetime.now(ET)
    message = (
        "**Pitcher Update Test**\n"
        "**TEST @ TEST**\n"
        f"Sent: {now.strftime('%b')} {now.day}, {now.strftime('%I:%M %p').lstrip('0')} ET\n\n"
        "- This is a manual test from the pitcher-change bot."
    )
    payload = {
        "type": "pitcher_change",
        "category": "pitching_changes",
        "notification_category": "pitching_changes",
        "source": "pitcher_change_bot",
        "title": "Pitcher Update Test",
        "message": message,
        "priority": "normal",
        "event_id": f"pitcher-change:test:{now.strftime('%Y%m%d%H%M%S')}",
        "game_pk": "test",
        "away_team": "TEST",
        "home_team": "TEST",
        "matchup": "TEST @ TEST",
        "game_time": now.isoformat(),
        "game_iso": now.isoformat(),
        "changes": [
            {
                "type": "test",
                "text": "Manual pitcher notification test.",
            }
        ],
        "is_test": True,
    }
    deliver_alert(message, payload)
    print("Sent pitcher notification test.")


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
                game_time = format_first_pitch(game_dt)
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


def normalize_pitcher_name(value):
    if value is None:
        return "TBD"
    name = str(value).strip()
    return name if name else "TBD"


def is_unknown_pitcher(value):
    return normalize_pitcher_name(value).upper() in UNKNOWN_PITCHER_VALUES


def pitcher_changes(old_game, new_game):
    changes = []

    sides = [
        ("away", new_game["away_team"]),
        ("home", new_game["home_team"]),
    ]

    for side, team_name in sides:
        old_pitcher = normalize_pitcher_name(
            old_game.get(f"{side}_pitcher", "TBD") if old_game else "TBD"
        )
        new_pitcher = normalize_pitcher_name(new_game.get(f"{side}_pitcher", "TBD"))

        if old_pitcher == new_pitcher:
            continue

        old_unknown = is_unknown_pitcher(old_pitcher)
        new_unknown = is_unknown_pitcher(new_pitcher)
        team = team_label(team_name)

        if not old_unknown and new_unknown:
            changes.append(
                {
                    "type": "scratch",
                    "text": f"🚨 {team}: {old_pitcher} removed / scratched -> {new_pitcher}",
                }
            )
        elif old_unknown and not new_unknown:
            changes.append(
                {
                    "type": "posted",
                    "text": f"{team}: pitcher posted - {new_pitcher}",
                }
            )
        elif not old_unknown and not new_unknown:
            changes.append(
                {
                    "type": "swap",
                    "text": f"🚨 {team}: {old_pitcher} -> {new_pitcher}",
                }
            )
        else:
            changes.append(
                {
                    "type": "unknown_change",
                    "text": f"{team}: pitcher status changed - {old_pitcher} -> {new_pitcher}",
                }
            )

    return changes


def should_send_pitcher_alert(changes, game_iso):
    if not changes:
        return False

    # Named pitcher removals/swaps move markets immediately. Do not hide them
    # behind the 6-hour window; that was the Skubal miss.
    urgent_types = {"scratch", "swap", "unknown_change"}
    if any(change["type"] in urgent_types for change in changes):
        return True

    # Routine TBD -> named pitcher posts can stay window-gated to avoid spam.
    return is_within_pitcher_alert_window(game_iso)


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        return None

    if not is_after_pitcher_alert_start():
        return None

    changes = pitcher_changes(old_game, new_game)
    if not should_send_pitcher_alert(changes, new_game.get("game_iso")):
        return None

    message = (
        f"**Pitcher Update**\n"
        f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
        f"First pitch: {new_game['game_time']}\n\n"
        + "\n".join(f"- {change['text']}" for change in changes)
    )
    payload = {
        "type": "pitcher_change",
        "category": "pitching_changes",
        "notification_category": "pitching_changes",
        "source": "pitcher_change_bot",
        "title": "Pitcher Update",
        "message": message,
        "priority": "high" if any(change["type"] in {"scratch", "swap"} for change in changes) else "normal",
        "event_id": f"pitcher-change:{new_game.get('game_pk')}:{','.join(change['type'] for change in changes)}:{','.join(change['text'] for change in changes)}",
        "game_pk": new_game.get("game_pk"),
        "away_team": team_label(new_game["away_team"]),
        "home_team": team_label(new_game["home_team"]),
        "matchup": f"{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}",
        "game_time": new_game.get("game_time"),
        "game_iso": new_game.get("game_iso"),
        "changes": changes,
    }
    return {"message": message, "payload": payload}


def run():
    if TEST_NOTIFICATION:
        send_test_notification()
        return

    print(
        "[notify] Config: "
        f"discord_enabled={ENABLE_DISCORD_NOTIFY} discord_webhook_set={bool(WEBHOOK_URL)} "
        f"website_enabled={ENABLE_WEBSITE_NOTIFY} website_url_set={bool(WEBSITE_NOTIFY_URL)} "
        f"website_secret_len={len(WEBSITE_NOTIFY_SECRET or '')}"
    )

    state = load_state()
    today = datetime.now(ET).date()
    dates_to_check = [today]

    if CHECK_TOMORROW:
        dates_to_check.append(today + timedelta(days=1))

    print(f"Loaded state keys: {list(state.keys())}")

    total_alerts = 0
    discord_deliveries = 0
    website_deliveries = 0

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
                delivery = deliver_alert(alert["message"], alert["payload"])
                discord_deliveries += 1 if delivery.get("discord") else 0
                website_deliveries += 1 if delivery.get("website") else 0
                total_alerts += 1
                print(f"Sent alert for: {game_key}")

        state[date_key] = new_games

    print("About to save state...")
    preview = json.dumps(state, indent=2)
    print(preview[:2000])
    save_state(state)
    print("State saved.")
    print(
        "Done. "
        f"Total alerts built: {total_alerts}; "
        f"Discord deliveries: {discord_deliveries}; "
        f"Website deliveries: {website_deliveries}"
    )


if __name__ == "__main__":
    run()
