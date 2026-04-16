import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "combined_state.json"
ET = ZoneInfo("America/New_York")
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

# Replace these values with your real Discord custom emoji strings
# Example:
# "Tampa Bay Rays": "<:rays:123456789012345678>"
TEAM_EMOJIS = {
    "Arizona Diamondbacks": "⚾",
    "Atlanta Braves": "⚾",
    "Baltimore Orioles": "⚾",
    "Boston Red Sox": "⚾",
    "Chicago Cubs": "⚾",
    "Chicago White Sox": "⚾",
    "Cincinnati Reds": "⚾",
    "Cleveland Guardians": "⚾",
    "Colorado Rockies": "⚾",
    "Detroit Tigers": "⚾",
    "Houston Astros": "⚾",
    "Kansas City Royals": "⚾",
    "Los Angeles Angels": "⚾",
    "Los Angeles Dodgers": "⚾",
    "Miami Marlins": "⚾",
    "Milwaukee Brewers": "⚾",
    "Minnesota Twins": "⚾",
    "New York Mets": "⚾",
    "New York Yankees": "⚾",
    "Athletics": "⚾",
    "Philadelphia Phillies": "⚾",
    "Pittsburgh Pirates": "⚾",
    "San Diego Padres": "⚾",
    "San Francisco Giants": "⚾",
    "Seattle Mariners": "⚾",
    "St. Louis Cardinals": "⚾",
    "Tampa Bay Rays": "⚾",
    "Texas Rangers": "⚾",
    "Toronto Blue Jays": "⚾",
    "Washington Nationals": "⚾",
}

# Edit this list however you want
STAR_PLAYERS = {
    "Aaron Judge",
    "Shohei Ohtani",
    "Juan Soto",
    "Mookie Betts",
    "Ronald Acuña Jr.",
    "Bobby Witt Jr.",
    "Yordan Alvarez",
    "Fernando Tatis Jr.",
    "Freddie Freeman",
    "Corey Seager",
    "Bryce Harper",
    "Gunnar Henderson",
    "Julio Rodríguez",
    "Vladimir Guerrero Jr.",
    "Kyle Tucker",
    "Elly De La Cruz",
    "José Ramírez",
    "Francisco Lindor",
    "Pete Alonso",
    "Adley Rutschman",
}

# If True, checks both today and tomorrow
CHECK_TOMORROW = True


def team_label(team_name):
    emoji = TEAM_EMOJIS.get(team_name, "⚾")
    return f"{emoji} {team_name.upper()}"


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
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
    r.raise_for_status()


def format_game_time(game_dt):
    return game_dt.strftime("%Y-%m-%d %I:%M %p ET")


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


def get_games_for_date(target_date):
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
                game_time_et = format_game_time(game_dt)
                game_dt_et_iso = game_dt.isoformat()
            except Exception:
                game_time_et = game_date_raw
                game_dt_et_iso = None

            away_lineup = []
            home_lineup = []

            boxscore_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
            try:
                br = requests.get(boxscore_url, timeout=20)
                br.raise_for_status()
                boxscore = br.json()

                teams = boxscore.get("teams", {})
                away_team_data = teams.get("away", {})
                home_team_data = teams.get("home", {})

                away_lineup = extract_lineup(away_team_data)
                home_lineup = extract_lineup(home_team_data)
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
                "game_time_et": game_time_et,
                "game_dt_et_iso": game_dt_et_iso,
                "game_pk": game_pk,
            }

    return games


def is_pregame(game_dt_et_iso):
    if not game_dt_et_iso:
        return True
    try:
        game_dt = datetime.fromisoformat(game_dt_et_iso)
        return datetime.now(ET) < game_dt
    except Exception:
        return True


def analyze_pitcher_changes(old_game, new_game):
    changes = []

    old_away = old_game.get("away_pitcher", "TBD")
    new_away = new_game.get("away_pitcher", "TBD")
    old_home = old_game.get("home_pitcher", "TBD")
    new_home = new_game.get("home_pitcher", "TBD")

    if old_away != new_away:
        if old_away == "TBD" and new_away != "TBD":
            changes.append(
                f"🆕 {new_game['away_team']}: probable pitcher posted — {new_away}"
            )
        else:
            changes.append(
                f"🔄 {new_game['away_team']}: {old_away} → {new_away}"
            )

    if old_home != new_home:
        if old_home == "TBD" and new_home != "TBD":
            changes.append(
                f"🆕 {new_game['home_team']}: probable pitcher posted — {new_home}"
            )
        else:
            changes.append(
                f"🔄 {new_game['home_team']}: {old_home} → {new_home}"
            )

    return changes


def analyze_lineup_changes(old_lineup, new_lineup, star_players):
    result = {
        "labels": set(),
        "changes": [],
        "star_alerts": [],
        "posted": False,
    }

    if not old_lineup and new_lineup:
        result["posted"] = True
        result["labels"].add("LINEUP POSTED")

        for i, player in enumerate(new_lineup):
            if player in star_players:
                result["star_alerts"].append(
                    f"⭐ {player} posted in lineup at {i + 1}"
                )

        return result

    if not new_lineup:
        return result

    old_positions = {player: i for i, player in enumerate(old_lineup)}
    new_positions = {player: i for i, player in enumerate(new_lineup)}

    used_old = set()
    used_new = set()

    # 1. Direct replacements in same spot
    max_len = max(len(old_lineup), len(new_lineup))
    for i in range(max_len):
        old_player = old_lineup[i] if i < len(old_lineup) else None
        new_player = new_lineup[i] if i < len(new_lineup) else None

        if old_player and new_player and old_player != new_player:
            if old_player not in new_positions and new_player not in old_positions:
                result["changes"].append(
                    f"🔄 {new_player} replaces {old_player} at {i + 1}"
                )
                result["labels"].add("LINEUP SWITCH")
                used_old.add(old_player)
                used_new.add(new_player)

                if old_player in star_players:
                    result["star_alerts"].append(
                        f"⭐ {old_player} removed from {i + 1}"
                    )
                if new_player in star_players:
                    result["star_alerts"].append(
                        f"⭐ {new_player} added at {i + 1}"
                    )

    # 2. Same player moved
    for player in new_lineup:
        if player in old_positions and player not in used_new:
            old_pos = old_positions[player]
            new_pos = new_positions[player]

            if old_pos != new_pos:
                direction = "📈" if new_pos < old_pos else "📉"
                result["changes"].append(
                    f"{direction} {player} moved from {old_pos + 1} → {new_pos + 1}"
                )
                result["labels"].add("LINEUP REARRANGED")

                if player in star_players:
                    result["star_alerts"].append(
                        f"{direction} ⭐ {player} moved from {old_pos + 1} → {new_pos + 1}"
                    )

    # 3. Additions
    for player in new_lineup:
        if player not in old_positions and player not in used_new:
            pos = new_positions[player] + 1
            result["changes"].append(f"➕ {player} added at {pos}")
            result["labels"].add("LINEUP SWITCH")

            if player in star_players:
                result["star_alerts"].append(f"⭐ {player} added at {pos}")

    # 4. Removals
    for player in old_lineup:
        if player not in new_positions and player not in used_old:
            pos = old_positions[player] + 1
            result["changes"].append(f"❌ {player} removed from {pos}")
            result["labels"].add("LINEUP SWITCH")

            if player in star_players:
                result["star_alerts"].append(f"⭐ {player} removed from {pos}")

    return result


def build_game_alert(old_game, new_game):
    if not is_pregame(new_game.get("game_dt_et_iso")):
        return None

    labels = []
    sections = []
    star_alerts = []

    pitcher_changes = analyze_pitcher_changes(old_game, new_game)
    if pitcher_changes:
        labels.append("PITCHER CHANGE")
        sections.append(
            f"**PITCHER UPDATES**\n" + "\n".join(f"- {x}" for x in pitcher_changes)
        )

    away_analysis = analyze_lineup_changes(
        old_game.get("away_lineup", []),
        new_game.get("away_lineup", []),
        STAR_PLAYERS,
    )
    home_analysis = analyze_lineup_changes(
        old_game.get("home_lineup", []),
        new_game.get("home_lineup", []),
        STAR_PLAYERS,
    )

    away_labels = away_analysis["labels"]
    home_labels = home_analysis["labels"]

    for label in ["LINEUP POSTED", "LINEUP REARRANGED", "LINEUP SWITCH"]:
        if label in away_labels or label in home_labels:
            labels.append(label)

    star_alerts.extend(away_analysis["star_alerts"])
    star_alerts.extend(home_analysis["star_alerts"])

    # Away lineup section
    if away_analysis["posted"]:
        sections.append(
            lineup_to_text(
                f"{team_label(new_game['away_team'])} LINEUP POSTED",
                new_game.get("away_lineup", []),
            )
        )
    elif away_analysis["changes"]:
        sections.append(
            f"**{team_label(new_game['away_team'])} CHANGES**\n"
            + "\n".join(f"- {x}" for x in away_analysis["changes"])
        )
        sections.append(
            lineup_to_text(
                f"UPDATED {team_label(new_game['away_team'])} LINEUP",
                new_game.get("away_lineup", []),
            )
        )

    # Home lineup section
    if home_analysis["posted"]:
        sections.append(
            lineup_to_text(
                f"{team_label(new_game['home_team'])} LINEUP POSTED",
                new_game.get("home_lineup", []),
            )
        )
    elif home_analysis["changes"]:
        sections.append(
            f"**{team_label(new_game['home_team'])} CHANGES**\n"
            + "\n".join(f"- {x}" for x in home_analysis["changes"])
        )
        sections.append(
            lineup_to_text(
                f"UPDATED {team_label(new_game['home_team'])} LINEUP",
                new_game.get("home_lineup", []),
            )
        )

    if star_alerts:
        labels.append("STAR PLAYER ALERT")
        deduped_star_alerts = list(dict.fromkeys(star_alerts))
        sections.append(
            f"**⭐ STAR PLAYER ALERT**\n"
            + "\n".join(f"- {x}" for x in deduped_star_alerts)
        )

    labels = list(dict.fromkeys(labels))

    if not labels:
        return None

    top_label = " + ".join(labels)

    msg = (
        f"🚨 **{top_label}**\n\n"
        f"**{new_game['away_team']} @ {new_game['home_team']}**\n"
        f"**First pitch:** {new_game['game_time_et']}\n\n"
        + "\n\n".join(sections)
        + "\n\n🔥 **DRIZZPLAYS**"
    )

    if len(msg) > 1900:
        msg = (
            f"🚨 **{top_label}**\n\n"
            f"**{new_game['away_team']} @ {new_game['home_team']}**\n"
            f"**First pitch:** {new_game['game_time_et']}\n\n"
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
            + "\n\n🔥 **DRIZZPLAYS**"
        )

    return msg


def process_games_for_date(target_date_str, old_games, new_games):
    alerts_sent = 0

    for key, new_game in new_games.items():
        old_game = old_games.get(key, {})

        alert = build_game_alert(old_game, new_game)

        if alert:
            try:
                send_discord_message(alert)
                alerts_sent += 1
                print("Sent alert:")
                print(alert)
                print("-" * 40)
            except Exception as e:
                print(f"Failed to send Discord alert for {key}: {e}")

    return alerts_sent


def run_check():
    now = datetime.now(ET).date()
    dates_to_check = [now]

    if CHECK_TOMORROW:
        dates_to_check.append(now + timedelta(days=1))

    state = load_state()
    total_alerts = 0

    for target_date in dates_to_check:
        date_key = str(target_date)

        print(f"Checking games for {date_key}...")
        new_games = get_games_for_date(target_date)
        old_games = state.get(date_key, {})

        print(f"Games found: {len(new_games)}")

        alerts_sent = process_games_for_date(date_key, old_games, new_games)
        total_alerts += alerts_sent

        state[date_key] = new_games

    save_state(state)
    print(f"Done. Total alerts sent: {total_alerts}")


if __name__ == "__main__":
    run_check()
