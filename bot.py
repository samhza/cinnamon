from discord.ext.commands import AutoShardedBot, Context
import discord.ext.commands as commands
import discord
import config
import logging
import aiohttp

initial_extensions = [
    "cogs.basic",
    "cogs.processing",
]

log = logging.getLogger(__name__)


class Cinnamon(AutoShardedBot):
    def __init__(self):
        intents = discord.Intents(
            guilds=True,
            members=True,
            bans=True,
            emojis=True,
            voice_states=True,
            messages=True,
            reactions=True,
            message_content=True,
        )
        super().__init__(command_prefix=config.prefix, intents=intents)

    async def on_command_error(
        self, ctx: Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.author.send("This command cannot be used in private messages.")
        elif isinstance(error, commands.DisabledCommand):
            await ctx.author.send("Sorry. This command is disabled and cannot be used.")
        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            if not isinstance(original, discord.HTTPException):
                log.exception("In %s:", ctx.command.qualified_name, exc_info=original)
        elif isinstance(error, commands.ArgumentParsingError):
            await ctx.send(str(error))

    async def start(self) -> None:
        await super().start(config.token, reconnect=True)

    async def setup_hook(self) -> None:
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()
        for extension in initial_extensions:
            try:
                await self.load_extension(extension)
            except Exception as e:
                log.exception("Failed to load extension %s.", extension)
