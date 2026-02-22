"""
League of Legends Custom Game Bot
Entry point - run this file to start the bot.
"""

import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv
from database import Database

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.db = None


@bot.event
async def on_ready():
    # Default to the volume path — never falls back to /app which is not persisted
    db_path = os.getenv("DB_PATH", "/app/data/lol_bot.db")
    # Ensure the directory exists (safe even if volume isn't mounted)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    bot.db = await Database.create(db_path)
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print(f"📦 Database: {db_path}")
    await bot.load_extension("cogs.players")
    await bot.load_extension("cogs.session")
    await bot.load_extension("cogs.teams")
    await bot.load_extension("cogs.settings")
    await bot.load_extension("cogs.champions")
    await bot.tree.sync()


if __name__ == "__main__":
    bot.run(TOKEN)