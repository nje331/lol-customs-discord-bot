"""
Champions Cog
Syncs League of Legends champion role statistics from CommunityDragon.
Run /update_champs once to populate the DB; data is used by /make_teams random_champs:True.

Custom champion commands (bot-admin only):
  /add_custom_champ    — add a champion/role entry that persists across patch syncs
  /remove_custom_champ — remove a custom entry by name + role
  /clear_custom_champs — remove ALL custom entries for this server
  /view_champs         — paginated role browser showing synced + custom entries,
                         with inline Add / Remove buttons
"""

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import json
import re
import logging

from utils import build_embed, is_admin

log = logging.getLogger(__name__)

# CommunityDragon role keys we care about
VALID_ROLES = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "SUPPORT"}

# Display order and labels
ROLE_ORDER  = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "SUPPORT"]
ROLE_LABELS = {
    "TOP":     "🛡️ Top",
    "JUNGLE":  "🌿 Jungle",
    "MIDDLE":  "⚡ Mid",
    "BOTTOM":  "🏹 Bot",
    "SUPPORT": "💊 Support",
}
# User-facing choice label → DB role key
ROLE_CHOICES = {
    "Top":     "TOP",
    "Jungle":  "JUNGLE",
    "Mid":     "MIDDLE",
    "Bot":     "BOTTOM",
    "Support": "SUPPORT",
}

# Max synced champs shown per page in /view_champs
PAGE_SIZE = 20


# ── Embed builder ─────────────────────────────────────────────────────────────

def _build_role_embed(
    role: str,
    synced: list[dict],
    custom: list[dict],
    page: int,
    total_pages: int,
) -> discord.Embed:
    label = ROLE_LABELS.get(role, role)
    embed = build_embed(f"Champion Pool — {label}", color_key="gold")

    # Synced (paginated)
    start      = page * PAGE_SIZE
    page_champs = synced[start : start + PAGE_SIZE]
    if page_champs:
        lines = []
        for c in page_champs:
            pr = c.get("play_rate", 0)
            pr_str = f"{pr:.1%}" if isinstance(pr, float) and pr <= 1 else f"{pr:.2f}"
            lines.append(f"• {c['name']} _(play rate: {pr_str})_")
        synced_value = "\n".join(lines)
    else:
        synced_value = "_No synced data — run `/update_champs` first._"

    embed.add_field(
        name=f"📋 Synced (page {page + 1}/{max(total_pages, 1)})",
        value=synced_value,
        inline=False,
    )

    # Custom
    if custom:
        embed.add_field(
            name=f"✏️ Custom ({len(custom)})",
            value="\n".join(f"⭐ {c['name']}" for c in custom),
            inline=False,
        )
    else:
        embed.add_field(name="✏️ Custom", value="_None added yet._", inline=False)

    embed.set_footer(
        text="Synced champions refresh with /update_champs · Custom entries persist across patches."
    )
    return embed


# ── Modals ────────────────────────────────────────────────────────────────────

class AddChampModal(discord.ui.Modal, title="Add Custom Champion"):
    champ_name = discord.ui.TextInput(
        label="Champion Name",
        placeholder="e.g. Yasuo",
        min_length=1,
        max_length=50,
    )

    def __init__(self, role: str, db, guild_id: str, added_by: str, refresh_cb):
        super().__init__()
        self.role       = role
        self.db         = db
        self.guild_id   = guild_id
        self.added_by   = added_by
        self.refresh_cb = refresh_cb

    async def on_submit(self, interaction: discord.Interaction):
        name = self.champ_name.value.strip()
        ok   = await self.db.add_custom_champion(
            self.guild_id, name, self.role, self.added_by
        )
        role_label = ROLE_LABELS.get(self.role, self.role)
        if ok:
            await interaction.response.send_message(
                f"✅ **{name}** added as a custom **{role_label}** champion.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ **{name}** is already in the custom list for **{role_label}**.",
                ephemeral=True,
            )
        await self.refresh_cb()


class RemoveChampModal(discord.ui.Modal, title="Remove Custom Champion"):
    champ_name = discord.ui.TextInput(
        label="Champion Name (exact, case-insensitive)",
        placeholder="e.g. Yasuo",
        min_length=1,
        max_length=50,
    )

    def __init__(self, role: str, db, guild_id: str, refresh_cb):
        super().__init__()
        self.role       = role
        self.db         = db
        self.guild_id   = guild_id
        self.refresh_cb = refresh_cb

    async def on_submit(self, interaction: discord.Interaction):
        name       = self.champ_name.value.strip()
        ok         = await self.db.remove_custom_champion(self.guild_id, name, self.role)
        role_label = ROLE_LABELS.get(self.role, self.role)
        if ok:
            await interaction.response.send_message(
                f"✅ **{name}** removed from custom **{role_label}** champions.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ No custom champion named **{name}** found for **{role_label}**.",
                ephemeral=True,
            )
        await self.refresh_cb()


# ── Paginated browser view ────────────────────────────────────────────────────

class ChampBrowserView(discord.ui.View):
    """
    Role-tab navigator + prev/next page for synced list + Add/Remove custom buttons.

    Layout:
      Row 0: [🛡️ Top] [🌿 Jungle] [⚡ Mid] [🏹 Bot] [💊 Support]
      Row 1: [◀ Prev] [Next ▶] [➕ Add Custom] [➖ Remove Custom]
    """

    def __init__(self, db, guild_id: str, invoker_id: int, start_role: str = "TOP"):
        super().__init__(timeout=180)
        self.db         = db
        self.guild_id   = guild_id
        self.invoker_id = invoker_id
        self.role_idx   = ROLE_ORDER.index(start_role) if start_role in ROLE_ORDER else 0
        self.page       = 0
        self._synced: list[dict] = []
        self._custom: list[dict] = []
        self._message: discord.Message = None

    # ── data ─────────────────────────────────────────────────────────────────

    async def load(self):
        role   = ROLE_ORDER[self.role_idx]
        data   = await self.db.get_all_champions_for_role(self.guild_id, role)
        self._synced = data["synced"]
        self._custom = data["custom"]

    def _total_pages(self) -> int:
        return max(1, -(-len(self._synced) // PAGE_SIZE))

    def _build_embed(self) -> discord.Embed:
        return _build_role_embed(
            ROLE_ORDER[self.role_idx],
            self._synced,
            self._custom,
            self.page,
            self._total_pages(),
        )

    # ── button builder ────────────────────────────────────────────────────────

    def _rebuild_buttons(self):
        self.clear_items()

        # Row 0: role tabs
        for i, role in enumerate(ROLE_ORDER):
            btn = discord.ui.Button(
                label=ROLE_LABELS[role],
                style=(
                    discord.ButtonStyle.primary
                    if i == self.role_idx
                    else discord.ButtonStyle.secondary
                ),
                row=0,
            )
            btn.callback = self._make_role_cb(i)
            self.add_item(btn)

        # Row 1: navigation + custom management
        prev_btn = discord.ui.Button(
            label="◀ Prev",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page == 0),
            row=1,
        )
        prev_btn.callback = self._prev_page
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=(self.page >= self._total_pages() - 1),
            row=1,
        )
        next_btn.callback = self._next_page
        self.add_item(next_btn)

        add_btn = discord.ui.Button(
            label="➕ Add Custom",
            style=discord.ButtonStyle.success,
            row=1,
        )
        add_btn.callback = self._add_custom
        self.add_item(add_btn)

        rm_btn = discord.ui.Button(
            label="➖ Remove Custom",
            style=discord.ButtonStyle.danger,
            disabled=(len(self._custom) == 0),
            row=1,
        )
        rm_btn.callback = self._remove_custom
        self.add_item(rm_btn)

    # ── guard ─────────────────────────────────────────────────────────────────

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "Only the person who opened this browser can use these controls.",
                ephemeral=True,
            )
            return False
        return True

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _make_role_cb(self, idx: int):
        async def callback(interaction: discord.Interaction):
            self.role_idx = idx
            self.page     = 0
            await self.load()
            self._rebuild_buttons()
            await interaction.response.edit_message(
                embed=self._build_embed(), view=self
            )
        return callback

    async def _prev_page(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _next_page(self, interaction: discord.Interaction):
        self.page = min(self._total_pages() - 1, self.page + 1)
        self._rebuild_buttons()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _add_custom(self, interaction: discord.Interaction):
        role = ROLE_ORDER[self.role_idx]

        async def refresh():
            await self.load()
            self._rebuild_buttons()
            if self._message:
                try:
                    await self._message.edit(embed=self._build_embed(), view=self)
                except Exception:
                    pass

        await interaction.response.send_modal(
            AddChampModal(
                role=role,
                db=self.db,
                guild_id=self.guild_id,
                added_by=str(interaction.user.id),
                refresh_cb=refresh,
            )
        )

    async def _remove_custom(self, interaction: discord.Interaction):
        role = ROLE_ORDER[self.role_idx]

        async def refresh():
            await self.load()
            self._rebuild_buttons()
            if self._message:
                try:
                    await self._message.edit(embed=self._build_embed(), view=self)
                except Exception:
                    pass

        await interaction.response.send_modal(
            RemoveChampModal(
                role=role,
                db=self.db,
                guild_id=self.guild_id,
                refresh_cb=refresh,
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

class Champions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ── /update_champs ────────────────────────────────────────────────────────

    @app_commands.command(
        name="update_champs",
        description="[Admin] Sync champion role statistics for the current patch."
    )
    @is_admin()
    async def update_champs(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            patch, updated_count = await self._fetch_and_store()
        except Exception as e:
            log.exception("update_champs failed")
            await interaction.followup.send(
                f"❌ Failed to sync champion data: `{e}`\nCheck bot logs for details.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Champion data synced for **Patch {patch}** — "
            f"**{updated_count}** champion/role entries updated.\n"
            f"_Custom champions are unaffected._",
            ephemeral=True,
        )

    # ── /add_custom_champ ─────────────────────────────────────────────────────

    @app_commands.command(
        name="add_custom_champ",
        description="[Admin] Add a custom champion to a role pool. Persists across patch syncs."
    )
    @app_commands.describe(
        name="Champion name (e.g. Yasuo)",
        role="Role to assign this champion to",
    )
    @app_commands.choices(role=[
        app_commands.Choice(name=label, value=db_key)
        for label, db_key in ROLE_CHOICES.items()
    ])
    @is_admin()
    async def add_custom_champ(
        self,
        interaction: discord.Interaction,
        name: str,
        role: str,
    ):
        ok         = await self.db.add_custom_champion(
            str(interaction.guild_id), name, role, str(interaction.user.id)
        )
        role_label = ROLE_LABELS.get(role, role)
        if ok:
            await interaction.response.send_message(
                f"✅ **{name}** added as a custom **{role_label}** champion.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ **{name}** is already in the custom list for **{role_label}**.",
                ephemeral=True,
            )

    # ── /remove_custom_champ ──────────────────────────────────────────────────

    @app_commands.command(
        name="remove_custom_champ",
        description="[Admin] Remove a custom champion from a role pool."
    )
    @app_commands.describe(
        name="Champion name (case-insensitive)",
        role="Role the champion was assigned to",
    )
    @app_commands.choices(role=[
        app_commands.Choice(name=label, value=db_key)
        for label, db_key in ROLE_CHOICES.items()
    ])
    @is_admin()
    async def remove_custom_champ(
        self,
        interaction: discord.Interaction,
        name: str,
        role: str,
    ):
        ok         = await self.db.remove_custom_champion(
            str(interaction.guild_id), name, role
        )
        role_label = ROLE_LABELS.get(role, role)
        if ok:
            await interaction.response.send_message(
                f"✅ **{name}** removed from custom **{role_label}** champions.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ No custom champion named **{name}** found for **{role_label}**.",
                ephemeral=True,
            )

    # ── /clear_custom_champs ──────────────────────────────────────────────────

    @app_commands.command(
        name="clear_custom_champs",
        description="[Admin] Remove ALL custom champion entries for this server."
    )
    @is_admin()
    async def clear_custom_champs(self, interaction: discord.Interaction):
        count = await self.db.clear_custom_champions(str(interaction.guild_id))
        if count:
            await interaction.response.send_message(
                f"🗑️ Cleared **{count}** custom champion entr{'y' if count == 1 else 'ies'}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "ℹ️ No custom champions to clear.",
                ephemeral=True,
            )

    # ── /view_champs ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="view_champs",
        description="[Admin] Browse champion pools by role, with Add / Remove custom buttons."
    )
    @app_commands.describe(role="Jump directly to a role (default: Top)")
    @app_commands.choices(role=[
        app_commands.Choice(name=label, value=db_key)
        for label, db_key in ROLE_CHOICES.items()
    ])
    @is_admin()
    async def view_champs(
        self,
        interaction: discord.Interaction,
        role: str = "TOP",
    ):
        start_role = role if role in ROLE_ORDER else "TOP"
        view = ChampBrowserView(
            db=self.db,
            guild_id=str(interaction.guild_id),
            invoker_id=interaction.user.id,
            start_role=start_role,
        )
        await view.load()
        view._rebuild_buttons()

        await interaction.response.send_message(
            embed=view._build_embed(),
            view=view,
            ephemeral=True,
        )
        view._message = await interaction.original_response()

    # ── patch sync internals ──────────────────────────────────────────────────

    async def _fetch_and_store(self) -> tuple[str, int]:
        async with aiohttp.ClientSession() as session:

            async with session.get(
                "https://ddragon.leagueoflegends.com/api/versions.json"
            ) as r:
                r.raise_for_status()
                patch = (await r.json(content_type=None))[0]

            stats_url = (
                "https://raw.communitydragon.org/latest/plugins/"
                "rcp-fe-lol-champion-statistics/global/default/"
                "rcp-fe-lol-champion-statistics.js"
            )
            async with session.get(stats_url) as r:
                r.raise_for_status()
                text = await r.text()

            match = re.search(r"JSON\.parse\('(.+?)'\)", text, re.DOTALL)
            if not match:
                raise ValueError(
                    "Could not find JSON.parse(...) in champion statistics JS. "
                    "CommunityDragon may have changed the file format."
                )
            raw_json    = match.group(1).replace("\\'", "'")
            stats_data  = json.loads(raw_json)

            summary_url = (
                "https://raw.communitydragon.org/latest/plugins/"
                "rcp-be-lol-game-data/global/default/v1/champion-summary.json"
            )
            async with session.get(summary_url) as r:
                r.raise_for_status()
                champ_summary = await r.json(content_type=None)

        id_to_name: dict[str, str] = {
            str(c["id"]): c["name"]
            for c in champ_summary
            if c["id"] != -1
        }

        updated_count = 0
        await self.db.clear_champions()          # only wipes synced table; custom_champions untouched
        for raw_role, champs in stats_data.items():
            role_key = raw_role.upper()
            if role_key not in VALID_ROLES:
                continue
            for champ_id, stats in champs.items():
                name = id_to_name.get(str(champ_id))
                if not name:
                    continue
                await self.db.upsert_champion(
                    champ_id=str(champ_id),
                    name=name,
                    role=role_key,
                    play_rate=stats,
                    patch=patch,
                )
                updated_count += 1

        await self.db.commit()
        return patch, updated_count


async def setup(bot: commands.Bot):
    await bot.add_cog(Champions(bot))