"""
Database module - handles all SQLite operations via aiosqlite.
"""

import aiosqlite
import json
import os
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
                power_weight REAL DEFAULT 5.0,
                PRIMARY KEY (discord_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS bot_admins (
                discord_id   TEXT NOT NULL,
                guild_id     TEXT NOT NULL,
                PRIMARY KEY (discord_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id            TEXT PRIMARY KEY,
                team1_channel_id    TEXT,
                team2_channel_id    TEXT,
                lobby_channel_id    TEXT,
                use_power_rankings  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id         TEXT NOT NULL,
                owner_id         TEXT NOT NULL DEFAULT '',
                started_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at         DATETIME,
                is_active        INTEGER DEFAULT 1,
                game_number      INTEGER DEFAULT 0,
                track_roles      INTEGER DEFAULT 1
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
                champ_id     TEXT NOT NULL,   -- numeric champion ID as string
                name         TEXT NOT NULL,
                role         TEXT NOT NULL,   -- TOP, JUNGLE, MIDDLE, BOTTOM, SUPPORT
                play_rate    REAL DEFAULT 0,
                patch        TEXT NOT NULL,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (champ_id, role)
            );
        """)
        # Safe migrations for existing databases
        for migration in [
            "ALTER TABLE sessions ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN track_roles INTEGER DEFAULT 1",
        ]:
            try:
                await self.db.execute(migration)
                await self.db.commit()
            except Exception:
                pass
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
            "DELETE FROM bot_admins WHERE discord_id=? AND guild_id=?", (discord_id, guild_id)
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

    async def update_player_weight(self, discord_id: str, guild_id: str, weight: float):
        await self.db.execute(
            "UPDATE players SET power_weight=? WHERE discord_id=? AND guild_id=?",
            (weight, discord_id, guild_id)
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

    async def create_session(self, guild_id: str, owner_id: str, track_roles: bool = True) -> int:
        cursor = await self.db.execute(
            "INSERT INTO sessions (guild_id, owner_id, track_roles) VALUES (?, ?, ?)",
            (guild_id, owner_id, 1 if track_roles else 0)
        )
        await self.db.commit()
        return cursor.lastrowid

    async def end_session(self, session_id: int):
        await self.db.execute(
            "UPDATE sessions SET is_active=0, ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (session_id,)
        )
        await self.db.commit()

    async def increment_session_game(self, session_id: int):
        await self.db.execute(
            "UPDATE sessions SET game_number=game_number+1 WHERE id=?",
            (session_id,)
        )
        await self.db.commit()

    # ── Session Players ──────────────────────────────────────────────────────

    async def add_session_player(self, session_id: int, discord_id: str, guild_id: str):
        await self.db.execute(
            "INSERT OR IGNORE INTO session_players VALUES (?, ?, ?)",
            (session_id, discord_id, guild_id)
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
                "use_power_rankings": 0,
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
            SELECT * FROM champions
            WHERE role = ?
            ORDER BY play_rate DESC
            LIMIT ?
        """, (role.upper(), limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_champion(self, name: str) -> list[dict]:
        """Return all role entries for a champion (case-insensitive partial match)."""
        async with self.db.execute("""
            SELECT * FROM champions
            WHERE name LIKE ?
            ORDER BY play_rate DESC
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