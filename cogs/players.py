"""
Players Cog
Handles: player registration, role preferences, stats, leaderboard, bot admin management.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils import ROLES, ROLE_EMOJIS, build_embed, fmt_player, is_admin, check_is_admin

# Discord Select menus cap at 25 options
_SELECT_MAX = 25


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
        self._message: discord.Message = None
        self._rebuild()

    async def on_timeout(self):
        if not self._message:
            return
        try:
            await self._message.edit(
                content="⏰ Role selection timed out. Run the command again to set your preferences.",
                view=None,
            )
        except Exception:
            pass

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


# ── Bot Admins view ───────────────────────────────────────────────────────────

class AdminsView(discord.ui.View):
    """
    Shown by /admins.
    Lists: explicitly-added bot admins + members who pass the Discord admin check.
    Row 0: [➕ Add Admin] [➖ Remove Admin]
    Add  → dropdown of server members who are NOT already bot admins or Discord admins.
    Remove → dropdown of ONLY explicitly-added bot admins (Discord admins can't be removed here).
    """

    def __init__(self, db, guild: discord.Guild, guild_id: str,
                 bot_admin_ids: list[str], invoker_id: int):
        super().__init__(timeout=180)
        self.db           = db
        self.guild        = guild
        self.guild_id     = guild_id
        self.bot_admin_ids = list(bot_admin_ids)
        self.invoker_id   = invoker_id
        self._message: discord.Message = None
        self._rebuild()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _is_discord_admin(self, member: discord.Member) -> bool:
        return (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_guild
        )

    def build_embed(self) -> discord.Embed:
        embed = build_embed("Bot Admins", color_key="gray")

        # Discord admins (always have access, can't be removed via bot)
        discord_admins = [
            m for m in self.guild.members
            if not m.bot and self._is_discord_admin(m)
        ]
        if discord_admins:
            embed.add_field(
                name="🔐 Discord Admins (always have access)",
                value="\n".join(f"• {m.display_name}" for m in discord_admins[:20]),
                inline=False,
            )

        # Explicit bot admins
        if self.bot_admin_ids:
            lines = []
            for did in self.bot_admin_ids:
                m = self.guild.get_member(int(did))
                lines.append(f"• {m.display_name if m else f'Unknown ({did})'}")
            embed.add_field(
                name="🤖 Bot Admins (added via bot)",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="🤖 Bot Admins (added via bot)",
                value="_None added yet._",
                inline=False,
            )

        embed.set_footer(text="Use the buttons to add or remove bot admins.")
        return embed

    def _rebuild(self):
        self.clear_items()

        # Determine who can be added: non-bot members who aren't already Discord admins
        # or explicit bot admins
        existing = set(self.bot_admin_ids)
        addable  = [
            m for m in self.guild.members
            if not m.bot
            and str(m.id) not in existing
            and not self._is_discord_admin(m)
        ][:_SELECT_MAX]

        add_btn = discord.ui.Button(
            label="➕ Add Admin",
            style=discord.ButtonStyle.success,
            row=0,
            disabled=(len(addable) == 0),
        )
        add_btn.callback = self._add_admin
        self.add_item(add_btn)

        rm_btn = discord.ui.Button(
            label="➖ Remove Admin",
            style=discord.ButtonStyle.danger,
            row=0,
            disabled=(len(self.bot_admin_ids) == 0),
        )
        rm_btn.callback = self._remove_admin
        self.add_item(rm_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the admin who opened this panel can use these buttons.", ephemeral=True
            )
            return False
        return True

    async def _update_panel(self):
        self.bot_admin_ids = await self.db.get_bot_admins(self.guild_id)
        self._rebuild()
        if self._message:
            try:
                await self._message.edit(embed=self.build_embed(), view=self)
            except Exception:
                pass

    # ── Add ───────────────────────────────────────────────────────────────────

    async def _add_admin(self, interaction: discord.Interaction):
        existing = set(self.bot_admin_ids)
        addable  = [
            m for m in self.guild.members
            if not m.bot
            and str(m.id) not in existing
            and not self._is_discord_admin(m)
        ][:_SELECT_MAX]

        if not addable:
            await interaction.response.send_message(
                "No eligible members to add.", ephemeral=True
            )
            return

        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in addable
        ]

        class AddSelect(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=60)
                sel = discord.ui.Select(
                    placeholder="Choose a member to make bot admin…",
                    options=options,
                )
                sel.callback = self_v._on_select
                self_v.add_item(sel)

            async def _on_select(self_v, inter: discord.Interaction):
                did    = inter.data["values"][0]
                member = self.guild.get_member(int(did))
                self_v.stop()
                await self.db.add_bot_admin(did, self.guild_id)
                name = member.display_name if member else f"User {did}"
                await inter.response.edit_message(
                    content=f"✅ **{name}** is now a bot admin.", view=None
                )
                await self._update_panel()

        await interaction.response.send_message(
            "Select a member to grant bot admin:", view=AddSelect(), ephemeral=True
        )

    # ── Remove ────────────────────────────────────────────────────────────────

    async def _remove_admin(self, interaction: discord.Interaction):
        if not self.bot_admin_ids:
            await interaction.response.send_message(
                "No bot admins to remove.", ephemeral=True
            )
            return

        options = []
        for did in self.bot_admin_ids[:_SELECT_MAX]:
            m = self.guild.get_member(int(did))
            label = m.display_name if m else f"Unknown ({did})"
            options.append(discord.SelectOption(label=label, value=did))

        class RemoveSelect(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=60)
                sel = discord.ui.Select(
                    placeholder="Choose a bot admin to remove…",
                    options=options,
                )
                sel.callback = self_v._on_select
                self_v.add_item(sel)

            async def _on_select(self_v, inter: discord.Interaction):
                did    = inter.data["values"][0]
                member = self.guild.get_member(int(did))
                self_v.stop()
                await self.db.remove_bot_admin(did, self.guild_id)
                name = member.display_name if member else f"User {did}"
                await inter.response.edit_message(
                    content=f"✅ **{name}** is no longer a bot admin.", view=None
                )
                await self._update_panel()

        await interaction.response.send_message(
            "Select a bot admin to remove:", view=RemoveSelect(), ephemeral=True
        )

    async def on_timeout(self):
        if self._message:
            for item in self.children:
                item.disabled = True
            try:
                await self._message.edit(view=self)
            except Exception:
                pass


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
        view._message = await interaction.original_response()

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

    # ── Admin: /players ───────────────────────────────────────────────────────

    @app_commands.command(name="players", description="[Admin] List all registered players and their role preferences.")
    @is_admin()
    async def players_cmd(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        all_players = await self.db.get_all_players(guild_id)
        if not all_players:
            await interaction.response.send_message("No players registered yet.", ephemeral=True)
            return

        lines = []
        for p in all_players:
            roles = " > ".join(f"{ROLE_EMOJIS.get(r, r)} {r}" for r in p["role_prefs"]) or "_none_"
            lines.append(f"**{p['display_name']}** — {roles}")

        embed = build_embed(
            f"Registered Players ({len(all_players)})",
            "\n".join(lines),
            color_key="blue",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Admin: /admins ───────────────────────────────────────────────────────

    @app_commands.command(
        name="admins",
        description="[Admin] View, add, and remove bot admins for this server.",
    )
    @is_admin()
    async def admins(self, interaction: discord.Interaction):
        guild_id      = str(interaction.guild_id)
        bot_admin_ids = await self.db.get_bot_admins(guild_id)

        view = AdminsView(
            db=self.db,
            guild=interaction.guild,
            guild_id=guild_id,
            bot_admin_ids=bot_admin_ids,
            invoker_id=interaction.user.id,
        )
        await interaction.response.send_message(
            embed=view.build_embed(), view=view, ephemeral=True
        )
        view._message = await interaction.original_response()


    # ── Admin: /reset_stats ────────────────────────────────────────────

    @app_commands.command(name="reset_stats", description="[Admin] Reset all players' stats, ELOs, and ELO history for this server.")
    @is_admin()
    async def reset_stats(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)

        class ConfirmReset(discord.ui.View):
            def __init__(self_v):
                super().__init__(timeout=30)

            @discord.ui.button(label="Yes, reset everything", style=discord.ButtonStyle.danger)
            async def confirm(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await self.db.reset_leaderboard(guild_id)
                await btn_inter.response.edit_message(
                    content="✅ All players' wins, losses, ELOs (reset to 1500), and ELO history have been wiped.", view=None
                )

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
            async def cancel(self_v, btn_inter: discord.Interaction, btn: discord.ui.Button):
                self_v.stop()
                await btn_inter.response.edit_message(content="Cancelled.", view=None)

        await interaction.response.send_message(
            "⚠️ This will reset **all players'** wins, losses, ELOs (back to 1500), and wipe all ELO history. **This cannot be undone.**",
            view=ConfirmReset(),
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Players(bot))