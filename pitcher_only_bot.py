import json
import hashlib
import hmac
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ROYALTYWAGERS_WEBHOOK = os.getenv("ROYALTYWAGERS_WEBHOOK", "")
WEBSITE_NOTIFY_URL = os.getenv("WEBSITE_NOTIFY_URL", "")
WEBSITE_NOTIFY_SECRET = os.getenv("WEBSITE_NOTIFY_SECRET") or os.getenv("SBH_WEBHOOK_SECRET", "")
ENABLE_DISCORD_NOTIFY = os.getenv("ENABLE_DISCORD_NOTIFY", "true").strip().lower() not in {"0", "false", "no", "off"}
ENABLE_WEBSITE_NOTIFY = os.getenv("ENABLE_WEBSITE_NOTIFY", "true").strip().lower() not in {"0", "false", "no", "off"}
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


def send_discord_message(content):
    if not ENABLE_DISCORD_NOTIFY:
        print("[notify] Discord disabled by ENABLE_DISCORD_NOTIFY.")
        return False
    if not WEBHOOK_URL:
        raise RuntimeError("Missing Discord webhook. Add DISCORD_WEBHOOK_URL / PITCHER_WEBHOOK_URL secret.")

    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()
    print("[notify] Discord pitcher alert sent.")

    if ROYALTYWAGERS_WEBHOOK:
        try:
            r2 = requests.post(ROYALTYWAGERS_WEBHOOK, json={"content": content}, timeout=20)
            r2.raise_for_status()
            print("[notify] Discord pitcher alert sent to RoyaltyWagers webhook.")
        except Exception as exc:
            print(f"[notify] RoyaltyWagers webhook failed: {exc}")
    else:
        print("[notify] RoyaltyWagers webhook not set; skipped secondary Discord send.")

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
        print(f"[notify] Signature header set: true secret_len={len(WEBSITE_NOTIFY_SECRET)}")
    else:
        print("[notify] WEBSITE_NOTIFY_SECRET is not set; posting unsigned website notification.")

    r = requests.post(WEBSITE_NOTIFY_URL, data=body, headers=headers, timeout=20)
    response_preview = (r.text or "").replace("\n", " ")[:500]
    print(f"[notify] Website response HTTP {r.status_code}: {response_preview}")
    if not r.ok:
        raise RuntimeError(f"Website notification HTTP {r.status_code}: {response_preview}")

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


def pitcher_change_items(old_game, new_game):
    updates = []

    old_away = old_game.get("away_pitcher", "TBD") if old_game else "TBD"
    new_away = new_game.get("away_pitcher", "TBD")
    old_home = old_game.get("home_pitcher", "TBD") if old_game else "TBD"
    new_home = new_game.get("home_pitcher", "TBD")

    def add_update(side, team_name, old_pitcher, new_pitcher):
        if old_pitcher == new_pitcher or new_pitcher in ("", "TBD"):
            return

        team = team_label(team_name)
        is_initial_post = old_pitcher in ("", "TBD")
        label = (
            f"{team}: pitcher posted - {new_pitcher}"
            if is_initial_post
            else f"{team}: {old_pitcher} -> {new_pitcher}"
        )

        updates.append(
            {
                "team": team,
                "side": side,
                "old_pitcher": old_pitcher or "TBD",
                "new_pitcher": new_pitcher,
                "change_type": "posted" if is_initial_post else "changed",
                "label": label,
            }
        )

    add_update("away", new_game["away_team"], old_away, new_away)
    add_update("home", new_game["home_team"], old_home, new_home)

    return updates


def pitcher_changes(old_game, new_game):
    return [item["label"] for item in pitcher_change_items(old_game, new_game)]


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        return None

    if not is_after_pitcher_alert_start():
        return None

    if not is_within_pitcher_alert_window(new_game.get("game_iso")):
        return None

    pitcher_updates = pitcher_change_items(old_game, new_game)
    changes = [item["label"] for item in pitcher_updates]
    if not changes:
        return None

    matchup = f"{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}"
    changes_text = "\n".join(changes)

    discord_message = (
        f"**Pitcher Update**\n"
        f"**{matchup}**\n"
        f"First pitch: {new_game['game_time']}\n\n"
        + "\n".join(f"- {x}" for x in changes)
    )

    website_message = (
        f"{matchup}\n"
        f"{new_game['game_time']}\n"
        f"Pitcher update: " + "; ".join(changes)
    )

    change_digest = hashlib.sha1(
        json.dumps(pitcher_updates, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    payload = {
        "type": "pitcher_change",
        "category": "pitching_changes",
        "notification_category": "pitching_changes",
        "source": "pitcher_change_bot",
        "title": "Pitcher Update",
        "message": website_message,
        "discord_message": discord_message,
        "priority": "high",
        "event_id": f"pitcher-change:{new_game.get('game_pk')}:{change_digest}",
        "game_pk": new_game.get("game_pk"),
        "away_team": team_label(new_game["away_team"]),
        "home_team": team_label(new_game["home_team"]),
        "matchup": matchup,
        "game_time": new_game.get("game_time"),
        "game_iso": new_game.get("game_iso"),
        "changes": changes,
        "changes_text": changes_text,
        "change_count": len(changes),
        "pitcher_updates": pitcher_updates,
        "away_pitcher": new_game.get("away_pitcher"),
        "home_pitcher": new_game.get("home_pitcher"),
    }

    return {"message": discord_message, "payload": payload}


def run():
    state = load_state()
    today = datetime.now(ET).date()
    dates_to_check = [today]

    if CHECK_TOMORROW:
        dates_to_check.append(today + timedelta(days=1))

    print(
        "[notify] Config: "
        f"discord_enabled={ENABLE_DISCORD_NOTIFY} discord_webhook_set={bool(WEBHOOK_URL)} "
        f"royalty_webhook_set={bool(ROYALTYWAGERS_WEBHOOK)} "
        f"website_enabled={ENABLE_WEBSITE_NOTIFY} website_url_set={bool(WEBSITE_NOTIFY_URL)} "
        f"website_secret_len={len(WEBSITE_NOTIFY_SECRET or '')}"
    )
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
                deliver_alert(alert["message"], alert["payload"])
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
