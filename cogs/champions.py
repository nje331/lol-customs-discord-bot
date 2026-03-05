"""
Champions Cog
Syncs League of Legends champion role statistics from CommunityDragon.
Run /update_champs once to populate the DB; data is used by /make_teams random_champs:True.
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


class Champions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

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
                ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Champion data synced for **Patch {patch}** — "
            f"**{updated_count}** champion/role entries updated.",
            ephemeral=True
        )

    async def _fetch_and_store(self) -> tuple[str, int]:
        async with aiohttp.ClientSession() as session:

            # 1. Latest patch version
            async with session.get(
                "https://ddragon.leagueoflegends.com/api/versions.json"
            ) as r:
                r.raise_for_status()
                patch = (await r.json(content_type=None))[0]

            # 2. Champion role stats JS file
            stats_url = (
                "https://raw.communitydragon.org/latest/plugins/"
                "rcp-fe-lol-champion-statistics/global/default/"
                "rcp-fe-lol-champion-statistics.js"
            )
            async with session.get(stats_url) as r:
                r.raise_for_status()
                text = await r.text()

            # Extract JSON from:  JSON.parse('...')
            match = re.search(r"JSON\.parse\('(.+?)'\)", text, re.DOTALL)
            if not match:
                raise ValueError(
                    "Could not find JSON.parse(...) in champion statistics JS. "
                    "CommunityDragon may have changed the file format."
                )
            raw_json = match.group(1).replace("\\'", "'")
            stats_data = json.loads(raw_json)

            # 3. Champion ID → name
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
        await self.db.clear_champions()
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