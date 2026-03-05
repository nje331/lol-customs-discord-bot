"""
Teams Cog
Handles: random teams, captain draft, role assignment, random champion assignment,
         voice moving, game results, bench management (11+ players).

Flow:
  _finalize_teams()  →  shows teams + bench + "Start Game" button  (StartGameView)
  "Start Game" click →  moves playing 10 to VCs, saves DB records, shows InProgressView
  winner click       →  saves result for playing 10 only, moves all back, shows NextGameView
  NextGameView       →  re-draft / random / rematch / random-champs (re-rolls from full session roster)
"""

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
from typing import Optional

from utils import ROLES, ROLE_EMOJIS, LANE_ORDER, build_embed, is_session_owner, sort_by_lane

TEAM_SIZE = 5   # players per team

# Map our role names → CommunityDragon role keys used in the champions table
ROLE_TO_CDR = {
    "Top":     "TOP",
    "Jungle":  "JUNGLE",
    "Mid":     "MIDDLE",
    "ADC":     "BOTTOM",
    "Support": "SUPPORT",
}

# Maps the (assign_roles, use_prefs, random_champs) flags to an ELO type key
# draft is handled separately
def _elo_type_for_mode(assign_roles: bool, use_prefs: bool, random_champs: bool) -> str:
    if not assign_roles:
        return "no_roles"
    if use_prefs:
        return "champs_roles_pref" if random_champs else "roles_pref"
    return "champs_roles_random" if random_champs else "roles_random"


# ── ELO calculation (mirrors elo.py logic) ────────────────────────────────────

def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def _k_factor(elo: float, games: int) -> float:
    if elo > 1650:
        return 20
    return max(32, 64 - games)


def _compute_elo_changes(
    winners: list[dict], losers: list[dict],
    elo_type: str, guild_id: str,
    elo_rows: dict  # (discord_id, elo_type) -> {elo, games, wins, losses}
) -> dict:
    """
    Returns {discord_id: new_elo} for all players.
    Uses inverse-ELO weighting for winners (lower ELO players gain more)
    and proportional ELO weighting for losers (higher ELO players lose more),
    matching the simulation in elo.py.
    """
    def get_row(did):
        return elo_rows.get((did, elo_type), {"elo": 1500.0, "games": 0})

    winner_elos = [get_row(p["discord_id"])["elo"] for p in winners]
    loser_elos  = [get_row(p["discord_id"])["elo"] for p in losers]
    avg_winner  = sum(winner_elos) / len(winner_elos)
    avg_loser   = sum(loser_elos) / len(loser_elos)

    exp_winner = _expected(avg_winner, avg_loser)
    exp_loser  = 1.0 - exp_winner

    total_loser_elo = sum(loser_elos) or 1.0

    # Winners: distribute gain proportional to inverse ELO (lower elo players gain more)
    inv_elos = [1.0 / max(e, 1) for e in winner_elos]
    total_inv = sum(inv_elos) or 1.0

    new_elos: dict[str, float] = {}

    for i, p in enumerate(winners):
        row = get_row(p["discord_id"])
        k = _k_factor(row["elo"], row["games"])
        total_gain = k * (1.0 - exp_winner)
        share = inv_elos[i] / total_inv
        new_elos[p["discord_id"]] = row["elo"] + total_gain * share

    for i, p in enumerate(losers):
        row = get_row(p["discord_id"])
        k = _k_factor(row["elo"], row["games"])
        total_loss = k * (0.0 - exp_loser)
        share = loser_elos[i] / total_loser_elo
        new_elos[p["discord_id"]] = row["elo"] + total_loss * share

    return new_elos


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _split_players_random(players: list) -> tuple[list, list, list]:
    """Pure random split."""
    pool = players[:]
    if len(pool) > TEAM_SIZE * 2:
        random.shuffle(pool)
        playing = pool[:TEAM_SIZE * 2]
        bench = pool[TEAM_SIZE * 2:]
    else:
        playing = pool
        bench = []
    random.shuffle(playing)
    mid = len(playing) // 2
    return playing[:mid], playing[mid:], bench


def _split_players_balanced_by_elo(
    players: list, elo_map: dict[str, float]
) -> tuple[list, list, list]:
    """
    ELO-balanced split: enumerate splits and pick the one with smallest |avg_elo_diff|.
    For large pools (>10), bench first then balance from the selected 10.
    Role preference optimization is handled separately in _finalize_teams.
    """
    pool = players[:]
    if len(pool) > TEAM_SIZE * 2:
        random.shuffle(pool)
        playing = pool[:TEAM_SIZE * 2]
        bench = pool[TEAM_SIZE * 2:]
    else:
        playing = pool
        bench = []

    n = len(playing)
    if n < 2:
        return playing, [], bench

    half = n // 2

    # For small n use exhaustive search; for n=10 that's C(10,5)=252 — fast enough
    from itertools import combinations
    best_t1, best_t2, best_diff = None, None, float("inf")
    indices = list(range(n))
    for combo in combinations(indices, half):
        t1 = [playing[i] for i in combo]
        t2 = [playing[i] for i in indices if i not in combo]
        e1 = sum(elo_map.get(p["discord_id"], 1500.0) for p in t1) / len(t1)
        e2 = sum(elo_map.get(p["discord_id"], 1500.0) for p in t2) / len(t2)
        diff = abs(e1 - e2)
        if diff < best_diff:
            best_diff = diff
            best_t1, best_t2 = t1, t2

    return best_t1, best_t2, bench


def _split_players(players: list, use_power: bool) -> tuple[list, list, list]:
    """Legacy signature kept for callers that pass use_power=False (random)."""
    return _split_players_random(players)


def _assign_roles(team: list, session_role_history: dict, track_roles: bool,
                  use_prefs: bool = True) -> dict[str, str]:
    """
    Returns {discord_id: role}, with two modes:

    use_prefs=True  (default): honour player role preferences, avoid repeats when track_roles.
      Pass 1: assign each player a fresh preferred role (not played, not taken by teammate).
              Falls back to any untaken preferred role if all fresh ones are gone.
      Pass 2: fill remaining players history-aware from leftover roles.

    use_prefs=False (random roles): skip preferences entirely.
      Single pass: shuffle all roles, assign each player a role they haven't played yet.
      Falls back to any leftover role only if all 5 have been played this session.
    """
    if not use_prefs:
        # Truly random — no preferences, just history-aware shuffle
        all_roles = list(ROLES)
        random.shuffle(all_roles)
        assignment: dict[str, str] = {}
        taken_roles: set[str] = set()

        for player in team:
            played = session_role_history.get(player["discord_id"], []) if track_roles else []
            available = [r for r in all_roles if r not in taken_roles and r not in played]
            if not available:
                # All fresh roles exhausted — fall back to any untaken role
                available = [r for r in all_roles if r not in taken_roles]
            if not available:
                assignment[player["discord_id"]] = "Fill"
            else:
                role = available[0]
                assignment[player["discord_id"]] = role
                taken_roles.add(role)

        return assignment

    # ── Preference-aware assignment ──────────────────────────────────────────
    assignment: dict[str, str] = {}
    taken_roles: set[str] = set()
    unassigned = list(team)

    for player in list(unassigned):
        prefs = player.get("role_prefs", [])
        played = session_role_history.get(player["discord_id"], []) if track_roles else []

        # Preferred roles the player hasn't played yet this session
        fresh_prefs = [r for r in prefs if r not in played and r not in taken_roles]
        # Fall back to any preferred role if all fresh ones are taken
        any_prefs = [r for r in prefs if r not in taken_roles]

        candidates = fresh_prefs or any_prefs
        if candidates:
            role = candidates[0]
            assignment[player["discord_id"]] = role
            taken_roles.add(role)
            unassigned.remove(player)

    # Fill remaining players — prefer roles they haven't played yet
    remaining_roles = [r for r in ROLES if r not in taken_roles]
    random.shuffle(remaining_roles)

    for player in unassigned:
        if not remaining_roles:
            assignment[player["discord_id"]] = "Fill"
            continue

        played = session_role_history.get(player["discord_id"], []) if track_roles else []

        fresh = [r for r in remaining_roles if r not in played]
        role = fresh[0] if fresh else remaining_roles[0]

        assignment[player["discord_id"]] = role
        remaining_roles.remove(role)

    return assignment


async def _assign_champs(assignments: dict[str, str], db,
                         use_weights: bool = False,
                         exclude: set[str] | None = None) -> dict[str, str]:
    """
    Given {discord_id: role}, returns {discord_id: champion_name}.
    use_weights: if True, weight picks by play rate; otherwise uniform random.
    exclude: set of champion names already in use (prevents duplicates across calls).
    No champion assigned twice within this call.
    """
    champ_assignment: dict[str, str] = {}
    used_champs: set[str] = set(exclude or [])

    for did, role in assignments.items():
        cdr_role = ROLE_TO_CDR.get(role)
        if not cdr_role:
            champ_assignment[did] = ""
            continue

        rows = await db.get_champions_for_role(cdr_role, limit=50)
        if not rows:
            champ_assignment[did] = ""
            continue

        available = [r for r in rows if r["name"] not in used_champs]
        if not available:
            available = rows  # fallback: allow repeats if pool exhausted

        if use_weights:
            weights = [max(r["play_rate"], 0.001) for r in available]
            chosen = random.choices(available, weights=weights, k=1)[0]
        else:
            chosen = random.choice(available)

        champ_assignment[did] = chosen["name"]
        used_champs.add(chosen["name"])

    return champ_assignment


async def _reroll_one_champ(discord_id: str, role: str, db,
                             use_weights: bool, exclude: set[str]) -> str:
    """Pick a single new champion for one player, excluding already-assigned champs."""
    cdr_role = ROLE_TO_CDR.get(role, "")
    if not cdr_role:
        return ""
    rows = await db.get_champions_for_role(cdr_role, limit=50)
    if not rows:
        return ""
    available = [r for r in rows if r["name"] not in exclude]
    if not available:
        available = rows
    if use_weights:
        weights = [max(r["play_rate"], 0.001) for r in available]
        return random.choices(available, weights=weights, k=1)[0]["name"]
    return random.choice(available)["name"]


def _team_field(team: list, assignments: dict,
                champ_assignments: dict | None = None) -> str:
    sorted_team = sort_by_lane(team, assignments)
    lines = []
    for p in sorted_team:
        role = assignments.get(p["discord_id"], "Fill")
        emoji = ROLE_EMOJIS.get(role, "❓")
        champ = champ_assignments.get(p["discord_id"], "") if champ_assignments else ""
        champ_str = f" *({champ})*" if champ else ""
        lines.append(f"{emoji} **{role}** — {p['display_name']}{champ_str}")
    return "\n".join(lines)


def _team_field_no_roles(team: list,
                          champ_assignments: dict | None = None) -> str:
    lines = []
    for p in team:
        champ = champ_assignments.get(p["discord_id"], "") if champ_assignments else ""
        champ_str = f" *({champ})*" if champ else ""
        lines.append(f"• {p['display_name']}{champ_str}")
    return "\n".join(lines)


def _pick_one_captain_randomly(players: list, past_captain_ids: list[str]) -> dict:
    """
    Pick one captain, prioritising players who have been captain the fewest times.
    Tracks cycle count so everyone gets an equal number of turns over time.
    past_captain_ids is a flat ordered list; players who appear fewer times take priority.
    """
    from collections import Counter
    count = Counter(past_captain_ids)
    min_times = min((count.get(p["discord_id"], 0) for p in players), default=0)
    least_used = [p for p in players if count.get(p["discord_id"], 0) == min_times]
    return random.choice(least_used)


def _pick_captains_randomly(players: list, past_captain_ids: list[str]) -> tuple[dict, dict]:
    """Pick two distinct captains, both chosen from whoever has been captain the fewest times."""
    from collections import Counter
    count = Counter(past_captain_ids)
    min_times = min((count.get(p["discord_id"], 0) for p in players), default=0)
    least_used = [p for p in players if count.get(p["discord_id"], 0) == min_times]

    if len(least_used) >= 2:
        picks = random.sample(least_used, 2)
    elif len(least_used) == 1:
        # One spot from least-used, second from next tier
        cap1 = least_used[0]
        next_tier_min = min_times + 1
        next_tier = [p for p in players if count.get(p["discord_id"], 0) == next_tier_min
                     and p["discord_id"] != cap1["discord_id"]]
        if not next_tier:
            next_tier = [p for p in players if p["discord_id"] != cap1["discord_id"]]
        cap2 = random.choice(next_tier)
        picks = [cap1, cap2]
    else:
        picks = random.sample(players, 2)
    return picks[0], picks[1]


async def _move_players_to_channels(
    guild: discord.Guild,
    team1: list, team2: list,
    t1_ch_id: Optional[int], t2_ch_id: Optional[int]
) -> str:
    if not (t1_ch_id and t2_ch_id):
        return "Tip: use /configure_channels to enable auto voice splits"

    t1_ch = guild.get_channel(t1_ch_id) or await guild.fetch_channel(t1_ch_id)
    t2_ch = guild.get_channel(t2_ch_id) or await guild.fetch_channel(t2_ch_id)
    if not (t1_ch and t2_ch):
        return "⚠️ Configured voice channels not found"

    async def move(player: dict, channel: discord.VoiceChannel):
        member = guild.get_member(int(player["discord_id"]))
        if member is None:
            try:
                member = await guild.fetch_member(int(player["discord_id"]))
            except (discord.NotFound, discord.HTTPException):
                return
        if member.voice:
            try:
                await member.move_to(channel)
            except (discord.Forbidden, discord.HTTPException):
                pass

    for p in team1:
        await move(p, t1_ch)
    for p in team2:
        await move(p, t2_ch)

    return f"Players moved → {t1_ch.name} / {t2_ch.name}"


# ── Views ─────────────────────────────────────────────────────────────────────

class StartGameView(discord.ui.View):
    """
    Shown after teams are set. Displays lineup (+ bench if any) and waits for 'Start Game'.
    On Start Game: saves DB records, moves playing players to VCs, swaps to InProgressView.

    If champ_rerolls > 0 and random_champs is True, per-player reroll buttons are shown.
    game_id is None until Start Game is pressed; reroll buttons are disabled until then.
    """

    def __init__(self, session_id: int, team1: list, team2: list, bench: list,
                 team1_assign: dict, team2_assign: dict,
                 team1_champs: dict, team2_champs: dict,
                 assign_roles: bool, use_prefs: bool, random_champs: bool,
                 use_weights: bool,
                 settings: dict, game_num: int,
                 all_players: list,       # playing 10 (or fewer)
                 session_players: list,   # full session roster incl. bench
                 elo_type: str = "total",
                 cog=None):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.team1 = team1
        self.team2 = team2
        self.bench = bench
        self.team1_assign = team1_assign
        self.team2_assign = team2_assign
        # Combined champ assignments — mutable so rerolls can update in place
        self.champ_assignments: dict[str, str] = {**team1_champs, **team2_champs}
        self.assign_roles = assign_roles
        self.use_prefs = use_prefs
        self.random_champs = random_champs
        self.use_weights = use_weights
        self.settings = settings
        self.game_num = game_num
        self.all_players = all_players
        self.session_players = session_players
        self.elo_type = elo_type
        self.cog = cog

        # Populated by _post_elo_to_mod_channel; used by InProgressView for gain follow-up
        self.pre_game_elos: dict[str, dict[str, float]] = {}
        self.elo_breakdown_msg = None   # the discord.Message sent to mod channel
        self.elo_mod_ch = None          # the mod channel object

        # Reroll state — populated after Start Game is pressed
        self.game_id: int | None = None
        self.champ_rerolls_allowed: int = int(settings.get("champ_rerolls", 0))
        self._reroll_locks: dict[str, asyncio.Lock] = {}
        self._rerolls_used: dict[str, int] = {}
        self._reroll_log: list[dict] = []  # ordered list of reroll events for summary
        # Per-player set of every champ they've been assigned (initial + all rerolls)
        # Used to prevent re-rolling back to a champ they already had this game
        self._rolled_champs: dict[str, set[str]] = {
            p["discord_id"]: {self.champ_assignments.get(p["discord_id"])}
            for p in (team1 + team2)
            if self.champ_assignments.get(p["discord_id"])
        }

        # Add Reroll Champion button on row 0 (button 2) only when champs are assigned.
        # Always added when random_champs is True — reroll count is re-checked live at click
        # time so mid-session setting changes take effect without needing a new make_teams.
        if random_champs:
            btn = discord.ui.Button(
                label="🎲 Reroll Champion",
                style=discord.ButtonStyle.primary,
                custom_id="reroll_champ_pre",
                row=0
            )
            async def _reroll_btn_callback(inter: discord.Interaction, b=btn):
                await self.reroll_champion(inter, b)
            btn.callback = _reroll_btn_callback
            self.add_item(btn)

    def _all_assign(self) -> dict[str, str]:
        """Merge both team role assignments."""
        return {**self.team1_assign, **self.team2_assign}

    def build_embed(self, title_suffix: str = "Teams Ready",
                    description: str = "Review the teams below, then press **Start Game** to begin.",
                    color: str = "blue") -> discord.Embed:
        embed = build_embed(f"Game #{self.game_num} — {title_suffix}", description, color)

        champs1 = {did: self.champ_assignments.get(did, "") for p in self.team1 for did in [p["discord_id"]]} if self.random_champs else None
        champs2 = {did: self.champ_assignments.get(did, "") for p in self.team2 for did in [p["discord_id"]]} if self.random_champs else None

        if self.assign_roles:
            embed.add_field(name="🔵 Team 1", value=_team_field(self.team1, self.team1_assign, champs1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field(self.team2, self.team2_assign, champs2), inline=True)
        else:
            embed.add_field(name="🔵 Team 1", value=_team_field_no_roles(self.team1, champs1), inline=True)
            embed.add_field(name="🔴 Team 2", value=_team_field_no_roles(self.team2, champs2), inline=True)

        if self.bench:
            bench_names = "\n".join(f"• {p['display_name']}" for p in self.bench)
            embed.add_field(
                name=f"⏸️ Sitting Out ({len(self.bench)})",
                value=bench_names,
                inline=False
            )

        footer_parts = []
        if self.random_champs:
            footer_parts.append("Champions randomly assigned" + (" by play rate" if self.use_weights else ""))
        if self.game_id and self.random_champs and self.champ_rerolls_allowed > 0:
            footer_parts.append(f"{self.champ_rerolls_allowed} reroll(s) per player")
        if footer_parts:
            embed.set_footer(text=" · ".join(footer_parts))

        return embed

    def _refresh_reroll_button(self):
        """No-op — kept for compatibility. Reroll button lives in InProgressView after start."""
        pass

    async def reroll_champion(self, interaction: discord.Interaction, button):
        """
        Called by the Reroll Champion button (pre-game) and by InProgressView post-game.
        Identifies presser via interaction.user.id. Handles locking, DB updates,
        embed refresh, ephemeral notify, and log accumulation.
        Always re-fetches guild settings so mid-session changes to reroll count and
        champ weight take effect immediately.
        """
        discord_id = str(interaction.user.id)
        db = self.cog.db

        player = next((p for p in self.all_players if p["discord_id"] == discord_id), None)
        if player is None:
            await interaction.response.send_message(
                "You are not in this game.", ephemeral=True
            )
            return

        if discord_id not in self._reroll_locks:
            self._reroll_locks[discord_id] = asyncio.Lock()
        lock = self._reroll_locks[discord_id]

        if lock.locked():
            await interaction.response.send_message(
                "Your reroll is already being processed, please wait.", ephemeral=True
            )
            return

        async with lock:
            # Re-fetch settings every time so mid-session changes take effect
            live_settings = await db.get_settings(str(interaction.guild_id))
            rerolls_allowed = int(live_settings.get("champ_rerolls", 0))
            use_weights = bool(live_settings.get("champ_weight_enabled", 0))
            # Keep stored values in sync so the embed footer stays accurate
            self.champ_rerolls_allowed = rerolls_allowed
            self.use_weights = use_weights

            used = await db.get_champ_rerolls_used(self.game_id, discord_id) if self.game_id else self._rerolls_used.get(discord_id, 0)
            if used >= rerolls_allowed:
                await interaction.response.send_message(
                    f"You have no rerolls left (used {used}/{rerolls_allowed}).",
                    ephemeral=True
                )
                return

            all_assign = self._all_assign()
            role = all_assign.get(discord_id, "")
            old_champ = self.champ_assignments.get(discord_id, "?")

            # Exclude: all champs currently held by other players + every champ
            # this player has ever been assigned this game (no repeating their own history)
            others_champs = {c for did, c in self.champ_assignments.items() if did != discord_id}
            own_history = self._rolled_champs.get(discord_id, set())
            exclude = others_champs | own_history

            new_champ = await _reroll_one_champ(discord_id, role, db, use_weights, exclude)
            if not new_champ:
                await interaction.response.send_message(
                    "No champion data available for your role.", ephemeral=True
                )
                return

            self.champ_assignments[discord_id] = new_champ
            # Track this new champ in the player's personal history
            if discord_id not in self._rolled_champs:
                self._rolled_champs[discord_id] = set()
            self._rolled_champs[discord_id].add(new_champ)

            used_after = used + 1
            self._rerolls_used[discord_id] = used_after

            if self.game_id:
                await db.increment_champ_reroll(self.game_id, discord_id)

            self._reroll_log.append({
                "name": player["display_name"],
                "discord_id": discord_id,
                "from": old_champ,
                "to": new_champ,
                "used_after": used_after,
            })

            embed = self.build_embed()
            await interaction.response.defer()
            await interaction.message.edit(embed=embed)

            remaining = rerolls_allowed - used_after
            await interaction.followup.send(
                f"🎲 Rerolled: **{old_champ}** → **{new_champ}** ({remaining} reroll(s) left)",
                ephemeral=True
            )

    @discord.ui.button(label="▶️ Start Game", style=discord.ButtonStyle.success, row=0)
    async def start_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        from utils import check_is_session_owner, check_is_admin
        is_owner = await check_is_session_owner(interaction)
        is_admin = await check_is_admin(interaction)
        if not (is_owner or is_admin):
            await interaction.response.send_message(
                "Only the session owner or an admin can start the game.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.defer()
        guild_id = str(interaction.guild_id)
        db = self.cog.db

        # Save role history for playing players only
        if self.assign_roles:
            for did, role in self._all_assign().items():
                if role != "Fill":
                    await db.add_role_history(self.session_id, did, guild_id, role)

        # Increment game counter and create game record (bench not included)
        await db.increment_session_game(self.session_id)
        session = await db.get_active_session(guild_id)
        actual_game_num = session["game_number"] if session else self.game_num

        game_id = await db.create_game(
            self.session_id, guild_id, actual_game_num,
            [p["discord_id"] for p in self.team1],
            [p["discord_id"] for p in self.team2]
        )
        self.game_id = game_id

        # Move playing players to team VCs (bench stays in lobby)
        t1_ch_id = int(self.settings["team1_channel_id"]) if self.settings.get("team1_channel_id") else None
        t2_ch_id = int(self.settings["team2_channel_id"]) if self.settings.get("team2_channel_id") else None
        lobby_id = int(self.settings["lobby_channel_id"]) if self.settings.get("lobby_channel_id") else None

        footer = await _move_players_to_channels(
            interaction.guild, self.team1, self.team2, t1_ch_id, t2_ch_id
        )
        if self.bench:
            footer += f" · {len(self.bench)} sitting out"

        embed = self.build_embed(
            title_suffix=f"In Progress",
            description="Good luck! Click the winning team when the game ends.",
            color="gold"
        )
        embed.set_footer(text=footer)

        in_progress_view = InProgressView(
            start_view=self,
            game_id=game_id,
            session_id=self.session_id,
            team1=self.team1,
            team2=self.team2,
            bench=self.bench,
            team1_ch_id=t1_ch_id,
            team2_ch_id=t2_ch_id,
            lobby_ch_id=lobby_id,
            db=db,
            settings=self.settings,
            all_players=self.all_players,
            session_players=self.session_players,
            cog=self.cog
        )
        await interaction.message.edit(embed=embed, view=in_progress_view)

        # Post ELO breakdown to mod channel
        await self._post_elo_to_mod_channel(interaction, game_id, guild_id, db)

    async def _post_elo_to_mod_channel(self, interaction: discord.Interaction,
                                        game_id: int, guild_id: str, db):
        """
        Post per-player ELO breakdown to the mod channel when a game starts.
        Shows both Total ELO and mode-specific ELO for each player (with labels).
        Stores the sent message and pre-game ELO snapshots on self so
        InProgressView can follow up with ELO gains after the game ends.
        """
        mod_ch_id = self.settings.get("mod_channel_id")
        if not mod_ch_id:
            return
        try:
            guild = interaction.guild
            mod_ch = guild.get_channel(int(mod_ch_id))
            if mod_ch is None:
                mod_ch = await guild.fetch_channel(int(mod_ch_id))
            if not mod_ch:
                return

            session = await db.get_active_session(guild_id)
            auto_balance = session.get("auto_balance", "off") if session else "off"

            from cogs.elo import ELO_TYPE_LABELS
            mode_label = ELO_TYPE_LABELS.get(self.elo_type, self.elo_type)

            balance_note = {
                "off":   "⚖️ Auto-balance: **Off** (random split)",
                "total": "⚖️ Auto-balance: **On — Total ELO**",
                "mode":  f"⚖️ Auto-balance: **On — Mode ELO** ({mode_label})",
            }.get(auto_balance, f"⚖️ Auto-balance: {auto_balance}")

            # Fetch both total and mode ELO for every player
            # pre_elos stores snapshots BEFORE the game for use in the gain message
            self.pre_game_elos: dict[str, dict[str, float]] = {}  # discord_id -> {elo_type: elo}

            async def fetch_both(player: dict) -> dict:
                did = player["discord_id"]
                total_row = await db.get_player_elo(did, guild_id, "total")
                mode_row  = await db.get_player_elo(did, guild_id, self.elo_type)
                self.pre_game_elos[did] = {
                    "total": total_row["elo"],
                    self.elo_type: mode_row["elo"],
                }
                return {
                    "name":       player["display_name"],
                    "discord_id": did,
                    "total":      total_row["elo"],
                    "mode":       mode_row["elo"],
                }

            t1_data = [await fetch_both(p) for p in self.team1]
            t2_data = [await fetch_both(p) for p in self.team2]

            def avg(data, key): return sum(d[key] for d in data) / len(data) if data else 0

            t1_total_avg = avg(t1_data, "total")
            t2_total_avg = avg(t2_data, "total")
            t1_mode_avg  = avg(t1_data, "mode")
            t2_mode_avg  = avg(t2_data, "mode")

            same_type = self.elo_type == "total"

            def player_line(d: dict) -> str:
                if same_type:
                    return f"  {d['name']}: **{d['total']:.0f}** (Total ELO)"
                return (
                    f"  {d['name']}: **{d['total']:.0f}** Total"
                    f" | **{d['mode']:.0f}** Mode"
                )

            def team_block(data):
                return "\n".join(player_line(d) for d in data)

            if same_type:
                t1_avg_str = f"avg **{t1_total_avg:.0f}** Total ELO"
                t2_avg_str = f"avg **{t2_total_avg:.0f}** Total ELO"
                diff_str   = f"Total ELO diff: **{abs(t1_total_avg - t2_total_avg):.0f}**"
            else:
                t1_avg_str = (
                    f"avg **{t1_total_avg:.0f}** Total"
                    f" | avg **{t1_mode_avg:.0f}** Mode"
                )
                t2_avg_str = (
                    f"avg **{t2_total_avg:.0f}** Total"
                    f" | avg **{t2_mode_avg:.0f}** Mode"
                )
                diff_str = (
                    f"Total ELO diff: **{abs(t1_total_avg - t2_total_avg):.0f}**"
                    f" | Mode ELO diff: **{abs(t1_mode_avg - t2_mode_avg):.0f}**"
                )

            if not same_type:
                type_footer = (
                    f"\n_Total = across all modes  |  Mode = {mode_label}_"
                )
            else:
                type_footer = ""

            msg = (
                f"📊 **ELO Breakdown — Game #{self.game_num}**\n"
                f"{balance_note}\n\n"
                f"🔵 **Team 1** — {t1_avg_str}\n"
                f"{team_block(t1_data)}\n\n"
                f"🔴 **Team 2** — {t2_avg_str}\n"
                f"{team_block(t2_data)}\n\n"
                f"{diff_str}{type_footer}"
            )
            sent = await mod_ch.send(msg)
            # Stash for the post-game ELO gain follow-up
            self.elo_breakdown_msg = sent
            self.elo_mod_ch = mod_ch
        except Exception:
            pass  # Never block the game from starting

    @discord.ui.button(label="🎲 Re-roll Teams", style=discord.ButtonStyle.secondary, row=1)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        from utils import check_is_session_owner, check_is_admin
        is_owner = await check_is_session_owner(interaction)
        is_admin = await check_is_admin(interaction)
        if not (is_owner or is_admin):
            await interaction.response.send_message(
                "Only the session owner or an admin can re-roll teams.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=self.assign_roles, use_prefs=self.use_prefs,
            random_champs=self.random_champs, use_power=False, send_mode="message_edit"
        )

    @discord.ui.button(label="✖️ Cancel", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        embed = build_embed(
            "Teams Cancelled",
            "No game was started. Use `/make_teams` or `/start_draft` to try again.",
            "gray"
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ── Peer Rating Flow ──────────────────────────────────────────────────────────

STAR_LABELS = {1: "1 ⭐", 2: "2 ⭐⭐", 3: "3 ⭐⭐⭐", 4: "4 ⭐⭐⭐⭐", 5: "5 ⭐⭐⭐⭐⭐"}


def _player_context_line(player: dict, all_assign: dict, champ_assign: dict) -> str:
    """Build a compact context string: Name (Role · Champion) if available."""
    parts = []
    role = all_assign.get(player["discord_id"])
    if role and role != "Fill":
        parts.append(role)
    champ = champ_assign.get(player["discord_id"])
    if champ:
        parts.append(champ)
    ctx = f" ({' · '.join(parts)})" if parts else ""
    return f"**{player['display_name']}**{ctx}"


class SingleRatingView(discord.ui.View):
    """
    A view with 5 star buttons for rating one player.
    Resolves a future with the chosen score.
    """
    def __init__(self):
        super().__init__(timeout=300)
        self.future: asyncio.Future = asyncio.get_event_loop().create_future()

        for score in range(1, 6):
            btn = discord.ui.Button(
                label=STAR_LABELS[score],
                style=discord.ButtonStyle.secondary,
                custom_id=f"rate_{score}",
                row=0
            )
            async def _cb(inter: discord.Interaction, s=score):
                if not self.future.done():
                    self.future.set_result(s)
                self.stop()
                # Acknowledge without sending a new message; we'll edit the original
                await inter.response.defer()
            btn.callback = _cb
            self.add_item(btn)


async def _run_rating_flow(
    member: discord.Member,
    rater_id: str,
    guild_id: str,
    teammates: list,
    opponents: list,
    all_assign: dict,
    champ_assign: dict,
    game_num: int,
    db,
):
    """
    Send a DM rating flow to one player.
    Iterates teammates then opponents, editing the same message each step.
    """
    try:
        dm = await member.create_dm()
    except Exception:
        return

    to_rate = [("teammate", p) for p in teammates] + [("opponent", p) for p in opponents]
    total = len(to_rate)

    # Send the initial message
    try:
        intro_embed = discord.Embed(
            title=f"⭐ Post-Game Ratings — Game #{game_num}",
            description=(
                f"Rate your {total} fellow players one by one.\n"
                "Your ratings are anonymous and averaged over time."
            ),
            color=0xF1C40F
        )
        dm_msg = await dm.send(embed=intro_embed)
    except discord.Forbidden:
        return  # DMs closed
    except Exception:
        return

    ratings_given = 0

    for idx, (rel, target) in enumerate(to_rate, start=1):
        rel_label = "🤝 Teammate" if rel == "teammate" else "⚔️ Opponent"
        context_line = _player_context_line(target, all_assign, champ_assign)

        embed = discord.Embed(
            title=f"⭐ Rate Player ({idx}/{total})",
            description=(
                f"{rel_label}: {context_line}\n\n"
                "How would you rate this player's performance?"
            ),
            color=0x5865F2
        )

        view = SingleRatingView()
        try:
            await dm_msg.edit(embed=embed, view=view)
        except Exception:
            return

        try:
            score = await asyncio.wait_for(view.future, timeout=300)
        except asyncio.TimeoutError:
            # Player timed out — stop the flow silently
            try:
                timeout_embed = discord.Embed(
                    title="⏰ Rating Timed Out",
                    description="You didn't respond in time. Your remaining ratings were skipped.",
                    color=0xED4245
                )
                await dm_msg.edit(embed=timeout_embed, view=None)
            except Exception:
                pass
            return

        # Store the rating
        await db.add_rating(
            rated_id=target["discord_id"],
            rater_id=rater_id,
            guild_id=guild_id,
            score=float(score),
        )
        ratings_given += 1

    # All done
    await db.finish_rating_session(rater_id, guild_id)

    try:
        done_embed = discord.Embed(
            title="✅ Ratings Complete",
            description=f"You've finished rating all {ratings_given} player(s). Thanks!",
            color=0x57F287
        )
        await dm_msg.edit(embed=done_embed, view=None)
    except Exception:
        pass


class InProgressView(discord.ui.View):
    """
    Shown after Start Game is pressed. Has winner buttons (row 0) and optionally
    a Reroll Champion button (row 1) when random_champs + champ_rerolls > 0.
    Holds a reference to StartGameView for champ assignment state and reroll logic.
    """

    def __init__(self, start_view, game_id: int, session_id: int,
                 team1: list, team2: list, bench: list,
                 team1_ch_id: Optional[int], team2_ch_id: Optional[int],
                 lobby_ch_id: Optional[int], db, settings: dict,
                 all_players: list, session_players: list, cog):
        super().__init__(timeout=None)
        self.start_view = start_view  # StartGameView — owns champ_assignments, locks, etc.
        self.game_id = game_id
        self.session_id = session_id
        self.team1 = team1
        self.team2 = team2
        self.bench = bench
        self.team1_ch_id = team1_ch_id
        self.team2_ch_id = team2_ch_id
        self.lobby_ch_id = lobby_ch_id
        self.db = db
        self.settings = settings
        self.all_players = all_players
        self.session_players = session_players
        self.cog = cog

    async def _reroll_callback(self, interaction: discord.Interaction):
        """Delegate to StartGameView's reroll logic, which owns all the state."""
        await self.start_view.reroll_champion(interaction, None)

    async def _update_elos(self, winners: list, losers: list, guild_id: str):
        """Compute and persist ELO changes for both the mode-specific and total ELO."""
        all_players = winners + losers
        elo_type = self.start_view.elo_type

        # Fetch all needed ELO rows in bulk
        elo_rows: dict = {}
        for p in all_players:
            did = p["discord_id"]
            for et in (elo_type, "total"):
                if (did, et) not in elo_rows:
                    elo_rows[(did, et)] = await self.db.get_player_elo(did, guild_id, et)

        # Calculate and save for mode-specific ELO and total ELO
        for et in (elo_type, "total") if elo_type != "total" else ("total",):
            new_elos = _compute_elo_changes(winners, losers, et, guild_id, elo_rows)
            winner_ids = {p["discord_id"] for p in winners}
            for p in all_players:
                did = p["discord_id"]
                new_e = new_elos.get(did, elo_rows[(did, et)]["elo"])
                won = did in winner_ids
                await self.db.update_player_elo(did, guild_id, et, new_e, won)
                await self.db.record_elo_history(did, guild_id, et, new_e, self.game_id)

    async def _post_elo_gains(self, interaction: discord.Interaction,
                               winners: list, losers: list, guild_id: str):
        """
        Send a follow-up message to the mod channel (as a reply to the breakdown message
        if available) showing each player's ELO change after the game.
        """
        sv = self.start_view
        mod_ch = sv.elo_mod_ch
        if not mod_ch:
            return
        try:
            from cogs.elo import ELO_TYPE_LABELS
            elo_type = sv.elo_type
            mode_label = ELO_TYPE_LABELS.get(elo_type, elo_type)
            same_type = elo_type == "total"

            winner_ids = {p["discord_id"] for p in winners}
            all_players = winners + losers

            lines = ["📈 **ELO Gains / Losses**\n"]
            for p in all_players:
                did = p["discord_id"]
                pre = sv.pre_game_elos.get(did, {})

                total_row = await self.db.get_player_elo(did, guild_id, "total")
                mode_row  = await self.db.get_player_elo(did, guild_id, elo_type)

                new_total = total_row["elo"]
                new_mode  = mode_row["elo"]
                old_total = pre.get("total", new_total)
                old_mode  = pre.get(elo_type, new_mode)

                delta_total = new_total - old_total
                delta_mode  = new_mode  - old_mode

                won = did in winner_ids
                result_icon = "✅" if won else "❌"

                def fmt_delta(d: float) -> str:
                    sign = "+" if d >= 0 else ""
                    return f"{sign}{d:.1f}"

                if same_type:
                    lines.append(
                        f"{result_icon} **{p['display_name']}**: "
                        f"{old_total:.0f} → **{new_total:.0f}** "
                        f"({fmt_delta(delta_total)} Total ELO)"
                    )
                else:
                    lines.append(
                        f"{result_icon} **{p['display_name']}**: "
                        f"Total {old_total:.0f}→**{new_total:.0f}** ({fmt_delta(delta_total)})"
                        f" | Mode {old_mode:.0f}→**{new_mode:.0f}** ({fmt_delta(delta_mode)})"
                    )

            msg_text = "\n".join(lines)
            if sv.elo_breakdown_msg:
                await sv.elo_breakdown_msg.reply(msg_text)
            else:
                await mod_ch.send(msg_text)
        except Exception:
            pass

    async def _record_winner(self, interaction: discord.Interaction, winner: int):
        self.stop()
        await interaction.response.defer()
        await self.db.set_game_winner(self.game_id, winner)
        winners = self.team1 if winner == 1 else self.team2
        losers  = self.team2 if winner == 1 else self.team1

        for p in winners:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=True)
        for p in losers:
            await self.db.increment_games(p["discord_id"], p["guild_id"], won=False)

        # ── ELO update ────────────────────────────────────────────────────────
        await self._update_elos(winners, losers, str(interaction.guild_id))

        # ── ELO gain follow-up to mod channel ─────────────────────────────────
        await self._post_elo_gains(interaction, winners, losers, str(interaction.guild_id))

        guild = interaction.guild
        dest_id = self.lobby_ch_id or self.team1_ch_id
        if dest_id and guild:
            dest_ch = guild.get_channel(dest_id)
            if dest_ch is None:
                try:
                    dest_ch = await guild.fetch_channel(dest_id)
                except (discord.NotFound, discord.HTTPException):
                    dest_ch = None
            if dest_ch:
                for p in self.team1 + self.team2 + self.bench:
                    member = guild.get_member(int(p["discord_id"]))
                    if member is None:
                        try:
                            member = await guild.fetch_member(int(p["discord_id"]))
                        except (discord.NotFound, discord.HTTPException):
                            continue
                    if member.voice:
                        try:
                            await member.move_to(dest_ch)
                        except (discord.Forbidden, discord.HTTPException):
                            pass

        win_label = "🔵 Team 1" if winner == 1 else "🔴 Team 2"
        next_view = NextGameView(
            session_id=self.session_id,
            settings=self.settings,
            session_players=self.session_players,
            team1=self.team1,
            team2=self.team2,
            bench=self.bench,
            cog=self.cog
        )

        bench_note = f"\n{len(self.bench)} player(s) were sitting out and received no stats." if self.bench else ""
        embed = build_embed(
            f"{win_label} Wins!",
            f"Stats updated.{bench_note} Everyone moved back to lobby.\n\nWhat's next?",
            "green"
        )

        # Post reroll summary to mod channel last
        await self._post_reroll_summary(interaction)

        # Trigger peer rating DMs if enabled
        await self._send_rating_dms(interaction)

        await interaction.message.edit(embed=embed, view=next_view)

    async def _post_reroll_summary(self, interaction: discord.Interaction):
        """Post a per-player reroll summary to the mod channel after the game ends."""
        sv = self.start_view
        if not (sv.random_champs and sv.champ_rerolls_allowed > 0):
            return
        mod_ch_id = self.settings.get("mod_channel_id")
        if not mod_ch_id:
            return
        try:
            guild = interaction.guild
            mod_ch = guild.get_channel(int(mod_ch_id))
            if mod_ch is None:
                mod_ch = await guild.fetch_channel(int(mod_ch_id))
            if not mod_ch:
                return

            # Build per-player champ chain from the reroll log
            # Start each player's chain with their original champ (first assignment),
            # then append each reroll in order.
            chains: dict[str, list[str]] = {}  # discord_id -> [champ0, champ1, ...]
            names: dict[str, str] = {}

            # Seed chains with the original assignments
            for p in self.all_players:
                did = p["discord_id"]
                original = sv.champ_assignments.get(did)
                # Walk the log backwards to reconstruct: final champ is current assignment,
                # originals are the "from" fields of the first reroll per player.
                chains[did] = []
                names[did] = p["display_name"]

            # Replay the log in order to build each chain
            # First entry "from" is the original champ for that player
            seen: dict[str, bool] = {}
            for entry in getattr(sv, "_reroll_log", []):
                did = entry["discord_id"]
                if did not in seen:
                    chains[did].append(entry["from"])
                    seen[did] = True
                chains[did].append(entry["to"])

            # Players with no rerolls: chain is just their current (original) champ
            for p in self.all_players:
                did = p["discord_id"]
                if not chains[did]:
                    chains[did] = [sv.champ_assignments.get(did, "?")]

            lines = [f"📋 **Champion Reroll Summary — Game #{sv.game_num}**"]
            for p in self.all_players:
                did = p["discord_id"]
                used = sv._rerolls_used.get(did, 0)
                remaining = sv.champ_rerolls_allowed - used
                chain_str = " → ".join(chains[did])
                lines.append(f"• **{names[did]}** ({remaining} left): {chain_str}")

            await mod_ch.send("\n".join(lines))
        except Exception:
            pass

    async def _send_rating_dms(self, interaction: discord.Interaction):
        """Send peer rating DM flows to all playing players (not bench) if enabled."""
        try:
            if not self.settings.get("peer_ratings_enabled"):
                return

            guild = interaction.guild
            guild_id = str(interaction.guild_id)
            sv = self.start_view

            # Build context dicts for each playing player
            # assign_roles and champ_assignments live on start_view
            all_assign: dict[str, str] = {**sv.team1_assign, **sv.team2_assign} if sv.assign_roles else {}
            champ_assign: dict[str, str] = sv.champ_assignments if sv.random_champs else {}

            playing_ids = {p["discord_id"] for p in self.team1 + self.team2}

            for rater in self.team1 + self.team2:
                rater_id = rater["discord_id"]
                # teammates are same-team players excluding self
                teammates = [p for p in (self.team1 if rater in self.team1 else self.team2) if p["discord_id"] != rater_id]
                opponents = self.team2 if rater in self.team1 else self.team1

                member = guild.get_member(int(rater_id))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(rater_id))
                    except Exception:
                        continue

                # Fire-and-forget per player; errors are silently swallowed
                asyncio.create_task(
                    _run_rating_flow(
                        member=member,
                        rater_id=rater_id,
                        guild_id=guild_id,
                        teammates=teammates,
                        opponents=opponents,
                        all_assign=all_assign,
                        champ_assign=champ_assign,
                        game_num=sv.game_num,
                        db=self.db,
                    )
                )
        except Exception:
            pass

    @discord.ui.button(label="🔵 Team 1 Won", style=discord.ButtonStyle.primary, row=0)
    async def team1_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        from utils import check_is_session_owner, check_is_admin
        is_owner = await check_is_session_owner(interaction)
        is_admin = await check_is_admin(interaction)
        if not (is_owner or is_admin):
            await interaction.response.send_message(
                "Only the session owner or an admin can record the winner.", ephemeral=True
            )
            return
        await self._record_winner(interaction, 1)

    @discord.ui.button(label="🔴 Team 2 Won", style=discord.ButtonStyle.danger, row=0)
    async def team2_win(self, interaction: discord.Interaction, button: discord.ui.Button):
        from utils import check_is_session_owner, check_is_admin
        is_owner = await check_is_session_owner(interaction)
        is_admin = await check_is_admin(interaction)
        if not (is_owner or is_admin):
            await interaction.response.send_message(
                "Only the session owner or an admin can record the winner.", ephemeral=True
            )
            return
        await self._record_winner(interaction, 2)


class NextGameView(discord.ui.View):
    """Buttons shown after a game result is recorded. Always re-rolls from the full session roster."""

    def __init__(self, session_id: int, settings: dict, session_players: list,
                 team1: list, team2: list, bench: list, cog):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.settings = settings
        self.session_players = session_players  # full roster
        self.team1 = team1
        self.team2 = team2
        self.bench = bench
        self.cog = cog

    # Row 0: preference-based role options
    @discord.ui.button(label="Roles (Pref)", style=discord.ButtonStyle.primary, emoji="🎲", row=0)
    async def random_with_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, use_prefs=True, random_champs=False,
            use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Roles (Random)", style=discord.ButtonStyle.primary, emoji="🔀", row=0)
    async def random_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, use_prefs=False, random_champs=False,
            use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="No Roles", style=discord.ButtonStyle.secondary, emoji="👤", row=0)
    async def random_no_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=False, use_prefs=True, random_champs=False,
            use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Rematch", style=discord.ButtonStyle.secondary, emoji="🔁", row=0)
    async def rematch(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.team2 + self.team1, self.settings,
            assign_roles=True, use_prefs=True, random_champs=False,
            use_power=False, send_mode="followup",
            force_teams=(self.team2, self.team1)
        )

    # Row 1: random champion options
    @discord.ui.button(label="Champs + Roles (Pref)", style=discord.ButtonStyle.primary, emoji="🎰", row=1)
    async def random_champs_pref_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, use_prefs=True, random_champs=True,
            use_power=False, send_mode="followup"
        )

    @discord.ui.button(label="Champs + Roles (Random)", style=discord.ButtonStyle.secondary, emoji="🎰", row=1)
    async def random_champs_random_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, use_prefs=False, random_champs=True,
            use_power=False, send_mode="followup"
        )

    # Row 2: draft
    @discord.ui.button(label="Captain Draft", style=discord.ButtonStyle.success, emoji="🎯", row=2)
    async def captain_draft(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if len(self.session_players) < 3:
            await interaction.response.send_message("Need at least 3 players for a draft.", ephemeral=True)
            return
        db = self.cog.db
        guild_id = str(interaction.guild_id)
        past_captains = await db.get_past_captains(self.session_id, guild_id)
        view = CaptainDraftView(
            session_id=self.session_id,
            players=self.session_players,
            db=db,
            guild=interaction.guild,
            settings=self.settings,
            cog=self.cog,
            past_captain_ids=past_captains
        )
        await interaction.response.edit_message(embed=view._get_embed(), view=view)


class CaptainDraftView(discord.ui.View):
    """
    Snake draft: captains alternate picking players up to TEAM_SIZE each (10 total).
    Any remaining unchosen players become the bench.
    Snake order: T1 picks 1st, T2 picks 2nd & 3rd, T1 picks 4th & 5th, etc.

    Auth rules:
      - Captain selection: session owner or bot admin only
      - Draft picks: the active captain, session owner, or bot admin
    """

    def __init__(self, session_id: int, players: list, db, guild: discord.Guild,
                 settings: dict, cog, past_captain_ids: list[str] = None):
        super().__init__(timeout=600)  # 10 min — deferred interactions extend this
        self.session_id = session_id
        self.session_players = players
        self.player_map: dict[str, dict] = {p["discord_id"]: p for p in players}
        self.pool: list[str] = [p["discord_id"] for p in players]
        self.db = db
        self.guild = guild
        self.settings = settings
        self.cog = cog
        self.past_captain_ids: list[str] = list(past_captain_ids or [])
        self.team1: list[str] = []
        self.team2: list[str] = []
        self.captain1_id: str = None
        self.captain2_id: str = None
        # Snake order: T1 picks 1st, T2 picks 2nd & 3rd, T1 picks 4th & 5th,
        # T2 picks 6th & 7th, T1 picks 8th.  SNAKE[idx] gives the active team.
        # snake_pick_index starts at 0 (T1's first pick after captains set).
        self.snake_pick_index: int = 0
        self.turn: int = 1
        self.phase: str = "pick_captain1"

        self._build_buttons()

    # ── Auth helpers ─────────────────────────────────────────────────────────

    async def _is_admin_or_owner(self, interaction: discord.Interaction) -> bool:
        from utils import check_is_session_owner
        return await check_is_session_owner(interaction)

    async def _is_active_captain_or_admin(self, interaction: discord.Interaction) -> bool:
        """During draft phase, the active captain, any admin, or session owner may pick."""
        if await self._is_admin_or_owner(interaction):
            return True
        active_cap_id = self.captain1_id if self.turn == 1 else self.captain2_id
        return str(interaction.user.id) == active_cap_id

    # ── Shared edit helper ───────────────────────────────────────────────────

    async def _edit(self, interaction: discord.Interaction):
        """Deferred edit so we get maximum response time."""
        await interaction.response.defer()
        await interaction.message.edit(embed=self._get_embed(), view=self)

    # ── UI construction ──────────────────────────────────────────────────────

    def _player_option(self, did: str) -> discord.SelectOption:
        p = self.player_map[did]
        roles_str = " / ".join(p.get("role_prefs", [])) or "No preference"
        return discord.SelectOption(
            label=p["display_name"],
            value=did,
            description=roles_str[:50]
        )

    def _build_buttons(self):
        self.clear_items()

        if self.phase in ("pick_captain1", "pick_captain2"):
            placeholder = ("Pick Team 1 Captain..." if self.phase == "pick_captain1"
                           else "Pick Team 2 Captain...")
            sel = discord.ui.Select(placeholder=placeholder,
                                    options=[self._player_option(did) for did in self.pool])
            sel.callback = self._on_captain_pick
            self.add_item(sel)

            # Random captain button
            rnd_btn = discord.ui.Button(
                label="🎲 Random Captain",
                style=discord.ButtonStyle.secondary,
                row=1
            )
            rnd_btn.callback = self._on_random_captain
            self.add_item(rnd_btn)

        elif self.phase == "draft" and self.pool and not self._draft_complete():
            cap_id = self.captain1_id if self.turn == 1 else self.captain2_id
            cap_name = self.player_map[cap_id]["display_name"]
            team_size_now = len(self.team1) if self.turn == 1 else len(self.team2)
            team_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
            sel = discord.ui.Select(
                placeholder=f"{team_label} ({cap_name}): pick #{team_size_now + 1}/{TEAM_SIZE}...",
                options=[self._player_option(did) for did in self.pool]
            )
            sel.callback = self._on_draft_pick
            self.add_item(sel)

    # ── Embed ────────────────────────────────────────────────────────────────

    def _get_embed(self) -> discord.Embed:
        if self.phase == "pick_captain1":
            return build_embed(
                "Draft — Pick Team 1 Captain",
                "An admin or session owner can select or randomise the captain.\n"
                "Use the dropdown or the **🎲 Random Captain** button.",
                "blue"
            )
        if self.phase == "pick_captain2":
            cap1_name = self.player_map[self.captain1_id]["display_name"]
            return build_embed(
                "Draft — Pick Team 2 Captain",
                f"👑 **{cap1_name}** captains Team 1.\n"
                "Now pick or randomise Team 2's captain.",
                "red"
            )

        def fmt_team(ids: list[str], captain_id: str) -> str:
            if not ids:
                return "_empty_"
            lines = []
            for did in ids:
                p = self.player_map[did]
                crown = "👑 " if did == captain_id else ""
                roles = " / ".join(p.get("role_prefs", [])) or "Fill"
                lines.append(f"{crown}{p['display_name']} ({roles})")
            return "\n".join(lines)

        cap1_name = self.player_map[self.captain1_id]["display_name"] if self.captain1_id else "?"
        cap2_name = self.player_map[self.captain2_id]["display_name"] if self.captain2_id else "?"
        draft_done = self._draft_complete()

        if not draft_done:
            turn_cap = cap1_name if self.turn == 1 else cap2_name
            turn_label = "🔵 Team 1" if self.turn == 1 else "🔴 Team 2"
            title = f"Draft — {turn_label} ({turn_cap})'s Pick"
            color = "blue" if self.turn == 1 else "red"
            pool_lines = [
                f"• **{self.player_map[did]['display_name']}** — "
                f"{' / '.join(self.player_map[did].get('role_prefs', [])) or 'No preference'}"
                for did in self.pool
            ]
            desc = (
                f"_{turn_cap} or an admin/owner may pick._\n\n"
                "**Available:**\n" + "\n".join(pool_lines)
            )
        else:
            title = "Draft — Complete!"
            color = "green"
            remaining = self._bench()
            desc = (
                f"⏸️ **Sitting out:** {', '.join(p['display_name'] for p in remaining)}"
                if remaining else "All players picked!"
            )

        embed = build_embed(title, desc, color)
        embed.add_field(
            name=f"🔵 Team 1 ({len(self.team1)}/{TEAM_SIZE}) — cap: {cap1_name}",
            value=fmt_team(self.team1, self.captain1_id),
            inline=True
        )
        embed.add_field(
            name=f"🔴 Team 2 ({len(self.team2)}/{TEAM_SIZE}) — cap: {cap2_name}",
            value=fmt_team(self.team2, self.captain2_id),
            inline=True
        )
        return embed

    # ── Turn logic ───────────────────────────────────────────────────────────

    def _draft_complete(self) -> bool:
        return (
            (len(self.team1) >= TEAM_SIZE and len(self.team2) >= TEAM_SIZE)
            or not self.pool
        )

    def _active_team_full(self) -> bool:
        team = self.team1 if self.turn == 1 else self.team2
        return len(team) >= TEAM_SIZE

    def _advance_turn(self):
        """
        Desired snake: T1, T2, T2, T1, T1, T2, T2, T1
        (idx 0→T1, 1→T2, 2→T2, 3→T1, 4→T1, 5→T2, 6→T2, 7→T1)
        Pattern repeats every 4 picks after the first: [T1, T2, T2, T1] x2
        Implemented as a lookup table; auto-skip if the next team is already full.
        """
        SNAKE = [1, 2, 2, 1, 1, 2, 2, 1]  # 8 picks covers a full 5v5 draft
        self.snake_pick_index += 1
        idx = min(self.snake_pick_index, len(SNAKE) - 1)
        self.turn = SNAKE[idx]
        # If the assigned team is already full, give the pick to the other team
        if self._active_team_full():
            self.turn = 2 if self.turn == 1 else 1

    def _bench(self) -> list[dict]:
        return [self.player_map[did] for did in self.pool]

    # ── Callbacks ────────────────────────────────────────────────────────────

    async def _on_random_captain(self, interaction: discord.Interaction):
        if not await self._is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the session owner or an admin can randomise captains.", ephemeral=True
            )
            return

        pool_players = [self.player_map[did] for did in self.pool]
        cap = _pick_one_captain_randomly(pool_players, self.past_captain_ids)
        await self._assign_captain(interaction, cap["discord_id"])

    async def _on_captain_pick(self, interaction: discord.Interaction):
        if not await self._is_admin_or_owner(interaction):
            await interaction.response.send_message(
                "❌ Only the session owner or an admin can select captains.", ephemeral=True
            )
            return
        did = interaction.data["values"][0]
        if did not in self.pool:
            await interaction.response.send_message("That player was already picked.", ephemeral=True)
            return
        await self._assign_captain(interaction, did)

    async def _assign_captain(self, interaction: discord.Interaction, did: str):
        """Common path for both manual and random captain assignment."""
        self.pool.remove(did)

        if self.phase == "pick_captain1":
            self.captain1_id = did
            self.team1.append(did)
            self.phase = "pick_captain2"
        else:
            self.captain2_id = did
            self.team2.append(did)
            self.phase = "draft"
            self.turn = 1  # T1 always picks first after captains chosen
            self.snake_pick_index = 0

        self._build_buttons()

        if self._draft_complete():
            await interaction.response.defer()
            await interaction.message.edit(embed=self._get_embed(), view=None)
            await self._finish(interaction)
            return

        await self._edit(interaction)

    async def _on_draft_pick(self, interaction: discord.Interaction):
        if not await self._is_active_captain_or_admin(interaction):
            active_cap_id = self.captain1_id if self.turn == 1 else self.captain2_id
            cap_name = self.player_map[active_cap_id]["display_name"]
            await interaction.response.send_message(
                f"❌ It's **{cap_name}**'s pick. Only they, the session owner, or an admin can pick.",
                ephemeral=True
            )
            return

        did = interaction.data["values"][0]
        if did not in self.pool:
            await interaction.response.send_message("That player was already picked.", ephemeral=True)
            return
        self.pool.remove(did)

        if self.turn == 1:
            self.team1.append(did)
        else:
            self.team2.append(did)

        self._advance_turn()
        self._build_buttons()

        if not self._draft_complete():
            await self._edit(interaction)
        else:
            self.stop()
            await interaction.response.defer()
            await interaction.message.edit(embed=self._get_embed(), view=None)
            await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction):
        """Draft complete — bench the leftover pool and hand off to _finalize_teams."""
        guild_id = str(interaction.guild_id)
        if self.captain1_id:
            await self.db.add_captain(self.session_id, self.captain1_id, guild_id)
        if self.captain2_id:
            await self.db.add_captain(self.session_id, self.captain2_id, guild_id)

        team1 = [self.player_map[did] for did in self.team1]
        team2 = [self.player_map[did] for did in self.team2]
        bench = self._bench()

        await self.cog._finalize_teams(
            interaction, self.session_id, self.session_players, self.settings,
            assign_roles=True, random_champs=False, use_power=False,
            send_mode="message_edit",
            force_teams=(team1, team2, bench),
            override_elo_type="draft"
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def _get_role_history(self, session_id: int, players: list, guild_id: str) -> dict:
        history = {}
        for p in players:
            history[p["discord_id"]] = await self.db.get_played_roles(
                session_id, p["discord_id"], guild_id
            )
        return history

    async def _finalize_teams(self, interaction: discord.Interaction, session_id: int,
                               session_players: list, settings: dict,
                               assign_roles: bool = True,
                               use_prefs: bool = True,
                               random_champs: bool = False,
                               use_power: bool = False,
                               send_mode: str = "send",
                               force_teams: tuple = None,
                               override_elo_type: str = None):
        """
        Splits players into teams + bench, builds StartGameView, posts it.

        assign_roles: assign lane roles at all (if False, no roles shown)
        use_prefs:    honour player role preferences (if False, fully random roles but still history-aware)
        force_teams: optional (team1, team2) or (team1, team2, bench) tuple to
                     skip splitting (used by Rematch and CaptainDraft finish).

        send_mode:
          "send"         → interaction.response.send_message
          "followup"     → interaction.followup.send  (after defer())
          "message_edit" → interaction.message.edit() (already responded)
        """
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        repeat_roles = bool(session.get("repeat_roles", 0)) if session else False
        # track_roles is the inverse of repeat_roles
        track_roles = not repeat_roles
        auto_balance = session.get("auto_balance", "off") if session else "off"
        game_num = (session["game_number"] + 1) if session else 1

        if force_teams is not None:
            if len(force_teams) == 3:
                team1, team2, bench = force_teams
            else:
                team1, team2 = force_teams
                bench = []
        else:
            # Determine ELO type for this mode
            elo_type_key = _elo_type_for_mode(assign_roles, use_prefs, random_champs)

            if auto_balance in ("total", "mode"):
                fetch_type = "total" if auto_balance == "total" else elo_type_key
                elo_map: dict[str, float] = {}
                for p in session_players:
                    row = await self.db.get_player_elo(p["discord_id"], guild_id, fetch_type)
                    elo_map[p["discord_id"]] = row["elo"]
                team1, team2, bench = _split_players_balanced_by_elo(session_players, elo_map)
            else:
                team1, team2, bench = _split_players_random(session_players)

        # Assign roles for playing players only (preview — not saved until Start Game)
        team1_assign: dict = {}
        team2_assign: dict = {}
        if assign_roles:
            history = await self._get_role_history(session_id, team1 + team2, guild_id)
            team1_assign = _assign_roles(team1, history, track_roles, use_prefs=use_prefs)
            team2_assign = _assign_roles(team2, history, track_roles, use_prefs=use_prefs)

        # Assign random champions if requested
        team1_champs: dict = {}
        team2_champs: dict = {}
        no_champ_warning = ""
        use_weights = bool(settings.get("champ_weight_enabled", 0))
        if random_champs:
            patch = await self.db.get_champion_patch()
            if not patch:
                random_champs = False
                no_champ_warning = "\n⚠️ No champion data found — run `/update_champs` first."
            else:
                if assign_roles:
                    team1_champs = await _assign_champs(team1_assign, self.db, use_weights=use_weights)
                    # Pass team1 champs as exclude so team2 can't get the same champ
                    team2_champs = await _assign_champs(
                        team2_assign, self.db, use_weights=use_weights,
                        exclude=set(team1_champs.values())
                    )
                else:
                    all_playing = team1 + team2
                    temp_roles = ROLES * ((len(all_playing) // len(ROLES)) + 1)
                    random.shuffle(temp_roles)
                    temp_assign = {p["discord_id"]: temp_roles[i] for i, p in enumerate(all_playing)}
                    t1_temp = {p["discord_id"]: temp_assign[p["discord_id"]] for p in team1}
                    t2_temp = {p["discord_id"]: temp_assign[p["discord_id"]] for p in team2}
                    team1_champs = await _assign_champs(t1_temp, self.db, use_weights=use_weights)
                    team2_champs = await _assign_champs(
                        t2_temp, self.db, use_weights=use_weights,
                        exclude=set(team1_champs.values())
                    )

        # Resolve the ELO type for this game (stored on the view for use when recording results)
        elo_type_key = override_elo_type or _elo_type_for_mode(assign_roles, use_prefs, random_champs)

        start_view = StartGameView(
            session_id=session_id,
            team1=team1,
            team2=team2,
            bench=bench,
            team1_assign=team1_assign,
            team2_assign=team2_assign,
            team1_champs=team1_champs,
            team2_champs=team2_champs,
            assign_roles=assign_roles,
            use_prefs=use_prefs,
            random_champs=random_champs,
            use_weights=use_weights,
            settings=settings,
            game_num=game_num,
            all_players=team1 + team2,
            session_players=session_players,
            elo_type=elo_type_key,
            cog=self
        )
        embed = start_view.build_embed()
        if no_champ_warning:
            embed.description = (embed.description or "") + no_champ_warning

        if send_mode == "send":
            await interaction.response.send_message(embed=embed, view=start_view)
        elif send_mode == "followup":
            await interaction.followup.send(embed=embed, view=start_view)
        elif send_mode == "message_edit":
            await interaction.message.edit(embed=embed, view=start_view)

    # ── /make_teams ────────────────────────────────────────────────────────────

    @app_commands.command(name="make_teams", description="Split session players into two teams of 5. Extras sit out.")
    @app_commands.describe(
        assign_roles="Assign roles to players (default: True)",
        random_roles="Ignore role preferences — assign roles randomly but still avoid repeats (default: False)",
        random_champs="Randomly assign a champion to each player (default: False)",
    )
    @is_session_owner()
    async def make_teams(self, interaction: discord.Interaction,
                          assign_roles: bool = True,
                          random_roles: bool = False,
                          random_champs: bool = False):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], guild_id)
        if len(players) < 2:
            await interaction.response.send_message("Need at least 2 players in the session.", ephemeral=True)
            return

        settings = await self.db.get_settings(guild_id)

        await interaction.response.defer()
        await self._finalize_teams(
            interaction, session["id"], players, settings,
            assign_roles=assign_roles, use_prefs=not random_roles,
            random_champs=random_champs, use_power=False, send_mode="followup"
        )

    # ── /start_draft ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="start_draft",
        description="Captain snake draft. Captains pick up to 5 each; extras sit out."
    )
    @is_session_owner()
    async def start_draft(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        session = await self.db.get_active_session(guild_id)
        if not session:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return

        players = await self.db.get_session_players(session["id"], guild_id)
        if len(players) < 3:
            await interaction.response.send_message(
                "Need at least 3 players for a draft (2 captains + 1 to pick).", ephemeral=True
            )
            return

        settings = await self.db.get_settings(guild_id)
        past_captains = await self.db.get_past_captains(session["id"], guild_id)

        view = CaptainDraftView(
            session_id=session["id"],
            players=players,
            db=self.db,
            guild=interaction.guild,
            settings=settings,
            cog=self,
            past_captain_ids=past_captains,
        )
        await interaction.response.send_message(embed=view._get_embed(), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))