import json
import hashlib
import hmac
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import requests

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
WEBSITE_NOTIFY_URL = os.getenv("WEBSITE_NOTIFY_URL", "")
WEBSITE_NOTIFY_SECRET = os.getenv("WEBSITE_NOTIFY_SECRET") or os.getenv("SBH_WEBHOOK_SECRET", "")
ENABLE_DISCORD_NOTIFY = os.getenv("ENABLE_DISCORD_NOTIFY", "true").strip().lower() not in {"0", "false", "no", "off"}
ENABLE_WEBSITE_NOTIFY = os.getenv("ENABLE_WEBSITE_NOTIFY", "true").strip().lower() not in {"0", "false", "no", "off"}
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


def send_discord(content):
    if not ENABLE_DISCORD_NOTIFY:
        return False
    if not WEBHOOK_URL:
        raise RuntimeError("Missing Discord webhook. Add DISCORD_WEBHOOK_URL / LINEUP_WEBHOOK_URL secret.")
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()
    return True



def website_target_label():
    if not WEBSITE_NOTIFY_URL:
        return "not-set"
    parsed = urlparse(WEBSITE_NOTIFY_URL)
    return f"{parsed.netloc}{parsed.path}"


def send_website_notification(payload):
    if not ENABLE_WEBSITE_NOTIFY:
        print("[notify] Website notifications disabled by ENABLE_WEBSITE_NOTIFY.")
        return False
    if not WEBSITE_NOTIFY_URL:
        print("[notify] Website notifications disabled: WEBSITE_NOTIFY_URL is not set.")
        return False

    print(f"[notify] Website target: {website_target_label()}")
    print(
        "[notify] Website payload: "
        f"event_id={payload.get('event_id')} "
        f"type={payload.get('type')} "
        f"category={payload.get('notification_category') or payload.get('category')} "
        f"source={payload.get('source')}"
    )

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"content-type": "application/json"}

    if WEBSITE_NOTIFY_SECRET:
        signature = hmac.new(WEBSITE_NOTIFY_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["x-sbh-signature"] = f"sha256={signature}"
        print(f"[notify] Signature header set: true secret_len={len(WEBSITE_NOTIFY_SECRET)}")
    else:
        print("[notify] Signature header set: false secret_len=0")

    r = requests.post(WEBSITE_NOTIFY_URL, data=body, headers=headers, timeout=20)
    response_preview = (r.text or "").replace("\n", " ")[:1000]
    print(f"[notify] Website response HTTP {r.status_code}: {response_preview}")

    if not r.ok:
        raise RuntimeError(f"Website notification HTTP {r.status_code}: {response_preview}")

    print(f"[notify] Website lineup alert accepted: HTTP {r.status_code}.")
    return True



def deliver_alert(content, payload):
    errors = []
    delivery = {"discord": False, "website": False}

    try:
        delivery["discord"] = bool(send_discord(content))
        if delivery["discord"]:
            print("[notify] Discord lineup alert sent.")
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


def format_numbered_lineup(lineup):
    return "\n".join(f"{idx}. {name}" for idx, name in enumerate(lineup, start=1))


def collect_watchlist_lines(team_name, lineup, roster):
    lineup_set = set(lineup or [])
    roster_set = set(roster or [])
    active = []
    missing = []

    for batter in WATCHED_BATTERS:
        if batter not in roster_set:
            continue

        line = f"- {batter} ({team_label(team_name)})"
        if batter in lineup_set:
            active.append(line)
        else:
            missing.append(line)

    return sorted(set(active)), sorted(set(missing))


def lineup_digest(posted_lineups):
    raw = json.dumps(posted_lineups, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def preserve_posted_lineups(old_game, new_game):
    """Keep already-seen lineups in state if the API briefly returns blanks.

    MLB StatsAPI can occasionally expose an empty battingOrder during pregame refreshes.
    Without this guard, one empty poll can erase the posted lineup from state and cause
    a duplicate "lineup posted" alert on the next successful poll.
    """
    merged = dict(new_game)

    if old_game.get("away_lineup") and not merged.get("away_lineup"):
        merged["away_lineup"] = old_game.get("away_lineup", [])

    if old_game.get("home_lineup") and not merged.get("home_lineup"):
        merged["home_lineup"] = old_game.get("home_lineup", [])

    return merged


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        debug("[BUILD] Skipped: game already started")
        return None

    away_team = new_game["away_team"]
    home_team = new_game["home_team"]

    old_away_posted = bool(old_game.get("away_lineup"))
    old_home_posted = bool(old_game.get("home_lineup"))
    new_away_lineup = new_game.get("away_lineup", [])
    new_home_lineup = new_game.get("home_lineup", [])
    new_away_posted = bool(new_away_lineup)
    new_home_posted = bool(new_home_lineup)

    newly_posted = []

    if new_away_posted and not old_away_posted:
        newly_posted.append(
            {
                "side": "away",
                "team": away_team,
                "team_label": team_label(away_team),
                "lineup": new_away_lineup,
                "roster": new_game.get("away_roster", []),
            }
        )

    if new_home_posted and not old_home_posted:
        newly_posted.append(
            {
                "side": "home",
                "team": home_team,
                "team_label": team_label(home_team),
                "lineup": new_home_lineup,
                "roster": new_game.get("home_roster", []),
            }
        )

    if not newly_posted:
        debug(f"[BUILD] No newly posted lineup for {away_team} @ {home_team}")
        return None

    lineup_sections = []
    website_lineup_sections = []
    watched_active = []
    watched_missing = []
    posted_team_labels = []
    posted_lineups = {}

    for item in newly_posted:
        posted_team_labels.append(item["team_label"])
        posted_lineups[item["side"]] = item["lineup"]

        lineup_sections.append(
            f"**{item['team_label']} lineup posted**\n"
            f"{format_numbered_lineup(item['lineup'])}"
        )
        website_lineup_sections.append(
            f"{item['team_label']} lineup: " + "; ".join(item["lineup"])
        )

        active, missing = collect_watchlist_lines(item["team"], item["lineup"], item["roster"])
        watched_active.extend(active)
        watched_missing.extend(missing)

    watched_active = sorted(set(watched_active))
    watched_missing = sorted(set(watched_missing))

    watch_sections = []
    if watched_active:
        watch_sections.append("**Watchlist in lineup**\n" + "\n".join(watched_active))
    if watched_missing:
        watch_sections.append("**Watchlist not in lineup**\n" + "\n".join(watched_missing))

    discord_message = (
        f"**Lineup Posted**\n"
        f"**{team_label(away_team)} @ {team_label(home_team)}**\n"
        f"First pitch: {new_game['game_time']}\n"
        f"Posted: {', '.join(posted_team_labels)}\n\n"
        + "\n\n".join(lineup_sections + watch_sections)
    )

    website_sections = [f"Lineup posted: {', '.join(posted_team_labels)}"]
    website_sections.extend(website_lineup_sections)

    if watched_active:
        website_sections.append(
            "Watchlist in lineup: " + ", ".join(line.removeprefix("- ") for line in watched_active)
        )
    if watched_missing:
        website_sections.append(
            "Watchlist not in lineup: " + ", ".join(line.removeprefix("- ") for line in watched_missing)
        )

    website_message = (
        f"{team_label(away_team)} @ {team_label(home_team)}\n"
        f"{new_game['game_time']}\n"
        + "\n".join(website_sections)
    )

    digest = lineup_digest(posted_lineups)

    payload = {
        "type": "lineup_posted",
        "category": "lineup_changes",
        "notification_category": "lineup_changes",
        "source": "lineup_posted_bot",
        "title": "Lineup Posted",
        "message": website_message,
        "priority": "normal",
        "event_id": f"lineup-posted:{new_game.get('game_pk')}:{','.join(posted_team_labels)}:{digest}",
        "game_pk": new_game.get("game_pk"),
        "away_team": team_label(away_team),
        "home_team": team_label(home_team),
        "matchup": f"{team_label(away_team)} @ {team_label(home_team)}",
        "game_time": new_game.get("game_time"),
        "game_iso": new_game.get("game_iso"),
        "posted_teams": posted_team_labels,
        "posted_lineups": posted_lineups,
        "watchlist_active": watched_active,
        "watchlist_missing": watched_missing,
        "missing_batters": watched_missing,
        "added_batters": [],
    }

    return {"message": discord_message, "payload": payload}


def run():
    print(
        "[notify] Config: "
        f"discord_enabled={ENABLE_DISCORD_NOTIFY} discord_webhook_set={bool(WEBHOOK_URL)} "
        f"website_enabled={ENABLE_WEBSITE_NOTIFY} website_url_set={bool(WEBSITE_NOTIFY_URL)} "
        f"website_target={website_target_label()} "
        f"website_secret_len={len(WEBSITE_NOTIFY_SECRET or '')}"
    )

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

        date_state = {}

        for game_key, game_data in new_games.items():
            old_game = old_games.get(game_key, {})
            alert = build(old_game, game_data)
            if alert:
                deliver_alert(alert["message"], alert["payload"])
                total_alerts += 1
                print(f"Sent alert for: {game_key}")

            date_state[game_key] = preserve_posted_lineups(old_game, game_data)

        new_state[date_key] = date_state

    print("About to save lineup state...")
    preview = json.dumps(new_state, indent=2)
    print(preview[:3000])

    save_state(new_state)

    print("Lineup state saved.")
    print(f"Done. Total alerts sent: {total_alerts}")


if __name__ == "__main__":
    run()
