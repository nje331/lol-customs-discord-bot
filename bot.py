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
    bot.db = await Database.create("lol_bot.db")
    await bot.load_extension("cogs.players")
    await bot.load_extension("cogs.session")
    await bot.load_extension("cogs.teams")
    await bot.load_extension("cogs.settings")
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")


if __name__ == "__main__":
    bot.run(TOKEN)
