"""
Session Cog
Handles: creating/ending sessions, adding/removing players, voice channel grabbing.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import ROLE_EMOJIS, build_embed, fmt_player, is_admin


class Session(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _require_session(self, interaction: discord.Interaction) -> dict | None:
        session = await self.db.get_active_session(str(interaction.guild_id))
        if not session:
            await interaction.response.send_message(
                "No active session. Use `/start_session` first.", ephemeral=True
            )
        return session

    # ── /start_session ────────────────────────────────────────────────────────

    @app_commands.command(name="start_session", description="[Admin] Start a new custom game session.")
    @is_admin()
    async def start_session(self, interaction: discord.Interaction):
        existing = await self.db.get_active_session(str(interaction.guild_id))
        if existing:
            class ConfirmView(discord.ui.View):
                def __init__(self_v):
                    super().__init__(timeout=30)

                @discord.ui.button(label="End old & start new", style=discord.ButtonStyle.danger)
                async def confirm(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                    self_v.stop()
                    await self.db.end_session(existing["id"])
                    sid = await self.db.create_session(str(btn_inter.guild_id))
                    await btn_inter.response.edit_message(
                        content=f"✅ New session started (ID #{sid}). Previous session closed.",
                        view=None
                    )

                @discord.ui.button(label="Keep current session", style=discord.ButtonStyle.secondary)
                async def cancel(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                    self_v.stop()
                    await btn_inter.response.edit_message(content="Kept current session.", view=None)

            await interaction.response.send_message(
                "⚠️ There's already an active session. End it and start a new one?",
                view=ConfirmView(), ephemeral=True
            )
            return

        sid = await self.db.create_session(str(interaction.guild_id))
        embed = build_embed(
            "🎮 Session Started!",
            f"Session **#{sid}** is now active.\n"
            "• Use `/add_from_voice` to grab players from a voice channel\n"
            "• Use `/add_player` to add individuals\n"
            "• Use `/session_players` to see the current list",
            "green"
        )
        await interaction.response.send_message(embed=embed)

    # ── /end_session ──────────────────────────────────────────────────────────

    @app_commands.command(name="end_session", description="[Admin] End the current session.")
    @is_admin()
    async def end_session(self, interaction: discord.Interaction):
        session = await self._require_session(interaction)
        if not session:
            return

        class ConfirmView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, end session", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await self.db.end_session(session["id"])
                await btn_inter.response.edit_message(
                    content=f"✅ Session #{session['id']} ended.", view=None
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            f"⚠️ End session #{session['id']}? This cannot be undone.",
            view=ConfirmView(), ephemeral=True
        )

    # ── /add_from_voice ───────────────────────────────────────────────────────

    @app_commands.command(
        name="add_from_voice",
        description="[Admin] Add all members from a voice channel to the session."
    )
    @app_commands.describe(channel="The voice channel to pull players from")
    @is_admin()
    async def add_from_voice(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        session = await self._require_session(interaction)
        if not session:
            return

        if not channel.members:
            await interaction.response.send_message("That channel is empty.", ephemeral=True)
            return

        added = []
        auto_registered = []
        for member in channel.members:
            if member.bot:
                continue
            player = await self.db.get_player(str(member.id), str(interaction.guild_id))
            if not player:
                await self.db.upsert_player(str(member.id), str(interaction.guild_id), member.display_name, [])
                auto_registered.append(member.display_name)
            await self.db.add_session_player(session["id"], str(member.id), str(interaction.guild_id))
            added.append(member.display_name)

        players = await self.db.get_session_players(session["id"], str(interaction.guild_id))
        desc = f"Added **{len(added)}** player(s) from **{channel.name}**."
        if auto_registered:
            desc += f"\n🆕 Auto-registered: {', '.join(auto_registered)}"
        desc += f"\n\n**Session roster ({len(players)} players):**\n"
        desc += "\n".join(f"• {fmt_player(p, show_stats=False)}" for p in players)

        embed = build_embed("👥 Players Added", desc, "blue")
        await interaction.response.send_message(embed=embed)

    # ── /add_player ───────────────────────────────────────────────────────────

    @app_commands.command(name="add_player", description="[Admin] Add a specific player to the session.")
    @app_commands.describe(member="The member to add")
    @is_admin()
    async def add_player(self, interaction: discord.Interaction, member: discord.Member):
        session = await self._require_session(interaction)
        if not session:
            return

        player = await self.db.get_player(str(member.id), str(interaction.guild_id))
        if not player:
            await self.db.upsert_player(str(member.id), str(interaction.guild_id), member.display_name, [])
            note = " (auto-registered)"
        else:
            note = ""

        await self.db.add_session_player(session["id"], str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ Added **{member.display_name}**{note} to the session.", ephemeral=True
        )

    # ── /remove_player ────────────────────────────────────────────────────────

    @app_commands.command(name="remove_player", description="[Admin] Remove a player from the current session.")
    @app_commands.describe(member="The member to remove")
    @is_admin()
    async def remove_player(self, interaction: discord.Interaction, member: discord.Member):
        session = await self._require_session(interaction)
        if not session:
            return

        await self.db.remove_session_player(session["id"], str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ Removed **{member.display_name}** from the session.", ephemeral=True
        )

    # ── /session_players ──────────────────────────────────────────────────────

    @app_commands.command(name="session_players", description="Show all players in the current session.")
    async def session_players(self, interaction: discord.Interaction):
        session = await self._require_session(interaction)
        if not session:
            return

        players = await self.db.get_session_players(session["id"], str(interaction.guild_id))
        if not players:
            await interaction.response.send_message("No players in this session yet.", ephemeral=True)
            return

        lines = [fmt_player(p, show_stats=False) for p in players]
        embed = build_embed(
            f"👥 Session #{session['id']} Roster ({len(players)} players)",
            "\n".join(lines),
            "blue"
        )
        await interaction.response.send_message(embed=embed)

    # ── /clear_players ────────────────────────────────────────────────────────

    @app_commands.command(name="clear_players", description="[Admin] Remove all players from the session roster.")
    @is_admin()
    async def clear_players(self, interaction: discord.Interaction):
        session = await self._require_session(interaction)
        if not session:
            return

        class ConfirmView(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, clear roster", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await self.db.db.execute(
                    "DELETE FROM session_players WHERE session_id=?", (session["id"],)
                )
                await self.db.db.commit()
                await btn_inter.response.edit_message(content="✅ Roster cleared.", view=None)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, button: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "⚠️ Remove all players from the session roster?",
            view=ConfirmView(), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Session(bot))
