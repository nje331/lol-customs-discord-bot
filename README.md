# 🎮 LoL Custom Game Bot

A Discord bot for running League of Legends custom 5v5 (or more) games in your server.

---

## Setup

### 1. Prerequisites
- Python 3.11+
- A Discord bot token ([create one here](https://discord.com/developers/applications))

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your bot token
```bash
cp .env.example .env
# Edit .env and paste your token
```

### 4. Required Discord Bot Permissions
In the Developer Portal, enable these **Privileged Gateway Intents**:
- ✅ Server Members Intent
- ✅ Message Content Intent

Bot needs these **permissions** in your server:
- Move Members (to split into voice channels)
- Send Messages / Embed Links
- Use Slash Commands

### 5. Run
```bash
python bot.py
```

The bot will sync slash commands on startup. They may take up to an hour to appear in Discord.

---

## Initial Admin Setup

1. **Configure voice channels** → `/configure_channels`  
   Set your "Team 1", "Team 2", and "Lobby/Return" voice channels.

2. **Set player weights** (optional) → `/set_weight @player 7`  
   Power weights are 1–10 and only affect team balancing when power rankings are enabled.

3. **Toggle features** → `/toggle_setting`
   - Power Rankings (off by default — only visible/usable by admins)
   - Track Session Roles (avoid giving the same role twice in one session)

---

## Typical Game Flow

```
/start_session
  → /add_from_voice #lobby-vc          (grabs everyone in a VC)
  → /add_player @someone               (add extras)
  → /session_players                   (review roster)

/make_teams                            (random split + role assignment)
  OR
/start_draft                           (captain snake draft)

  [Bot moves players into Team 1/Team 2 VC automatically]

  [Game is played]

  [Bot posts "🔵 Team 1 Won / 🔴 Team 2 Won" buttons]
  → Click winner → stats updated → everyone moved back to lobby → ready for next game

/end_session                           (when done for the day)
```

---

## All Commands

### Player Commands (anyone)
| Command | Description |
|---------|-------------|
| `/register` | Register and set role preferences (priority order) |
| `/edit_roles` | Update your role preferences |
| `/unregister` | Remove yourself from the database |
| `/stats [member]` | View win/loss stats |
| `/leaderboard` | Server win-rate leaderboard |
| `/random_roles` | Get a randomly assigned role for yourself |
| `/session_players` | View current session roster |
| `/lol_help` | Show all commands |

### Admin Commands
| Command | Description |
|---------|-------------|
| `/start_session` | Start a new session (prompts to close existing one) |
| `/end_session` | End current session |
| `/add_from_voice [channel]` | Add all VC members to session |
| `/add_player [member]` | Add one player |
| `/remove_player [member]` | Remove one player |
| `/clear_players` | Reset the roster |
| `/make_teams [assign_roles] [use_power]` | Random teams + optional role assignment |
| `/start_draft` | Captain snake draft with player+role display |
| `/random_roles all` | Reassign all player roles |
| `/settings` | View server settings |
| `/configure_channels` | Set team/lobby voice channels |
| `/toggle_setting` | Toggle features |
| `/admin_register [member]` | Manually register someone |
| `/set_weight [member] [1-10]` | Set power ranking weight |
| `/view_weights` | View all weights (ephemeral, admin-only) |

---

## Features

### Role Preference System
Players select up to 5 roles in priority order (Top → Jungle → Mid → ADC → Support).
The bot tries to honor preferences and avoids duplicates. Players with no preference are
assigned whatever is left.

### Session Role Tracking
When enabled via `/toggle_setting`, the bot remembers which roles each player has been
assigned this session and avoids repeating them. Once all 5 have been played, it resets.

### Power Rankings
- Admin-only feature, **off by default**
- Each player has a weight (1–10, default 5)
- When enabled for `/make_teams`, the bot snake-drafts by weight (1st pick team1, 2nd team2, 3rd team1, etc.) for more balanced games
- Weights are **never shown to non-admins**

### Captain Draft
`/start_draft` opens an interactive snake draft:
1. Admin selects Team 1 captain
2. Admin selects Team 2 captain  
3. Captains alternate picking (Team 1 → Team 2 → Team 2 → Team 1 → ...) — snake format
4. Player role preferences shown during picks
5. Roles auto-assigned after all players are picked

### Voice Channel Management
After teams are set, the bot automatically moves players into their team's VC.
After a winner is declared, everyone is moved back to the lobby channel.

---

## File Structure
```
lol-bot/
├── bot.py           # Entry point
├── database.py      # SQLite via aiosqlite
├── utils.py         # Constants and helpers
├── requirements.txt
├── .env             # Your bot token (never commit this)
└── cogs/
    ├── players.py   # Registration, stats, leaderboard
    ├── session.py   # Session management, player lists
    ├── teams.py     # Team formation, draft, voice moving, results
    └── settings.py  # Channel config, feature toggles, help
```

---

## Future Ideas
- Champion randomizer per role (requires Riot Data Dragon API — free, no auth needed)
- Per-session stat summaries
- Role-specific leaderboards
- `/rematch` command to flip teams and play again
