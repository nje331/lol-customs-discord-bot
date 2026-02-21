"""
Teams Cog
Handles: random teams, captain draft, role assignment, voice moving, game results.

Flow:
  _finalize_teams()  →  shows teams + "Start Game" button  (StartGameView)
  "Start Game" click →  moves players to VCs, saves DB records, shows winner buttons  (WinnerView)
  winner click       →  saves result, moves everyone back, shows NextGameView
  NextGameView       →  re-draft / random / rematch options
"""

import discord
from discord import app_commands
from discord.ext import commands
import random
from typing import Optional

from utils import ROLES, ROLE_EMOJIS, LANE_ORDER, build_embed, is_session_owner, sort_by_lane


# ── Pure helpers ─────────────────────────────────────────────────────────────

def _balance_by_power(players: list, use_power: bool) -> tuple[list, list]:
    pool = players[:]
    if use_power:
        pool.sort(key=lambda p: p.get("power_weight", 5.0), reverse=True)
        team1, team2 = [], []
        for i, p in enumerate(pool):
            (team1 if i % 2 == 0 else team2).append(p)
    else:
        random.shuffle(pool)
        mid = len(pool) // 2
        team1, team2 = pool[:mid], pool[mid:]
    return team1, team2


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
<<<<<<< HEAD
<<<<<<< HEAD
                available_prefs = prefs
=======
                available_prefs = prefs  # all roles played, reset for this player
>>>>>>> parent of b14a484 (fixed last bugs)
=======
                available_prefs = prefs  # all roles played, reset for this player
>>>>>>> parent of b14a484 (fixed last bugs)
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


def _team_field(team: list, assignments: dict) -> str:
    sorted_team = sort_by_lane(team, assignments)
    lines = []
    for p in sorted_team:
        role = assignments.get(p["discord_id"], "Fill")
        emoji = ROLE_EMOJIS.get(role, "❓")
        lines.append(f"{emoji} **{role}** — {p['display_name']}")
    return "\n".join(lines)


def _team_field_no_roles(team: list) -> str:
    return "\n".join(f"• {p['display_name']}" for p in team)


<<<<<<< HEAD
<<<<<<< HEAD
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
    """Moves players into their team VCs. Returns a status string for the embed footer."""
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
    Shown after teams are set. Displays the lineup and waits for "Start Game".
    On click: saves DB records, moves players to VCs, swaps to WinnerView.
    Also has a "Re-roll" button to regenerate teams without starting.
    """

    def __init__(self, session_id: int, team1: list, team2: list,
                 team1_assign: dict, team2_assign: dict,
                 assign_roles: bool, settings: dict, game_num: int,
                 all_players: list, cog):
        super().__init__(timeout=None)
        self.session_id = session_id
=======
class NextGameView(discord.ui.View):
    """Buttons shown after a game result is recorded."""

    def __init__(self, session_id: int, session: dict, settings: dict, players: list,
                 team1: list, team2: list, cog):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.session = session
        self.settings = settings
        self.all_players = players
>>>>>>> parent of b14a484 (fixed last bugs)
        self.team1 = team1
        self.team2 = team2
        self.team1_assign = team1_assign
        self.team2_assign = team2_assign
        self.assign_roles = assign_roles
        self.settings = settings
        self.game_num = game_num
        self.all_players = all_players
        self.cog = cog

<<<<<<< HEAD
    def build_embed(self) -> discord.Embed:
        embed = build_embed(
            f"Game #{self.game_num} — Teams Ready",
            "Review the teams below, then press **Start Game** to move players and begin.",
            "blue"
=======
    @discord.ui.button(label="Random Teams + Roles", style=discord.ButtonStyle.primary, emoji="🎲", row=0)
    async def random_with_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        team1, team2 = _balance_by_power(self.all_players, use_power=False)
        await self.cog._finalize_teams(
            interaction, self.session_id, team1, team2, self.settings,
            assign_roles=True, follow_up=True
>>>>>>> parent of b14a484 (fixed last bugs)
        )
        if self.assign_roles:
            embed.add_field(name="🔵 Team 1", value=_team_field(self.team1, self.team1_assign), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field(self.team2, self.team2_assign), inline=True)
        else:
            embed.add_field(name="🔵 Team 1", value=_team_field_no_roles(self.team1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field_no_roles(self.team2), inline=True)
        return embed

    @discord.ui.button(label="Start Game", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
<<<<<<< HEAD
        guild_id = str(interaction.guild_id)
        db = self.cog.db

        # Save role history to DB
        if self.assign_roles:
            for did, role in {**self.team1_assign, **self.team2_assign}.items():
                if role != "Fill":
                    await db.add_role_history(self.session_id, did, guild_id, role)

        # Increment game counter and save game record
        await db.increment_session_game(self.session_id)
        session = await db.get_active_session(guild_id)
        actual_game_num = session["game_number"] if session else self.game_num

        game_id = await db.create_game(
            self.session_id, guild_id, actual_game_num,
            [p["discord_id"] for p in self.team1],
            [p["discord_id"] for p in self.team2]
        )

        # Move players to VCs
        t1_ch_id = int(self.settings["team1_channel_id"]) if self.settings.get("team1_channel_id") else None
        t2_ch_id = int(self.settings["team2_channel_id"]) if self.settings.get("team2_channel_id") else None
        lobby_id = int(self.settings["lobby_channel_id"]) if self.settings.get("lobby_channel_id") else None

        footer = await _move_players_to_channels(
            interaction.guild, self.team1, self.team2, t1_ch_id, t2_ch_id
        )

        # Build the "game live" embed
        embed = build_embed(
            f"Game #{actual_game_num} — In Progress",
            "Good luck! Click the winning team when the game ends.",
            "gold"
        )
        if self.assign_roles:
            embed.add_field(name="🔵 Team 1", value=_team_field(self.team1, self.team1_assign), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field(self.team2, self.team2_assign), inline=True)
        else:
            embed.add_field(name="🔵 Team 1", value=_team_field_no_roles(self.team1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field_no_roles(self.team2), inline=True)
        embed.set_footer(text=footer)

        winner_view = WinnerView(
            game_id=game_id,
            session_id=self.session_id,
            team1=self.team1,
            team2=self.team2,
            team1_ch_id=t1_ch_id,
            team2_ch_id=t2_ch_id,
            lobby_ch_id=lobby_id,
            db=db,
            guild=interaction.guild,
            settings=self.settings,
            all_players=self.all_players,
=======
        await interaction.response.defer()
        team1, team2 = _balance_by_power(self.all_players, use_power=False)
        await self.cog._finalize_teams(
            interaction, self.session_id, team1, team2, self.settings,
            assign_roles=False, follow_up=True
        )

    @discord.ui.button(label="Rematch (Swap Sides)", style=discord.ButtonStyle.secondary, emoji="🔁", row=0)
    async def rematch(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        # Swap team1 and team2
        await self.cog._finalize_teams(
            interaction, self.session_id, self.team2, self.team1, self.settings,
            assign_roles=True, follow_up=True
        )

    @discord.ui.button(label="Captain Draft", style=discord.ButtonStyle.success, emoji="🎯", row=1)
    async def captain_draft(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if len(self.all_players) < 4:
            await interaction.response.send_message("Need at least 4 players for a draft.", ephemeral=True)
            return
        view = CaptainDraftView(
            session_id=self.session_id,
            players=self.all_players,
            db=self.cog.db,
            guild=interaction.guild,
            settings=self.settings,
>>>>>>> parent of b14a484 (fixed last bugs)
            cog=self.cog
        )
        await interaction.response.edit_message(embed=embed, view=winner_view)

    @discord.ui.button(label="Re-roll Teams", style=discord.ButtonStyle.secondary, emoji="🎲", row=0)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        # Re-randomise from the same player pool, same settings
        team1, team2 = _balance_by_power(self.all_players, use_power=False)
        await self.cog._finalize_teams(
            interaction, self.session_id, team1, team2, self.settings,
            assign_roles=self.assign_roles, send_mode="message_edit"
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️", row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = build_embed("Teams Cancelled", "No game was started. Use `/make_teams` or `/start_draft` to try again.", "gray")
        await interaction.response.edit_message(embed=embed, view=None)


class WinnerView(discord.ui.View):
    """Buttons for declaring the winning team."""

    def __init__(self, game_id: int, session_id: int, team1: list, team2: list,
                 team1_ch_id: Optional[int], team2_ch_id: Optional[int],
                 lobby_ch_id: Optional[int], db, guild: discord.Guild,
                 settings: dict, all_players: list, cog):
        super().__init__(timeout=None)
        self.game_id = game_id
        self.session_id = session_id
        self.team1 = team1
        self.team2 = team2
        self.team1_ch_id = team1_ch_id
        self.team2_ch_id = team2_ch_id
        self.lobby_ch_id = lobby_ch_id
        self.db = db
        self.guild = guild
        self.settings = settings
        self.all_players = all_players
        self.cog = cog

    async def _record_winner(self, interaction: discord.Interaction, winner: int):
        self.stop()
        await self.db.set_game_winner(self.game_id, winner)
        winners = self.team1 if winner == 1 else self.team2
        losers  = self.team2 if winner == 1 else self.team1

        for p in winners:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=True)
        for p in losers:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=False)

        # Move everyone back
        dest_id = self.lobby_ch_id or self.team1_ch_id
        if dest_id:
            dest_ch = self.guild.get_channel(dest_id)
            if dest_ch:
                for p in self.team1 + self.team2:
                    member = self.guild.get_member(int(p["discord_id"]))
                    if member and member.voice:
                        try:
                            await member.move_to(dest_ch)
                        except discord.Forbidden:
                            pass

        win_label = "🔵 Team 1" if winner == 1 else "🔴 Team 2"

        session = await self.db.get_active_session(str(interaction.guild_id))
        next_view = NextGameView(
            session_id=self.session_id,
            session=session,
            settings=self.settings,
            players=self.all_players,
            team1=self.team1,
            team2=self.team2,
            cog=self.cog
        )
        embed = build_embed(
            f"{win_label} Wins!",
            "Stats updated. Everyone moved back to lobby.\n\nWhat's next?",
            "green"
        )
        await interaction.response.edit_message(embed=embed, view=next_view)

    @discord.ui.button(label="🔵 Team 1 Won", style=discord.ButtonStyle.primary)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 1)

    @discord.ui.button(label="🔴 Team 2 Won", style=discord.ButtonStyle.danger)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 2)


=======
>>>>>>> parent of b14a484 (fixed last bugs)
class NextGameView(discord.ui.View):
    """Buttons shown after a game result is recorded."""

    def __init__(self, session_id: int, session: dict, settings: dict, players: list,
                 team1: list, team2: list, cog):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.session = session
        self.settings = settings
        self.all_players = players
        self.team1 = team1
        self.team2 = team2
        self.cog = cog

    @discord.ui.button(label="Random Teams + Roles", style=discord.ButtonStyle.primary, emoji="🎲", row=0)
    async def random_with_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        team1, team2 = _balance_by_power(self.all_players, use_power=False)
        await self.cog._finalize_teams(
            interaction, self.session_id, team1, team2, self.settings,
            assign_roles=True, follow_up=True
        )

    @discord.ui.button(label="Random Teams, No Roles", style=discord.ButtonStyle.secondary, emoji="🔀", row=0)
    async def random_no_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        team1, team2 = _balance_by_power(self.all_players, use_power=False)
        await self.cog._finalize_teams(
            interaction, self.session_id, team1, team2, self.settings,
            assign_roles=False, follow_up=True
        )

    @discord.ui.button(label="Rematch (Swap Sides)", style=discord.ButtonStyle.secondary, emoji="🔁", row=0)
    async def rematch(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        # Swap team1 and team2
        await self.cog._finalize_teams(
            interaction, self.session_id, self.team2, self.team1, self.settings,
            assign_roles=True, follow_up=True
        )

    @discord.ui.button(label="Captain Draft", style=discord.ButtonStyle.success, emoji="🎯", row=1)
    async def captain_draft(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if len(self.all_players) < 3:
            await interaction.response.send_message("Need at least 3 players for a draft.", ephemeral=True)
            return
        view = CaptainDraftView(
            session_id=self.session_id,
            players=self.all_players,
            db=self.cog.db,
            guild=interaction.guild,
            settings=self.settings,
            cog=self.cog
        )
        await interaction.response.edit_message(embed=view._get_embed(), view=view)


<<<<<<< HEAD
class CaptainDraftView(discord.ui.View):
    """
<<<<<<< HEAD
    Snake draft: pick captains (manually or randomly) then alternate picks.
    Snake order: 1, 2, 2, 1, 1, 2, 2, 1 ...
=======
    Snake draft: pick captain 1, captain 2, then alternate picks.
    Select options use discord_id as value so they survive label changes.
>>>>>>> parent of b14a484 (fixed last bugs)
=======
class WinnerView(discord.ui.View):
    """Buttons for declaring the winning team."""

    def __init__(self, game_id: int, session_id: int, team1: list, team2: list,
                 team1_ch_id: Optional[int], team2_ch_id: Optional[int],
                 lobby_ch_id: Optional[int], db, guild: discord.Guild,
                 settings: dict, all_players: list, cog):
        super().__init__(timeout=None)
        self.game_id = game_id
        self.session_id = session_id
        self.team1 = team1
        self.team2 = team2
        self.team1_ch_id = team1_ch_id
        self.team2_ch_id = team2_ch_id
        self.lobby_ch_id = lobby_ch_id
        self.db = db
        self.guild = guild
        self.settings = settings
        self.all_players = all_players
        self.cog = cog

    async def _record_winner(self, interaction: discord.Interaction, winner: int):
        self.stop()
        await self.db.set_game_winner(self.game_id, winner)
        winners = self.team1 if winner == 1 else self.team2
        losers  = self.team2 if winner == 1 else self.team1

        for p in winners:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=True)
        for p in losers:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=False)

        # Move everyone back
        dest_id = self.lobby_ch_id or self.team1_ch_id
        if dest_id:
            dest_ch = self.guild.get_channel(dest_id)
            if dest_ch:
                for p in self.team1 + self.team2:
                    member = self.guild.get_member(int(p["discord_id"]))
                    if member and member.voice:
                        try:
                            await member.move_to(dest_ch)
                        except discord.Forbidden:
                            pass

        win_label = "🔵 Team 1" if winner == 1 else "🔴 Team 2"

        session = await self.db.get_active_session(str(interaction.guild_id))
        next_view = NextGameView(
            session_id=self.session_id,
            session=session,
            settings=self.settings,
            players=self.all_players,
            team1=self.team1,
            team2=self.team2,
            cog=self.cog
        )
        embed = build_embed(
            f"{win_label} Wins!",
            "Stats updated. Everyone moved back to lobby.\n\nWhat's next?",
            "green"
        )
        await interaction.response.edit_message(embed=embed, view=next_view)

    @discord.ui.button(label="🔵 Team 1 Won", style=discord.ButtonStyle.primary)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 1)

    @discord.ui.button(label="🔴 Team 2 Won", style=discord.ButtonStyle.danger)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 2)


class CaptainDraftView(discord.ui.View):
    """
    Snake draft: pick captain 1, captain 2, then alternate picks.
    Select options use discord_id as value so they survive label changes.
>>>>>>> parent of b14a484 (fixed last bugs)
    """

    def __init__(self, session_id: int, players: list, db, guild: discord.Guild,
                 settings: dict, cog):
        super().__init__(timeout=300)
        self.session_id = session_id
        # Build a stable lookup: discord_id -> player dict
        self.player_map: dict[str, dict] = {p["discord_id"]: p for p in players}
        self.pool: list[str] = [p["discord_id"] for p in players]  # remaining ids
        self.db = db
        self.guild = guild
        self.settings = settings
        self.cog = cog
        self.team1: list[str] = []
        self.team2: list[str] = []
        self.captain1_id: str = None
        self.captain2_id: str = None
<<<<<<< HEAD
<<<<<<< HEAD
        self.snake_pick_index: int = 0
=======
        self.phase = "pick_captain1"
>>>>>>> parent of b14a484 (fixed last bugs)
=======
        self.phase = "pick_captain1"
>>>>>>> parent of b14a484 (fixed last bugs)
        self.turn = 1
        self._build_buttons()

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
        if self.phase in ("pick_captain1", "pick_captain2"):
            label = "Pick Team 1 Captain..." if self.phase == "pick_captain1" else "Pick Team 2 Captain..."
            select = discord.ui.Select(
                placeholder=label,
                options=[self._player_option(did) for did in self.pool]
            )
<<<<<<< HEAD
<<<<<<< HEAD
            sel.callback = self._on_captain_pick
            self.add_item(sel)
        elif self.phase == "pick_captain2":
            sel = discord.ui.Select(
                placeholder="Pick Team 2 Captain...",
                options=[self._player_option(did) for did in self.pool]
            )
            sel.callback = self._on_captain_pick
            self.add_item(sel)
=======
            select.callback = self._on_captain_pick
            self.add_item(select)
>>>>>>> parent of b14a484 (fixed last bugs)
=======
            select.callback = self._on_captain_pick
            self.add_item(select)
>>>>>>> parent of b14a484 (fixed last bugs)
        elif self.phase == "draft" and self.pool:
            team_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
            captain_name = self.player_map[
                self.captain1_id if self.turn == 1 else self.captain2_id
            ]["display_name"]
            select = discord.ui.Select(
                placeholder=f"{team_label} ({captain_name}): pick a player...",
                options=[self._player_option(did) for did in self.pool]
            )
            select.callback = self._on_draft_pick
            self.add_item(select)

    def _get_embed(self) -> discord.Embed:
        if self.phase == "pick_captain1":
            return build_embed("Draft — Pick Team 1 Captain", "Select a player to captain Team 1.", "blue")
        if self.phase == "pick_captain2":
            return build_embed("Draft — Pick Team 2 Captain", "Select a player to captain Team 2.", "red")

        def fmt_team(ids: list[str], captain_id: str) -> str:
            lines = []
            for did in ids:
                p = self.player_map[did]
                crown = "👑 " if did == captain_id else ""
                roles = " / ".join(p.get("role_prefs", [])) or "Fill"
                lines.append(f"{crown}{p['display_name']} ({roles})")
            return "\n".join(lines) or "_empty_"

        pool_lines = []
        for did in self.pool:
            p = self.player_map[did]
            roles = " / ".join(p.get("role_prefs", [])) or "No preference"
            pool_lines.append(f"• **{p['display_name']}** — {roles}")

<<<<<<< HEAD
<<<<<<< HEAD
        cap1_name = self.player_map[self.captain1_id]["display_name"] if self.captain1_id else "?"
        cap2_name = self.player_map[self.captain2_id]["display_name"] if self.captain2_id else "?"

        if self.pool:
            turn_cap = cap1_name if self.turn == 1 else cap2_name
            turn_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
            title = f"Draft — {turn_label} ({turn_cap})'s Pick"
            color = "blue" if self.turn == 1 else "red"
            desc = "**Available:**\n" + "\n".join(pool_lines)
        else:
            title = "Draft — All Players Picked!"
            color = "green"
            desc = "All picked!"

        embed = build_embed(title, desc, color)
        embed.add_field(name=f"🔵 Team 1 (cap: {cap1_name})", value=fmt_team(self.team1, self.captain1_id), inline=True)
        embed.add_field(name=f"🔴 Team 2 (cap: {cap2_name})", value=fmt_team(self.team2, self.captain2_id), inline=True)
        return embed

    def _advance_turn(self):
        """Snake: 1,2,2,1,1,2,2,1,..."""
        self.snake_pick_index += 1
        self.turn = 1 if (self.snake_pick_index // 2) % 2 == 0 else 2

=======
        turn_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
        cap_name = self.player_map[
            self.captain1_id if self.turn == 1 else self.captain2_id
        ]["display_name"]
        embed = build_embed(
            f"Draft — {turn_label} ({cap_name})'s Pick",
            "**Available:**\n" + "\n".join(pool_lines),
            "blue" if self.turn == 1 else "red"
        )
        embed.add_field(name="🔵 Team 1", value=fmt_team(self.team1, self.captain1_id), inline=True)
        embed.add_field(name="🔴 Team 2", value=fmt_team(self.team2, self.captain2_id), inline=True)
        return embed

>>>>>>> parent of b14a484 (fixed last bugs)
=======
        turn_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
        cap_name = self.player_map[
            self.captain1_id if self.turn == 1 else self.captain2_id
        ]["display_name"]
        embed = build_embed(
            f"Draft — {turn_label} ({cap_name})'s Pick",
            "**Available:**\n" + "\n".join(pool_lines),
            "blue" if self.turn == 1 else "red"
        )
        embed.add_field(name="🔵 Team 1", value=fmt_team(self.team1, self.captain1_id), inline=True)
        embed.add_field(name="🔴 Team 2", value=fmt_team(self.team2, self.captain2_id), inline=True)
        return embed

>>>>>>> parent of b14a484 (fixed last bugs)
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
        if not self.pool:
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

        # Snake: flip turn, but at start/end of each "round" pick twice
        # Simple alternating for now; true snake is: 1,2,2,1,1,2,2...
        # Track picks since last switch
        picks_so_far = len(self.team1) + len(self.team2)
        # After cap picks (2 picks), we do: 1,2,2,1,1,2,2,1...
        # Position in snake (0-indexed, after 2 captain picks)
        pos = picks_so_far - 2
        # Snake pattern: turn = 1 if (pos // 2) % 2 == 0 else 2
        self.turn = 1 if (pos // 2) % 2 == 0 else 2

<<<<<<< HEAD
<<<<<<< HEAD
        if self.pool:
            await interaction.response.edit_message(embed=self._get_embed(), view=self)
        else:
            self.stop()
            await interaction.response.edit_message(embed=self._get_embed(), view=None)
            await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction):
        """All players picked — record captains and hand off to _finalize_teams."""
        guild_id = str(interaction.guild_id)
        if self.captain1_id:
            await self.db.add_captain(self.session_id, self.captain1_id, guild_id)
        if self.captain2_id:
            await self.db.add_captain(self.session_id, self.captain2_id, guild_id)

        team1 = [self.player_map[did] for did in self.team1]
        team2 = [self.player_map[did] for did in self.team2]

        # interaction already responded — use message_edit mode
=======
        self._build_buttons()
        await interaction.response.edit_message(embed=self._get_embed(), view=self)

        if not self.pool:
            await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction):
        self.stop()
        team1 = [self.player_map[did] for did in self.team1]
        team2 = [self.player_map[did] for did in self.team2]
>>>>>>> parent of b14a484 (fixed last bugs)
=======
        self._build_buttons()
        await interaction.response.edit_message(embed=self._get_embed(), view=self)

        if not self.pool:
            await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction):
        self.stop()
        team1 = [self.player_map[did] for did in self.team1]
        team2 = [self.player_map[did] for did in self.team2]
>>>>>>> parent of b14a484 (fixed last bugs)
        await self.cog._finalize_teams(
            interaction, self.session_id, team1, team2,
            self.settings, assign_roles=True, follow_up=False, edit=True
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
                               team1: list, team2: list, settings: dict,
                               assign_roles: bool = True,
<<<<<<< HEAD
<<<<<<< HEAD
                               send_mode: str = "send"):
        """
        Builds the StartGameView (teams preview + Start Game button) and posts it.

        send_mode:
          "send"         → interaction.response.send_message  (fresh slash command)
          "followup"     → interaction.followup.send           (after defer())
          "message_edit" → interaction.message.edit()          (interaction already responded)
        """
=======
                               follow_up: bool = False,
                               edit: bool = False):
>>>>>>> parent of b14a484 (fixed last bugs)
=======
                               follow_up: bool = False,
                               edit: bool = False):
>>>>>>> parent of b14a484 (fixed last bugs)
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        track_roles = bool(session.get("track_roles", 1)) if session else True
        game_num = (session["game_number"] + 1) if session else 1

        # Assign roles now (preview), but DON'T save to DB yet — that happens on Start Game
        team1_assign = {}
        team2_assign = {}
        if assign_roles:
            history = await self._get_role_history(session_id, team1 + team2, guild_id)
            team1_assign = _assign_roles(team1, history, track_roles)
            team2_assign = _assign_roles(team2, history, track_roles)
<<<<<<< HEAD
<<<<<<< HEAD
=======
=======
>>>>>>> parent of b14a484 (fixed last bugs)
            # Save role history
            for did, role in {**team1_assign, **team2_assign}.items():
                if role != "Fill":
                    await self.db.add_role_history(session_id, did, guild_id, role)

        await self.db.increment_session_game(session_id)
        session = await self.db.get_active_session(guild_id)
        game_num = session["game_number"] if session else 1

        game_id = await self.db.create_game(
            session_id, guild_id, game_num,
            [p["discord_id"] for p in team1],
            [p["discord_id"] for p in team2]
        )

        embed = build_embed(f"Game #{game_num} — Teams Set!", f"Session #{session_id}", "blue")

        if assign_roles:
            embed.add_field(name="🔵 Team 1", value=_team_field(team1, team1_assign), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field(team2, team2_assign), inline=True)
        else:
            embed.add_field(name="🔵 Team 1", value=_team_field_no_roles(team1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field_no_roles(team2), inline=True)

        # Move players to VC
        t1_ch_id = int(settings["team1_channel_id"]) if settings.get("team1_channel_id") else None
        t2_ch_id = int(settings["team2_channel_id"]) if settings.get("team2_channel_id") else None
        lobby_id = int(settings["lobby_channel_id"]) if settings.get("lobby_channel_id") else None

        if t1_ch_id and t2_ch_id:
            t1_ch = interaction.guild.get_channel(t1_ch_id)
            t2_ch = interaction.guild.get_channel(t2_ch_id)
            if t1_ch and t2_ch:
                for p in team1:
                    m = interaction.guild.get_member(int(p["discord_id"]))
                    if m and m.voice:
                        try:
                            await m.move_to(t1_ch)
                        except discord.Forbidden:
                            pass
                for p in team2:
                    m = interaction.guild.get_member(int(p["discord_id"]))
                    if m and m.voice:
                        try:
                            await m.move_to(t2_ch)
                        except discord.Forbidden:
                            pass
                embed.set_footer(text=f"Players moved to {t1_ch.name} / {t2_ch.name}")
        elif not t1_ch_id:
            embed.set_footer(text="Tip: use /configure_channels to enable auto voice splits")
>>>>>>> parent of b14a484 (fixed last bugs)

        all_players = team1 + team2
        start_view = StartGameView(
            session_id=session_id,
            team1=team1,
            team2=team2,
            team1_assign=team1_assign,
            team2_assign=team2_assign,
            assign_roles=assign_roles,
            settings=settings,
            game_num=game_num,
            all_players=all_players,
            cog=self
        )
        embed = start_view.build_embed()

<<<<<<< HEAD
<<<<<<< HEAD
        if send_mode == "send":
            await interaction.response.send_message(embed=embed, view=start_view)
        elif send_mode == "followup":
            await interaction.followup.send(embed=embed, view=start_view)
        elif send_mode == "message_edit":
            await interaction.message.edit(embed=embed, view=start_view)
=======
=======
>>>>>>> parent of b14a484 (fixed last bugs)
        if edit:
            await interaction.response.edit_message(embed=embed, view=winner_view)
        elif follow_up:
            await interaction.followup.send(embed=embed, view=winner_view)
        else:
            await interaction.response.send_message(embed=embed, view=winner_view)
<<<<<<< HEAD
>>>>>>> parent of b14a484 (fixed last bugs)
=======
>>>>>>> parent of b14a484 (fixed last bugs)

    # ── /make_teams ────────────────────────────────────────────────────────────

    @app_commands.command(name="make_teams", description="Randomly split session players into two teams.")
    @app_commands.describe(
        assign_roles="Automatically assign roles to players (default: True)",
        use_power="Use power rankings to balance teams"
    )
    @is_session_owner()
    async def make_teams(self, interaction: discord.Interaction,
                          assign_roles: bool = True,
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

        team1, team2 = _balance_by_power(players, use_power=use_power)
        await interaction.response.defer()
        await self._finalize_teams(
            interaction, session["id"], team1, team2, settings,
            assign_roles=assign_roles, follow_up=True
        )

    # ── /start_draft ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="start_draft",
        description="Start a captain snake draft — captains alternate picking players."
    )
<<<<<<< HEAD
<<<<<<< HEAD
    @app_commands.describe(
        random_captains="Auto-pick captains, rotating who hasn't been captain yet"
    )
    @is_session_owner()
    async def start_draft(self, interaction: discord.Interaction, random_captains: bool = False):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
=======
    @is_session_owner()
=======
    @is_session_owner()
>>>>>>> parent of b14a484 (fixed last bugs)
    async def start_draft(self, interaction: discord.Interaction):
        session = await self.db.get_active_session(str(interaction.guild_id))
>>>>>>> parent of b14a484 (fixed last bugs)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

<<<<<<< HEAD
<<<<<<< HEAD
        players = await self.db.get_session_players(session["id"], guild_id)
        if len(players) < 3:
=======
        players = await self.db.get_session_players(session["id"], str(interaction.guild_id))
        if len(players) < 4:
>>>>>>> parent of b14a484 (fixed last bugs)
=======
        players = await self.db.get_session_players(session["id"], str(interaction.guild_id))
        if len(players) < 4:
>>>>>>> parent of b14a484 (fixed last bugs)
            await interaction.response.send_message(
                "Need at least 4 players for a draft.", ephemeral=True
            )
            return

<<<<<<< HEAD
<<<<<<< HEAD
        settings = await self.db.get_settings(guild_id)
        past_captains = await self.db.get_past_captains(session["id"], guild_id)

=======
        settings = await self.db.get_settings(str(interaction.guild_id))
>>>>>>> parent of b14a484 (fixed last bugs)
=======
        settings = await self.db.get_settings(str(interaction.guild_id))
>>>>>>> parent of b14a484 (fixed last bugs)
        view = CaptainDraftView(
            session_id=session["id"],
            players=players,
            db=self.db,
            guild=interaction.guild,
            settings=settings,
            cog=self
        )
<<<<<<< HEAD
<<<<<<< HEAD

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
=======
        await interaction.response.send_message(embed=view._get_embed(), view=view)
>>>>>>> parent of b14a484 (fixed last bugs)
=======
        await interaction.response.send_message(embed=view._get_embed(), view=view)
>>>>>>> parent of b14a484 (fixed last bugs)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))