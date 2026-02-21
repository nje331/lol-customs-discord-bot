"""
Teams Cog
Handles: random teams, captain draft, role assignment, voice channel moving, game results.
"""

import discord
from discord import app_commands
from discord.ext import commands
import random
from typing import Optional

from utils import ROLES, ROLE_EMOJIS, build_embed, is_admin


def _balance_by_power(players: list, use_power: bool) -> tuple[list, list]:
    """Split players into two balanced teams. If use_power, sort by weight and snake-draft."""
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
    """
    Returns {discord_id: role}.
    Tries to honor role prefs while avoiding already-played roles this session.
    """
    assignment: dict[str, str] = {}
    taken_roles: set[str] = set()
    unassigned = list(team)
    roles_pool = ROLES[:]

    # First pass: assign by preference
    for player in unassigned[:]:
        prefs = player.get("role_prefs", [])
        if track_roles:
            played = session_role_history.get(player["discord_id"], [])
            # Filter out already-played roles unless all 5 have been played
            available_prefs = [r for r in prefs if r not in played]
            if not available_prefs:
                available_prefs = prefs  # all played, reset
        else:
            available_prefs = prefs

        for pref in available_prefs:
            if pref not in taken_roles:
                assignment[player["discord_id"]] = pref
                taken_roles.add(pref)
                unassigned.remove(player)
                break

    # Second pass: assign remaining players to remaining roles
    remaining_roles = [r for r in roles_pool if r not in taken_roles]
    random.shuffle(remaining_roles)
    for player in unassigned:
        if remaining_roles:
            role = remaining_roles.pop(0)
        else:
            # More than 5 players on a team (e.g., bench/flex): use "Fill"
            role = "Fill"
        assignment[player["discord_id"]] = role

    return assignment


def _team_embed_field(team: list, assignments: dict, label: str) -> str:
    lines = []
    for p in team:
        role = assignments.get(p["discord_id"], "Fill")
        emoji = ROLE_EMOJIS.get(role, "❓")
        lines.append(f"{emoji} **{role}** — {p['display_name']}")
    return "\n".join(lines)


class WinnerView(discord.ui.View):
    """Buttons for declaring the winning team after a game."""

    def __init__(self, game_id: int, session_id: int, team1: list, team2: list,
                 team1_ch_id: Optional[int], team2_ch_id: Optional[int],
                 lobby_ch_id: Optional[int], db, guild: discord.Guild):
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

    async def _record_winner(self, interaction: discord.Interaction, winner: int):
        self.stop()
        await self.db.set_game_winner(self.game_id, winner)
        winners = self.team1 if winner == 1 else self.team2
        losers = self.team2 if winner == 1 else self.team1

        for p in winners:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=True)
        for p in losers:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=False)

        # Move everyone back to lobby or team1 channel
        dest_id = self.lobby_ch_id or self.team1_ch_id
        if dest_id:
            dest_ch = self.guild.get_channel(dest_id)
            if dest_ch:
                all_players = self.team1 + self.team2
                for p in all_players:
                    member = self.guild.get_member(int(p["discord_id"]))
                    if member and member.voice:
                        try:
                            await member.move_to(dest_ch)
                        except discord.Forbidden:
                            pass

        win_label = "🔵 Team 1" if winner == 1 else "🔴 Team 2"
        embed = build_embed(
            f"🏆 {win_label} Wins!",
            "Stats updated. Players moved back to lobby.\nReady for next game — use `/make_teams` or `/start_draft`.",
            "green"
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="🔵 Team 1 Won", style=discord.ButtonStyle.primary)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 1)

    @discord.ui.button(label="🔴 Team 2 Won", style=discord.ButtonStyle.danger)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._record_winner(interaction, 2)


class CaptainDraftView(discord.ui.View):
    """
    Interactive snake draft: captains alternate picking players.
    Phases: pick captains → snake draft picks
    """

    def __init__(self, session_id: int, players: list, db, guild: discord.Guild,
                 settings: dict, cog):
        super().__init__(timeout=300)
        self.session_id = session_id
        self.pool = list(players)
        self.db = db
        self.guild = guild
        self.settings = settings
        self.cog = cog
        self.team1: list = []
        self.team2: list = []
        self.captain1: dict = None
        self.captain2: dict = None
        self.phase = "pick_captain1"  # → pick_captain2 → draft
        self.turn = 1  # which team is picking
        self.message: discord.Message = None
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        if self.phase in ("pick_captain1", "pick_captain2"):
            label = "Select Team 1 Captain" if self.phase == "pick_captain1" else "Select Team 2 Captain"
            select = discord.ui.Select(
                placeholder=label,
                options=[
                    discord.SelectOption(label=p["display_name"], value=p["discord_id"])
                    for p in self.pool
                ]
            )
            select.callback = self._on_captain_pick
            self.add_item(select)
        elif self.phase == "draft":
            if self.pool:
                select = discord.ui.Select(
                    placeholder=f"{'🔵 Team 1' if self.turn == 1 else '🔴 Team 2'} Captain: pick a player…",
                    options=[
                        discord.SelectOption(
                            label=p["display_name"],
                            description=" / ".join(p.get("role_prefs", [])) or "No preference"
                        )
                        for p in self.pool
                    ]
                )
                select.callback = self._on_draft_pick
                self.add_item(select)

    def _get_embed(self) -> discord.Embed:
        if self.phase == "pick_captain1":
            return build_embed("🎯 Draft — Pick Team 1 Captain", "Select a player to be Team 1 captain.", "blue")
        if self.phase == "pick_captain2":
            return build_embed("🎯 Draft — Pick Team 2 Captain", "Select a player to be Team 2 captain.", "red")

        t1_names = "\n".join(
            f"{'👑 ' if p == self.captain1 else ''}{p['display_name']} "
            f"({' / '.join(p.get('role_prefs', [])) or 'Fill'})"
            for p in self.team1
        ) or "_empty_"
        t2_names = "\n".join(
            f"{'👑 ' if p == self.captain2 else ''}{p['display_name']} "
            f"({' / '.join(p.get('role_prefs', [])) or 'Fill'})"
            for p in self.team2
        ) or "_empty_"
        pool_names = "\n".join(
            f"• **{p['display_name']}** — {' / '.join(p.get('role_prefs', [])) or 'Fill'}"
            for p in self.pool
        ) or "_(all picked)_"

        current = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
        embed = build_embed(
            f"🎯 Captain Draft — {current}'s Pick",
            f"**Pool:**\n{pool_names}",
            "blue" if self.turn == 1 else "red"
        )
        embed.add_field(name="🔵 Team 1", value=t1_names, inline=True)
        embed.add_field(name="🔴 Team 2", value=t2_names, inline=True)
        return embed

    async def _on_captain_pick(self, interaction: discord.Interaction):
        did = interaction.data["values"][0]
        captain = next(p for p in self.pool if p["discord_id"] == did)
        self.pool.remove(captain)

        if self.phase == "pick_captain1":
            self.captain1 = captain
            self.team1.append(captain)
            self.phase = "pick_captain2"
        else:
            self.captain2 = captain
            self.team2.append(captain)
            self.phase = "draft"
            self.turn = 1

        self._build_buttons()
        embed = self._get_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        await self._check_draft_complete(interaction)

    async def _on_draft_pick(self, interaction: discord.Interaction):
        did = interaction.data["values"][0]
        player = next(p for p in self.pool if p["discord_id"] == did)
        self.pool.remove(player)

        if self.turn == 1:
            self.team1.append(player)
        else:
            self.team2.append(player)

        # Snake draft: alternate except at boundaries
        self.turn = 2 if self.turn == 1 else 1

        self._build_buttons()
        embed = self._get_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        await self._check_draft_complete(interaction)

    async def _check_draft_complete(self, interaction: discord.Interaction):
        if not self.pool:
            self.stop()
            await self.cog._finalize_teams(
                interaction, self.session_id, self.team1, self.team2,
                self.settings, follow_up=True
            )


class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _get_role_history(self, session_id: int, players: list, guild_id: str) -> dict:
        """Returns {discord_id: [played_roles]}"""
        history = {}
        for p in players:
            history[p["discord_id"]] = await self.db.get_played_roles(
                session_id, p["discord_id"], guild_id
            )
        return history

    async def _finalize_teams(self, interaction: discord.Interaction, session_id: int,
                               team1: list, team2: list, settings: dict,
                               follow_up: bool = False):
        """Assign roles, post embed, move to VC, post winner buttons."""
        guild_id = str(interaction.guild_id)
        track_roles = bool(settings.get("track_session_roles", 0))

        history = await self._get_role_history(session_id, team1 + team2, guild_id)
        team1_assign = _assign_roles(team1, history, track_roles)
        team2_assign = _assign_roles(team2, history, track_roles)

        # Save role history
        for did, role in {**team1_assign, **team2_assign}.items():
            if role != "Fill":
                await self.db.add_role_history(session_id, did, guild_id, role)

        # Increment game number
        session = await self.db.get_active_session(guild_id)
        await self.db.increment_session_game(session_id)
        game_num = session["game_number"] + 1

        # Create game record
        game_id = await self.db.create_game(
            session_id, guild_id, game_num,
            [p["discord_id"] for p in team1],
            [p["discord_id"] for p in team2]
        )

        # Build embed
        embed = build_embed(
            f"⚔️ Game #{game_num} — Teams Set!",
            f"Session #{session_id}",
            "blue"
        )
        embed.add_field(
            name="🔵 Team 1",
            value=_team_embed_field(team1, team1_assign, "Team 1"),
            inline=True
        )
        embed.add_field(
            name="🔴 Team 2",
            value=_team_embed_field(team2, team2_assign, "Team 2"),
            inline=True
        )

        # Move players to voice channels
        t1_ch_id = int(settings["team1_channel_id"]) if settings.get("team1_channel_id") else None
        t2_ch_id = int(settings["team2_channel_id"]) if settings.get("team2_channel_id") else None
        lobby_id = int(settings["lobby_channel_id"]) if settings.get("lobby_channel_id") else None

        moved_note = ""
        if t1_ch_id and t2_ch_id:
            guild = interaction.guild
            t1_ch = guild.get_channel(t1_ch_id)
            t2_ch = guild.get_channel(t2_ch_id)
            if t1_ch and t2_ch:
                for p in team1:
                    member = guild.get_member(int(p["discord_id"]))
                    if member and member.voice:
                        try:
                            await member.move_to(t1_ch)
                        except discord.Forbidden:
                            pass
                for p in team2:
                    member = guild.get_member(int(p["discord_id"]))
                    if member and member.voice:
                        try:
                            await member.move_to(t2_ch)
                        except discord.Forbidden:
                            pass
                moved_note = f"\nPlayers moved to **{t1_ch.name}** / **{t2_ch.name}**."
        elif not t1_ch_id:
            moved_note = "\n⚠️ Team channels not configured — use `/settings` to set them."

        if moved_note:
            embed.set_footer(text=moved_note.strip())

        winner_view = WinnerView(
            game_id=game_id,
            session_id=session_id,
            team1=team1,
            team2=team2,
            team1_ch_id=t1_ch_id,
            team2_ch_id=t2_ch_id,
            lobby_ch_id=lobby_id,
            db=self.db,
            guild=interaction.guild
        )

        send = interaction.followup.send if follow_up else interaction.response.send_message
        await send(embed=embed, view=winner_view)

    # ── /make_teams ────────────────────────────────────────────────────────────

    @app_commands.command(name="make_teams", description="[Admin] Randomly split session players into two teams.")
    @app_commands.describe(
        assign_roles="Automatically assign roles to players",
        use_power="Use power rankings to balance teams (admin-only setting must be on)"
    )
    @is_admin()
    async def make_teams(self, interaction: discord.Interaction,
                          assign_roles: bool = True,
                          use_power: bool = False):
        session = await self.db.get_active_session(str(interaction.guild_id))
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], str(interaction.guild_id))
        if len(players) < 2:
            await interaction.response.send_message(
                "Need at least 2 players in the session.", ephemeral=True
            )
            return

        settings = await self.db.get_settings(str(interaction.guild_id))

        # Respect the server-level power ranking toggle
        if use_power and not settings.get("use_power_rankings"):
            await interaction.response.send_message(
                "⚠️ Power rankings are disabled in server settings. "
                "Enable them with `/toggle_setting use_power_rankings`.",
                ephemeral=True
            )
            return

        team1, team2 = _balance_by_power(players, use_power=use_power)

        if not assign_roles:
            # Just split teams, no role assignment
            embed = build_embed("⚔️ Teams Split!", color_key="blue")
            embed.add_field(
                name="🔵 Team 1",
                value="\n".join(f"• {p['display_name']}" for p in team1),
                inline=True
            )
            embed.add_field(
                name="🔴 Team 2",
                value="\n".join(f"• {p['display_name']}" for p in team2),
                inline=True
            )
            await interaction.response.send_message(embed=embed)
            return

        await interaction.response.defer()
        await self._finalize_teams(
            interaction, session["id"], team1, team2, settings, follow_up=True
        )

    # ── /start_draft ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="start_draft",
        description="[Admin] Start a captain draft — captains take turns picking players."
    )
    @is_admin()
    async def start_draft(self, interaction: discord.Interaction):
        session = await self.db.get_active_session(str(interaction.guild_id))
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], str(interaction.guild_id))
        if len(players) < 4:
            await interaction.response.send_message(
                "Need at least 4 players for a draft.", ephemeral=True
            )
            return

        settings = await self.db.get_settings(str(interaction.guild_id))

        view = CaptainDraftView(
            session_id=session["id"],
            players=players,
            db=self.db,
            guild=interaction.guild,
            settings=settings,
            cog=self
        )
        embed = view._get_embed()
        await interaction.response.send_message(embed=embed, view=view)

    # ── /random_roles ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="random_roles",
        description="Randomly assign roles to yourself (or all session players if admin)."
    )
    @app_commands.describe(target="all = reassign roles for the whole session")
    async def random_roles(self, interaction: discord.Interaction, target: str = "self"):
        session = await self.db.get_active_session(str(interaction.guild_id))
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        settings = await self.db.get_settings(str(interaction.guild_id))
        is_admin_user = (
            interaction.user.guild_permissions.administrator
            or interaction.user.guild_permissions.manage_guild
        )

        if target == "all" and not is_admin_user:
            await interaction.response.send_message(
                "Only admins can reassign roles for all players.", ephemeral=True
            )
            return

        guild_id = str(interaction.guild_id)
        if target == "all":
            players = await self.db.get_session_players(session["id"], guild_id)
        else:
            player = await self.db.get_player(str(interaction.user.id), guild_id)
            if not player:
                await interaction.response.send_message(
                    "You're not registered. Use `/register` first.", ephemeral=True
                )
                return
            players = [player]

        track_roles = bool(settings.get("track_session_roles", 0))
        history = await self._get_role_history(session["id"], players, guild_id)
        assignments = _assign_roles(players, history, track_roles)

        for did, role in assignments.items():
            if role != "Fill":
                await self.db.add_role_history(session["id"], did, guild_id, role)

        lines = []
        id_to_name = {p["discord_id"]: p["display_name"] for p in players}
        for did, role in assignments.items():
            emoji = ROLE_EMOJIS.get(role, "❓")
            lines.append(f"{emoji} **{role}** — {id_to_name[did]}")

        embed = build_embed("🎲 Role Assignments", "\n".join(lines), "gold")
        await interaction.response.send_message(embed=embed, ephemeral=(target == "self"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
