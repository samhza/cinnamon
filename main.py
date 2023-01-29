from discord.ext.commands import bot
import tomli
import discord
import asyncio
from bot import Cinnamon
import config

async def main():
    async with Cinnamon() as bot:
        await bot.start()

if __name__ == '__main__':
    asyncio.run(main())