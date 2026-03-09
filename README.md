# 🎮 LoL Custom Game Bot

A Discord bot for running League of Legends custom 5v5 (or more) games in your server. Handles session management, team formation, captain drafts, role assignment, random champion assignment, ELO tracking, peer ratings, voice channel splitting, and win/loss tracking.

---

## Docker Setup (Recommended)

### 1. Prerequisites
- Docker + Docker Compose installed on your server
- A Discord bot token ([create one here](https://discord.com/developers/applications))

### 2. Configure your bot token
```bash
cp .env.example .env
nano .env   # paste your DISCORD_TOKEN
```

### 3. Start the bot
```bash
docker compose up -d --build
docker compose logs -f   # watch startup logs
```

### 4. Update after code changes
```bash
docker compose up -d --build
```

### Data persistence
Bot data is stored in `./data/lol_bot.db` — a folder right next to your `docker-compose.yml` on the host machine. This directory is **never touched by Docker**, including `docker compose down` or `docker compose down -v`. You can back it up with a simple file copy:
```bash
cp ./data/lol_bot.db ./data/lol_bot.backup.db
```

---

## Local / Manual Setup

### 1. Prerequisites
- Python 3.11+

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your bot token
```bash
cp .env.example .env
# Edit .env and paste your DISCORD_TOKEN
```

### 4. Run
```bash
python bot.py
```

---

## Discord Bot Configuration

In the [Developer Portal](https://discord.com/developers/applications), enable these **Privileged Gateway Intents**:
- ✅ Server Members Intent
- ✅ Message Content Intent

Bot needs these **server permissions**:
- Move Members *(required for auto voice channel splitting)*
- Send Messages / Embed Links
- Use Slash Commands

Slash commands sync on startup and may take up to an hour to appear globally in Discord.

---

## Initial Server Setup (Admin)

1. **Configure voice channels** → `/settings` → Configure Channels button
   Set your Team 1, Team 2, Lobby voice channels, and Mod Log text channel via dropdowns.

2. **Sync champion data** (required for random champ assignment) → `/update_champs`
   Pulls champion role stats from CommunityDragon for the current patch. Re-run after major patches.

3. **Grant bot-admin access** (optional) → `/bot_admins` → ➕ Add Admin
   Promote trusted non-Discord-admins to run bot commands.

4. **Toggle features** (optional) → `/settings`
   - **Champ Weight** button — when on, random champ picks are weighted by play rate
   - **Peer Ratings** button — when on, players receive a DM after each game to rate their teammates
   - **Rerolls** button — set how many champion rerolls each player gets per game

---

## Typical Game Flow

```
Anyone:
  /start_session [repeat_roles] [auto_balance]   starts session, you become session owner
    → [Add from Voice] button                    grab everyone from a voice channel
    → /add_player @p1 @p2 @p3                   add up to 5 players at once
    → [View Roster] button                       confirm who's in

Session Owner or Admin:
  /make_teams [assign_roles] [ignore_prefs] [random_champs]
    OR
  /start_draft                                   interactive captain snake draft

    ↓ Teams preview posted ("Game #N — Teams Ready")
    ↓ Review lineup
    [▶️ Start Game]                               moves players to team VCs, starts timer
    ↓
    [🔵 Team 1 Won] / [🔴 Team 2 Won]           click when game ends
    ↓ Stats + ELO saved, everyone moved back to lobby
    ↓
    [🎲 Random + Roles]                          next game options
    [🔀 Random, No Roles]
    [🔁 Rematch (Swap Sides)]
    [🎯 Captain Draft]

  /end_session                                   when done for the day
```

---

## Permissions Model

| Role | Who | Can Do |
|------|-----|--------|
| **Anyone** | All server members | Register, view stats, start a session |
| **Session Owner** | Whoever ran `/start_session` | Add/remove players, make teams, end session |
| **Bot Admin** | Set via `/add_bot_admin` | Everything a session owner can do, plus all admin commands |
| **Discord Admin** | Server Administrator / Manage Guild permission | Full access to everything |

> Session owners can run all session management commands even if they are not a Discord admin or bot admin. This lets you delegate game-running to anyone without giving them server permissions.

---

## All Commands

### Anyone
| Command | Description |
|---------|-------------|
| `/register` | Register and set role preferences in priority order |
| `/edit_roles` | Update your role preferences |
| `/unregister` | Remove yourself from the database |
| `/stats [member]` | View win/loss stats for yourself or another player |
| `/leaderboard` | Server win-rate leaderboard (top 15) |
| `/session_players` | View the current session roster |
| `/start_session [repeat_roles] [auto_balance]` | Start a new session — you become the session owner |
| `/lol_help` | Show available commands (context-aware: shows more if you're owner/admin) |

### Session Owner + Admins
| Command | Description |
|---------|-------------|
| `/end_session` | End the current session |
| `/session_settings` | View or change `repeat_roles` / `auto_balance` mid-session |
| `/add_from_voice [channel]` | Add all members from a voice channel to the session |
| `/add_player @p1 [@p2] [@p3] [@p4] [@p5]` | Add up to 5 players at once |
| `/remove_player [member]` | Remove a player from the session |
| `/clear_players` | Clear the entire session roster |
| `/make_teams [assign_roles] [ignore_prefs] [random_champs]` | Random team split with optional role/champion assignment |
| `/start_draft` | Captain snake draft (manual or auto captain selection) |

### Admins — Server
| Command | Description |
|---------|-------------|
| `/settings` | View and manage all server settings — channels, feature toggles, and reroll count all accessible via buttons |
| `/bot_admins` | View all current admins (Discord + bot-granted), add members via dropdown, remove bot admins via dropdown |
| `/reset_stats` | Reset all players' stats, ELOs (to 1500), and ELO history |

### Admins — Champions
| Command | Description |
|---------|-------------|
| `/update_champs` | Sync champion role stats from CommunityDragon for the current patch |
| `/view_champs [role]` | Paginated role browser showing synced + custom champions; includes inline Add/Remove buttons |
| `/add_custom_champ [name] [role]` | Add a custom champion to a role pool (persists across patch syncs) |
| `/remove_custom_champ [name] [role]` | Remove a custom champion from a role pool |
| `/clear_custom_champs` | Remove all custom champion entries for this server |

### Admins — ELO & Ratings
| Command | Description |
|---------|-------------|
| `/view_elo [type]` | View ELO leaderboard for any of the 7 tracked modes |
| `/elo_history [type] [member]` | View an ELO history chart (all players for a mode, or all modes for one player) |
| `/view_ratings` | View peer rating averages and engagement metrics for all players |

---

## Features

### Session Ownership
Any server member can run `/start_session` and becomes the **session owner** for that session. Owners can add/remove players, form teams, and end the session — no Discord permissions required. Admins and bot-admins always have the same access.

### Session Control Panel
When a session starts, a persistent control panel is posted with quick-action buttons:
- **Add from Voice** — pick a voice channel from a dropdown and grab all members
- **View Roster** — see the current player list (ephemeral)
- **End Session** — with confirmation prompt

### Role Preference System
Players select up to 5 roles in priority order (Top → Jungle → Mid → ADC → Support) via an interactive dropdown during `/register` or `/edit_roles`. The bot honors preferences when assigning roles with `/make_teams`. Players with no preference are assigned whatever is left.

### Session Role Tracking
Enabled **by default** when starting a session (`repeat_roles: False`). The bot remembers which roles each player has been assigned this session and avoids repeats. Once a player has played all 5 roles, their history resets. Toggle mid-session with `/session_settings`.

### Team Formation — Random (`/make_teams`)
Randomly splits the roster into two teams. Options:
- `assign_roles` (default `True`) — assign lane roles, respecting preferences and avoiding repeats
- `ignore_prefs` (default `False`) — assign roles randomly, ignoring preferences but still avoiding repeats
- `random_champs` (default `False`) — randomly assign a champion to each player for their role

### Team Formation — Captain Draft (`/start_draft`)
Opens an interactive snake draft:
1. Pick Team 1 captain (dropdown — rotates players who haven't been captain yet)
2. Pick Team 2 captain
3. Captains alternate picking players — snake order: 1 → 2 → 2 → 1 → 1 → 2...
4. Role preferences shown for each available player
5. Roles auto-assigned after all players are picked

Odd player counts are handled — the extra player is assigned to whichever team ends up smaller.

### Start Game Flow
After teams are formed (via random or draft), the bot posts a **Teams Ready** preview before starting:
- Review the full lineup, role assignments, and champion assignments (if enabled)
- **▶️ Start Game** — moves players into team voice channels and brings up winner buttons
- **🎲 Re-roll Teams** — reshuffle with the same player pool (no DB writes until Start is pressed)
- **✖️ Cancel** — discard without recording anything

> Role history and game records are only saved to the database when Start Game is pressed, not when teams are previewed.

### Random Champions
When `random_champs:True` is passed to `/make_teams`, each player is assigned a random champion for their role. Requires running `/update_champs` first to populate champion data.

- **Champion Weight** (toggleable via `/toggle_setting`) — when on, picks are weighted by play rate so meta champions appear more often
- **Champ Rerolls** — set a per-player reroll budget with `/set_champ_rerolls`; players can spend rerolls to get a new champion during the game

### Custom Champions
Bot admins can maintain a per-server list of custom champion entries that supplement the CommunityDragon patch data and persist across `/update_champs` syncs:

- `/view_champs [role]` — full paginated browser (Top → Support) with inline **➕ Add Custom** and **➖ Remove Custom** buttons
- `/add_custom_champ` / `/remove_custom_champ` — quick slash-command alternatives
- `/clear_custom_champs` — wipe all custom entries for the server
- Custom champions are included in the random pool alongside synced data

### Voice Channel Automation
On **Start Game**: players are moved to their team's voice channel.
On **winner declared**: all players are moved back to the lobby channel (or Team 1 channel if no lobby is set).
Requires the bot to have **Move Members** permission in your server.

### Winner & Next Game
After a game ends, click the winning team button. The bot:
- Records the win/loss for every player
- Updates ELO ratings for all players across all applicable modes
- Moves everyone back to the lobby
- Presents next-game options: 🎲 Random + Roles · 🔀 Random No Roles · 🔁 Rematch · 🎯 Captain Draft

### ELO System
ELO is tracked automatically after every game across **7 separate modes**:

| Mode Key | When it's used |
|----------|----------------|
| `total` | Every game, regardless of mode |
| `roles_pref` | Random teams, roles assigned by preference |
| `roles_random` | Random teams, roles assigned randomly |
| `no_roles` | Random teams, no role assignment |
| `champs_roles_pref` | Random teams + random champs, roles by preference |
| `champs_roles_random` | Random teams + random champs, random roles |
| `draft` | Captain snake draft |

View leaderboards with `/view_elo [type]` and history charts with `/elo_history`.

### Auto-Balance (ELO-based)
Set at session start via `/start_session [auto_balance]` or changed mid-session via `/session_settings`. Options:
- **Off** — fully random team splits (default)
- **Total ELO** — balances teams using each player's overall ELO
- **Mode ELO** — balances using the ELO specific to the draft mode being played

### Peer Ratings
When enabled via `/settings` (Peer Ratings toggle), players receive a DM after each game to rate their teammates (1–5 stars). Admins can view aggregated rating averages and engagement metrics with `/view_ratings`.

### Settings Panel (`/settings`)
`/settings` opens an interactive panel showing all current server configuration with action buttons:
- **Configure Channels** — opens dropdown selectors for Team 1/2 VCs, Lobby VC, and Mod Log text channel. Pre-populated with currently saved values
- **Champ Weight** toggle — enables/disables play-rate weighting for random champion picks
- **Peer Ratings** toggle — enables/disables post-game rating DMs
- **Rerolls** button — opens a modal to set the per-player reroll budget (0 = disabled)

### Bot Admins (`/bot_admins`)
`/bot_admins` opens an interactive panel showing:
- **Discord Admins** — members with Administrator or Manage Guild permission (always have full access, can't be removed via bot)
- **Bot Admins** — members explicitly granted access via the bot

**➕ Add Admin** — dropdown of all eligible server members (excludes bots and existing admins)
**➖ Remove Admin** — dropdown of only explicitly-added bot admins (Discord admins are not listed)

---

## Role Display Order
Team fields are always sorted by lane: **Top → Jungle → Mid → ADC → Support**, making the lineup easy to read at a glance.

---

## File Structure
```
lol-bot/
├── bot.py              # Entry point
├── database.py         # SQLite schema + all DB operations (aiosqlite)
├── utils.py            # Shared constants, role emojis, permission helpers
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env                # Your bot token (never commit this)
├── .env.example        # Template
├── data/               # Created automatically — holds lol_bot.db (gitignored)
└── cogs/
    ├── players.py      # Registration, stats, leaderboard, /bot_admins panel
    ├── session.py      # Session lifecycle, player roster management
    ├── teams.py        # Team formation, draft, VC moves, game results, ELO updates
    ├── champions.py    # Champion sync, custom champion management, /view_champs browser
    ├── elo.py          # ELO leaderboards and history charts
    └── settings.py     # /settings panel (channels, toggles, rerolls), peer ratings view, help command
```

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `players` | Registered players, role prefs, stats, power weight |
| `bot_admins` | Per-server bot admin grants |
| `guild_settings` | Voice/text channel IDs, feature flags (champ weight, rerolls, peer ratings) |
| `sessions` | Active/ended sessions, owner, repeat_roles, auto_balance setting |
| `session_players` | Which players are in each session |
| `session_role_history` | Roles played per player per session (for repeat-avoidance) |
| `session_captain_history` | Who has been captain each session (for rotation) |
| `games` | Game records with team compositions and winner |
| `champions` | Patch-synced champion/role data from CommunityDragon |
| `custom_champions` | Admin-added custom champion entries (per server, persist across syncs) |
| `game_champ_rerolls` | Per-game reroll usage tracking per player |
| `player_elo` | Current ELO per player per guild per mode (7 modes) |
| `elo_history` | Full ELO history — one row per game per player per mode |
| `player_ratings` | Aggregated peer rating scores received |
| `rating_engagement` | Peer rating participation tracking per player |

---

## Troubleshooting

**Commands not appearing in Discord**
Slash commands can take up to an hour to propagate globally. They should appear within ~1 minute in the server where the bot is present after startup.

**Bot not moving players to voice channels**
Ensure the bot role has **Move Members** permission. Also check that `/configure_channels` has been run and the correct channels are set (`/settings` to verify).

**Random champs not working**
Run `/update_champs` first to populate the champion database. If a role has no champions in the pool (no synced data and no custom entries), champ assignment will be skipped for that player.

**Database not persisting across restarts**
The database is saved to `./data/lol_bot.db` on the host machine (bind mount). Make sure you're running `docker compose up` from the directory containing `docker-compose.yml`. Never use `docker compose down -v` as this would delete named volumes (though the current config uses a bind mount which is immune to this).

**Bot goes offline / crashes**
Check logs: `docker compose logs -f`. Common causes are an invalid token in `.env` or missing Privileged Intents in the Developer Portal.

---

## Future Ideas
- Per-session stat summaries posted on `/end_session`
- Role-specific leaderboards
- MVP voting after each game