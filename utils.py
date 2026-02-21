"""
Shared constants and helper utilities.
"""

import discord
from discord import app_commands

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]

ROLE_EMOJIS = {
    "Top": "<475084847057207296:1474658409000210545>",
    "Jungle": "<475084847057207296:1474658407892914337>",
    "Mid": "<475084847057207296:1474658412251058389>",
    "ADC": "<475084847057207296:1474658411126722580>",
    "Support": "<475084847057207296:1474658409935671431>",
}

COLORS = {
    "blue":   0x5865F2,
    "green":  0x57F287,
    "red":    0xED4245,
    "yellow": 0xFEE75C,
    "gray":   0x95A5A6,
    "gold":   0xF1C40F,
}


def is_admin():
    """Check decorator: must have Administrator or Manage Guild permission."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message(
            "❌ You need Administrator or Manage Guild permission to do that.",
            ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def fmt_player(player: dict, show_stats: bool = True) -> str:
    """Format a player dict into a readable string."""
    roles = " / ".join(
        f"{ROLE_EMOJIS.get(r, '')}{r}" for r in player["role_prefs"]
    ) or "No preferences"
    line = f"**{player['display_name']}** — {roles}"
    if show_stats and player["games_played"] > 0:
        wr = round(player["games_won"] / player["games_played"] * 100, 1)
        line += f" | {player['games_won']}W/{player['games_lost']}L ({wr}%)"
    return line


def build_embed(title: str, description: str = "", color_key: str = "blue") -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=COLORS.get(color_key, COLORS["blue"])
    )
