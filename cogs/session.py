"""
Session Cog
Handles: creating/ending sessions, adding/removing players, voice channel grabbing.
Session owner can run management commands; admins can always run everything.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import ROLE_EMOJIS, build_embed, fmt_player, is_admin, is_session_owner, check_is_admin, check_is_session_owner


class CreateGameView(discord.ui.View):
    """
    Sub-menu shown when 'Create Game' is pressed on the session control panel.
    Lets the user pick a team/role setup before launching into the teams flow.
    """

    def __init__(self, session_id: int, players: list, settings: dict, db, teams_cog):
        super().__init__(timeout=60)
        self.session_id = session_id
        self.players = players
        self.settings = settings
        self.db = db
        self.teams_cog = teams_cog

    def build_embed(self) -> discord.Embed:
        from utils import build_embed as _be
        return _be(
            "Create Game — Choose Format",
            f"**{len(self.players)} players** in session. Pick a format to generate teams.",
            "blue"
        )

    async def _make(self, interaction: discord.Interaction,
                    assign_roles: bool, use_prefs: bool, random_champs: bool):
        self.stop()
        await interaction.response.defer()
        await self.teams_cog._finalize_teams(
            interaction, self.session_id, self.players, self.settings,
            assign_roles=assign_roles, use_prefs=use_prefs,
            random_champs=random_champs, use_power=False, send_mode="followup"
        )

    # Row 0 — role assignment options
    @discord.ui.button(label="Roles (Pref)", style=discord.ButtonStyle.primary, emoji="🎲", row=0)
    async def roles_pref(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._make(interaction, assign_roles=True, use_prefs=True, random_champs=False)

    @discord.ui.button(label="Roles (Random)", style=discord.ButtonStyle.primary, emoji="🔀", row=0)
    async def roles_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._make(interaction, assign_roles=True, use_prefs=False, random_champs=False)

    @discord.ui.button(label="No Roles", style=discord.ButtonStyle.secondary, emoji="👤", row=0)
    async def no_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._make(interaction, assign_roles=False, use_prefs=False, random_champs=False)

    # Row 1 — random champs options
    @discord.ui.button(label="Champs + Roles (Pref)", style=discord.ButtonStyle.primary, emoji="🎰", row=1)
    async def champs_pref(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._make(interaction, assign_roles=True, use_prefs=True, random_champs=True)

    @discord.ui.button(label="Champs + Roles (Random)", style=discord.ButtonStyle.secondary, emoji="🎰", row=1)
    async def champs_random(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._make(interaction, assign_roles=True, use_prefs=False, random_champs=True)

    # Row 2 — draft
    @discord.ui.button(label="Captain Draft", style=discord.ButtonStyle.success, emoji="🎯", row=2)
    async def captain_draft(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if len(self.players) < 3:
            await interaction.response.send_message(
                "Need at least 3 players for a draft.", ephemeral=True
            )
            return
        from cogs.teams import CaptainDraftView
        past_captains = await self.db.get_past_captains(self.session_id, str(interaction.guild_id))
        view = CaptainDraftView(
            session_id=self.session_id,
            players=self.players,
            db=self.db,
            guild=interaction.guild,
            settings=self.settings,
            cog=self.teams_cog,
            past_captain_ids=past_captains,
        )
        await interaction.response.send_message(embed=view._get_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️", row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Cancelled.", colour=0x808080), view=None
        )


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
                await self.cog.db.add_session_player(session["id"], str(member.id), guild_id, member.display_name)
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

    @discord.ui.button(label="Create Game", style=discord.ButtonStyle.success, emoji="⚔️", row=1)
    async def create_game_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
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

        view = CreateGameView(
            session_id=session["id"],
            players=players,
            settings=settings,
            db=self.cog.db,
            teams_cog=teams_cog,
        )
        await interaction.response.send_message(
            embed=view.build_embed(), view=view
        )

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


class SessionView(discord.ui.View):
    """
    Shown by /session. Displays roster + session settings in one panel.
    Session owners/admins get toggle buttons for repeat_roles and auto_balance,
    plus a Clear Roster button.
    """

    _AB_CYCLE  = ["off", "total", "mode"]
    _AB_LABELS = {"off": "Balance: Off", "total": "Balance: Total ELO", "mode": "Balance: Mode ELO"}

    def __init__(self, session: dict, players: list, db,
                 guild_id: str, invoker_id: int, is_owner: bool):
        super().__init__(timeout=180)
        self.session    = session
        self.players    = players
        self.db         = db
        self.guild_id   = guild_id
        self.invoker_id = invoker_id
        self.is_owner   = is_owner
        self._message: discord.Message = None
        self._rebuild()

    # ── embed ─────────────────────────────────────────────────────────────────

    def build_embed(self) -> discord.Embed:
        rr = bool(self.session.get("repeat_roles", 0))
        ab = self.session.get("auto_balance", "off")
        ab_text = {"off": "Off", "total": "Total ELO", "mode": "Mode ELO"}.get(ab, ab)

        header = (
            f"{'✅' if rr else '❌'} Repeat Roles  ·  "
            f"⚖️ Balance: {ab_text}\n\n"
        )
        if self.players:
            roster = "\n".join(fmt_player(p, show_stats=False) for p in self.players)
        else:
            roster = "_No players yet._"

        return build_embed(
            f"Session #{self.session['id']} — {len(self.players)} player{'s' if len(self.players) != 1 else ''}",
            header + roster,
            color_key="blue",
        )

    # ── buttons (owner/admin only) ────────────────────────────────────────────

    def _rebuild(self):
        self.clear_items()
        if not self.is_owner:
            return

        rr     = bool(self.session.get("repeat_roles", 0))
        rr_btn = discord.ui.Button(
            label=f"Repeat Roles: {'ON' if rr else 'OFF'}",
            style=discord.ButtonStyle.success if rr else discord.ButtonStyle.secondary,
            row=0,
        )
        rr_btn.callback = self._toggle_repeat_roles
        self.add_item(rr_btn)

        ab     = self.session.get("auto_balance", "off")
        ab_btn = discord.ui.Button(
            label=self._AB_LABELS.get(ab, ab),
            style=discord.ButtonStyle.primary if ab != "off" else discord.ButtonStyle.secondary,
            emoji="⚖️",
            row=0,
        )
        ab_btn.callback = self._cycle_auto_balance
        self.add_item(ab_btn)

        clear_btn = discord.ui.Button(
            label="Clear Roster",
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            disabled=(len(self.players) == 0),
            row=0,
        )
        clear_btn.callback = self._clear_roster
        self.add_item(clear_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who opened this can use these buttons.", ephemeral=True
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        self.session = await self.db.get_active_session(self.guild_id) or self.session
        self.players = await self.db.get_session_players(self.session["id"], self.guild_id)
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _toggle_repeat_roles(self, interaction: discord.Interaction):
        current = bool(self.session.get("repeat_roles", 0))
        new_val = 0 if current else 1
        await self.db.update_session(self.session["id"], repeat_roles=new_val)
        self.session["repeat_roles"] = new_val
        await self._refresh(interaction)

    async def _cycle_auto_balance(self, interaction: discord.Interaction):
        current = self.session.get("auto_balance", "off")
        idx     = self._AB_CYCLE.index(current) if current in self._AB_CYCLE else 0
        nxt     = self._AB_CYCLE[(idx + 1) % len(self._AB_CYCLE)]
        await self.db.update_session(self.session["id"], auto_balance=nxt)
        self.session["auto_balance"] = nxt
        await self._refresh(interaction)

    async def _clear_roster(self, interaction: discord.Interaction):
        db      = self.db
        session = self.session

        class ConfirmClear(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, clear", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await db.db.execute(
                    "DELETE FROM session_players WHERE session_id=?", (session["id"],)
                )
                await db.db.commit()
                await btn_inter.response.edit_message(content="✅ Roster cleared.", view=None)
                # Refresh the parent panel
                self.players = []
                self._rebuild()
                if self._message:
                    try:
                        await self._message.edit(embed=self.build_embed(), view=self)
                    except Exception:
                        pass

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "Remove all players from the roster?", view=ConfirmClear(), ephemeral=True
        )

    async def on_timeout(self):
        if self._message:
            for item in self.children:
                item.disabled = True
            try:
                await self._message.edit(view=self)
            except Exception:
                pass


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
        repeat_roles="Allow players to get the same role multiple times in one session (default: OFF)",
        auto_balance="Auto-balance teams by ELO: 'off', 'total', or 'mode' (default: off)"
    )
    @app_commands.choices(auto_balance=[
        app_commands.Choice(name="Off — random teams", value="off"),
        app_commands.Choice(name="Total ELO — balance by overall ELO", value="total"),
        app_commands.Choice(name="Mode ELO — balance by the draft mode's ELO", value="mode"),
    ])
    async def start_session(self, interaction: discord.Interaction,
                             repeat_roles: bool = False,
                             auto_balance: str = "off"):
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
                    sid = await self.db.create_session(gid, str(btn_inter.user.id), repeat_roles, auto_balance)
                    view = SessionControlView(sid, gid, self)
                    embed = self._session_started_embed(sid, btn_inter.user, repeat_roles, auto_balance)
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

        sid = await self.db.create_session(guild_id, str(interaction.user.id), repeat_roles, auto_balance)
        view = SessionControlView(sid, guild_id, self)
        embed = self._session_started_embed(sid, interaction.user, repeat_roles, auto_balance)
        await interaction.response.send_message(embed=embed, view=view)

    def _session_started_embed(self, sid: int, user: discord.User,
                                 repeat_roles: bool, auto_balance: str) -> discord.Embed:
        repeat_note = (
            "✅ **Repeat roles ON** — players may receive the same role multiple times."
            if repeat_roles else
            "❌ **Repeat roles OFF** — players won't get the same role twice this session."
        )
        balance_labels = {"off": "❌ Off (random)", "total": "✅ Total ELO", "mode": "✅ Mode ELO"}
        balance_note = f"⚖️ **Auto-balance:** {balance_labels.get(auto_balance, auto_balance)}"
        return build_embed(
            "Session Started!",
            f"Session **#{sid}** — started by {user.mention}\n\n"
            f"{repeat_note}\n{balance_note}\n\n"
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
            await self.db.add_session_player(session["id"], str(member.id), guild_id, member.display_name)
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
            await self.db.add_session_player(session["id"], str(member.id), guild_id, member.display_name)
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

    # ── /session ──────────────────────────────────────────────────────────────

    @app_commands.command(name="session", description="View the current roster and session settings.")
    @is_session_owner()
    async def session_cmd(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        session  = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players  = await self.db.get_session_players(session["id"], guild_id)
        is_owner = await check_is_session_owner(interaction)

        view = SessionView(
            session=session,
            players=players,
            db=self.db,
            guild_id=guild_id,
            invoker_id=interaction.user.id,
            is_owner=is_owner,
        )
        await interaction.response.send_message(
            embed=view.build_embed(), view=view, ephemeral=True
        )
        view._message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Session(bot))