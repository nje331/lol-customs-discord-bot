"""
Settings Cog
Handles: configuring team voice channels, lobby channel, feature toggles.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import build_embed, is_admin


BOOL_SETTINGS = {
    "use_power_rankings": "Use power rankings when balancing teams (default OFF, admin-only)",
    "track_session_roles": "Avoid giving players a role they've already played this session",
}


class ChannelSelectView(discord.ui.View):
    """Lets an admin pick voice channels for team1, team2, and lobby."""

    def __init__(self, guild: discord.Guild, db, interaction: discord.Interaction):
        super().__init__(timeout=120)
        self.db = db
        self.guild = guild
        self.interaction = interaction
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

        self.add_item(make_select("Select 🔵 Team 1 Voice Channel", "team1_id"))
        self.add_item(make_select("Select 🔴 Team 2 Voice Channel", "team2_id"))
        self.add_item(make_select("Select 🏠 Lobby / Return Channel", "lobby_id"))

        save_btn = discord.ui.Button(label="💾 Save Channels", style=discord.ButtonStyle.success)
        save_btn.callback = self._save
        self.add_item(save_btn)

    async def _update(self, interaction: discord.Interaction):
        def ch_name(cid):
            if not cid:
                return "_not set_"
            ch = self.guild.get_channel(int(cid))
            return ch.name if ch else "_unknown_"

        content = (
            f"🔵 Team 1: **{ch_name(self.team1_id)}**\n"
            f"🔴 Team 2: **{ch_name(self.team2_id)}**\n"
            f"🏠 Lobby:  **{ch_name(self.lobby_id)}**"
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

        def ch_name(cid):
            if not cid:
                return "_not set_"
            ch = interaction.guild.get_channel(int(cid))
            return ch.name if ch else "_unknown_"

        embed = build_embed(
            "✅ Channels Saved",
            f"🔵 Team 1: **{ch_name(self.team1_id)}**\n"
            f"🔴 Team 2: **{ch_name(self.team2_id)}**\n"
            f"🏠 Lobby:  **{ch_name(self.lobby_id)}**",
            "green"
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class Settings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ── /settings ─────────────────────────────────────────────────────────────

    @app_commands.command(name="settings", description="[Admin] View or configure bot settings for this server.")
    @is_admin()
    async def settings_cmd(self, interaction: discord.Interaction):
        s = await self.db.get_settings(str(interaction.guild_id))

        def ch_name(cid):
            if not cid:
                return "_not configured_"
            ch = interaction.guild.get_channel(int(cid))
            return f"**{ch.name}**" if ch else f"_unknown ({cid})_"

        def bool_val(v):
            return "✅ ON" if v else "❌ OFF"

        embed = build_embed("⚙️ Server Settings", color_key="gray")
        embed.add_field(
            name="📢 Voice Channels",
            value=(
                f"Team 1: {ch_name(s.get('team1_channel_id'))}\n"
                f"Team 2: {ch_name(s.get('team2_channel_id'))}\n"
                f"Lobby:  {ch_name(s.get('lobby_channel_id'))}"
            ),
            inline=False
        )
        embed.add_field(
            name="🎮 Features",
            value=(
                f"Power Rankings: {bool_val(s.get('use_power_rankings', 0))}\n"
                f"Track Session Roles: {bool_val(s.get('track_session_roles', 0))}"
            ),
            inline=False
        )
        embed.set_footer(text="Use /configure_channels and /toggle_setting to change these.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /configure_channels ───────────────────────────────────────────────────

    @app_commands.command(
        name="configure_channels",
        description="[Admin] Set the Team 1, Team 2, and Lobby voice channels."
    )
    @is_admin()
    async def configure_channels(self, interaction: discord.Interaction):
        if not interaction.guild.voice_channels:
            await interaction.response.send_message(
                "This server has no voice channels.", ephemeral=True
            )
            return

        view = ChannelSelectView(interaction.guild, self.db, interaction)
        await interaction.response.send_message(
            "Select channels for each role:", view=view, ephemeral=True
        )

    # ── /toggle_setting ───────────────────────────────────────────────────────

    @app_commands.command(name="toggle_setting", description="[Admin] Toggle a boolean bot setting.")
    @app_commands.describe(setting="Which setting to toggle")
    @app_commands.choices(setting=[
        app_commands.Choice(name="Power Rankings (balance teams by skill)", value="use_power_rankings"),
        app_commands.Choice(name="Track Session Roles (avoid repeating roles)", value="track_session_roles"),
    ])
    @is_admin()
    async def toggle_setting(self, interaction: discord.Interaction, setting: str):
        s = await self.db.get_settings(str(interaction.guild_id))
        current = bool(s.get(setting, 0))
        new_val = 0 if current else 1
        await self.db.update_setting(str(interaction.guild_id), setting, new_val)

        desc = BOOL_SETTINGS.get(setting, setting)
        state = "✅ **ON**" if new_val else "❌ **OFF**"
        await interaction.response.send_message(
            f"**{desc}** is now {state}.", ephemeral=True
        )

    # ── /help ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="lol_help", description="Show all available bot commands.")
    async def lol_help(self, interaction: discord.Interaction):
        is_admin_user = (
            interaction.user.guild_permissions.administrator
            or interaction.user.guild_permissions.manage_guild
        )

        embed = build_embed("📖 LoL Custom Game Bot — Commands", color_key="gold")

        embed.add_field(name="👤 Player Commands", value="""
`/register` — Register & set role preferences
`/edit_roles` — Update your role preferences  
`/unregister` — Remove your data
`/stats [member]` — View win/loss stats
`/leaderboard` — Server win-rate leaderboard
`/random_roles` — Get a randomly assigned role
""", inline=False)

        embed.add_field(name="🎮 Session Commands", value="""
`/session_players` — See current session roster
""", inline=False)

        if is_admin_user:
            embed.add_field(name="🔧 Admin — Session", value="""
`/start_session` — Begin a new session
`/end_session` — End current session
`/add_from_voice [channel]` — Add all players in a VC
`/add_player [member]` — Add a specific player
`/remove_player [member]` — Remove a player
`/clear_players` — Clear the roster
""", inline=False)

            embed.add_field(name="⚔️ Admin — Teams", value="""
`/make_teams` — Randomly split + assign roles
`/start_draft` — Captain snake draft
`/random_roles all` — Reassign all session roles
""", inline=False)

            embed.add_field(name="⚙️ Admin — Settings & Players", value="""
`/settings` — View server settings
`/configure_channels` — Set Team 1/2/Lobby VCs
`/toggle_setting` — Toggle features
`/admin_register [member]` — Manually register a player
`/set_weight [member] [1-10]` — Set power ranking weight
`/view_weights` — View all power weights
""", inline=False)

        embed.set_footer(text="Power ranking weights and settings are admin-only. Win result buttons appear after teams are set.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
