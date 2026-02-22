"""
Settings Cog
Handles: configuring voice channels, feature toggles, help command.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import build_embed, is_admin, check_is_admin, check_is_session_owner


class ChannelSelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, db):
        super().__init__(timeout=120)
        self.db = db
        self.guild = guild
        self.team1_id: str = None
        self.team2_id: str = None
        self.lobby_id: str = None

        vc_options = [
            discord.SelectOption(label=vc.name, value=str(vc.id))
            for vc in guild.voice_channels
        ]
        if not vc_options:
            return

        def make_select(placeholder, attr):
            sel = discord.ui.Select(placeholder=placeholder, options=vc_options)

            async def callback(inter: discord.Interaction):
                setattr(self, attr, inter.data["values"][0])
                await self._update(inter)

            sel.callback = callback
            return sel

        self.add_item(make_select("Select Team 1 Voice Channel", "team1_id"))
        self.add_item(make_select("Select Team 2 Voice Channel", "team2_id"))
        self.add_item(make_select("Select Lobby / Return Channel", "lobby_id"))

        save_btn = discord.ui.Button(label="Save Channels", style=discord.ButtonStyle.success, emoji="💾")
        save_btn.callback = self._save
        self.add_item(save_btn)

    def _ch_name(self, cid, guild):
        if not cid:
            return "_not set_"
        ch = guild.get_channel(int(cid))
        return f"**{ch.name}**" if ch else f"_unknown ({cid})_"

    async def _update(self, interaction: discord.Interaction):
        content = (
            f"🔵 Team 1: {self._ch_name(self.team1_id, interaction.guild)}\n"
            f"🔴 Team 2: {self._ch_name(self.team2_id, interaction.guild)}\n"
            f"🏠 Lobby:  {self._ch_name(self.lobby_id, interaction.guild)}"
        )
        await interaction.response.edit_message(content=content, view=self)

    async def _save(self, interaction: discord.Interaction):
        self.stop()
        gid = str(interaction.guild_id)
        if self.team1_id:
            await self.db.update_setting(gid, "team1_channel_id", self.team1_id)
        if self.team2_id:
            await self.db.update_setting(gid, "team2_channel_id", self.team2_id)
        if self.lobby_id:
            await self.db.update_setting(gid, "lobby_channel_id", self.lobby_id)

        embed = build_embed(
            "Channels Saved",
            f"🔵 Team 1: {self._ch_name(self.team1_id, interaction.guild)}\n"
            f"🔴 Team 2: {self._ch_name(self.team2_id, interaction.guild)}\n"
            f"🏠 Lobby:  {self._ch_name(self.lobby_id, interaction.guild)}",
            "green"
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    @app_commands.command(name="settings", description="[Admin] View server bot settings.")
    @is_admin()
    async def settings_cmd(self, interaction: discord.Interaction):
        s = await self.db.get_settings(str(interaction.guild_id))

        def ch_name(cid):
            if not cid:
                return "_not configured_"
            ch = interaction.guild.get_channel(int(cid))
            return f"**{ch.name}**" if ch else f"_unknown ({cid})_"

        embed = build_embed("Server Settings", color_key="gray")
        embed.add_field(
            name="Voice Channels",
            value=(
                f"🔵 Team 1: {ch_name(s.get('team1_channel_id'))}\n"
                f"🔴 Team 2: {ch_name(s.get('team2_channel_id'))}\n"
                f"🏠 Lobby:  {ch_name(s.get('lobby_channel_id'))}"
            ),
            inline=False
        )
        embed.add_field(
            name="Features",
            value=f"Power Rankings: {'✅ ON' if s.get('use_power_rankings') else '❌ OFF'}",
            inline=False
        )
        embed.set_footer(text="Use /configure_channels and /toggle_setting to change.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="configure_channels", description="[Admin] Set Team 1, Team 2, and Lobby voice channels.")
    @is_admin()
    async def configure_channels(self, interaction: discord.Interaction):
        if not interaction.guild.voice_channels:
            await interaction.response.send_message("This server has no voice channels.", ephemeral=True)
            return
        view = ChannelSelectView(interaction.guild, self.db)
        await interaction.response.send_message("Select channels for each role:", view=view, ephemeral=True)

    @app_commands.command(name="toggle_setting", description="[Admin] Toggle a bot feature setting.")
    @app_commands.choices(setting=[
        app_commands.Choice(name="Power Rankings (balance teams by skill weight)", value="use_power_rankings"),
    ])
    @is_admin()
    async def toggle_setting(self, interaction: discord.Interaction, setting: str):
        s = await self.db.get_settings(str(interaction.guild_id))
        current = bool(s.get(setting, 0))
        new_val = 0 if current else 1
        await self.db.update_setting(str(interaction.guild_id), setting, new_val)
        state = "✅ **ON**" if new_val else "❌ **OFF**"
        labels = {"use_power_rankings": "Power Rankings"}
        await interaction.response.send_message(
            f"**{labels.get(setting, setting)}** is now {state}.", ephemeral=True
        )

    # ── /lol_help ─────────────────────────────────────────────────────────────

    @app_commands.command(name="lol_help", description="Show available bot commands.")
    async def lol_help(self, interaction: discord.Interaction):
        is_admin_user = await check_is_admin(interaction)
        is_owner = await check_is_session_owner(interaction)

        embed = build_embed("LoL Custom Game Bot — Commands", color_key="gold")

        embed.add_field(name="👤 Anyone", value=(
            "`/register` — Register & set role preferences\n"
            "`/edit_roles` — Update your role preferences\n"
            "`/unregister` — Remove your data\n"
            "`/stats [member]` — View win/loss stats\n"
            "`/leaderboard` — Server leaderboard\n"
            "`/session_players` — View current roster\n"
            "`/start_session` — Start a session (you become owner)"
        ), inline=False)

        if is_owner:
            embed.add_field(name="🎮 Session Owner", value=(
                "`/end_session` — End the current session\n"
                "`/add_from_voice [channel]` — Grab players from a VC\n"
                "`/add_player @p1 [@p2...]` — Add up to 5 players at once\n"
                "`/remove_player [member]` — Remove a player\n"
                "`/clear_players` — Clear the roster\n"
                "`/make_teams` — Random split + optional role assignment + optional champion assignment\n"
                "`/start_draft` — Captain snake draft"
            ), inline=False)

        if is_admin_user:
            embed.add_field(name="🔧 Admin", value=(
                "`/settings` — View server settings\n"
                "`/configure_channels` — Set Team 1/2/Lobby VCs\n"
                "`/toggle_setting` — Toggle features\n"
                "`/admin_register [member]` — Manually register a player\n"
                "`/set_weight [member] [1-10]` — Set power ranking weight\n"
                "`/view_weights` — View all power weights\n"
                "`/update_champs` — Update champion role statistics\n"
                "`/add_bot_admin [member]` — Grant bot admin to a user\n"
                "`/remove_bot_admin [member]` — Revoke bot admin\n"
                "`/list_bot_admins` — List all bot admins"
            ), inline=False)

        embed.set_footer(text=(
            "Session role tracking is ON by default. "
            "To disable, start a new session with track_roles: False."
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))