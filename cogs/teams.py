"""
Teams Cog
Handles: random teams, captain draft, role assignment, random champion assignment,
         voice moving, game results, bench management (11+ players).

Flow:
  _finalize_teams()  →  shows teams + bench + "Start Game" button  (StartGameView)
  "Start Game" click →  moves playing 10 to VCs, saves DB records, shows WinnerView
  winner click       →  saves result for playing 10 only, moves all back, shows NextGameView
  NextGameView       →  re-draft / random / rematch / random-champs (re-rolls from full session roster)
"""

import discord
from discord import app_commands
from discord.ext import commands
import random
from typing import Optional

from utils import ROLES, ROLE_EMOJIS, LANE_ORDER, build_embed, is_session_owner, sort_by_lane

TEAM_SIZE = 5   # players per team

# Map our role names → CommunityDragon role keys used in the champions table
ROLE_TO_CDR = {
    "Top":     "TOP",
    "Jungle":  "JUNGLE",
    "Mid":     "MIDDLE",
    "ADC":     "BOTTOM",
    "Support": "SUPPORT",
}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _split_players(players: list, use_power: bool) -> tuple[list, list, list]:
    """
    Split players into (team1, team2, bench).
    Team size is capped at TEAM_SIZE each (10 playing total).
    Any extras beyond 10 go to bench, randomly selected.
    """
    pool = players[:]

    if len(pool) > TEAM_SIZE * 2:
        random.shuffle(pool)
        playing = pool[:TEAM_SIZE * 2]
        bench = pool[TEAM_SIZE * 2:]
    else:
        playing = pool
        bench = []

    # Now split playing 10 into two teams
    if use_power:
        playing.sort(key=lambda p: p.get("power_weight", 5.0), reverse=True)
        team1, team2 = [], []
        for i, p in enumerate(playing):
            (team1 if i % 2 == 0 else team2).append(p)
    else:
        random.shuffle(playing)
        mid = len(playing) // 2
        team1, team2 = playing[:mid], playing[mid:]

    return team1, team2, bench


def _assign_roles(team: list, session_role_history: dict, track_roles: bool) -> dict[str, str]:
    """Returns {discord_id: role}, honouring prefs and optionally avoiding repeated roles."""
    assignment: dict[str, str] = {}
    taken_roles: set[str] = set()
    unassigned = list(team)

    for player in list(unassigned):
        prefs = player.get("role_prefs", [])
        if track_roles:
            played = session_role_history.get(player["discord_id"], [])
            available_prefs = [r for r in prefs if r not in played]
            if not available_prefs:
                available_prefs = prefs
        else:
            available_prefs = prefs

        for pref in available_prefs:
            if pref not in taken_roles:
                assignment[player["discord_id"]] = pref
                taken_roles.add(pref)
                unassigned.remove(player)
                break

    remaining_roles = [r for r in ROLES if r not in taken_roles]
    random.shuffle(remaining_roles)
    for player in unassigned:
        role = remaining_roles.pop(0) if remaining_roles else "Fill"
        assignment[player["discord_id"]] = role

    return assignment


async def _assign_champs(assignments: dict[str, str], db) -> dict[str, str]:
    """
    Given {discord_id: role}, returns {discord_id: champion_name}.
    Picks a random champion weighted by play rate for each role.
    If no champion data exists for a role, returns empty string for that player.
    Ensures no champion is assigned twice in the same game.
    """
    champ_assignment: dict[str, str] = {}
    used_champs: set[str] = set()

    for did, role in assignments.items():
        cdr_role = ROLE_TO_CDR.get(role)
        if not cdr_role:
            champ_assignment[did] = ""
            continue

        # Fetch all champions for this role ordered by play rate
        rows = await db.get_champions_for_role(cdr_role, limit=50)
        if not rows:
            champ_assignment[did] = ""
            continue

        # Filter out already-used champs, then weighted-random pick
        available = [r for r in rows if r["name"] not in used_champs]
        if not available:
            available = rows  # fallback: allow repeats if pool exhausted

        # Weighted random by play rate (higher play rate = more likely to appear)
        # weights = [max(r["play_rate"], 0.001) for r in available]
        # chosen = random.choices(available, weights=weights, k=1)[0]
        chosen = random.choices(available, k=1)[0]
        champ_assignment[did] = chosen["name"]
        used_champs.add(chosen["name"])

    return champ_assignment


def _team_field(team: list, assignments: dict,
                champ_assignments: dict | None = None) -> str:
    sorted_team = sort_by_lane(team, assignments)
    lines = []
    for p in sorted_team:
        role = assignments.get(p["discord_id"], "Fill")
        emoji = ROLE_EMOJIS.get(role, "❓")
        champ = champ_assignments.get(p["discord_id"], "") if champ_assignments else ""
        champ_str = f" *({champ})*" if champ else ""
        lines.append(f"{emoji} **{role}** — {p['display_name']}{champ_str}")
    return "\n".join(lines)


def _team_field_no_roles(team: list,
                          champ_assignments: dict | None = None) -> str:
    lines = []
    for p in team:
        champ = champ_assignments.get(p["discord_id"], "") if champ_assignments else ""
        champ_str = f" *({champ})*" if champ else ""
        lines.append(f"• {p['display_name']}{champ_str}")
    return "\n".join(lines)


def _pick_captains_randomly(players: list, past_captain_ids: list[str]) -> tuple[dict, dict]:
    never_captain = [p for p in players if p["discord_id"] not in past_captain_ids]
    if len(never_captain) >= 2:
        picks = random.sample(never_captain, 2)
    elif len(never_captain) == 1:
        repeat_pool = [p for p in players if p["discord_id"] != never_captain[0]["discord_id"]]
        picks = [never_captain[0], random.choice(repeat_pool)]
    else:
        picks = random.sample(players, 2)
    return picks[0], picks[1]


async def _move_players_to_channels(
    guild: discord.Guild,
    team1: list, team2: list,
    t1_ch_id: Optional[int], t2_ch_id: Optional[int]
) -> str:
    if not (t1_ch_id and t2_ch_id):
        return "Tip: use /configure_channels to enable auto voice splits"
    t1_ch = guild.get_channel(t1_ch_id)
    t2_ch = guild.get_channel(t2_ch_id)
    if not (t1_ch and t2_ch):
        return "⚠️ Configured voice channels not found"
    for p in team1:
        m = guild.get_member(int(p["discord_id"]))
        if m and m.voice:
            try:
                await m.move_to(t1_ch)
            except discord.Forbidden:
                pass
    for p in team2:
        m = guild.get_member(int(p["discord_id"]))
        if m and m.voice:
            try:
                await m.move_to(t2_ch)
            except discord.Forbidden:
                pass
    return f"Players moved → {t1_ch.name} / {t2_ch.name}"


# ── Views ─────────────────────────────────────────────────────────────────────

class StartGameView(discord.ui.View):
    """
    Shown after teams are set. Displays lineup (+ bench if any) and waits for 'Start Game'.
    On Start Game: saves DB records, moves playing players to VCs, swaps to WinnerView.
    Bench players are tracked here but receive no stats and are not moved to team VCs.
    """

    def __init__(self, session_id: int, team1: list, team2: list, bench: list,
                 team1_assign: dict, team2_assign: dict,
                 team1_champs: dict, team2_champs: dict,
                 assign_roles: bool, random_champs: bool,
                 settings: dict, game_num: int,
                 all_players: list,       # playing 10 (or fewer)
                 session_players: list,   # full session roster incl. bench
                 cog):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.team1 = team1
        self.team2 = team2
        self.bench = bench                       # players not playing this game
        self.team1_assign = team1_assign
        self.team2_assign = team2_assign
        self.team1_champs = team1_champs
        self.team2_champs = team2_champs
        self.assign_roles = assign_roles
        self.random_champs = random_champs
        self.settings = settings
        self.game_num = game_num
        self.all_players = all_players           # playing only
        self.session_players = session_players   # everyone (for re-roll / next game)
        self.cog = cog

    def build_embed(self, title_suffix: str = "Teams Ready",
                    description: str = "Review the teams below, then press **Start Game** to begin.",
                    color: str = "blue") -> discord.Embed:
        embed = build_embed(f"Game #{self.game_num} — {title_suffix}", description, color)

        champs1 = self.team1_champs if self.random_champs else None
        champs2 = self.team2_champs if self.random_champs else None

        if self.assign_roles:
            embed.add_field(name="🔵 Team 1", value=_team_field(self.team1, self.team1_assign, champs1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field(self.team2, self.team2_assign, champs2), inline=True)
        else:
            embed.add_field(name="🔵 Team 1", value=_team_field_no_roles(self.team1, champs1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field_no_roles(self.team2, champs2), inline=True)

        if self.bench:
            bench_names = "\n".join(f"• {p['display_name']}" for p in self.bench)
            embed.add_field(
                name=f"⏸️ Sitting Out ({len(self.bench)})",
                value=bench_names,
                inline=False
            )

        if self.random_champs:
            embed.set_footer(text="Champions randomly assigned by play rate")

        return embed

    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        guild_id = str(interaction.guild_id)
        db = self.cog.db

        # Save role history for playing players only
        if self.assign_roles:
            for did, role in {**self.team1_assign, **self.team2_assign}.items():
                if role != "Fill":
                    await db.add_role_history(self.session_id, did, guild_id, role)

        # Increment game counter and create game record (bench not included)
        await db.increment_session_game(self.session_id)
        session = await db.get_active_session(guild_id)
        actual_game_num = session["game_number"] if session else self.game_num

        game_id = await db.create_game(
            self.session_id, guild_id, actual_game_num,
            [p["discord_id"] for p in self.team1],
            [p["discord_id"] for p in self.team2]
        )

        # Move playing players to team VCs (bench stays in lobby)
        t1_ch_id = int(self.settings["team1_channel_id"]) if self.settings.get("team1_channel_id") else None
        t2_ch_id = int(self.settings["team2_channel_id"]) if self.settings.get("team2_channel_id") else None
        lobby_id = int(self.settings["lobby_channel_id"]) if self.settings.get("lobby_channel_id") else None

        footer = await _move_players_to_channels(
            interaction.guild, self.team1, self.team2, t1_ch_id, t2_ch_id
        )
        if self.bench:
            footer += f" · {len(self.bench)} sitting out"

        embed = self.build_embed(
            title_suffix=f"#{actual_game_num} — In Progress",
            description="Good luck! Click the winning team when the game ends.",
            color="gold"
        )
        embed.set_footer(text=footer)

        winner_view = WinnerView(
            game_id=game_id,
            session_id=self.session_id,
            team1=self.team1,
            team2=self.team2,
            bench=self.bench,
            team1_ch_id=t1_ch_id,
            team2_ch_id=t2_ch_id,
            lobby_ch_id=lobby_id,
            db=db,
            guild=interaction.guild,
            settings=self.settings,
            all_players=self.all_players,
            session_players=self.session_players,
            cog=self.cog
        )
        await interaction.response.edit_message(embed=embed, view=winner_view)

    @discord.ui.button(label="Re-roll Teams", style=discord.ButtonStyle.secondary, emoji="🎲", row=0)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        # Re-roll from the full session roster so bench rotation changes too
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=self.assign_roles, random_champs=self.random_champs,
            use_power=False, send_mode="message_edit"
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️", row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = build_embed(
            "Teams Cancelled",
            "No game was started. Use `/make_teams` or `/start_draft` to try again.",
            "gray"
        )
        await interaction.response.edit_message(embed=embed, view=None)


class WinnerView(discord.ui.View):
    """Buttons for declaring the winning team. Only playing players get stats."""

    def __init__(self, game_id: int, session_id: int, team1: list, team2: list,
                 bench: list,
                 team1_ch_id: Optional[int], team2_ch_id: Optional[int],
                 lobby_ch_id: Optional[int], db, guild: discord.Guild,
                 settings: dict, all_players: list, session_players: list, cog):
        super().__init__(timeout=None)
        self.game_id = game_id
        self.session_id = session_id
        self.team1 = team1
        self.team2 = team2
        self.bench = bench
        self.team1_ch_id = team1_ch_id
        self.team2_ch_id = team2_ch_id
        self.lobby_ch_id = lobby_ch_id
        self.db = db
        self.guild = guild
        self.settings = settings
        self.all_players = all_players       # playing only
        self.session_players = session_players  # everyone
        self.cog = cog

    async def _record_winner(self, interaction: discord.Interaction, winner: int):
        self.stop()
        await self.db.set_game_winner(self.game_id, winner)
        winners = self.team1 if winner == 1 else self.team2
        losers  = self.team2 if winner == 1 else self.team1

        # Only update stats for the players who actually played
        for p in winners:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=True)
        for p in losers:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=False)
        # bench: no stat update

        # Move everyone (playing + bench) back to lobby
        dest_id = self.lobby_ch_id or self.team1_ch_id
        if dest_id:
            dest_ch = self.guild.get_channel(dest_id)
            if dest_ch:
                for p in self.team1 + self.team2 + self.bench:
                    member = self.guild.get_member(int(p["discord_id"]))
                    if member and member.voice:
                        try:
                            await member.move_to(dest_ch)
                        except discord.Forbidden:
                            pass

        win_label = "🔵 Team 1" if winner == 1 else "🔴 Team 2"
        next_view = NextGameView(
            session_id=self.session_id,
            settings=self.settings,
            session_players=self.session_players,  # full roster for re-roll
            team1=self.team1,
            team2=self.team2,
            bench=self.bench,
            cog=self.cog
        )

        bench_note = f"\n{len(self.bench)} player(s) were sitting out and received no stats." if self.bench else ""
        embed = build_embed(
            f"{win_label} Wins!",
            f"Stats updated.{bench_note} Everyone moved back to lobby.\n\nWhat's next?",
            "green"
        )
        await interaction.response.edit_message(embed=embed, view=next_view)

    @discord.ui.button(label="🔵 Team 1 Won", style=discord.ButtonStyle.primary)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 1)

    @discord.ui.button(label="🔴 Team 2 Won", style=discord.ButtonStyle.danger)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 2)


class NextGameView(discord.ui.View):
    """Buttons shown after a game result is recorded. Always re-rolls from the full session roster."""

    def __init__(self, session_id: int, settings: dict, session_players: list,
                 team1: list, team2: list, bench: list, cog):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.settings = settings
        self.session_players = session_players  # full roster
        self.team1 = team1
        self.team2 = team2
        self.bench = bench
        self.cog = cog

    # Row 0: random team options
    @discord.ui.button(label="Random + Roles", style=discord.ButtonStyle.primary, emoji="🎲", row=0)
    async def random_with_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, random_champs=False, use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Random, No Roles", style=discord.ButtonStyle.secondary, emoji="🔀", row=0)
    async def random_no_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=False, random_champs=False, use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Rematch", style=discord.ButtonStyle.secondary, emoji="🔁", row=0)
    async def rematch(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        # Rematch: same 10 players, flip sides — bench stays on bench
        await self.cog._finalize_teams(
            interaction, self.session_id, self.team2 + self.team1, self.settings,
            assign_roles=True, random_champs=False, use_power=False, send_mode="followup",
            force_teams=(self.team2, self.team1)  # explicit sides, no re-split
        )

    # Row 1: random champion options
    @discord.ui.button(label="Random Champs + Roles", style=discord.ButtonStyle.primary, emoji="🎰", row=1)
    async def random_champs_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, random_champs=True, use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Random Champs, No Roles", style=discord.ButtonStyle.secondary, emoji="🎰", row=1)
    async def random_champs_no_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=False, random_champs=True, use_power=False, send_mode="followup"
        )

    # Row 2: draft
    @discord.ui.button(label="Captain Draft", style=discord.ButtonStyle.success, emoji="🎯", row=2)
    async def captain_draft(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if len(self.session_players) < 3:
            await interaction.response.send_message("Need at least 3 players for a draft.", ephemeral=True)
            return
        db = self.cog.db
        guild_id = str(interaction.guild_id)
        past_captains = await db.get_past_captains(self.session_id, guild_id)
        view = CaptainDraftView(
            session_id=self.session_id,
            players=self.session_players,  # full roster — draft picks its own 10
            db=db,
            guild=interaction.guild,
            settings=self.settings,
            cog=self.cog,
            past_captain_ids=past_captains
        )
        await interaction.response.edit_message(embed=view._get_embed(), view=view)


class CaptainDraftView(discord.ui.View):
    """
    Snake draft: captains alternate picking players up to TEAM_SIZE each (10 total).
    Any remaining unchosen players become the bench.
    Snake order: 1, 2, 2, 1, 1, 2, 2, 1 ...
    """

    def __init__(self, session_id: int, players: list, db, guild: discord.Guild,
                 settings: dict, cog, past_captain_ids: list[str] = None,
                 auto_captains: bool = False):
        super().__init__(timeout=300)
        self.session_id = session_id
        self.session_players = players  # full roster including potential bench
        self.player_map: dict[str, dict] = {p["discord_id"]: p for p in players}
        self.pool: list[str] = [p["discord_id"] for p in players]
        self.db = db
        self.guild = guild
        self.settings = settings
        self.cog = cog
        self.past_captain_ids = past_captain_ids or []
        self.team1: list[str] = []
        self.team2: list[str] = []
        self.captain1_id: str = None
        self.captain2_id: str = None
        self.snake_pick_index: int = 0
        self.turn = 1

        if auto_captains:
            self.phase = "draft"
            cap1, cap2 = _pick_captains_randomly(players, self.past_captain_ids)
            self.captain1_id = cap1["discord_id"]
            self.captain2_id = cap2["discord_id"]
            self.team1.append(self.captain1_id)
            self.team2.append(self.captain2_id)
            self.pool.remove(self.captain1_id)
            self.pool.remove(self.captain2_id)
        else:
            self.phase = "pick_captain1"

        self._build_buttons()

    def _draft_complete(self) -> bool:
        """Returns True when both teams have TEAM_SIZE players or pool is exhausted."""
        return (
            (len(self.team1) >= TEAM_SIZE and len(self.team2) >= TEAM_SIZE)
            or not self.pool
        )

    def _active_team_full(self) -> bool:
        """Returns True if the team whose turn it is already has TEAM_SIZE players."""
        team = self.team1 if self.turn == 1 else self.team2
        return len(team) >= TEAM_SIZE

    def _player_option(self, did: str) -> discord.SelectOption:
        p = self.player_map[did]
        roles_str = " / ".join(p.get("role_prefs", [])) or "No preference"
        return discord.SelectOption(
            label=p["display_name"],
            value=did,
            description=roles_str[:50]
        )

    def _build_buttons(self):
        self.clear_items()
        if self.phase == "pick_captain1":
            sel = discord.ui.Select(
                placeholder="Pick Team 1 Captain...",
                options=[self._player_option(did) for did in self.pool]
            )
            sel.callback = self._on_captain_pick
            self.add_item(sel)
        elif self.phase == "pick_captain2":
            sel = discord.ui.Select(
                placeholder="Pick Team 2 Captain...",
                options=[self._player_option(did) for did in self.pool]
            )
            sel.callback = self._on_captain_pick
            self.add_item(sel)
        elif self.phase == "draft" and self.pool and not self._draft_complete():
            cap_name = self.player_map[
                self.captain1_id if self.turn == 1 else self.captain2_id
            ]["display_name"]
            team_size_now = len(self.team1) if self.turn == 1 else len(self.team2)
            team_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
            sel = discord.ui.Select(
                placeholder=f"{team_label} ({cap_name}): pick player {team_size_now + 1}/{TEAM_SIZE}...",
                options=[self._player_option(did) for did in self.pool]
            )
            sel.callback = self._on_draft_pick
            self.add_item(sel)

    def _bench(self) -> list[dict]:
        """Players left in the pool after draft is complete become the bench."""
        return [self.player_map[did] for did in self.pool]

    def _get_embed(self) -> discord.Embed:
        if self.phase == "pick_captain1":
            return build_embed("Draft — Pick Team 1 Captain", "Select a player to captain Team 1.", "blue")
        if self.phase == "pick_captain2":
            return build_embed("Draft — Pick Team 2 Captain", "Select a player to captain Team 2.", "red")

        def fmt_team(ids: list[str], captain_id: str) -> str:
            if not ids:
                return "_empty_"
            lines = []
            for did in ids:
                p = self.player_map[did]
                crown = "👑 " if did == captain_id else ""
                roles = " / ".join(p.get("role_prefs", [])) or "Fill"
                lines.append(f"{crown}{p['display_name']} ({roles})")
            return "\n".join(lines)

        cap1_name = self.player_map[self.captain1_id]["display_name"] if self.captain1_id else "?"
        cap2_name = self.player_map[self.captain2_id]["display_name"] if self.captain2_id else "?"

        draft_done = self._draft_complete()

        if not draft_done:
            turn_cap = cap1_name if self.turn == 1 else cap2_name
            turn_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
            title = f"Draft — {turn_label} ({turn_cap})'s Pick"
            color = "blue" if self.turn == 1 else "red"
            pool_lines = [
                f"• **{self.player_map[did]['display_name']}** — "
                f"{' / '.join(self.player_map[did].get('role_prefs', [])) or 'No preference'}"
                for did in self.pool
            ]
            desc = "**Available:**\n" + "\n".join(pool_lines)
        else:
            title = "Draft — Complete!"
            color = "green"
            remaining = self._bench()
            desc = (
                f"⏸️ **Sitting out:** {', '.join(p['display_name'] for p in remaining)}"
                if remaining else "All players picked!"
            )

        embed = build_embed(title, desc, color)
        embed.add_field(
            name=f"🔵 Team 1 ({len(self.team1)}/{TEAM_SIZE}) — cap: {cap1_name}",
            value=fmt_team(self.team1, self.captain1_id),
            inline=True
        )
        embed.add_field(
            name=f"🔴 Team 2 ({len(self.team2)}/{TEAM_SIZE}) — cap: {cap2_name}",
            value=fmt_team(self.team2, self.captain2_id),
            inline=True
        )
        return embed

    def _advance_turn(self):
        """Snake: 1,2,2,1,1,2,2,1,...  Auto-skip if one team is already full."""
        self.snake_pick_index += 1
        self.turn = 1 if (self.snake_pick_index // 2) % 2 == 0 else 2
        # If the next team is already full, flip to the other one
        if self._active_team_full():
            self.turn = 2 if self.turn == 1 else 1

    async def _on_captain_pick(self, interaction: discord.Interaction):
        did = interaction.data["values"][0]
        if did not in self.pool:
            await interaction.response.send_message("That player was already picked.", ephemeral=True)
            return
        self.pool.remove(did)

        if self.phase == "pick_captain1":
            self.captain1_id = did
            self.team1.append(did)
            self.phase = "pick_captain2"
        else:
            self.captain2_id = did
            self.team2.append(did)
            self.phase = "draft"
            self.turn = 1

        self._build_buttons()
        await interaction.response.edit_message(embed=self._get_embed(), view=self)

        if self._draft_complete():
            await self._finish(interaction)

    async def _on_draft_pick(self, interaction: discord.Interaction):
        did = interaction.data["values"][0]
        if did not in self.pool:
            await interaction.response.send_message("That player was already picked.", ephemeral=True)
            return
        self.pool.remove(did)

        if self.turn == 1:
            self.team1.append(did)
        else:
            self.team2.append(did)

        self._advance_turn()
        self._build_buttons()

        if not self._draft_complete():
            await interaction.response.edit_message(embed=self._get_embed(), view=self)
        else:
            self.stop()
            await interaction.response.edit_message(embed=self._get_embed(), view=None)
            await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction):
        """Draft complete — bench the leftover pool and hand off to _finalize_teams."""
        guild_id = str(interaction.guild_id)
        if self.captain1_id:
            await self.db.add_captain(self.session_id, self.captain1_id, guild_id)
        if self.captain2_id:
            await self.db.add_captain(self.session_id, self.captain2_id, guild_id)

        team1 = [self.player_map[did] for did in self.team1]
        team2 = [self.player_map[did] for did in self.team2]
        bench = self._bench()  # whoever wasn't picked

        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, random_champs=False, use_power=False,
            send_mode="message_edit",
            force_teams=(team1, team2, bench)
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _get_role_history(self, session_id: int, players: list, guild_id: str) -> dict:
        history = {}
        for p in players:
            history[p["discord_id"]] = await self.db.get_played_roles(
                session_id, p["discord_id"], guild_id
            )
        return history

    async def _finalize_teams(self, interaction: discord.Interaction, session_id: int,
                               session_players: list, settings: dict,
                               assign_roles: bool = True,
                               random_champs: bool = False,
                               use_power: bool = False,
                               send_mode: str = "send",
                               force_teams: tuple = None):
        """
        Splits players into teams + bench, builds StartGameView, posts it.

        force_teams: optional (team1, team2) or (team1, team2, bench) tuple to
                     skip splitting (used by Rematch and CaptainDraft finish).

        send_mode:
          "send"         → interaction.response.send_message
          "followup"     → interaction.followup.send  (after defer())
          "message_edit" → interaction.message.edit() (already responded)
        """
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        track_roles = bool(session.get("track_roles", 1)) if session else True
        game_num = (session["game_number"] + 1) if session else 1

        if force_teams is not None:
            if len(force_teams) == 3:
                team1, team2, bench = force_teams
            else:
                team1, team2 = force_teams
                bench = []
        else:
            team1, team2, bench = _split_players(session_players, use_power=use_power)

        # Assign roles for playing players only (preview — not saved until Start Game)
        team1_assign: dict = {}
        team2_assign: dict = {}
        if assign_roles:
            history = await self._get_role_history(session_id, team1 + team2, guild_id)
            team1_assign = _assign_roles(team1, history, track_roles)
            team2_assign = _assign_roles(team2, history, track_roles)

        # Assign random champions if requested
        team1_champs: dict = {}
        team2_champs: dict = {}
        no_champ_warning = ""
        if random_champs:
            patch = await self.db.get_champion_patch()
            if not patch:
                random_champs = False
                no_champ_warning = "\n⚠️ No champion data found — run `/update_champs` first."
            else:
                if assign_roles:
                    team1_champs = await _assign_champs(team1_assign, self.db)
                    team2_champs = await _assign_champs(team2_assign, self.db)
                else:
                    all_playing = team1 + team2
                    temp_roles = ROLES * ((len(all_playing) // len(ROLES)) + 1)
                    random.shuffle(temp_roles)
                    temp_assign = {p["discord_id"]: temp_roles[i] for i, p in enumerate(all_playing)}
                    t1_temp = {p["discord_id"]: temp_assign[p["discord_id"]] for p in team1}
                    t2_temp = {p["discord_id"]: temp_assign[p["discord_id"]] for p in team2}
                    team1_champs = await _assign_champs(t1_temp, self.db)
                    team2_champs = await _assign_champs(t2_temp, self.db)

        start_view = StartGameView(
            session_id=session_id,
            team1=team1,
            team2=team2,
            bench=bench,
            team1_assign=team1_assign,
            team2_assign=team2_assign,
            team1_champs=team1_champs,
            team2_champs=team2_champs,
            assign_roles=assign_roles,
            random_champs=random_champs,
            settings=settings,
            game_num=game_num,
            all_players=team1 + team2,
            session_players=session_players,
            cog=self
        )
        embed = start_view.build_embed()
        if no_champ_warning:
            embed.description = (embed.description or "") + no_champ_warning

        if send_mode == "send":
            await interaction.response.send_message(embed=embed, view=start_view)
        elif send_mode == "followup":
            await interaction.followup.send(embed=embed, view=start_view)
        elif send_mode == "message_edit":
            await interaction.message.edit(embed=embed, view=start_view)

    # ── /make_teams ────────────────────────────────────────────────────────────

    @app_commands.command(name="make_teams", description="Split session players into two teams of 5. Extras sit out.")
    @app_commands.describe(
        assign_roles="Assign roles based on player preferences (default: True)",
        random_champs="Randomly assign a champion to each player (default: False)",
        use_power="Use power rankings to balance teams (default: False)"
    )
    @is_session_owner()
    async def make_teams(self, interaction: discord.Interaction,
                          assign_roles: bool = True,
                          random_champs: bool = False,
                          use_power: bool = False):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], guild_id)
        if len(players) < 2:
            await interaction.response.send_message("Need at least 2 players in the session.", ephemeral=True)
            return

        settings = await self.db.get_settings(guild_id)

        if use_power and not settings.get("use_power_rankings"):
            await interaction.response.send_message(
                "Power rankings are disabled. Enable with `/toggle_setting`.", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self._finalize_teams(
            interaction, session["id"], players, settings,
            assign_roles=assign_roles, random_champs=random_champs,
            use_power=use_power, send_mode="followup"
        )

    # ── /start_draft ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="start_draft",
        description="Captain snake draft. Captains pick up to 5 each; extras sit out."
    )
    @app_commands.describe(
        random_captains="Auto-pick captains, rotating who hasn't been captain yet"
    )
    @is_session_owner()
    async def start_draft(self, interaction: discord.Interaction, random_captains: bool = False):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], guild_id)
        if len(players) < 3:
            await interaction.response.send_message(
                "Need at least 3 players for a draft (2 captains + 1 to pick).", ephemeral=True
            )
            return

        settings = await self.db.get_settings(guild_id)
        past_captains = await self.db.get_past_captains(session["id"], guild_id)

        view = CaptainDraftView(
            session_id=session["id"],
            players=players,
            db=self.db,
            guild=interaction.guild,
            settings=settings,
            cog=self,
            past_captain_ids=past_captains,
            auto_captains=random_captains
        )

        if random_captains:
            cap1_name = view.player_map[view.captain1_id]["display_name"]
            cap2_name = view.player_map[view.captain2_id]["display_name"]
            embed = view._get_embed()
            embed.description = (
                f"👑 **{cap1_name}** captains Team 1\n"
                f"👑 **{cap2_name}** captains Team 2\n\n"
                + (embed.description or "")
            )
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=view._get_embed(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))