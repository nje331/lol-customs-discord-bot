"""
Shared constants and helper utilities.
"""

import discord
from discord import app_commands

# Lane order for display sorting
ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]

ROLE_EMOJIS = {
    "Top":     "🛡️",
    "Jungle":  "🌿",
    "Mid":     "⚡",
    "ADC":     "🏹",
    "Support": "💊",
    "Fill":    "🔀",
}

LANE_ORDER = {role: i for i, role in enumerate(ROLES)}

COLORS = {
    "blue":   0x5865F2,
    "green":  0x57F287,
    "red":    0xED4245,
    "yellow": 0xFEE75C,
    "gray":   0x95A5A6,
    "gold":   0xF1C40F,
}


async def check_is_admin(interaction: discord.Interaction) -> bool:
    """Returns True if the user is a Discord admin OR a bot admin for this guild."""
    if interaction.user.guild_permissions.administrator:
        return True
    if interaction.user.guild_permissions.manage_guild:
        return True
    db = interaction.client.db
    return await db.is_bot_admin(str(interaction.user.id), str(interaction.guild_id))


async def check_is_session_owner(interaction: discord.Interaction) -> bool:
    """Returns True if the user is a bot admin OR the current session owner."""
    if await check_is_admin(interaction):
        return True
    db = interaction.client.db
    session = await db.get_active_session(str(interaction.guild_id))
    if session and session.get("owner_id") == str(interaction.user.id):
        return True
    return False


def is_admin():
    """Decorator: requires bot admin or Discord admin."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if await check_is_admin(interaction):
            return True
        await interaction.response.send_message(
            "❌ You need to be a bot admin to do that.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def is_session_owner():
    """Decorator: requires bot admin OR session owner."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if await check_is_session_owner(interaction):
            return True
        await interaction.response.send_message(
            "❌ Only the session owner or an admin can do that.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def fmt_player(player: dict, show_stats: bool = True) -> str:
    """Format a player dict into a readable string."""
    roles = " > ".join(
        f"{ROLE_EMOJIS.get(r, r)} {r}" for r in player["role_prefs"]
    ) or "No preference"
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


def sort_by_lane(team: list, assignments: dict) -> list:
    """Sort a team list by lane order (Top -> Support)."""
    return sorted(team, key=lambda p: LANE_ORDER.get(assignments.get(p["discord_id"], "Fill"), 99))