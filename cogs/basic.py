import discord.ext.commands as commands
from bot import Cinnamon


class Basic(commands.Cog):
    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send("Pong!")


async def setup(bot: Cinnamon) -> None:
    await bot.add_cog(Basic(bot))
