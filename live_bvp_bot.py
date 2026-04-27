import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
MLB_BASE = "https://statsapi.mlb.com/api/v1"
LIVE_BASE = "https://statsapi.mlb.com/api/v1.1"
STATE_FILE = Path(os.getenv("BVP_STATE_FILE", "state/live_bvp_state.json"))

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_BVP") or os.getenv("DISCORD_WEBHOOK_URL")

# Good-data thresholds. Change these in GitHub Actions env if you want.
MIN_BVP_AB = int(os.getenv("MIN_BVP_AB", "3"))
MIN_BVP_HITS = int(os.getenv("MIN_BVP_HITS", "2"))
MIN_BVP_AVG = float(os.getenv("MIN_BVP_AVG", "0.750"))
MIN_BVP_OPS = float(os.getenv("MIN_BVP_OPS", "0"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
SLEEP_BETWEEN_BVP_CALLS = float(os.getenv("SLEEP_BETWEEN_BVP_CALLS", "0.15"))

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


def log(msg: str) -> None:
    print(f"[{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}] {msg}", flush=True)


def team_label(name: str) -> str:
    return TEAM_ABBR.get(name, name)


def request_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"date": None, "games": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log("State file was invalid JSON. Resetting it.")
        return {"date": None, "games": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def reset_state_if_new_day(state: dict, today: str) -> dict:
    if state.get("date") != today:
        return {"date": today, "games": {}}
    state.setdefault("games", {})
    return state


def send_discord(content: str) -> None:
    if not DISCORD_WEBHOOK:
        raise RuntimeError("Missing Discord webhook. Add repo secret DISCORD_WEBHOOK_BVP.")
    response = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()


def today_schedule() -> list[dict]:
    today = datetime.now(ET).date().strftime("%Y-%m-%d")
    data = request_json(
        f"{MLB_BASE}/schedule",
        {
            "sportId": 1,
            "date": today,
            "hydrate": "team,linescore,probablePitcher",
        },
    )
    games = []
    for date_block in data.get("dates", []):
        games.extend(date_block.get("games", []))
    return games


def game_time_et(game: dict) -> datetime | None:
    raw = game.get("gameDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
    except ValueError:
        return None


def should_check_game(game: dict, now: datetime) -> bool:
    status = game.get("status", {})
    abstract = status.get("abstractGameState", "")
    detailed = status.get("detailedState", "")

    if abstract == "Final" or "Final" in detailed or "Postponed" in detailed or "Suspended" in detailed:
        return False

    start = game_time_et(game)
    if not start:
        return abstract == "Live"

    # Start checking near first pitch and stop after a generous game window.
    return (start - timedelta(minutes=15)) <= now <= (start + timedelta(hours=6, minutes=30))


def live_feed(game_pk: str) -> dict:
    return request_json(f"{LIVE_BASE}/game/{game_pk}/feed/live")


def current_pitcher_and_batting_side(feed: dict) -> tuple[dict | None, str | None]:
    current_play = feed.get("liveData", {}).get("plays", {}).get("currentPlay", {})
    matchup = current_play.get("matchup", {})
    pitcher = matchup.get("pitcher")

    half = (
        current_play.get("about", {}).get("halfInning")
        or feed.get("liveData", {}).get("linescore", {}).get("inningHalf")
        or ""
    ).lower()

    if half.startswith("top"):
        batting_side = "away"
    elif half.startswith("bottom"):
        batting_side = "home"
    else:
        batting_side = None

    if not pitcher or not pitcher.get("id"):
        return None, batting_side
    return pitcher, batting_side


def lineup_batters(feed: dict, side: str) -> list[dict]:
    team_box = feed.get("liveData", {}).get("boxscore", {}).get("teams", {}).get(side, {})
    players = team_box.get("players", {})
    batting_order = team_box.get("battingOrder", [])

    batters = []
    if batting_order:
        for player_id in batting_order:
            player = players.get(f"ID{player_id}", {})
            person = player.get("person", {})
            if person.get("id") and person.get("fullName"):
                batters.append({"id": int(person["id"]), "name": person["fullName"]})
        return batters

    # Fallback if lineup is not exposed yet: any player with batting stats in the boxscore.
    for player in players.values():
        person = player.get("person", {})
        batting = player.get("stats", {}).get("batting", {})
        if person.get("id") and person.get("fullName") and batting:
            batters.append({"id": int(person["id"]), "name": person["fullName"]})
    return batters


def as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def get_bvp_stat(batter_id: int, pitcher_id: int) -> dict | None:
    """Fetch batter-vs-pitcher hitting stats from MLB StatsAPI."""
    params = {
        "stats": "vsPlayer",
        "group": "hitting",
        "opposingPlayerId": pitcher_id,
    }
    data = request_json(f"{MLB_BASE}/people/{batter_id}/stats", params)
    stats_blocks = data.get("stats", [])
    for block in stats_blocks:
        splits = block.get("splits", [])
        if not splits:
            continue
        stat = splits[0].get("stat", {})
        ab = as_int(stat.get("atBats"))
        pa = as_int(stat.get("plateAppearances"))
        hits = as_int(stat.get("hits"))
        if ab == 0 and pa == 0 and hits == 0:
            continue
        return {
            "ab": ab,
            "pa": pa,
            "hits": hits,
            "avg": as_float(stat.get("avg")),
            "ops": as_float(stat.get("ops")),
            "hr": as_int(stat.get("homeRuns")),
            "rbi": as_int(stat.get("rbi")),
            "bb": as_int(stat.get("baseOnBalls")),
            "so": as_int(stat.get("strikeOuts")),
        }
    return None


def is_good_bvp(stat: dict) -> bool:
    return (
        stat.get("ab", 0) >= MIN_BVP_AB
        and stat.get("hits", 0) >= MIN_BVP_HITS
        and stat.get("avg", 0.0) >= MIN_BVP_AVG
        and stat.get("ops", 0.0) >= MIN_BVP_OPS
    )


def format_stat_line(stat: dict) -> str:
    ab = stat.get("ab", 0)
    hits = stat.get("hits", 0)
    avg = stat.get("avg", 0.0)
    ops = stat.get("ops", 0.0)
    extras = []
    if stat.get("hr", 0):
        extras.append(f"{stat['hr']} HR")
    if stat.get("bb", 0):
        extras.append(f"{stat['bb']} BB")
    if stat.get("so", 0):
        extras.append(f"{stat['so']} K")
    suffix = f" | {' / '.join(extras)}" if extras else ""
    return f"{hits}/{ab}, .{int(round(avg * 1000)):03d} AVG, {ops:.3f} OPS{suffix}"


def build_alert(game_name: str, batting_team: str, pitcher_name: str, batter_name: str, stat: dict) -> str:
    return (
        "🚨 **BvP Alert**\n"
        f"**{game_name}**\n"
        f"Batting team: **{batting_team}**\n\n"
        f"**{batter_name}** is **{stat['hits']}/{stat['ab']}** vs **{pitcher_name}** 🚨\n"
        f"{format_stat_line(stat)}"
    )


def check_game(game: dict, state: dict) -> int:
    game_pk = str(game.get("gamePk"))
    away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
    home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
    game_name = f"{team_label(away_team)} @ {team_label(home_team)}"

    feed = live_feed(game_pk)
    status = feed.get("gameData", {}).get("status", {}).get("abstractGameState", "")
    if status != "Live":
        log(f"{game_name}: not live ({status}).")
        return 0

    pitcher, batting_side = current_pitcher_and_batting_side(feed)
    if not pitcher or not batting_side:
        log(f"{game_name}: no active pitcher/batting side yet.")
        return 0

    pitcher_id = int(pitcher["id"])
    pitcher_name = pitcher.get("fullName", str(pitcher_id))
    batting_team = away_team if batting_side == "away" else home_team

    game_state = state.setdefault("games", {}).setdefault(game_pk, {})
    last_pitcher_id = game_state.get("active_pitcher_id")
    alerted_pairs = set(game_state.get("alerted_pairs", []))

    if last_pitcher_id == pitcher_id:
        log(f"{game_name}: pitcher unchanged ({pitcher_name}).")
        return 0

    log(f"{game_name}: new pitcher detected: {pitcher_name}. Checking {team_label(batting_team)} batters.")
    game_state["active_pitcher_id"] = pitcher_id
    game_state["active_pitcher_name"] = pitcher_name
    game_state["last_checked_at"] = datetime.now(ET).isoformat()

    sent = 0
    for batter in lineup_batters(feed, batting_side):
        pair_key = f"{pitcher_id}:{batter['id']}"
        if pair_key in alerted_pairs:
            continue
        try:
            stat = get_bvp_stat(batter["id"], pitcher_id)
            time.sleep(SLEEP_BETWEEN_BVP_CALLS)
        except Exception as exc:
            log(f"BvP lookup failed for {batter['name']} vs {pitcher_name}: {exc}")
            continue

        if not stat or not is_good_bvp(stat):
            continue

        alert = build_alert(game_name, team_label(batting_team), pitcher_name, batter["name"], stat)
        send_discord(alert)
        alerted_pairs.add(pair_key)
        sent += 1
        log(f"Sent alert: {batter['name']} vs {pitcher_name} ({format_stat_line(stat)})")

    game_state["alerted_pairs"] = sorted(alerted_pairs)
    return sent


def run() -> None:
    now = datetime.now(ET)
    today_key = now.date().isoformat()
    state = reset_state_if_new_day(load_state(), today_key)

    games = today_schedule()
    active_window_games = [game for game in games if should_check_game(game, now)]

    if not games:
        log("No MLB games today. Bot stops until the next scheduled run.")
        save_state(state)
        return

    if not active_window_games:
        log("No games are currently inside the live-check window. Bot stops until the next scheduled run.")
        save_state(state)
        return

    total_alerts = 0
    for game in active_window_games:
        try:
            total_alerts += check_game(game, state)
        except Exception as exc:
            log(f"Game check failed for gamePk={game.get('gamePk')}: {exc}")

    save_state(state)
    log(f"Done. Alerts sent: {total_alerts}")


if __name__ == "__main__":
    run()
