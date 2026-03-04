"""
ELO Cog
Handles: viewing ELO ratings (admin-only) and ELO history charts (admin-only).

ELO types:
  total              — updated after every game regardless of mode
  roles_pref         — random teams, role assignment with preferences
  roles_random       — random teams, fully random role assignment
  no_roles           — random teams, no role assignment
  champs_roles_pref  — random teams + random champs, roles by preference
  champs_roles_random— random teams + random champs, random roles
  draft              — captain snake draft
"""

import discord
from discord import app_commands
from discord.ext import commands
import io
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must be set before importing pyplot
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from utils import build_embed, is_admin

ELO_TYPE_LABELS = {
    "total":               "🌐 Total (all modes)",
    "roles_pref":          "🎲 Roles — by preference",
    "roles_random":        "🔀 Roles — random",
    "no_roles":            "👤 No roles",
    "champs_roles_pref":   "🎰 Champs + Roles (pref)",
    "champs_roles_random": "🎰 Champs + Roles (random)",
    "draft":               "🎯 Captain Draft",
}

# Colours used in charts — one per series
CHART_COLORS = [
    "#5865F2", "#ED4245", "#57F287", "#FEE75C", "#EB459E",
    "#F1C40F", "#3498DB", "#E74C3C", "#2ECC71", "#9B59B6",
    "#1ABC9C", "#E67E22", "#95A5A6", "#D35400", "#2980B9",
    "#27AE60", "#8E44AD", "#C0392B", "#16A085", "#F39C12",
]

# Discord dark theme background colours
BG_COLOR   = "#2f3136"
PANEL_BG   = "#36393f"
TEXT_COLOR  = "#dcddde"
GRID_COLOR  = "#40444b"
GOLD_COLOR  = "#F1C40F"


def _strip_emoji(text: str) -> str:
    """Remove emoji and other non-BMP characters that matplotlib's default font can't render."""
    import re
    # Remove anything outside the Basic Multilingual Plane (U+0000–U+FFFF)
    # and common emoji ranges within BMP
    return re.sub(
        r"[\U00010000-\U0010FFFF"   # supplementary planes (most emoji)
        r"\u2600-\u27BF"            # misc symbols, dingbats
        r"\u2B00-\u2BFF"            # misc symbols and arrows
        r"\uFE00-\uFE0F"            # variation selectors
        r"\u200D"                   # zero-width joiner
        r"]",
        "",
        text
    ).strip()


def _render_chart(
    title: str,
    series: dict[str, list[float]],   # label -> list of ELO values (x = index+1)
    x_label: str = "Games Played",
) -> discord.File:
    """
    Render a line chart with matplotlib and return it as a discord.File (PNG).
    Each series has its own x-axis range (1 ... len(values)), so players or modes
    with different game counts are each charted correctly.
    """
    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor=BG_COLOR)
    ax.set_facecolor(PANEL_BG)

    for idx, (label, values) in enumerate(series.items()):
        if not values:
            continue
        color = CHART_COLORS[idx % len(CHART_COLORS)]
        xs = list(range(1, len(values) + 1))
        ax.plot(xs, values, color=color, linewidth=2, marker="o",
                markersize=4, label=_strip_emoji(label))

    ax.set_title(_strip_emoji(title), color=GOLD_COLOR, fontsize=13, pad=10)
    ax.set_xlabel(_strip_emoji(x_label), color=TEXT_COLOR, fontsize=10)
    ax.set_ylabel("ELO", color=TEXT_COLOR, fontsize=10)

    ax.tick_params(colors=TEXT_COLOR, which="both")
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID_COLOR)
    ax.grid(color=GRID_COLOR, linestyle="--", linewidth=0.6, alpha=0.7)

    ax.legend(
        facecolor=PANEL_BG, edgecolor=GRID_COLOR,
        labelcolor=TEXT_COLOR, fontsize=9,
        loc="best", framealpha=0.85,
    )

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor=BG_COLOR)
    plt.close(fig)
    buf.seek(0)
    return discord.File(buf, filename="elo_history.png")


class Elo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    # ── /view_elo ─────────────────────────────────────────────────────────────

    @app_commands.command(name="view_elo", description="[Admin] View current ELO ratings for all players.")
    @app_commands.describe(elo_type="Which ELO leaderboard to show (default: total)")
    @app_commands.choices(elo_type=[
        app_commands.Choice(name=label, value=key)
        for key, label in ELO_TYPE_LABELS.items()
    ])
    @is_admin()
    async def view_elo(self, interaction: discord.Interaction, elo_type: str = "total"):
        guild_id = str(interaction.guild_id)
        all_elos = await self.db.get_all_elos(guild_id)

        rows = [r for r in all_elos if r["elo_type"] == elo_type]
        if not rows:
            await interaction.response.send_message(
                f"No ELO data yet for **{ELO_TYPE_LABELS.get(elo_type, elo_type)}**.",
                ephemeral=True
            )
            return

        rows.sort(key=lambda r: -r["elo"])

        lines = []
        for i, r in enumerate(rows, 1):
            p = await self.db.get_player(r["discord_id"], guild_id)
            name = p["display_name"] if p else f"<@{r['discord_id']}>"
            elo_str = f"{r['elo']:.0f}"
            wr_str = ""
            if r["games"] > 0:
                wr = round(r["wins"] / r["games"] * 100, 1)
                wr_str = f" | {r['wins']}W/{r['losses']}L ({wr}%)"
            lines.append(f"**{i}.** {name} — **{elo_str}** ELO{wr_str}")

        embed = build_embed(
            f"ELO Leaderboard — {ELO_TYPE_LABELS.get(elo_type, elo_type)}",
            "\n".join(lines[:25]),
            color_key="gold"
        )
        embed.set_footer(text="ELO is only visible to bot admins.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /elo_history ──────────────────────────────────────────────────────────

    @app_commands.command(name="elo_history", description="[Admin] View ELO history chart.")
    @app_commands.describe(
        elo_type=(
            "ELO type to chart. Leave blank + pick a player to see all their modes at once."
        ),
        member="Show only this player (omit for all players on a single type)"
    )
    @app_commands.choices(elo_type=[
        app_commands.Choice(name=label, value=key)
        for key, label in ELO_TYPE_LABELS.items()
    ])
    @is_admin()
    async def elo_history(
        self,
        interaction: discord.Interaction,
        elo_type: str = None,
        member: discord.Member = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = str(interaction.guild_id)

        # Case 1: specific player, no elo_type → show ALL their modes as separate lines
        if member is not None and elo_type is None:
            await self._send_player_all_modes(interaction, guild_id, member)
            return

        # Case 2: specific elo_type (with or without player filter)
        resolved_type = elo_type or "total"
        discord_id = str(member.id) if member else None
        history = await self.db.get_elo_history(guild_id, discord_id, resolved_type)

        if not history:
            label = ELO_TYPE_LABELS.get(resolved_type, resolved_type)
            suffix = f" for **{member.display_name}**" if member else ""
            await interaction.followup.send(
                f"No ELO history yet for **{label}**{suffix}.", ephemeral=True
            )
            return

        # Group by player; each player's own x-axis starts at 1
        player_series: dict[str, list[float]] = defaultdict(list)
        for row in history:
            player_series[row["discord_id"]].append(row["elo_after"])

        names = await self._fetch_names(guild_id, player_series.keys(), interaction.guild)

        # Current ELO list sorted descending
        current_elos = {did: vals[-1] for did, vals in player_series.items()}
        elo_lines = [
            f"**{names.get(did, did)}** — {elo:.0f} ELO"
            for did, elo in sorted(current_elos.items(), key=lambda x: -x[1])
        ]

        title = f"ELO History — {ELO_TYPE_LABELS.get(resolved_type, resolved_type)}"
        if member:
            title += f" — {member.display_name}"

        series = {names.get(did, did): vals for did, vals in player_series.items()}
        chart_file = _render_chart(title, series)

        embed = build_embed(title, "\n".join(elo_lines[:20]) or "_No data_", color_key="gold")
        embed.set_image(url="attachment://elo_history.png")
        embed.set_footer(text="X-axis = each player's own game count.")
        await interaction.followup.send(embed=embed, file=chart_file, ephemeral=True)

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _send_player_all_modes(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        member: discord.Member,
    ):
        """Plot all ELO modes for a single player as separate lines on one chart."""
        discord_id = str(member.id)

        mode_series: dict[str, list[float]] = {}
        elo_lines: list[str] = []

        for elo_type, label in ELO_TYPE_LABELS.items():
            history = await self.db.get_elo_history(guild_id, discord_id, elo_type)
            if not history:
                continue
            # Strip emoji so matplotlib legend renders cleanly
            clean_label = _strip_emoji(label)
            values = [row["elo_after"] for row in history]
            mode_series[clean_label] = values
            elo_lines.append(
                f"**{label}** — {values[-1]:.0f} ELO ({len(values)} game{'s' if len(values) != 1 else ''})"
            )

        if not mode_series:
            await interaction.followup.send(
                f"No ELO history found for **{member.display_name}**.", ephemeral=True
            )
            return

        title = f"ELO History — {member.display_name} (all modes)"
        chart_file = _render_chart(title, mode_series, x_label="Games Played (per mode)")

        embed = build_embed(title, "\n".join(elo_lines), color_key="gold")
        embed.set_image(url="attachment://elo_history.png")
        embed.set_footer(
            text="Each line = a different draft mode. X-axis = games played in that mode."
        )
        await interaction.followup.send(embed=embed, file=chart_file, ephemeral=True)

    async def _fetch_names(
        self,
        guild_id: str,
        discord_ids,
        guild: discord.Guild,
    ) -> dict[str, str]:
        names: dict[str, str] = {}
        for did in discord_ids:
            p = await self.db.get_player(did, guild_id)
            if p:
                names[did] = p["display_name"]
            else:
                m = guild.get_member(int(did))
                names[did] = m.display_name if m else f"User {did}"
        return names


async def setup(bot: commands.Bot):
    await bot.add_cog(Elo(bot))