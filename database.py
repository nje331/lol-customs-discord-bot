"""
Database module - handles all SQLite operations via aiosqlite.
"""

import aiosqlite
import json
from typing import Optional


class Database:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    @classmethod
    async def create(cls, path: str) -> "Database":
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        instance = cls(db)
        await instance._init_schema()
        return instance

    async def _init_schema(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                discord_id   TEXT NOT NULL,
                guild_id     TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role_prefs   TEXT DEFAULT '[]',
                games_played INTEGER DEFAULT 0,
                games_won    INTEGER DEFAULT 0,
                games_lost   INTEGER DEFAULT 0,
                PRIMARY KEY (discord_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS bot_admins (
                discord_id   TEXT NOT NULL,
                guild_id     TEXT NOT NULL,
                PRIMARY KEY (discord_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id             TEXT PRIMARY KEY,
                team1_channel_id     TEXT,
                team2_channel_id     TEXT,
                lobby_channel_id     TEXT,
                mod_channel_id       TEXT,
                champ_weight_enabled INTEGER DEFAULT 0,
                champ_rerolls        INTEGER DEFAULT 0,
                peer_ratings_enabled INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     TEXT NOT NULL,
                owner_id     TEXT NOT NULL DEFAULT '',
                started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at     DATETIME,
                is_active    INTEGER DEFAULT 1,
                game_number  INTEGER DEFAULT 0,
                repeat_roles INTEGER DEFAULT 0,
                auto_balance TEXT DEFAULT 'off'
            );

            CREATE TABLE IF NOT EXISTS session_players (
                session_id   INTEGER,
                discord_id   TEXT,
                guild_id     TEXT,
                PRIMARY KEY (session_id, discord_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS session_role_history (
                session_id   INTEGER,
                discord_id   TEXT,
                guild_id     TEXT,
                role         TEXT,
                PRIMARY KEY (session_id, discord_id, guild_id, role)
            );

            CREATE TABLE IF NOT EXISTS session_captain_history (
                session_id   INTEGER,
                discord_id   TEXT,
                guild_id     TEXT,
                PRIMARY KEY (session_id, discord_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS games (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   INTEGER,
                guild_id     TEXT,
                game_number  INTEGER,
                team1_ids    TEXT,
                team2_ids    TEXT,
                winner_team  INTEGER
            );

            CREATE TABLE IF NOT EXISTS champions (
                champ_id   TEXT NOT NULL,
                name       TEXT NOT NULL,
                role       TEXT NOT NULL,
                play_rate  REAL DEFAULT 0,
                patch      TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (champ_id, role)
            );

            CREATE TABLE IF NOT EXISTS game_champ_rerolls (
                game_id      INTEGER NOT NULL,
                discord_id   TEXT NOT NULL,
                rerolls_used INTEGER DEFAULT 0,
                PRIMARY KEY (game_id, discord_id)
            );

            -- ELO ratings per player per guild, one row per elo_type
            -- elo_type: 'total' | 'roles_pref' | 'roles_random' | 'no_roles'
            --           | 'champs_roles_pref' | 'champs_roles_random' | 'draft'
            CREATE TABLE IF NOT EXISTS player_elo (
                discord_id TEXT NOT NULL,
                guild_id   TEXT NOT NULL,
                elo_type   TEXT NOT NULL,
                elo        REAL DEFAULT 1500.0,
                wins       INTEGER DEFAULT 0,
                losses     INTEGER DEFAULT 0,
                games      INTEGER DEFAULT 0,
                PRIMARY KEY (discord_id, guild_id, elo_type)
            );

            -- Full ELO history: one row per game per player per elo_type
            CREATE TABLE IF NOT EXISTS elo_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id  TEXT NOT NULL,
                guild_id    TEXT NOT NULL,
                elo_type    TEXT NOT NULL,
                elo_after   REAL NOT NULL,
                game_id     INTEGER NOT NULL,
                recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Aggregated peer ratings received by each player
            CREATE TABLE IF NOT EXISTS player_ratings (
                discord_id   TEXT NOT NULL,
                guild_id     TEXT NOT NULL,
                rating_sum   REAL DEFAULT 0,
                rating_count INTEGER DEFAULT 0,
                PRIMARY KEY (discord_id, guild_id)
            );

            -- Engagement: tracks how much each rater participates
            CREATE TABLE IF NOT EXISTS rating_engagement (
                discord_id         TEXT NOT NULL,
                guild_id           TEXT NOT NULL,
                ratings_given      INTEGER DEFAULT 0,
                rating_sum_given   REAL DEFAULT 0,
                games_with_ratings INTEGER DEFAULT 0,
                PRIMARY KEY (discord_id, guild_id)
            );
        """)
        await self.db.commit()

    # ── Bot Admins ───────────────────────────────────────────────────────────

    async def is_bot_admin(self, discord_id: str, guild_id: str) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM bot_admins WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def add_bot_admin(self, discord_id: str, guild_id: str):
        await self.db.execute(
            "INSERT OR IGNORE INTO bot_admins VALUES (?, ?)", (discord_id, guild_id)
        )
        await self.db.commit()

    async def remove_bot_admin(self, discord_id: str, guild_id: str):
        await self.db.execute(
            "DELETE FROM bot_admins WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        )
        await self.db.commit()

    async def get_bot_admins(self, guild_id: str) -> list[str]:
        async with self.db.execute(
            "SELECT discord_id FROM bot_admins WHERE guild_id=?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r["discord_id"] for r in rows]

    # ── Player CRUD ──────────────────────────────────────────────────────────

    async def upsert_player(self, discord_id: str, guild_id: str, display_name: str,
                             role_prefs: list = None):
        await self.db.execute("""
            INSERT INTO players (discord_id, guild_id, display_name, role_prefs)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                display_name = excluded.display_name,
                role_prefs   = COALESCE(?, role_prefs)
        """, (
            discord_id, guild_id, display_name,
            json.dumps(role_prefs or []),
            json.dumps(role_prefs) if role_prefs is not None else None
        ))
        await self.db.commit()

    async def get_player(self, discord_id: str, guild_id: str) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM players WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                d["role_prefs"] = json.loads(d["role_prefs"])
                return d
            return None

    async def update_player_roles(self, discord_id: str, guild_id: str, role_prefs: list):
        await self.db.execute(
            "UPDATE players SET role_prefs=? WHERE discord_id=? AND guild_id=?",
            (json.dumps(role_prefs), discord_id, guild_id)
        )
        await self.db.commit()

    async def delete_player(self, discord_id: str, guild_id: str):
        await self.db.execute(
            "DELETE FROM players WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        )
        await self.db.commit()

    async def get_all_players(self, guild_id: str) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM players WHERE guild_id=? ORDER BY display_name",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["role_prefs"] = json.loads(d["role_prefs"])
                result.append(d)
            return result

    async def increment_games(self, discord_id: str, guild_id: str, won: bool):
        col = "games_won" if won else "games_lost"
        await self.db.execute(
            f"UPDATE players SET games_played = games_played+1, {col} = {col}+1 "
            "WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        )
        await self.db.commit()

    # ── Session ──────────────────────────────────────────────────────────────

    async def get_active_session(self, guild_id: str) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM sessions WHERE guild_id=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def create_session(self, guild_id: str, owner_id: str, repeat_roles: bool = False,
                              auto_balance: str = "off") -> int:
        cursor = await self.db.execute(
            "INSERT INTO sessions (guild_id, owner_id, repeat_roles, auto_balance) VALUES (?, ?, ?, ?)",
            (guild_id, owner_id, 1 if repeat_roles else 0, auto_balance)
        )
        await self.db.commit()
        return cursor.lastrowid

    async def end_session(self, session_id: int):
        await self.db.execute(
            "UPDATE sessions SET is_active=0, ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (session_id,)
        )
        await self.db.commit()

    async def update_session(self, session_id: int, **kwargs):
        """Update arbitrary session columns. kwargs keys must be valid column names."""
        for key, value in kwargs.items():
            await self.db.execute(
                f"UPDATE sessions SET {key}=? WHERE id=?",
                (value, session_id)
            )
        await self.db.commit()

    async def increment_session_game(self, session_id: int):
        await self.db.execute(
            "UPDATE sessions SET game_number=game_number+1 WHERE id=?",
            (session_id,)
        )
        await self.db.commit()

    # ── Session Players ──────────────────────────────────────────────────────

    async def add_session_player(self, session_id: int, discord_id: str, guild_id: str,
                                  display_name: str = None):
        await self.db.execute(
            "INSERT OR IGNORE INTO session_players VALUES (?, ?, ?)",
            (session_id, discord_id, guild_id)
        )
        if display_name:
            await self.db.execute(
                "UPDATE players SET display_name=? WHERE discord_id=? AND guild_id=?",
                (display_name, discord_id, guild_id)
            )
        await self.db.commit()

    async def remove_session_player(self, session_id: int, discord_id: str, guild_id: str):
        await self.db.execute(
            "DELETE FROM session_players WHERE session_id=? AND discord_id=? AND guild_id=?",
            (session_id, discord_id, guild_id)
        )
        await self.db.commit()

    async def get_session_players(self, session_id: int, guild_id: str) -> list[dict]:
        async with self.db.execute("""
            SELECT p.* FROM players p
            JOIN session_players sp ON sp.discord_id = p.discord_id AND sp.guild_id = p.guild_id
            WHERE sp.session_id = ? AND p.guild_id = ?
        """, (session_id, guild_id)) as cursor:
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["role_prefs"] = json.loads(d["role_prefs"])
                result.append(d)
            return result

    # ── Session Role History ─────────────────────────────────────────────────

    async def add_role_history(self, session_id: int, discord_id: str, guild_id: str, role: str):
        await self.db.execute(
            "INSERT OR IGNORE INTO session_role_history VALUES (?, ?, ?, ?)",
            (session_id, discord_id, guild_id, role)
        )
        await self.db.commit()

    async def get_played_roles(self, session_id: int, discord_id: str, guild_id: str) -> list:
        async with self.db.execute(
            "SELECT role FROM session_role_history WHERE session_id=? AND discord_id=? AND guild_id=?",
            (session_id, discord_id, guild_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r["role"] for r in rows]

    # ── Session Captain History ──────────────────────────────────────────────

    async def add_captain(self, session_id: int, discord_id: str, guild_id: str):
        await self.db.execute(
            "INSERT OR IGNORE INTO session_captain_history VALUES (?, ?, ?)",
            (session_id, discord_id, guild_id)
        )
        await self.db.commit()

    async def get_past_captains(self, session_id: int, guild_id: str) -> list[str]:
        """Returns list of discord_ids who have been captain this session."""
        async with self.db.execute(
            "SELECT discord_id FROM session_captain_history WHERE session_id=? AND guild_id=?",
            (session_id, guild_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r["discord_id"] for r in rows]

    # ── Games ────────────────────────────────────────────────────────────────

    async def create_game(self, session_id: int, guild_id: str, game_number: int,
                           team1_ids: list, team2_ids: list) -> int:
        cursor = await self.db.execute(
            "INSERT INTO games (session_id, guild_id, game_number, team1_ids, team2_ids) VALUES (?, ?, ?, ?, ?)",
            (session_id, guild_id, game_number, json.dumps(team1_ids), json.dumps(team2_ids))
        )
        await self.db.commit()
        return cursor.lastrowid

    async def set_game_winner(self, game_id: int, winner_team: int):
        await self.db.execute(
            "UPDATE games SET winner_team=? WHERE id=?", (winner_team, game_id)
        )
        await self.db.commit()

    async def get_game(self, game_id: int) -> Optional[dict]:
        async with self.db.execute("SELECT * FROM games WHERE id=?", (game_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                d["team1_ids"] = json.loads(d["team1_ids"])
                d["team2_ids"] = json.loads(d["team2_ids"])
                return d
            return None

    async def get_leaderboard(self, guild_id: str) -> list[dict]:
        async with self.db.execute("""
            SELECT *,
                   CASE WHEN games_played > 0
                        THEN ROUND(CAST(games_won AS FLOAT)/games_played*100, 1)
                        ELSE 0 END AS win_rate
            FROM players
            WHERE guild_id=? AND games_played > 0
            ORDER BY win_rate DESC, games_played DESC
        """, (guild_id,)) as cursor:
            rows = await cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["role_prefs"] = json.loads(d["role_prefs"])
                result.append(d)
            return result

    # ── Guild Settings ───────────────────────────────────────────────────────

    async def get_settings(self, guild_id: str) -> dict:
        async with self.db.execute(
            "SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {
                "guild_id": guild_id,
                "team1_channel_id": None,
                "team2_channel_id": None,
                "lobby_channel_id": None,
                "mod_channel_id": None,
                "champ_weight_enabled": 0,
                "champ_rerolls": 0,
                "peer_ratings_enabled": 0,
            }

    async def update_setting(self, guild_id: str, key: str, value):
        await self.db.execute("""
            INSERT INTO guild_settings (guild_id)
            VALUES (?)
            ON CONFLICT(guild_id) DO NOTHING
        """, (guild_id,))
        await self.db.execute(
            f"UPDATE guild_settings SET {key}=? WHERE guild_id=?",
            (value, guild_id)
        )
        await self.db.commit()

    # ── Champions ────────────────────────────────────────────────────────────

    async def clear_champions(self):
        """Delete all champion entries — called before a fresh patch sync."""
        await self.db.execute("DELETE FROM champions")
        await self.db.commit()

    async def upsert_champion(self, champ_id: str, name: str, role: str,
                               play_rate: float, patch: str):
        await self.db.execute("""
            INSERT INTO champions (champ_id, name, role, play_rate, patch, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(champ_id, role) DO UPDATE SET
                name       = excluded.name,
                play_rate  = excluded.play_rate,
                patch      = excluded.patch,
                updated_at = CURRENT_TIMESTAMP
        """, (champ_id, name, role, play_rate, patch))

    async def commit(self):
        await self.db.commit()

    async def get_champions_for_role(self, role: str, limit: int = 10) -> list[dict]:
        """Return top champions for a role sorted by play rate."""
        async with self.db.execute("""
            SELECT * FROM champions WHERE role=? ORDER BY play_rate DESC LIMIT ?
        """, (role.upper(), limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_champion(self, name: str) -> list[dict]:
        """Return all role entries for a champion (case-insensitive partial match)."""
        async with self.db.execute("""
            SELECT * FROM champions WHERE name LIKE ? ORDER BY play_rate DESC
        """, (f"%{name}%",)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_champion_patch(self) -> str | None:
        """Return the patch string the champion data was last synced for."""
        async with self.db.execute(
            "SELECT patch FROM champions ORDER BY updated_at DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row["patch"] if row else None

    # ── Champion Rerolls ─────────────────────────────────────────────────────

    async def get_champ_rerolls_used(self, game_id: int, discord_id: str) -> int:
        async with self.db.execute(
            "SELECT rerolls_used FROM game_champ_rerolls WHERE game_id=? AND discord_id=?",
            (game_id, discord_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row["rerolls_used"] if row else 0

    async def increment_champ_reroll(self, game_id: int, discord_id: str):
        await self.db.execute("""
            INSERT INTO game_champ_rerolls (game_id, discord_id, rerolls_used)
            VALUES (?, ?, 1)
            ON CONFLICT(game_id, discord_id) DO UPDATE SET
                rerolls_used = rerolls_used + 1
        """, (game_id, discord_id))
        await self.db.commit()

    # ── Peer Ratings ─────────────────────────────────────────────────────────

    async def add_rating(self, rated_id: str, rater_id: str, guild_id: str, score: float):
        await self.db.execute("""
            INSERT INTO player_ratings (discord_id, guild_id, rating_sum, rating_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                rating_sum   = rating_sum + excluded.rating_sum,
                rating_count = rating_count + 1
        """, (rated_id, guild_id, score))

        await self.db.execute("""
            INSERT INTO rating_engagement (discord_id, guild_id, ratings_given, rating_sum_given, games_with_ratings)
            VALUES (?, ?, 1, ?, 0)
            ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                ratings_given    = ratings_given + 1,
                rating_sum_given = rating_sum_given + excluded.rating_sum_given
        """, (rater_id, guild_id, score))

        await self.db.commit()

    async def finish_rating_session(self, rater_id: str, guild_id: str):
        """Increment games_with_ratings for the rater after they complete a full rating flow."""
        await self.db.execute("""
            INSERT INTO rating_engagement (discord_id, guild_id, ratings_given, rating_sum_given, games_with_ratings)
            VALUES (?, ?, 0, 0, 1)
            ON CONFLICT(discord_id, guild_id) DO UPDATE SET
                games_with_ratings = games_with_ratings + 1
        """, (rater_id, guild_id))
        await self.db.commit()

    async def get_player_rating(self, discord_id: str, guild_id: str) -> dict:
        async with self.db.execute(
            "SELECT * FROM player_ratings WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"discord_id": discord_id, "guild_id": guild_id,
                    "rating_sum": 0, "rating_count": 0}

    async def get_rating_engagement(self, discord_id: str, guild_id: str) -> dict:
        async with self.db.execute(
            "SELECT * FROM rating_engagement WHERE discord_id=? AND guild_id=?",
            (discord_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {
                "discord_id": discord_id, "guild_id": guild_id,
                "ratings_given": 0, "rating_sum_given": 0, "games_with_ratings": 0
            }

    async def get_all_ratings(self, guild_id: str) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM player_ratings WHERE guild_id=? ORDER BY rating_count DESC",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_engagement(self, guild_id: str) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM rating_engagement WHERE guild_id=? ORDER BY ratings_given DESC",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── ELO ──────────────────────────────────────────────────────────────────

    async def get_player_elo(self, discord_id: str, guild_id: str, elo_type: str) -> dict:
        """Return ELO row for a player/guild/type, returning a default 1500 if absent."""
        async with self.db.execute(
            "SELECT * FROM player_elo WHERE discord_id=? AND guild_id=? AND elo_type=?",
            (discord_id, guild_id, elo_type)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"discord_id": discord_id, "guild_id": guild_id, "elo_type": elo_type,
                    "elo": 1500.0, "wins": 0, "losses": 0, "games": 0}

    async def get_all_elos(self, guild_id: str) -> list[dict]:
        """Return all ELO rows for a guild."""
        async with self.db.execute(
            "SELECT * FROM player_elo WHERE guild_id=? ORDER BY elo_type, elo DESC",
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_player_elo(self, discord_id: str, guild_id: str, elo_type: str,
                                 new_elo: float, won: bool):
        """Upsert an ELO value and increment win/loss/games counters."""
        w_col = "wins" if won else "losses"
        await self.db.execute(f"""
            INSERT INTO player_elo (discord_id, guild_id, elo_type, elo, {w_col}, games)
            VALUES (?, ?, ?, ?, 1, 1)
            ON CONFLICT(discord_id, guild_id, elo_type) DO UPDATE SET
                elo     = excluded.elo,
                {w_col} = {w_col} + 1,
                games   = games + 1
        """, (discord_id, guild_id, elo_type, new_elo))
        await self.db.commit()

    async def record_elo_history(self, discord_id: str, guild_id: str, elo_type: str,
                                  elo_after: float, game_id: int):
        """Append one ELO history entry."""
        await self.db.execute(
            "INSERT INTO elo_history (discord_id, guild_id, elo_type, elo_after, game_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (discord_id, guild_id, elo_type, elo_after, game_id)
        )
        await self.db.commit()

    async def get_elo_history(self, guild_id: str, discord_id: str = None,
                               elo_type: str = "total") -> list[dict]:
        """Return ELO history rows ordered by game_id. Optionally filter by player."""
        if discord_id:
            async with self.db.execute(
                "SELECT * FROM elo_history "
                "WHERE guild_id=? AND discord_id=? AND elo_type=? ORDER BY game_id ASC",
                (guild_id, discord_id, elo_type)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self.db.execute(
                "SELECT * FROM elo_history WHERE guild_id=? AND elo_type=? ORDER BY game_id ASC",
                (guild_id, elo_type)
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Stats Reset ──────────────────────────────────────────────────────────

    async def reset_leaderboard(self, guild_id: str):
        """Reset all players' win/loss stats, ELOs back to 1500, wipe ELO history,
        and clear all peer ratings and engagement data."""
        await self.db.execute(
            "UPDATE players SET games_played=0, games_won=0, games_lost=0 WHERE guild_id=?",
            (guild_id,)
        )
        await self.db.execute(
            "UPDATE player_elo SET elo=1500.0, wins=0, losses=0, games=0 WHERE guild_id=?",
            (guild_id,)
        )
        await self.db.execute(
            "DELETE FROM elo_history WHERE guild_id=?",
            (guild_id,)
        )
        await self.db.execute(
            "DELETE FROM player_ratings WHERE guild_id=?",
            (guild_id,)
        )
        await self.db.execute(
            "DELETE FROM rating_engagement WHERE guild_id=?",
            (guild_id,)
        )
        await self.db.commit()