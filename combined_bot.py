import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "combined_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# Only show pitcher-related alerts this many hours before first pitch
PITCHER_ALERT_WINDOW_HOURS = 6

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


def load_watchlist_file(filename):
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


WATCHED_BATTERS = load_watchlist_file("batters.txt")
WATCHED_PITCHERS = load_watchlist_file("pitchers.txt")


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


def extract_lineup(team_data):
    players = team_data.get("players", {})
    batting_order = team_data.get("battingOrder", [])

    lineup = []
    for player_id in batting_order:
        player = players.get(f"ID{player_id}", {})
        name = player.get("person", {}).get("fullName", "Unknown")
        lineup.append(name)

    return lineup


def lineup_to_text(title, lineup):
    if not lineup:
        return f"**{title}**\nNot posted yet"

    return f"**{title}**\n" + "\n".join(
        f"{i + 1}. {player}" for i, player in enumerate(lineup)
    )


def is_pregame(game_iso):
    if not game_iso:
        return True

    try:
        game_dt = datetime.fromisoformat(game_iso)
        return datetime.now(ET) < game_dt
    except Exception:
        return True


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


def get_player_search_result(player_name):
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
        return people[0]
    except Exception as e:
        print(f"Player search failed for {player_name}: {e}")
        return None


def get_pitching_stats(player_id, season):
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
            params={
                "stats": "season",
                "group": "pitching",
                "season": season,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        stats_blocks = data.get("stats", [])
        if not stats_blocks:
            return None

        splits = stats_blocks[0].get("splits", [])
        if not splits:
            return None

        return splits[0].get("stat", {})
    except Exception as e:
        print(f"Pitching stats failed for player {player_id}, season {season}: {e}")
        return None


def get_career_pitching_stats(player_id):
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats",
            params={
                "stats": "career",
                "group": "pitching",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        stats_blocks = data.get("stats", [])
        if not stats_blocks:
            return None

        splits = stats_blocks[0].get("splits", [])
        if not splits:
            return None

        return splits[0].get("stat", {})
    except Exception as e:
        print(f"Career pitching stats failed for player {player_id}: {e}")
        return None


def classify_pitcher_role(pitcher_name):
    if not pitcher_name or pitcher_name == "TBD":
        return "uncertain"

    person = get_player_search_result(pitcher_name)
    if not person:
        return "unknown"

    player_id = person.get("id")
    if not player_id:
        return "unknown"

    current_year = datetime.now(ET).year
    recent_years = [current_year, current_year - 1]

    recent_gs = 0
    recent_games = 0
    recent_ip = 0.0

    for season in recent_years:
        stat = get_pitching_stats(player_id, season)
        if not stat:
            continue

        recent_gs += stat.get("gamesStarted", 0)
        recent_games += stat.get("gamesPlayed", 0)

        try:
            recent_ip += float(stat.get("inningsPitched", 0))
        except Exception:
            pass

    career_stat = get_career_pitching_stats(player_id)
    career_gs = 0

    if career_stat:
        career_gs = career_stat.get("gamesStarted", 0)

    if recent_gs >= 10:
        return "starter"
    if recent_gs >= 5 and recent_ip >= 40:
        return "starter"
    if career_gs >= 40:
        return "starter"

    if recent_gs == 0 and career_gs <= 5:
        return "bullpen"
    if recent_gs <= 1 and career_gs <= 10 and recent_games >= 10:
        return "bullpen"

    if recent_gs <= 4 and career_gs < 40:
        return "non_standard"

    return "unknown"


def build_pitcher_watch_section(new_game):
    away_pitcher = new_game.get("away_pitcher", "TBD")
    home_pitcher = new_game.get("home_pitcher", "TBD")

    alerts = []

    if away_pitcher in WATCHED_PITCHERS:
        away_role = classify_pitcher_role(away_pitcher)
        if away_role == "bullpen":
            alerts.append(
                f"🚨 {team_label(new_game['away_team'])}: likely bullpen game — {away_pitcher}"
            )
        elif away_role == "non_standard":
            alerts.append(
                f"👀 {team_label(new_game['away_team'])}: non-standard starter — {away_pitcher}"
            )

    if home_pitcher in WATCHED_PITCHERS:
        home_role = classify_pitcher_role(home_pitcher)
        if home_role == "bullpen":
            alerts.append(
                f"🚨 {team_label(new_game['home_team'])}: likely bullpen game — {home_pitcher}"
            )
        elif home_role == "non_standard":
            alerts.append(
                f"👀 {team_label(new_game['home_team'])}: non-standard starter — {home_pitcher}"
            )

    return alerts


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
                "away_pitcher": away_pitcher,
                "home_pitcher": home_pitcher,
                "away_lineup": away_lineup,
                "home_lineup": home_lineup,
                "game_time": game_time,
                "game_iso": game_iso,
                "game_pk": game_pk,
            }

    return games


def pitcher_changes(old_game, new_game):
    changes = []

    old_away = old_game.get("away_pitcher", "TBD")
    new_away = new_game.get("away_pitcher", "TBD")
    old_home = old_game.get("home_pitcher", "TBD")
    new_home = new_game.get("home_pitcher", "TBD")

    if old_away != new_away and (
        old_away in WATCHED_PITCHERS or new_away in WATCHED_PITCHERS
    ):
        if old_away == "TBD" and new_away != "TBD":
            changes.append(
                f"🆕 {team_label(new_game['away_team'])}: pitcher — {new_away}"
            )
        else:
            changes.append(
                f"🔄 {team_label(new_game['away_team'])}: {old_away} → {new_away}"
            )

    if old_home != new_home and (
        old_home in WATCHED_PITCHERS or new_home in WATCHED_PITCHERS
    ):
        if old_home == "TBD" and new_home != "TBD":
            changes.append(
                f"🆕 {team_label(new_game['home_team'])}: pitcher — {new_home}"
            )
        else:
            changes.append(
                f"🔄 {team_label(new_game['home_team'])}: {old_home} → {new_home}"
            )

    return changes


def lineup_changes(old_lineup, new_lineup):
    labels = set()
    changes = []
    star_alerts = []
    posted = False

    if not old_lineup and new_lineup:
        posted = True
        labels.add("LINEUP POSTED")

        for i, player in enumerate(new_lineup):
            if player in WATCHED_BATTERS:
                star_alerts.append(f"⭐ {player} posted at {i + 1}")

        if not star_alerts:
            labels = set()
            posted = False

        return labels, changes, star_alerts, posted

    if not new_lineup:
        return labels, changes, star_alerts, posted

    old_positions = {player: i for i, player in enumerate(old_lineup)}
    new_positions = {player: i for i, player in enumerate(new_lineup)}

    used_old = set()
    used_new = set()

    max_len = max(len(old_lineup), len(new_lineup))

    for i in range(max_len):
        old_player = old_lineup[i] if i < len(old_lineup) else None
        new_player = new_lineup[i] if i < len(new_lineup) else None

        if old_player and new_player and old_player != new_player:
            if old_player not in new_positions and new_player not in old_positions:
                if old_player in WATCHED_BATTERS or new_player in WATCHED_BATTERS:
                    changes.append(f"🔄 {new_player} replaces {old_player} at {i + 1}")
                    labels.add("LINEUP SWITCH")
                    used_old.add(old_player)
                    used_new.add(new_player)

                    if old_player in WATCHED_BATTERS:
                        star_alerts.append(f"⭐ {old_player} removed from {i + 1}")
                    if new_player in WATCHED_BATTERS:
                        star_alerts.append(f"⭐ {new_player} added at {i + 1}")

    for player in new_lineup:
        if player in old_positions and player not in used_new and player in WATCHED_BATTERS:
            old_pos = old_positions[player]
            new_pos = new_positions[player]

            if old_pos != new_pos:
                direction = "📈" if new_pos < old_pos else "📉"
                changes.append(f"{direction} {player} moved from {old_pos + 1} → {new_pos + 1}")
                labels.add("LINEUP REARRANGED")
                star_alerts.append(
                    f"{direction} ⭐ {player} moved from {old_pos + 1} → {new_pos + 1}"
                )

    for player in new_lineup:
        if player not in old_positions and player not in used_new and player in WATCHED_BATTERS:
            pos = new_positions[player] + 1
            changes.append(f"➕ {player} added at {pos}")
            labels.add("LINEUP SWITCH")
            star_alerts.append(f"⭐ {player} added at {pos}")

    for player in old_lineup:
        if player not in new_positions and player not in used_old and player in WATCHED_BATTERS:
            pos = old_positions[player] + 1
            changes.append(f"❌ {player} removed from {pos}")
            labels.add("LINEUP SWITCH")
            star_alerts.append(f"⭐ {player} removed from {pos}")

    return labels, changes, star_alerts, posted


def build(old_game, new_game):
    if not is_pregame(new_game.get("game_iso")):
        return None

    labels = []
    sections = []
    star_alerts = []

    if is_within_pitcher_alert_window(new_game.get("game_iso")):
        pitch_changes = pitcher_changes(old_game, new_game)
        if pitch_changes:
            labels.append("PITCHER CHANGE")
            sections.append("**PITCHER UPDATES**\n" + "\n".join(f"- {x}" for x in pitch_changes))

        pitcher_watch_alerts = build_pitcher_watch_section(new_game)
        if pitcher_watch_alerts:
            labels.append("BULLPEN WATCH")
            sections.append(
                "**BULLPEN / STARTER WATCH**\n"
                + "\n".join(f"- {x}" for x in pitcher_watch_alerts)
            )

    away_labels, away_changes, away_stars, away_posted = lineup_changes(
        old_game.get("away_lineup", []),
        new_game.get("away_lineup", []),
    )
    home_labels, home_changes, home_stars, home_posted = lineup_changes(
        old_game.get("home_lineup", []),
        new_game.get("home_lineup", []),
    )

    for label in ["LINEUP POSTED", "LINEUP REARRANGED", "LINEUP SWITCH"]:
        if label in away_labels or label in home_labels:
            labels.append(label)

    star_alerts.extend(away_stars)
    star_alerts.extend(home_stars)

    if away_posted:
        sections.append(
            lineup_to_text(
                f"{team_label(new_game['away_team'])} LINEUP POSTED",
                new_game.get("away_lineup", []),
            )
        )
    elif away_changes:
        sections.append(
            f"**{team_label(new_game['away_team'])} CHANGES**\n"
            + "\n".join(f"- {x}" for x in away_changes)
        )
        sections.append(
            lineup_to_text(
                f"UPDATED {team_label(new_game['away_team'])} LINEUP",
                new_game.get("away_lineup", []),
            )
        )

    if home_posted:
        sections.append(
            lineup_to_text(
                f"{team_label(new_game['home_team'])} LINEUP POSTED",
                new_game.get("home_lineup", []),
            )
        )
    elif home_changes:
        sections.append(
            f"**{team_label(new_game['home_team'])} CHANGES**\n"
            + "\n".join(f"- {x}" for x in home_changes)
        )
        sections.append(
            lineup_to_text(
                f"UPDATED {team_label(new_game['home_team'])} LINEUP",
                new_game.get("home_lineup", []),
            )
        )

    if star_alerts:
        labels.append("WATCHLIST ALERT")
        deduped = list(dict.fromkeys(star_alerts))
        sections.append("**⭐ WATCHLIST ALERT**\n" + "\n".join(f"- {x}" for x in deduped))

    labels = list(dict.fromkeys(labels))

    if not labels:
        return None

    top_label = " + ".join(labels)

    msg = (
        f"🚨 **{top_label}**\n\n"
        f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
        f"**First pitch:** {new_game['game_time']}\n\n"
        + "\n\n".join(sections)
        + "\n\n⚾ DRIZZPLAYS"
    )

    if len(msg) > 1900:
        msg = (
            f"🚨 **{top_label}**\n\n"
            f"**{team_label(new_game['away_team'])} @ {team_label(new_game['home_team'])}**\n"
            f"**First pitch:** {new_game['game_time']}\n\n"
            f"Message too long. Showing updated lineups only.\n\n"
            + lineup_to_text(
                f"UPDATED {team_label(new_game['away_team'])} LINEUP",
                new_game.get("away_lineup", []),
            )
            + "\n\n"
            + lineup_to_text(
                f"UPDATED {team_label(new_game['home_team'])} LINEUP",
                new_game.get("home_lineup", []),
            )
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
    print(f"Loaded watched pitchers: {len(WATCHED_PITCHERS)}")
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
