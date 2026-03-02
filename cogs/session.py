"""
Session Cog
Handles: creating/ending sessions, adding/removing players, voice channel grabbing.
Session owner can run management commands; admins can always run everything.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import ROLE_EMOJIS, build_embed, fmt_player, is_admin, is_session_owner, check_is_admin


class SessionControlView(discord.ui.View):
    """
    Persistent control panel posted when a session starts.
    """

    def __init__(self, session_id: int, guild_id: str, cog):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.guild_id = guild_id
        self.cog = cog

    async def _auth(self, interaction: discord.Interaction) -> bool:
        from utils import check_is_session_owner
        if await check_is_session_owner(interaction):
            return True
        await interaction.response.send_message(
            "Only the session owner or an admin can use these buttons.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Add from Voice", style=discord.ButtonStyle.primary, emoji="🎙️", row=0)
    async def add_from_voice_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._auth(interaction):
            return
        vcs = [vc for vc in interaction.guild.voice_channels if vc.members]
        if not vcs:
            await interaction.response.send_message("No occupied voice channels found.", ephemeral=True)
            return

        select = discord.ui.Select(
            placeholder="Choose a voice channel...",
            options=[
                discord.SelectOption(
                    label=f"{vc.name} ({len(vc.members)} members)",
                    value=str(vc.id)
                ) for vc in vcs
            ]
        )

        async def on_vc_select(inter: discord.Interaction):
            vc_id = int(inter.data["values"][0])
            vc = inter.guild.get_channel(vc_id)
            if not vc:
                await inter.response.send_message("Channel not found.", ephemeral=True)
                return
            session = await self.cog.db.get_active_session(str(inter.guild_id))
            if not session:
                await inter.response.send_message("No active session.", ephemeral=True)
                return
            added, auto_reg = [], []
            guild_id = str(inter.guild_id)
            for member in vc.members:
                if member.bot:
                    continue
                p = await self.cog.db.get_player(str(member.id), guild_id)
                if not p:
                    await self.cog.db.upsert_player(str(member.id), guild_id, member.display_name, [])
                    auto_reg.append(member.display_name)
                await self.cog.db.add_session_player(session["id"], str(member.id), guild_id)
                added.append(member.display_name)
            players = await self.cog.db.get_session_players(session["id"], guild_id)
            desc = f"Added **{len(added)}** from **{vc.name}**."
            if auto_reg:
                desc += f"\nAuto-registered: {', '.join(auto_reg)}"
            desc += f"\n\n**Roster ({len(players)}):**\n" + "\n".join(
                f"• {fmt_player(p, show_stats=False)}" for p in players
            )
            await inter.response.edit_message(
                content=None, embed=build_embed("Players Added", desc, "blue"), view=None
            )

        select.callback = on_vc_select
        v = discord.ui.View(timeout=60)
        v.add_item(select)
        await interaction.response.send_message("Select a voice channel:", view=v, ephemeral=True)

    @discord.ui.button(label="View Roster", style=discord.ButtonStyle.secondary, emoji="📋", row=0)
    async def view_roster_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = str(interaction.guild_id)
        session = await self.cog.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        players = await self.cog.db.get_session_players(session["id"], guild_id)
        if not players:
            await interaction.response.send_message("No players in session yet.", ephemeral=True)
            return
        lines = [fmt_player(p, show_stats=False) for p in players]
        embed = build_embed(
            f"Session #{session['id']} Roster ({len(players)} players)",
            "\n".join(lines), "blue"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Make Teams", style=discord.ButtonStyle.success, emoji="⚔️", row=1)
    async def make_teams_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._auth(interaction):
            return
        guild_id = str(interaction.guild_id)
        session = await self.cog.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        players = await self.cog.db.get_session_players(session["id"], guild_id)
        if len(players) < 2:
            await interaction.response.send_message("Need at least 2 players in the session.", ephemeral=True)
            return
        settings = await self.cog.db.get_settings(guild_id)
        teams_cog = interaction.client.cogs.get("Teams")
        if not teams_cog:
            await interaction.response.send_message("Teams cog not loaded.", ephemeral=True)
            return
        await interaction.response.defer()
        await teams_cog._finalize_teams(
            interaction, session["id"], players, settings,
            assign_roles=True, use_prefs=True, random_champs=False,
            use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Captain Draft", style=discord.ButtonStyle.success, emoji="🎯", row=1)
    async def captain_draft_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._auth(interaction):
            return
        guild_id = str(interaction.guild_id)
        session = await self.cog.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        players = await self.cog.db.get_session_players(session["id"], guild_id)
        if len(players) < 3:
            await interaction.response.send_message(
                "Need at least 3 players for a draft (2 captains + 1 to pick).", ephemeral=True
            )
            return
        settings = await self.cog.db.get_settings(guild_id)
        teams_cog = interaction.client.cogs.get("Teams")
        if not teams_cog:
            await interaction.response.send_message("Teams cog not loaded.", ephemeral=True)
            return
        from cogs.teams import CaptainDraftView
        past_captains = await self.cog.db.get_past_captains(session["id"], guild_id)
        view = CaptainDraftView(
            session_id=session["id"],
            players=players,
            db=self.cog.db,
            guild=interaction.guild,
            settings=settings,
            cog=teams_cog,
            past_captain_ids=past_captains
        )
        await interaction.response.send_message(embed=view._get_embed(), view=view)

    @discord.ui.button(label="End Session", style=discord.ButtonStyle.danger, emoji="🛑", row=1)
    async def end_session_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._auth(interaction):
            return
        session = await self.cog.db.get_active_session(str(interaction.guild_id))
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        class ConfirmEnd(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, end it", style=discord.ButtonStyle.danger)
            async def yes(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await self.cog.db.end_session(session["id"])
                await btn_inter.response.edit_message(
                    content=f"Session #{session['id']} ended.", view=None
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def no(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            f"End session #{session['id']}?", view=ConfirmEnd(), ephemeral=True
        )


class Session(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _get_session_or_error(self, interaction: discord.Interaction) -> dict | None:
        """
        Fetches the active session. Does NOT send any response.
        Returns None if no session. Caller must handle the None case and send its own response.
        """
        return await self.db.get_active_session(str(interaction.guild_id))

    # ── /start_session ────────────────────────────────────────────────────────

    @app_commands.command(name="start_session", description="Start a new custom game session.")
    @app_commands.describe(
        track_roles="Avoid giving the same role twice in one session (default: ON)"
    )
    async def start_session(self, interaction: discord.Interaction, track_roles: bool = True):
        guild_id = str(interaction.guild_id)
        existing = await self.db.get_active_session(guild_id)

        if existing:
            class ConfirmView(discord.ui.View):
                def __init__(self_v):
                    super().__init__(timeout=30)

                @discord.ui.button(label="End old & start new", style=discord.ButtonStyle.danger)
                async def confirm(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                    self_v.stop()
                    gid = str(btn_inter.guild_id)
                    await self.db.end_session(existing["id"])
                    sid = await self.db.create_session(gid, str(btn_inter.user.id), track_roles)
                    view = SessionControlView(sid, gid, self)
                    embed = self._session_started_embed(sid, btn_inter.user, track_roles)
                    await btn_inter.response.edit_message(content=None, embed=embed, view=view)

                @discord.ui.button(label="Keep current session", style=discord.ButtonStyle.secondary)
                async def cancel(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                    self_v.stop()
                    await btn_inter.response.edit_message(content="Kept current session.", view=None)

            await interaction.response.send_message(
                "There's already an active session. End it and start a new one?",
                view=ConfirmView(), ephemeral=True
            )
            return

        sid = await self.db.create_session(guild_id, str(interaction.user.id), track_roles)
        view = SessionControlView(sid, guild_id, self)
        embed = self._session_started_embed(sid, interaction.user, track_roles)
        await interaction.response.send_message(embed=embed, view=view)

    def _session_started_embed(self, sid: int, user: discord.User, track_roles: bool) -> discord.Embed:
        track_note = (
            "✅ **Role tracking ON** — players won't get the same role twice this session.\n"
            "Start a new session with `track_roles: False` to disable."
            if track_roles else
            "❌ **Role tracking OFF** — roles will be assigned freely."
        )
        return build_embed(
            "Session Started!",
            f"Session **#{sid}** — started by {user.mention}\n\n"
            f"{track_note}\n\n"
            "Use the buttons below or slash commands to manage the session.",
            "green"
        )

    # ── /end_session ──────────────────────────────────────────────────────────

    @app_commands.command(name="end_session", description="End the current session.")
    @is_session_owner()
    async def end_session(self, interaction: discord.Interaction):
        session = await self._get_session_or_error(interaction)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        class ConfirmView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, end session", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await self.db.end_session(session["id"])
                await btn_inter.response.edit_message(
                    content=f"Session #{session['id']} ended.", view=None
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            f"End session #{session['id']}? This cannot be undone.",
            view=ConfirmView(), ephemeral=True
        )

    # ── /add_from_voice ───────────────────────────────────────────────────────

    @app_commands.command(
        name="add_from_voice",
        description="Add all members from a voice channel to the session."
    )
    @app_commands.describe(channel="The voice channel to pull players from")
    @is_session_owner()
    async def add_from_voice(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        guild_id = str(interaction.guild_id)
        session = await self._get_session_or_error(interaction)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        if not channel.members:
            await interaction.response.send_message("That channel is empty.", ephemeral=True)
            return

        added, auto_registered = [], []
        for member in channel.members:
            if member.bot:
                continue
            player = await self.db.get_player(str(member.id), guild_id)
            if not player:
                await self.db.upsert_player(str(member.id), guild_id, member.display_name, [])
                auto_registered.append(member.display_name)
            await self.db.add_session_player(session["id"], str(member.id), guild_id)
            added.append(member.display_name)

        players = await self.db.get_session_players(session["id"], guild_id)
        desc = f"Added **{len(added)}** player(s) from **{channel.name}**."
        if auto_registered:
            desc += f"\nAuto-registered: {', '.join(auto_registered)}"
        desc += f"\n\n**Session roster ({len(players)} players):**\n"
        desc += "\n".join(f"• {fmt_player(p, show_stats=False)}" for p in players)

        await interaction.response.send_message(embed=build_embed("Players Added", desc, "blue"))

    # ── /add_player ───────────────────────────────────────────────────────────

    @app_commands.command(name="add_player", description="Add one or more players to the session.")
    @app_commands.describe(
        member1="First player to add",
        member2="Second player (optional)",
        member3="Third player (optional)",
        member4="Fourth player (optional)",
        member5="Fifth player (optional)"
    )
    @is_session_owner()
    async def add_player(self, interaction: discord.Interaction,
                          member1: discord.Member,
                          member2: discord.Member = None,
                          member3: discord.Member = None,
                          member4: discord.Member = None,
                          member5: discord.Member = None):
        guild_id = str(interaction.guild_id)

        # Get session directly — do NOT use _require_session (it sends its own response)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        members = [m for m in [member1, member2, member3, member4, member5] if m is not None]
        added, auto_reg = [], []

        for member in members:
            if member.bot:
                continue
            player = await self.db.get_player(str(member.id), guild_id)
            if not player:
                await self.db.upsert_player(str(member.id), guild_id, member.display_name, [])
                auto_reg.append(member.display_name)
            await self.db.add_session_player(session["id"], str(member.id), guild_id)
            added.append(member.display_name)

        if not added:
            await interaction.response.send_message("No valid players to add.", ephemeral=True)
            return

        desc = f"Added: {', '.join(f'**{n}**' for n in added)}"
        if auto_reg:
            desc += f"\nAuto-registered (no role prefs): {', '.join(auto_reg)}"

        await interaction.response.send_message(f"✅ {desc}", ephemeral=True)

    # ── /remove_player ────────────────────────────────────────────────────────

    @app_commands.command(name="remove_player", description="Remove a player from the current session.")
    @app_commands.describe(member="The member to remove")
    @is_session_owner()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        await self.db.remove_session_player(session["id"], str(member.id), guild_id)
        await interaction.response.send_message(
            f"✅ Removed **{member.display_name}** from the session.", ephemeral=True
        )

    # ── /session_players ──────────────────────────────────────────────────────

    @app_commands.command(name="session_players", description="Show all players in the current session.")
    async def session_players(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], guild_id)
        if not players:
            await interaction.response.send_message("No players in this session yet.", ephemeral=True)
            return

        lines = [fmt_player(p, show_stats=False) for p in players]
        embed = build_embed(
            f"Session #{session['id']} Roster ({len(players)} players)",
            "\n".join(lines), "blue"
        )
        await interaction.response.send_message(embed=embed)

    # ── /clear_players ────────────────────────────────────────────────────────

    @app_commands.command(name="clear_players", description="Remove all players from the session roster.")
    @is_session_owner()
    async def clear_players(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        db = self.db

        class ConfirmView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, clear roster", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await db.db.execute(
                    "DELETE FROM session_players WHERE session_id=?", (session["id"],)
                )
                await db.db.commit()
                await btn_inter.response.edit_message(content="✅ Roster cleared.", view=None)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "Remove all players from the session roster?",
            view=ConfirmView(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Session(bot))