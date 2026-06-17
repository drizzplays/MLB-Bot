# MLB Live Pitcher BvP Bot

This bot checks live MLB games every 10 minutes through GitHub Actions.
When a new active pitcher appears, it checks the batting team's lineup against that pitcher using MLB StatsAPI BvP data.
If the matchup passes the configured filters, it can post a Discord alert and a signed website notification.

## What it does

- Finds today's MLB schedule.
- Ignores days with no MLB games.
- Starts checking around first pitch.
- Checks live games only.
- Detects active pitcher changes from the live game feed.
- Pulls batter-vs-pitcher stats dynamically. No CSV database needed.
- Sends Discord alerts for strong BvP spots.
- Can send the same alert to your SheetWagers website notification system.
- Saves state to `state/live_bvp_state.json` so it does not spam duplicate alerts.

## Required GitHub secret

Add this repo secret:

```text
DISCORD_WEBHOOK_BVP
ROYALTYWAGERS_WEBHOOK  # optional secondary Discord webhook
```

Value: your Discord webhook URL. `ROYALTYWAGERS_WEBHOOK` is optional, but if you want the same alerts posted into the RoyaltyWagers Discord, this secret must exist and be passed by the workflow.

For pitcher-change notifications, add:

```text
PITCHER_WEBHOOK_URL
ROYALTYWAGERS_WEBHOOK  # optional secondary Discord webhook
WEBSITE_NOTIFY_URL
WEBSITE_NOTIFY_SECRET
```

`PITCHER_WEBHOOK_URL` keeps Discord alerts working.

`WEBSITE_NOTIFY_URL` should point at your website bot-notification endpoint, for example:

```text
https://sheetwagers.com/api/public/bot-notifications
```

`WEBSITE_NOTIFY_SECRET` must match the webhook secret your website uses to verify `x-sbh-signature`.
If `WEBSITE_NOTIFY_URL` is not set, the bot still runs and only posts to Discord.

The pitcher-change bot sends:

```json
{
  "type": "pitcher_change",
  "category": "pitching_changes",
  "notification_category": "pitching_changes"
}
```

The lineup bot alerts when a team lineup is first posted. It sends the user-facing posted-lineup alert to the primary lineup Discord hook only. It no longer sends lineup alerts to `ROYALTYWAGERS_WEBHOOK`.

```json
{
  "type": "lineup_posted",
  "category": "lineup_changes",
  "notification_category": "lineup_changes"
}
```

The lineup bot also sends a website-only slate sync every run so the website can show every checked game, confirmed lineups, pending lineups, complete games, partial games, and the exact teams still missing lineups before confirmation.

```json
{
  "type": "lineup_status_snapshot",
  "category": "lineup_changes",
  "notification_category": "lineup_changes",
  "notification_behavior": "state_sync",
  "suppress_user_notification": true
}
```

The `lineup_changes` category is intentionally preserved so the existing SheetWagers notification feed does not need a backend/category change.

GitHub path:

```text
Repo -> Settings -> Secrets and variables -> Actions -> New repository secret
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
