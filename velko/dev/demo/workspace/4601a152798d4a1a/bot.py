import os
import discord
from discord import app_commands
from logic import ping_response

class VelkoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.environ.get("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

client = VelkoBot()

@client.tree.command(name="ping", description="Vérifier que VELKO répond")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(ping_response(client.latency), ephemeral=True)

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN manquant : aucune connexion Discord effectuée.")
    client.run(token)
