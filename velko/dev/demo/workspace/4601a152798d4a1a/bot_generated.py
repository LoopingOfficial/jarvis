# Génération locale VELKO — modèle qwen2.5-coder:7b


import discord
from discord.ext import commands
from os import getenv

TOKEN = getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.messages = True

bot = commands.Bot(command_prefix='/', intents=intents)

@bot.slash_command(description='Ping the bot to check if it\'s online')
async def ping(ctx):
    await ctx.respond('Pong!')

bot.run(TOKEN)
