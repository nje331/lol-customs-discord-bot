"""
Settings Cog
Handles: configuring voice channels, mod channel, feature toggles, numeric settings, help command.
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
        self.mod_ch_id: str = None

        vc_options = [
            discord.SelectOption(label=vc.name, value=str(vc.id))
            for vc in guild.voice_channels
        ]
        tc_options = [
            discord.SelectOption(label=tc.name, value=str(tc.id))
            for tc in guild.text_channels
        ]
        if not vc_options:
            return

        def make_select(placeholder, attr, options):
            sel = discord.ui.Select(placeholder=placeholder, options=options)

            async def callback(inter: discord.Interaction):
                setattr(self, attr, inter.data["values"][0])
                await self._update(inter)

            sel.callback = callback
            return sel

        self.add_item(make_select("Select Team 1 Voice Channel", "team1_id", vc_options))
        self.add_item(make_select("Select Team 2 Voice Channel", "team2_id", vc_options))
        self.add_item(make_select("Select Lobby / Return Channel", "lobby_id", vc_options))
        if tc_options:
            self.add_item(make_select("Select Mod Log Text Channel", "mod_ch_id", tc_options))

        save_btn = discord.ui.Button(label="Save Channels", style=discord.ButtonStyle.success, emoji="💾")
        save_btn.callback = self._save
        self.add_item(save_btn)

    def _ch_name(self, cid, guild):
        if not cid:
            return "_not set_"
        ch = guild.get_channel(int(cid))
        return f"**{ch.name}**" if ch else f"_unknown ({cid})_"

    def _status_text(self, guild) -> str:
        return (
            f"🔵 Team 1: {self._ch_name(self.team1_id, guild)}\n"
            f"🔴 Team 2: {self._ch_name(self.team2_id, guild)}\n"
            f"🏠 Lobby:  {self._ch_name(self.lobby_id, guild)}\n"
            f"🔧 Mod Log: {self._ch_name(self.mod_ch_id, guild)}"
        )

    async def _update(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=self._status_text(interaction.guild), view=self
        )

    async def _save(self, interaction: discord.Interaction):
        self.stop()
        gid = str(interaction.guild_id)
        if self.team1_id:
            await self.db.update_setting(gid, "team1_channel_id", self.team1_id)
        if self.team2_id:
            await self.db.update_setting(gid, "team2_channel_id", self.team2_id)
        if self.lobby_id:
            await self.db.update_setting(gid, "lobby_channel_id", self.lobby_id)
        if self.mod_ch_id:
            await self.db.update_setting(gid, "mod_channel_id", self.mod_ch_id)

        embed = build_embed(
            "Channels Saved",
            self._status_text(interaction.guild),
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
            name="Text Channels",
            value=f"🔧 Mod Log: {ch_name(s.get('mod_channel_id'))}",
            inline=False
        )
        embed.add_field(
            name="Features",
            value=(
                f"Champ Weight (play rate): {'✅ ON' if s.get('champ_weight_enabled') else '❌ OFF'}\n"
                f"Champ Rerolls per game: **{s.get('champ_rerolls', 0)}** "
                f"({'disabled' if not s.get('champ_rerolls') else 'per player'})\n"
                f"Peer Ratings (post-game DM): {'✅ ON' if s.get('peer_ratings_enabled') else '❌ OFF'}"
            ),
            inline=False
        )
        embed.set_footer(text="Use /configure_channels, /toggle_setting, and /set_champ_rerolls to change.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="configure_channels", description="[Admin] Set Team 1, Team 2, Lobby voice channels and Mod Log text channel.")
    @is_admin()
    async def configure_channels(self, interaction: discord.Interaction):
        if not interaction.guild.voice_channels:
            await interaction.response.send_message("This server has no voice channels.", ephemeral=True)
            return
        view = ChannelSelectView(interaction.guild, self.db)
        await interaction.response.send_message(
            "Select channels for each role (voice for teams/lobby, text for mod log):",
            view=view, ephemeral=True
        )

    @app_commands.command(name="toggle_setting", description="[Admin] Toggle a bot feature on or off.")
    @app_commands.choices(setting=[
        app_commands.Choice(name="Champion Weight (use play rate for random champ picks)", value="champ_weight_enabled"),
        app_commands.Choice(name="Peer Ratings (send post-game rating DMs to players)", value="peer_ratings_enabled"),
    ])
    @is_admin()
    async def toggle_setting(self, interaction: discord.Interaction, setting: str):
        s = await self.db.get_settings(str(interaction.guild_id))
        current = bool(s.get(setting, 0))
        new_val = 0 if current else 1
        await self.db.update_setting(str(interaction.guild_id), setting, new_val)
        state = "✅ **ON**" if new_val else "❌ **OFF**"
        labels = {
            "champ_weight_enabled": "Champion Weight (play rate)",
            "peer_ratings_enabled": "Peer Ratings (post-game DM)",
        }
        await interaction.response.send_message(
            f"**{labels.get(setting, setting)}** is now {state}.", ephemeral=True
        )

    @app_commands.command(name="set_champ_rerolls", description="[Admin] Set how many champion rerolls each player gets per game (0 = disabled).")
    @app_commands.describe(count="Number of rerolls per player per game (0 to disable)")
    @is_admin()
    async def set_champ_rerolls(self, interaction: discord.Interaction, count: int):
        if count < 0:
            await interaction.response.send_message("Count must be 0 or higher.", ephemeral=True)
            return
        await self.db.update_setting(str(interaction.guild_id), "champ_rerolls", count)
        if count == 0:
            msg = "✅ Champion rerolls **disabled**."
        else:
            msg = f"✅ Champion rerolls set to **{count}** per player per game."
        await interaction.response.send_message(msg, ephemeral=True)

    # ── /view_ratings ──────────────────────────────────────────────────────────

    @app_commands.command(name="view_ratings", description="[Admin] View peer rating scores and engagement metrics for all players.")
    @is_admin()
    async def view_ratings(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        ratings   = await self.db.get_all_ratings(guild_id)
        engagement = await self.db.get_all_engagement(guild_id)

        # Index for quick lookup
        eng_map = {e["discord_id"]: e for e in engagement}

        # Gather all player names
        all_ids = set(r["discord_id"] for r in ratings) | set(e["discord_id"] for e in engagement)
        player_names: dict[str, str] = {}
        for did in all_ids:
            p = await self.db.get_player(did, guild_id)
            if p:
                player_names[did] = p["display_name"]
            else:
                member = interaction.guild.get_member(int(did))
                player_names[did] = member.display_name if member else f"<@{did}>"

        # Also get games_played for each player
        games_map: dict[str, int] = {}
        for did in all_ids:
            p = await self.db.get_player(did, guild_id)
            games_map[did] = p["games_played"] if p else 0

        embed = build_embed("Peer Ratings & Engagement", color_key="gold")

        # ── Received ratings ──────────────────────────────────────────────────
        if ratings:
            lines = []
            for r in sorted(ratings, key=lambda x: -(x["rating_sum"] / x["rating_count"] if x["rating_count"] else 0)):
                did = r["discord_id"]
                if r["rating_count"] == 0:
                    continue
                avg = round(r["rating_sum"] / r["rating_count"], 2)
                name = player_names.get(did, did)
                lines.append(f"**{name}** — ⭐ {avg}/5 ({r['rating_count']} ratings)")
            if lines:
                embed.add_field(name="⭐ Avg Rating Received", value="\n".join(lines[:20]), inline=False)
            else:
                embed.add_field(name="⭐ Avg Rating Received", value="_No ratings yet._", inline=False)
        else:
            embed.add_field(name="⭐ Avg Rating Received", value="_No ratings yet._", inline=False)

        # ── Engagement ────────────────────────────────────────────────────────
        if engagement:
            lines = []
            for e in sorted(engagement, key=lambda x: -(x["ratings_given"])):
                did = e["discord_id"]
                name = player_names.get(did, did)
                gp = games_map.get(did, 0)
                gwith = e["games_with_ratings"]
                # Engagement rate: rating sessions completed / games played (when enabled)
                eng_rate = round(gwith / gp * 100, 1) if gp > 0 else 0.0
                avg_given = round(e["rating_sum_given"] / e["ratings_given"], 2) if e["ratings_given"] > 0 else "—"
                lines.append(
                    f"**{name}** — {gwith}/{gp} games rated ({eng_rate}%) · "
                    f"avg given: {avg_given} · total given: {e['ratings_given']}"
                )
            if lines:
                embed.add_field(name="📊 Rating Engagement", value="\n".join(lines[:20]), inline=False)
            else:
                embed.add_field(name="📊 Rating Engagement", value="_No engagement data yet._", inline=False)
        else:
            embed.add_field(name="📊 Rating Engagement", value="_No engagement data yet._", inline=False)

        embed.set_footer(text="Engagement rate = rating sessions completed ÷ games played (only when peer ratings were enabled).")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
                "`/session_settings` — View or change repeat_roles / auto_balance mid-session\n"
                "`/add_from_voice [channel]` — Grab players from a VC\n"
                "`/add_player @p1 [@p2...]` — Add up to 5 players at once\n"
                "`/remove_player [member]` — Remove a player\n"
                "`/clear_players` — Clear the roster\n"
                "`/make_teams` — Random split + optional role assignment\n"
                "`/start_draft` — Captain snake draft"
            ), inline=False)

        if is_admin_user:
            embed.add_field(name="🔧 Admin", value=(
                "`/settings` — View server settings\n"
                "`/configure_channels` — Set Team 1/2/Lobby VCs and Mod Log channel\n"
                "`/toggle_setting` — Toggle features (Champ Weight, Peer Ratings)\n"
                "`/set_champ_rerolls [count]` — Rerolls per player per game\n"
                "`/view_ratings` — View peer rating scores and engagement\n"
                "`/view_elo [type]` — View ELO leaderboard\n"
                "`/elo_history [type] [member]` — ELO history chart\n"
                "`/reset_stats` — Reset all players' stats, ELOs, and ELO history\n"
                "`/admin_register [member]` — Manually register a player\n"
                "`/add_bot_admin [member]` — Grant bot admin to a user\n"
                "`/remove_bot_admin [member]` — Revoke bot admin\n"
                "`/list_bot_admins` — List all bot admins"
            ), inline=False)

        embed.set_footer(text=(
            "Roles are always tracked — use /session_settings to allow repeat_roles. "
            "Set auto_balance when starting a session or via /session_settings."
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))