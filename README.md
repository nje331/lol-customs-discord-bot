# 🎮 LoL Custom Game Bot

A Discord bot for running League of Legends custom 5v5 (or more) games in your server. Handles session management, team formation, captain drafts, role assignment, voice channel splitting, and win/loss tracking.

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

Slash commands sync on startup and may take up to an hour to appear in Discord globally.

---

## Initial Server Setup (Admin)

1. **Configure voice channels** → `/configure_channels`
   Set your Team 1, Team 2, and Lobby voice channels via dropdown.

2. **Grant bot-admin access** (optional) → `/add_bot_admin @member`
   Promote trusted non-Discord-admins to run bot commands.

3. **Set player weights** (optional) → `/set_weight @player 7`
   Power weights are 1–10 and only matter if Power Rankings is enabled.

4. **Toggle Power Rankings** (optional) → `/toggle_setting`
   Off by default. When on, `/make_teams use_power:True` balances teams by skill weight.

---

## Typical Game Flow

```
Anyone:
  /start_session                         starts session, you become session owner
    → [Add from Voice] button            grab everyone from a voice channel
    → /add_player @p1 @p2 @p3           add up to 5 players at once
    → [View Roster] button               confirm who's in

Session Owner or Admin:
  /make_teams                            random split + role assignment
    OR
  /start_draft                           interactive captain snake draft
    OR
  /start_draft random_captains:True      auto-picks captains (rotates who hasn't been cap yet)

    ↓ Teams preview posted ("Game #N — Teams Ready")
    ↓ Review lineup
    [▶️ Start Game]                       moves players to team VCs, starts timer
    ↓
    [🔵 Team 1 Won] / [🔴 Team 2 Won]   click when game ends
    ↓ Stats saved, everyone moved back to lobby
    ↓
    [🎲 Random + Roles]                  next game options
    [🔀 Random, No Roles]
    [🔁 Rematch (Swap Sides)]
    [🎯 Captain Draft]

  /end_session                           when done for the day
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
| `/leaderboard` | Server win-rate leaderboard |
| `/session_players` | View the current session roster |
| `/start_session [track_roles]` | Start a new session — you become the session owner |
| `/lol_help` | Show available commands (context-aware: shows more if you're owner/admin) |

### Session Owner + Admins
| Command | Description |
|---------|-------------|
| `/end_session` | End the current session |
| `/add_from_voice [channel]` | Add all members from a voice channel to the session |
| `/add_player @p1 [@p2] [@p3] [@p4] [@p5]` | Add up to 5 players at once |
| `/remove_player [member]` | Remove a player from the session |
| `/clear_players` | Clear the entire session roster |
| `/make_teams [assign_roles] [use_power]` | Random team split with optional role assignment |
| `/start_draft [random_captains]` | Captain snake draft (manual or auto captain selection) |

### Admins Only
| Command | Description |
|---------|-------------|
| `/settings` | View current server settings |
| `/configure_channels` | Set Team 1, Team 2, and Lobby voice channels |
| `/toggle_setting` | Toggle Power Rankings on/off |
| `/admin_register [member]` | Manually register a player with no role preferences |
| `/set_weight [member] [1-10]` | Set a player's power ranking weight |
| `/view_weights` | View all player power weights (ephemeral) |
| `/add_bot_admin [member]` | Grant bot-admin privileges to a user |
| `/remove_bot_admin [member]` | Revoke bot-admin privileges |
| `/list_bot_admins` | List all current bot admins |

---

## Features

### Session Ownership
Any server member can run `/start_session` and becomes the **session owner** for that session. Owners can add/remove players, form teams, and end the session — no Discord permissions required. Admins and bot-admins always have the same access.

### Session Control Panel
When a session starts, a persistent control panel is posted with quick-action buttons:
- **Add from Voice** — pick a voice channel from a dropdown and grab all members
- **View Roster** — see the current player list (ephemeral)
- **Make Teams** — reminder to use `/make_teams` or `/start_draft`
- **End Session** — with confirmation prompt

### Role Preference System
Players select up to 5 roles in priority order (Top → Jungle → Mid → ADC → Support) via an interactive dropdown during `/register` or `/edit_roles`. The bot honors preferences when assigning roles. Players with no preference are assigned whatever is left.

### Session Role Tracking
Enabled **by default** when starting a session (`track_roles: True`). The bot remembers which roles each player has been assigned this session and avoids repeats. Once a player has played all 5 roles, their history resets. Disable by running `/start_session track_roles:False`.

### Team Formation — Random
`/make_teams` randomly splits the roster into two teams. Options:
- `assign_roles` (default `True`) — assign lane roles based on preferences
- `use_power` (default `False`) — balance teams using power weights (requires Power Rankings enabled)

### Team Formation — Captain Draft
`/start_draft` opens an interactive snake draft:
1. Pick Team 1 captain (dropdown)
2. Pick Team 2 captain
3. Captains alternate picking players — snake order: 1 → 2 → 2 → 1 → 1 → 2...
4. Role preferences shown for each available player
5. Roles auto-assigned after all players are picked

**Random captains** (`random_captains:True`): the bot picks captains automatically. It rotates through the roster, prioritising players who haven't been captain yet this session. Once everyone has been captain, the cycle resets.

Odd player counts are handled — the extra player is assigned to whichever team ends up smaller.

### Start Game Flow
After teams are formed (via random or draft), the bot posts a **Teams Ready** preview before starting:
- Review the full lineup and role assignments
- **▶️ Start Game** — moves players into team voice channels and brings up winner buttons
- **🎲 Re-roll Teams** — reshuffle with the same player pool (no DB writes until Start is pressed)
- **✖️ Cancel** — discard without recording anything

> Role history and game records are only saved to the database when Start Game is pressed, not when teams are previewed.

### Voice Channel Automation
On **Start Game**: players are moved to their team's voice channel.
On **winner declared**: all players are moved back to the lobby channel (or Team 1 channel if no lobby is set).
Requires the bot to have **Move Members** permission in your server.

### Winner & Next Game
After a game ends, click the winning team button. The bot:
- Records the win/loss for every player
- Moves everyone back to the lobby
- Presents next-game options:
  - 🎲 **Random Teams + Roles**
  - 🔀 **Random Teams, No Roles**
  - 🔁 **Rematch** — same teams, sides swapped
  - 🎯 **Captain Draft**

### Power Rankings
- Admin-only feature, **off by default**
- Each player has a hidden weight (1–10, default 5.0), set via `/set_weight`
- When enabled and `use_power:True` is passed to `/make_teams`, teams are snake-drafted by weight for more balanced splits
- Weights are **never visible to non-admins**

### Bot Admins (Per-Server)
Discord Admins and users with Manage Guild can always run admin commands. You can also grant bot-admin access to specific users per server with `/add_bot_admin` — useful for a dedicated "game host" role without giving them Discord server permissions.

### Leaderboard
`/leaderboard` shows the top 15 players by win rate (minimum 1 game played), with W/L counts and medals for the top 3.

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
    ├── players.py      # Registration, stats, leaderboard, bot admin management
    ├── session.py      # Session lifecycle, player roster management
    ├── teams.py        # Team formation, draft, VC moves, game results
    └── settings.py     # Channel config, feature toggles, help command
```

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `players` | Registered players, role prefs, stats, power weight |
| `bot_admins` | Per-server bot admin grants |
| `guild_settings` | Voice channel IDs, feature flags |
| `sessions` | Active/ended sessions, owner, track_roles setting |
| `session_players` | Which players are in each session |
| `session_role_history` | Roles played per player per session (for tracking) |
| `session_captain_history` | Who has been captain each session (for rotation) |
| `games` | Game records with team compositions and winner |

---

## Troubleshooting

**Commands not appearing in Discord**
Slash commands can take up to an hour to propagate globally. They should appear immediately in the server where the bot is present if you wait ~1 minute after startup.

**Bot not moving players to voice channels**
Ensure the bot role has **Move Members** permission. Also check that `/configure_channels` has been run and the correct channels are set (`/settings` to verify).

**Database not persisting across restarts**
The database is saved to `./data/lol_bot.db` on the host machine (bind mount). Make sure you're running `docker compose up` from the directory containing `docker-compose.yml`. Never use `docker compose down -v` as this would delete named volumes (though the current config uses a bind mount which is immune to this).

**Bot goes offline / crashes**
Check logs: `docker compose logs -f`. Common causes are an invalid token in `.env` or missing Privileged Intents in the Developer Portal.

---

## Future Ideas
- Champion randomizer per role (uses Riot Data Dragon API — free, no auth needed)
- Per-session stat summaries posted on `/end_session`
- Role-specific leaderboards
- MVP voting after each game