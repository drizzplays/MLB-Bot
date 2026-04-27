# MLB Live Pitcher BvP Bot

This bot checks live MLB games every 10 minutes through GitHub Actions.
When a new active pitcher appears, it checks the batting team's lineup against that pitcher using MLB StatsAPI BvP data.
If the matchup passes the configured filters, it posts a Discord alert.

## What it does

- Finds today's MLB schedule.
- Ignores days with no MLB games.
- Starts checking around first pitch.
- Checks live games only.
- Detects active pitcher changes from the live game feed.
- Pulls batter-vs-pitcher stats dynamically. No CSV database needed.
- Sends Discord alerts for strong BvP spots.
- Saves state to `state/live_bvp_state.json` so it does not spam duplicate alerts.

## Required GitHub secret

Add this repo secret:

```text
DISCORD_WEBHOOK_BVP
```

Value: your Discord webhook URL.

GitHub path:

```text
Repo → Settings → Secrets and variables → Actions → New repository secret
```

## Alert filters

Default filters are in `.github/workflows/live_bvp_bot.yml`:

```yaml
MIN_BVP_AB: "3"
MIN_BVP_HITS: "2"
MIN_BVP_AVG: "0.750"
MIN_BVP_OPS: "0"
```

Examples:

- `4/4` qualifies.
- `3/4` qualifies.
- `2/3` does not qualify with default AVG because .667 is below .750.

To make it stricter for only perfect-type spots, use:

```yaml
MIN_BVP_AB: "4"
MIN_BVP_HITS: "4"
MIN_BVP_AVG: "1.000"
```

## How to run

1. Upload/push this repo to GitHub.
2. Add the `DISCORD_WEBHOOK_BVP` secret.
3. Go to Actions.
4. Open **Live Pitcher BvP Bot**.
5. Click **Run workflow** once to test.
6. Leave it on. GitHub will run it every 10 minutes.

## Important

GitHub Actions is not true live hosting. It runs on a schedule, so alerts can be delayed up to about 10 minutes.
For real 60-second live monitoring, run `python live_bvp_bot.py` on a VPS/Railway/Render instead.
