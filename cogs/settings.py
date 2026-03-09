"""
Settings Cog
Handles: configuring voice channels, mod channel, feature toggles, numeric settings, help command.

/settings shows a live interactive panel with buttons for everything.
Removed standalone slash commands: /configure_channels, /toggle_setting,
  /set_champ_rerolls — all functionality lives inside /settings.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import build_embed, is_admin, check_is_admin, check_is_session_owner


# ── Reroll count modal ────────────────────────────────────────────────────────

class RerollCountModal(discord.ui.Modal, title="Set Champion Rerolls"):
    count_input = discord.ui.TextInput(
        label="Rerolls per player per game (0 = disabled)",
        placeholder="Enter a whole number, e.g. 2",
        min_length=1,
        max_length=2,
    )

    def __init__(self, db, guild_id: str, refresh_cb):
        super().__init__()
        self.db         = db
        self.guild_id   = guild_id
        self.refresh_cb = refresh_cb

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.count_input.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ Please enter a whole number (0 or higher).", ephemeral=True
            )
            return
        count = int(raw)
        await self.db.update_setting(self.guild_id, "champ_rerolls", count)
        msg = (
            "✅ Champion rerolls **disabled**."
            if count == 0
            else f"✅ Champion rerolls set to **{count}** per player per game."
        )
        await interaction.response.send_message(msg, ephemeral=True)
        await self.refresh_cb()


# ── Channel select view ───────────────────────────────────────────────────────

class ChannelSelectView(discord.ui.View):
    """
    Shown when 'Configure Channels' is pressed on the settings panel.
    Pre-populates dropdowns with currently saved values so the status line
    always reflects what is actually saved rather than showing '_not set_'.
    """

    def __init__(self, guild: discord.Guild, db, current_settings: dict):
        super().__init__(timeout=120)
        self.db    = db
        self.guild = guild

        # Start from currently-saved values so the status shows real state
        self.team1_id  = current_settings.get("team1_channel_id")
        self.team2_id  = current_settings.get("team2_channel_id")
        self.lobby_id  = current_settings.get("lobby_channel_id")
        self.mod_ch_id = current_settings.get("mod_channel_id")

        vc_options = [
            discord.SelectOption(label=vc.name, value=str(vc.id))
            for vc in guild.voice_channels
        ][:25]
        tc_options = [
            discord.SelectOption(label=tc.name, value=str(tc.id))
            for tc in guild.text_channels
        ][:25]
        if not vc_options:
            return

        def make_select(placeholder, attr, options):
            sel = discord.ui.Select(placeholder=placeholder, options=options)

            async def callback(inter: discord.Interaction):
                setattr(self, attr, inter.data["values"][0])
                await self._update(inter)

            sel.callback = callback
            return sel

        self.add_item(make_select("Select Team 1 Voice Channel",    "team1_id",  vc_options))
        self.add_item(make_select("Select Team 2 Voice Channel",    "team2_id",  vc_options))
        self.add_item(make_select("Select Lobby / Return Channel",  "lobby_id",  vc_options))
        if tc_options:
            self.add_item(make_select("Select Mod Log Text Channel", "mod_ch_id", tc_options))

        save_btn = discord.ui.Button(
            label="Save Channels", style=discord.ButtonStyle.success, emoji="💾"
        )
        save_btn.callback = self._save
        self.add_item(save_btn)

    def _ch_name(self, cid) -> str:
        if not cid:
            return "_not set_"
        ch = self.guild.get_channel(int(cid))
        return f"**{ch.name}**" if ch else f"_unknown ({cid})_"

    def _status_text(self) -> str:
        return (
            f"🔵 Team 1:  {self._ch_name(self.team1_id)}\n"
            f"🔴 Team 2:  {self._ch_name(self.team2_id)}\n"
            f"🏠 Lobby:   {self._ch_name(self.lobby_id)}\n"
            f"🔧 Mod Log: {self._ch_name(self.mod_ch_id)}"
        )

    async def _update(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=self._status_text(), view=self
        )

    async def _save(self, interaction: discord.Interaction):
        self.stop()
        gid = str(interaction.guild_id)
        if self.team1_id:
            await self.db.update_setting(gid, "team1_channel_id",  self.team1_id)
        if self.team2_id:
            await self.db.update_setting(gid, "team2_channel_id",  self.team2_id)
        if self.lobby_id:
            await self.db.update_setting(gid, "lobby_channel_id",  self.lobby_id)
        if self.mod_ch_id:
            await self.db.update_setting(gid, "mod_channel_id",    self.mod_ch_id)

        embed = build_embed("Channels Saved", self._status_text(), "green")
        await interaction.response.edit_message(content=None, embed=embed, view=None)


# ── Settings panel view ───────────────────────────────────────────────────────

class SettingsPanelView(discord.ui.View):
    """
    Interactive panel attached to /settings.
    Row 0: [Configure Channels] [Toggle Champ Weight] [Toggle Peer Ratings] [Set Rerolls]
    """

    def __init__(self, db, guild_id: str, settings: dict, guild: discord.Guild, invoker_id: int):
        super().__init__(timeout=180)
        self.db         = db
        self.guild_id   = guild_id
        self.settings   = settings
        self.guild      = guild
        self.invoker_id = invoker_id
        self._message: discord.Message = None
        self._rebuild()

    def _rebuild(self):
        self.clear_items()

        ch_btn = discord.ui.Button(
            label="Configure Channels",
            style=discord.ButtonStyle.primary,
            emoji="📡",
            row=0,
        )
        ch_btn.callback = self._configure_channels
        self.add_item(ch_btn)

        cw_on  = bool(self.settings.get("champ_weight_enabled"))
        cw_btn = discord.ui.Button(
            label=f"Champ Weight: {'✅ ON' if cw_on else '❌ OFF'}",
            style=discord.ButtonStyle.success if cw_on else discord.ButtonStyle.secondary,
            row=0,
        )
        cw_btn.callback = self._toggle_champ_weight
        self.add_item(cw_btn)

        pr_on  = bool(self.settings.get("peer_ratings_enabled"))
        pr_btn = discord.ui.Button(
            label=f"Peer Ratings: {'✅ ON' if pr_on else '❌ OFF'}",
            style=discord.ButtonStyle.success if pr_on else discord.ButtonStyle.secondary,
            row=0,
        )
        pr_btn.callback = self._toggle_peer_ratings
        self.add_item(pr_btn)

        rerolls = self.settings.get("champ_rerolls", 0)
        rr_btn = discord.ui.Button(
            label=f"Rerolls: {rerolls if rerolls else 'Off'}",
            style=discord.ButtonStyle.primary,
            emoji="🎲",
            row=0,
        )
        rr_btn.callback = self._set_rerolls
        self.add_item(rr_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the admin who opened this panel can use these buttons.", ephemeral=True
            )
            return False
        return True

    async def _refresh(self):
        self.settings = await self.db.get_settings(self.guild_id)
        self._rebuild()
        if self._message:
            try:
                await self._message.edit(embed=self._build_embed(), view=self)
            except Exception:
                pass

    def _build_embed(self) -> discord.Embed:
        s = self.settings

        def ch_name(cid):
            if not cid:
                return "_not configured_"
            ch = self.guild.get_channel(int(cid))
            return f"**{ch.name}**" if ch else f"_unknown ({cid})_"

        embed = build_embed("Server Settings", color_key="gray")
        embed.add_field(
            name="Voice Channels",
            value=(
                f"🔵 Team 1: {ch_name(s.get('team1_channel_id'))}\n"
                f"🔴 Team 2: {ch_name(s.get('team2_channel_id'))}\n"
                f"🏠 Lobby:  {ch_name(s.get('lobby_channel_id'))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Text Channels",
            value=f"🔧 Mod Log: {ch_name(s.get('mod_channel_id'))}",
            inline=False,
        )
        rerolls = s.get("champ_rerolls", 0)
        embed.add_field(
            name="Features",
            value=(
                f"Champ Weight (play rate): {'✅ ON' if s.get('champ_weight_enabled') else '❌ OFF'}\n"
                f"Champ Rerolls per game: **{rerolls}** "
                f"({'disabled' if not rerolls else 'per player'})\n"
                f"Peer Ratings (post-game DM): {'✅ ON' if s.get('peer_ratings_enabled') else '❌ OFF'}"
            ),
            inline=False,
        )
        embed.set_footer(text="Use the buttons below to change settings.")
        return embed

    # ── button callbacks ──────────────────────────────────────────────────────

    async def _configure_channels(self, interaction: discord.Interaction):
        if not self.guild.voice_channels:
            await interaction.response.send_message(
                "This server has no voice channels.", ephemeral=True
            )
            return
        view = ChannelSelectView(self.guild, self.db, self.settings)
        await interaction.response.send_message(
            content=view._status_text(),
            view=view,
            ephemeral=True,
        )
        # Refresh main panel once the channel select view times out or saves
        # (best-effort: we just refresh after any interaction completes)

    async def _toggle_champ_weight(self, interaction: discord.Interaction):
        current = bool(self.settings.get("champ_weight_enabled"))
        await self.db.update_setting(self.guild_id, "champ_weight_enabled", 0 if current else 1)
        await self._refresh()
        await interaction.response.defer()

    async def _toggle_peer_ratings(self, interaction: discord.Interaction):
        current = bool(self.settings.get("peer_ratings_enabled"))
        await self.db.update_setting(self.guild_id, "peer_ratings_enabled", 0 if current else 1)
        await self._refresh()
        await interaction.response.defer()

    async def _set_rerolls(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            RerollCountModal(
                db=self.db,
                guild_id=self.guild_id,
                refresh_cb=self._refresh,
            )
        )

    async def on_timeout(self):
        if self._message:
            for item in self.children:
                item.disabled = True
            try:
                await self._message.edit(view=self)
            except Exception:
                pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ── /settings ─────────────────────────────────────────────────────────────

    @app_commands.command(name="settings", description="[Admin] View and manage server bot settings.")
    @is_admin()
    async def settings_cmd(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        s        = await self.db.get_settings(guild_id)

        view = SettingsPanelView(
            db=self.db,
            guild_id=guild_id,
            settings=s,
            guild=interaction.guild,
            invoker_id=interaction.user.id,
        )
        await interaction.response.send_message(
            embed=view._build_embed(), view=view, ephemeral=True
        )
        view._message = await interaction.original_response()

    # ── /view_ratings ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="view_ratings",
        description="[Admin] View peer rating scores and engagement metrics for all players.",
    )
    @is_admin()
    async def view_ratings(self, interaction: discord.Interaction):
        guild_id   = str(interaction.guild_id)
        ratings    = await self.db.get_all_ratings(guild_id)
        engagement = await self.db.get_all_engagement(guild_id)

        all_ids = set(r["discord_id"] for r in ratings) | set(e["discord_id"] for e in engagement)
        player_names: dict[str, str] = {}
        games_map:    dict[str, int] = {}
        for did in all_ids:
            p = await self.db.get_player(did, guild_id)
            if p:
                player_names[did] = p["display_name"]
                games_map[did]    = p["games_played"]
            else:
                member            = interaction.guild.get_member(int(did))
                player_names[did] = member.display_name if member else f"<@{did}>"
                games_map[did]    = 0

        embed = build_embed("Peer Ratings & Engagement", color_key="gold")

        if ratings:
            lines = []
            for r in sorted(
                ratings,
                key=lambda x: -(x["rating_sum"] / x["rating_count"] if x["rating_count"] else 0),
            ):
                if r["rating_count"] == 0:
                    continue
                avg  = round(r["rating_sum"] / r["rating_count"], 2)
                name = player_names.get(r["discord_id"], r["discord_id"])
                lines.append(f"**{name}** — ⭐ {avg}/5 ({r['rating_count']} ratings)")
            embed.add_field(
                name="⭐ Avg Rating Received",
                value="\n".join(lines[:20]) if lines else "_No ratings yet._",
                inline=False,
            )
        else:
            embed.add_field(name="⭐ Avg Rating Received", value="_No ratings yet._", inline=False)

        if engagement:
            lines = []
            for e in sorted(engagement, key=lambda x: -(x["ratings_given"])):
                did       = e["discord_id"]
                name      = player_names.get(did, did)
                gp        = games_map.get(did, 0)
                gwith     = e["games_with_ratings"]
                eng_rate  = round(gwith / gp * 100, 1) if gp > 0 else 0.0
                avg_given = (
                    round(e["rating_sum_given"] / e["ratings_given"], 2)
                    if e["ratings_given"] > 0 else "—"
                )
                lines.append(
                    f"**{name}** — {gwith}/{gp} games rated ({eng_rate}%) · "
                    f"avg given: {avg_given} · total given: {e['ratings_given']}"
                )
            embed.add_field(
                name="📊 Rating Engagement",
                value="\n".join(lines[:20]) if lines else "_No engagement data yet._",
                inline=False,
            )
        else:
            embed.add_field(name="📊 Rating Engagement", value="_No engagement data yet._", inline=False)

        embed.set_footer(
            text="Engagement rate = rating sessions completed ÷ games played (only when peer ratings were enabled)."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /lol_help ─────────────────────────────────────────────────────────────

    @app_commands.command(name="lol_help", description="Show available bot commands.")
    async def lol_help(self, interaction: discord.Interaction):
        is_admin_user = await check_is_admin(interaction)
        is_owner      = await check_is_session_owner(interaction)

        embed = build_embed("LoL Custom Game Bot — Commands", color_key="gold")

        embed.add_field(name="👤 Everyone", value=(
            "`/register` — register & set your role preferences\n"
            "`/edit_roles` — update your role preferences\n"
            "`/start_session` — open a session (you become session owner)\n"
            "`/leaderboard` — win/loss standings\n"
            "`/lol_help` — this message"
        ), inline=False)

        if is_owner:
            embed.add_field(name="🎮 Session Owner", value=(
                "`/session` — view roster and toggle settings\n"
                "`/add_from_voice [channel]` — pull players from a voice channel\n"
                "`/add_player @p1 [@p2...]` — add up to 5 players\n"
                "`/remove_player` — remove a player\n"
                "`/make_teams` — split into two teams\n"
                "`/start_draft` — captain snake draft\n"
                "`/end_session` — close the session"
            ), inline=False)

        if is_admin_user:
            embed.add_field(name="🔧 Admin", value=(
                "`/settings` — server config: channels, toggles, reroll count\n"
                "`/admins` — manage bot admins\n"
                "`/players` — registered players and role preferences\n"
                "`/update_champs` — sync champion data from the current patch\n"
                "`/view_champs [role]` — browse and edit champion pools\n"
                "`/clear_custom_champs` — remove all custom champions\n"
                "`/view_elo [type]` — ELO leaderboard\n"
                "`/elo_history [type] [member]` — ELO history chart\n"
                "`/view_ratings` — peer rating scores and engagement\n"
                "`/reset_stats` — wipe all stats and ELO history"
            ), inline=False)

        embed.set_footer(text=(
            "repeat_roles is OFF by default — players won't get the same role twice per session. "
            "auto_balance can be set at session start or changed via /session."
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))