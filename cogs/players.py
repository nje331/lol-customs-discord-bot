"""
Players Cog
Handles: player registration, role preferences, stats, leaderboard, bot admin management.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import ROLES, ROLE_EMOJIS, build_embed, fmt_player, is_admin, check_is_admin


def _build_role_select_view(author_id: int, callback, existing: list = None):
    """Build and return a RoleSelectView."""
    return RoleSelectView(author_id, callback, existing=existing or [])


class RoleSelectView(discord.ui.View):
    """Multi-step view for selecting role preferences in priority order."""

    def __init__(self, author_id: int, callback, *, existing: list = None):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.callback = callback
        self.selected: list[str] = []
        self.existing = existing or []
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        remaining = [r for r in ROLES if r not in self.selected]

        if remaining and len(self.selected) < 5:
            select = discord.ui.Select(
                placeholder=f"Pick role #{len(self.selected)+1} (priority order)...",
                options=[
                    discord.SelectOption(
                        label=r,
                        emoji=ROLE_EMOJIS[r],
                        description=f"Set as priority {len(self.selected)+1}"
                    ) for r in remaining
                ]
            )
            select.callback = self._on_select
            self.add_item(select)

        if self.selected:
            done_btn = discord.ui.Button(
                label=f"Done ({len(self.selected)} selected)",
                style=discord.ButtonStyle.success,
                emoji="✅"
            )
            done_btn.callback = self._on_done
            self.add_item(done_btn)

            clear_btn = discord.ui.Button(label="Reset", style=discord.ButtonStyle.secondary, emoji="🔄")
            clear_btn.callback = self._on_reset
            self.add_item(clear_btn)

        if self.existing:
            keep_btn = discord.ui.Button(
                label="Keep existing",
                style=discord.ButtonStyle.secondary
            )
            keep_btn.callback = self._on_keep
            self.add_item(keep_btn)

        no_pref_btn = discord.ui.Button(
            label="Clear Preferences",
            emoji="✖️",
            style=discord.ButtonStyle.red
        )
        no_pref_btn.callback = self._on_no_pref
        self.add_item(no_pref_btn)

    async def _check_author(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your menu!", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        role = interaction.data["values"][0]
        self.selected.append(role)
        self._rebuild()
        chosen = " > ".join(f"{ROLE_EMOJIS[r]} {r}" for r in self.selected)
        await interaction.response.edit_message(
            content=f"Priority order so far: {chosen}",
            view=self
        )

    async def _on_done(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        self.stop()
        await self.callback(interaction, self.selected)

    async def _on_reset(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        self.selected = []
        self._rebuild()
        await interaction.response.edit_message(
            content="Preferences cleared. Pick again:", view=self
        )

    async def _on_keep(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        self.stop()
        await self.callback(interaction, self.existing)

    async def _on_no_pref(self, interaction: discord.Interaction):
        if not await self._check_author(interaction):
            return
        self.stop()
        await self.callback(interaction, [])


class Players(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _run_register_flow(self, interaction: discord.Interaction):
        """Shared logic for register and edit_roles."""
        existing = await self.db.get_player(str(interaction.user.id), str(interaction.guild_id))

        async def _save(inter: discord.Interaction, roles: list):
            await self.db.upsert_player(
                str(inter.user.id),
                str(inter.guild_id),
                inter.user.display_name,
                roles
            )
            role_str = " > ".join(f"{ROLE_EMOJIS[r]} {r}" for r in roles) if roles else "No preference (fill any role)"
            embed = build_embed(
                "Registered!",
                f"**{inter.user.display_name}**\nRole preferences: {role_str}",
                "green"
            )
            await inter.response.edit_message(content=None, embed=embed, view=None)

        view = RoleSelectView(
            interaction.user.id,
            _save,
            existing=existing["role_prefs"] if existing else []
        )
        msg = "Update your role preferences:" if existing else "Welcome! Set your role preferences (priority order):"
        await interaction.response.send_message(msg, view=view, ephemeral=True)

    # ── /register ────────────────────────────────────────────────────────────

    @app_commands.command(name="register", description="Register with the LoL bot and set role preferences.")
    async def register(self, interaction: discord.Interaction):
        await self._run_register_flow(interaction)

    # ── /edit_roles ──────────────────────────────────────────────────────────

    @app_commands.command(name="edit_roles", description="Update your role preferences.")
    async def edit_roles(self, interaction: discord.Interaction):
        player = await self.db.get_player(str(interaction.user.id), str(interaction.guild_id))
        if not player:
            await interaction.response.send_message(
                "You're not registered yet. Use `/register` first.", ephemeral=True
            )
            return
        await self._run_register_flow(interaction)

    # ── /unregister ──────────────────────────────────────────────────────────

    @app_commands.command(name="unregister", description="Remove yourself from the bot database.")
    async def unregister(self, interaction: discord.Interaction):
        player = await self.db.get_player(str(interaction.user.id), str(interaction.guild_id))
        if not player:
            await interaction.response.send_message("You're not registered.", ephemeral=True)
            return

        db = self.db

        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, remove me", style=discord.ButtonStyle.danger)
            async def confirm(self, btn_inter: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await db.delete_player(str(btn_inter.user.id), str(btn_inter.guild_id))
                await btn_inter.response.edit_message(
                    content="You have been removed from the database.", view=None
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self, btn_inter: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "Are you sure? This will delete your stats and preferences.",
            view=ConfirmView(), ephemeral=True
        )

    # ── /stats ───────────────────────────────────────────────────────────────

    @app_commands.command(name="stats", description="View your stats (or another player's).")
    @app_commands.describe(member="The player to look up (leave blank for yourself)")
    async def stats(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        player = await self.db.get_player(str(target.id), str(interaction.guild_id))
        if not player:
            await interaction.response.send_message(
                f"{'They are' if member else 'You are'} not registered.", ephemeral=True
            )
            return

        gp = player["games_played"]
        gw = player["games_won"]
        gl = player["games_lost"]
        wr = round(gw / gp * 100, 1) if gp > 0 else 0
        roles = " > ".join(f"{ROLE_EMOJIS.get(r, r)} {r}" for r in player["role_prefs"]) or "No preference"

        embed = build_embed(f"Stats — {player['display_name']}", color_key="gold")
        embed.add_field(name="Games Played", value=str(gp), inline=True)
        embed.add_field(name="Wins", value=str(gw), inline=True)
        embed.add_field(name="Losses", value=str(gl), inline=True)
        embed.add_field(name="Win Rate", value=f"{wr}%", inline=True)
        embed.add_field(name="Role Preferences", value=roles, inline=False)
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /leaderboard ─────────────────────────────────────────────────────────

    @app_commands.command(name="leaderboard", description="Show server win-rate leaderboard.")
    async def leaderboard(self, interaction: discord.Interaction):
        players = await self.db.get_leaderboard(str(interaction.guild_id))
        if not players:
            await interaction.response.send_message("No stats recorded yet!", ephemeral=True)
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(players[:15]):
            wr = round(p["games_won"] / p["games_played"] * 100, 1) if p["games_played"] > 0 else 0
            rank = medals[i] if i < 3 else f"`#{i+1}`"
            lines.append(
                f"{rank} **{p['display_name']}** — {p['games_won']}W/{p['games_lost']}L ({wr}%)"
            )

        embed = build_embed("Leaderboard", "\n".join(lines), "gold")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Admin: /admin_register ────────────────────────────────────────────────

    @app_commands.command(name="admin_register", description="[Admin] Register a player manually.")
    @app_commands.describe(member="The member to register")
    @is_admin()
    async def admin_register(self, interaction: discord.Interaction, member: discord.Member):
        await self.db.upsert_player(str(member.id), str(interaction.guild_id), member.display_name, [])
        await interaction.response.send_message(
            f"✅ {member.mention} registered with no role preferences.", ephemeral=True
        )

    # ── Admin: /set_weight ────────────────────────────────────────────────────

    @app_commands.command(name="set_weight", description="[Admin] Set a player's power ranking weight (1-10).")
    @app_commands.describe(member="The player", weight="Power weight 1 (weakest) to 10 (strongest)")
    @is_admin()
    async def set_weight(self, interaction: discord.Interaction, member: discord.Member, weight: float):
        if not (1.0 <= weight <= 10.0):
            await interaction.response.send_message("Weight must be between 1 and 10.", ephemeral=True)
            return
        player = await self.db.get_player(str(member.id), str(interaction.guild_id))
        if not player:
            await interaction.response.send_message(f"{member.display_name} is not registered.", ephemeral=True)
            return
        await self.db.update_player_weight(str(member.id), str(interaction.guild_id), weight)
        await interaction.response.send_message(
            f"✅ Set **{member.display_name}**'s power weight to **{weight}**.", ephemeral=True
        )

    @app_commands.command(name="view_weights", description="[Admin] View all players' power weights.")
    @is_admin()
    async def view_weights(self, interaction: discord.Interaction):
        players = await self.db.get_all_players(str(interaction.guild_id))
        if not players:
            await interaction.response.send_message("No players registered.", ephemeral=True)
            return
        lines = [f"**{p['display_name']}** — Weight: **{p['power_weight']}**" for p in players]
        embed = build_embed("Power Weights (Admin Only)", "\n".join(lines), "gray")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Admin: /add_bot_admin / /remove_bot_admin ─────────────────────────────

    @app_commands.command(name="add_bot_admin", description="[Admin] Grant a user bot-admin privileges.")
    @app_commands.describe(member="The member to promote")
    @is_admin()
    async def add_bot_admin(self, interaction: discord.Interaction, member: discord.Member):
        await self.db.add_bot_admin(str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ **{member.display_name}** is now a bot admin.", ephemeral=True
        )

    @app_commands.command(name="remove_bot_admin", description="[Admin] Remove a user's bot-admin privileges.")
    @app_commands.describe(member="The member to demote")
    @is_admin()
    async def remove_bot_admin(self, interaction: discord.Interaction, member: discord.Member):
        await self.db.remove_bot_admin(str(member.id), str(interaction.guild_id))
        await interaction.response.send_message(
            f"✅ **{member.display_name}** is no longer a bot admin.", ephemeral=True
        )

    @app_commands.command(name="list_bot_admins", description="[Admin] List all bot admins in this server.")
    @is_admin()
    async def list_bot_admins(self, interaction: discord.Interaction):
        admin_ids = await self.db.get_bot_admins(str(interaction.guild_id))
        if not admin_ids:
            await interaction.response.send_message("No custom bot admins set.", ephemeral=True)
            return
        lines = []
        for did in admin_ids:
            member = interaction.guild.get_member(int(did))
            lines.append(f"• {member.display_name if member else f'Unknown ({did})'}")
        embed = build_embed("Bot Admins", "\n".join(lines), "gray")
        await interaction.response.send_message(embed=embed, ephemeral=True)


    # ── Admin: /reset_leaderboard ─────────────────────────────────────────────

    @app_commands.command(name="reset_leaderboard", description="[Admin] Reset all players' wins and losses to 0.")
    @is_admin()
    async def reset_leaderboard(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        class ConfirmReset(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, reset everything", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await self.db.reset_leaderboard(guild_id)
                await btn_inter.response.edit_message(
                    content="✅ All players' wins and losses have been reset to 0.", view=None
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "⚠️ This will reset **all players'** wins and losses to 0. This cannot be undone.",
            view=ConfirmReset(),
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Players(bot))