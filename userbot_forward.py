#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram 消息监听转发机器人
功能：监听群组消息，根据关键词过滤后转发到指定群组
支持：配置文件热重载、GitHub自动更新、模拟真人离线、群组警示
"""

from telethon import TelegramClient, events
from telethon.errors import ChatRestrictedError, FloodWaitError
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged
from telethon.utils import get_display_name
import re
import time
import json
import hashlib
import asyncio
import os
import subprocess
import sys
import random
from telethon import functions
from pathlib import Path
from datetime import datetime
import logging

# ========= 日志配置 =========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ========= 配置文件管理 =========
class Config:
    """配置管理类，支持热重载"""
    
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.last_load_time = 0
        self.reload_interval = 60  # 60秒检查一次更新
        self.load_config()
    
    def load_config(self):
        """加载配置文件"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            # API配置
            self.API_ID = config.get("api_id", 0)
            self.API_HASH = config.get("api_hash", "")
            self.FORWARD_CHAT_ID = config.get("forward_chat_id", 0)
            self.YOUR_USER_ID = config.get("your_user_id", None)
            
            # 休眠配置
            self.SLEEP_START = config.get("sleep_start", 3)
            self.SLEEP_END = config.get("sleep_end", 8)
            
            # 防抖配置
            self.DEBOUNCE_TIME = config.get("debounce_time", 60)
            self.CACHE_EXPIRE = config.get("cache_expire", 3600)
            
            # 心跳间隔
            self.HEARTBEAT_INTERVAL = config.get("heartbeat_interval", 1800)
            
            # 更新配置
            self.AUTO_UPDATE_INTERVAL = config.get("auto_update_interval", 14400)
            self.ENABLE_AUTO_UPDATE = config.get("enable_auto_update", True)
            self.UPDATE_BRANCH = config.get("update_branch", "main")
            
            # 关键词（转小写优化性能）
            self.WHITE_KEYWORDS = {k.lower() for k in config.get("white_keywords", [])}
            self.FILTER_KEYWORDS = {k.lower() for k in config.get("filter_keywords", [])}
            self.COUNTRIES = {c.lower() for c in config.get("countries", [])}
            self.BLOCK_KEYWORDS = {k.lower() for k in config.get("block_keywords", [])}
            self.AD_KEYWORDS = {k.lower() for k in config.get("ad_keywords", [])}
            
            # 正则表达式（预编译）
            ad_patterns = config.get("ad_patterns", [])
            self.AD_REGEX = [re.compile(p, re.I) for p in ad_patterns]
            
            filter_regexes = config.get("filter_regexes", [])
            self.FILTER_REGEX = [re.compile(p) for p in filter_regexes]
            
            # ========= 新增：警示配置 =========
            alert_config = config.get("alert_config", {})
            self.ALERT_ENABLED = alert_config.get("enabled", True)
            self.TRIGGER_KEYWORDS = [k.lower() for k in alert_config.get("trigger_keywords", ["暂停作业"])]
            self.ALERT_MESSAGE = alert_config.get("alert_message", "⚠️ 风险警示\n\n群组【{group_name}】已暂停作业！\n\n请谨慎交易，注意资金安全！\n\n时间：{time}")
            self.ALERT_MENTION_ALL = alert_config.get("mention_all", True)
            self.ALERT_COOLDOWN_MINUTES = alert_config.get("cooldown_minutes", 60)
            self.ALERT_FORWARD_CHAT_ID = alert_config.get("alert_forward_chat_id", self.FORWARD_CHAT_ID)
            
            logger.info(f"✅ 配置文件加载成功: {self.config_path}")
            
        except Exception as e:
            logger.error(f"❌ 配置文件加载失败: {e}")
            raise
    
    def check_reload(self):
        """检查是否需要重载配置"""
        if time.time() - self.last_load_time > self.reload_interval:
            self.load_config()
            self.last_load_time = time.time()
            return True
        return False

# 全局配置实例
config = Config()

# ========= 夜间休眠判断（北京时间） =========
def is_sleep_time():
    """判断是否为北京时间夜间休眠时间"""
    utc_hour = time.gmtime().tm_hour
    bj_hour = (utc_hour + 8) % 24
    
    if config.SLEEP_START < config.SLEEP_END:
        return config.SLEEP_START <= bj_hour < config.SLEEP_END
    else:
        return bj_hour >= config.SLEEP_START or bj_hour < config.SLEEP_END

# ========= 工具函数 =========
def safe_markdown(text):
    """安全处理Markdown特殊字符"""
    if not text:
        return ""
    
    replace_map = {
        "[": "【", "]": "】",
        "(": "（", ")": "）",
        "`": "", "_": "-",
        "*": "·", "~": "—",
        "|": "丨"
    }
    
    for k, v in replace_map.items():
        text = text.replace(k, v)
    
    return text

def normalize_text(text):
    """标准化文本：转小写、去空格"""
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    return text

# ========= 优化后的过滤函数 =========
def is_white(text):
    """白名单检查（已转小写优化）"""
    text_lower = text.lower()
    return any(k in text_lower for k in config.WHITE_KEYWORDS)

def is_block(text):
    """屏蔽词检查（已转小写优化）"""
    text_lower = text.lower()
    return any(k in text_lower for k in config.BLOCK_KEYWORDS)

def is_ad(text):
    """广告检查（优化版）"""
    text_lower = text.lower()
    # 关键词检查
    if any(k in text_lower for k in config.AD_KEYWORDS):
        return True
    # 正则检查（已预编译）
    return any(p.search(text) for p in config.AD_REGEX)

def is_target(text):
    """目标关键词检查（优化版）"""
    text_lower = text.lower()
    
    # 关键词检查
    if any(k in text_lower for k in config.FILTER_KEYWORDS):
        return True
    
    # 国家检查
    if any(c in text_lower for c in config.COUNTRIES):
        return True
    
    # 正则检查（已预编译）
    return any(p.search(text) for p in config.FILTER_REGEX)

# ========= 优化后的防抖 =========
class DebounceManager:
    """防抖管理器"""
    
    def __init__(self):
        self.cache = {}
        self.debounce_time = config.DEBOUNCE_TIME
        self.cache_expire = config.CACHE_EXPIRE
        self.hits = 0
        self.misses = 0
    
    def is_duplicate(self, text):
        """检查是否重复"""
        now = time.time()
        key = hashlib.md5(normalize_text(text).encode()).hexdigest()
        
        if key in self.cache:
            if now - self.cache[key] < self.debounce_time:
                self.hits += 1
                return True
        
        self.cache[key] = now
        self.misses += 1
        return False
    
    def clean_expired(self):
        """清理过期缓存"""
        now = time.time()
        remove_keys = [k for k, v in self.cache.items() if now - v > self.cache_expire]
        for k in remove_keys:
            del self.cache[k]
        if remove_keys:
            logger.debug(f"清理了 {len(remove_keys)} 条过期缓存")
        return len(remove_keys)
    
    def get_size(self):
        return len(self.cache)
    
    def get_stats(self):
        return {"size": self.get_size(), "hits": self.hits, "misses": self.misses}

debounce_manager = DebounceManager()

# ========= 标记用户管理 =========
MARKED_FILE = "marked_users.json"

def load_marked_users():
    """加载标记用户"""
    try:
        with open(MARKED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error(f"加载标记用户失败: {e}")
        return {}

def save_marked_users(users):
    """保存标记用户"""
    try:
        with open(MARKED_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存标记用户失败: {e}")

marked_users = load_marked_users()

# ========= 新增：警示管理器类 =========
class AlertManager:
    """群组警示管理器，防止重复警示"""
    
    def __init__(self):
        self.alerted_groups = {}  # {group_id: last_alert_time}
        self.cooldown = config.ALERT_COOLDOWN_MINUTES * 60
    
    def should_alert(self, group_id, group_name, message_text):
        """检查是否应该发送警示"""
        # 检查功能是否启用
        if not config.ALERT_ENABLED:
            return False
        
        # 检查消息中是否包含触发词
        text_lower = message_text.lower()
        is_triggered = any(kw in text_lower for kw in config.TRIGGER_KEYWORDS)
        
        if not is_triggered:
            return False
        
        # 检查冷却时间
        now = time.time()
        if group_id in self.alerted_groups:
            last_alert = self.alerted_groups[group_id]
            if now - last_alert < self.cooldown:
                logger.debug(f"群组 {group_name} 在冷却期内，跳过警示")
                return False
        
        return True
    
    def record_alert(self, group_id):
        """记录警示时间"""
        self.alerted_groups[group_id] = time.time()
    
    def clean_expired(self):
        """清理过期的警示记录"""
        now = time.time()
        expired = [gid for gid, last_time in self.alerted_groups.items() 
                   if now - last_time > self.cooldown * 2]
        for gid in expired:
            del self.alerted_groups[gid]
    
    def get_stats(self):
        """获取警示统计"""
        now = time.time()
        active = len([gid for gid, last_time in self.alerted_groups.items() 
                     if now - last_time < self.cooldown])
        return {"total": len(self.alerted_groups), "active": active}

alert_manager = AlertManager()

# ========= Telegram客户端初始化 =========
client = TelegramClient(
    "userbot_session",
    config.API_ID,
    config.API_HASH,
    connection=ConnectionTcpAbridged,
    auto_reconnect=True,
    retry_delay=5,
    request_retries=10
)

# ========= 全局统计变量 =========
message_counter = 0
forward_counter = 0
start_time = time.time()

# ========= 修复后的模拟真人离线 =========
async def simulate_human_offline():
    """模拟真人离线状态（修复版）"""
    logger.info("🟢 启动模拟在线/离线任务")
    
    while True:
        try:
            # 休眠期间不执行
            if is_sleep_time():
                await asyncio.sleep(300)
                continue
            
            # 在线时段
            online_time = random.randint(1800, 5400)
            logger.info(f"🟢 模拟在线 {online_time // 60} 分钟")
            await client(functions.account.UpdateStatusRequest(offline=False))
            await asyncio.sleep(online_time)
            
            # 再次检查是否进入休眠时间
            if is_sleep_time():
                continue
            
            # 离线时段
            offline_time = random.randint(120, 360)
            logger.info(f"🔴 模拟离线 {offline_time // 60} 分钟")
            await client(functions.account.UpdateStatusRequest(offline=True))
            await asyncio.sleep(offline_time)
            
        except Exception as e:
            logger.error(f"simulate_human_offline 异常: {e}")
            await asyncio.sleep(60)

# ========= GitHub自动更新 =========
async def github_auto_update():
    """GitHub自动更新"""
    if not os.path.isdir(".git"):
        logger.info("⚠️ 当前不是Git仓库，跳过自动更新")
        return
    
    if not config.ENABLE_AUTO_UPDATE:
        logger.info("⚠️ 自动更新已禁用")
        return
    
    logger.info(f"🟢 启动GitHub自动更新任务（间隔{config.AUTO_UPDATE_INTERVAL // 3600}小时）")
    
    while True:
        try:
            logger.info("🔍 检查 GitHub 更新")
            
            # 获取当前分支
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                text=True
            ).strip()
            
            # 获取远程更新
            subprocess.run(
                ["git", "fetch", "origin"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30
            )
            
            # 比较版本
            local = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                text=True
            ).strip()
            
            remote = subprocess.check_output(
                ["git", "rev-parse", f"origin/{branch}"],
                text=True
            ).strip()
            
            if local != remote:
                logger.info(f"🚀 发现新版本: {local[:7]} -> {remote[:7]}")
                await client.send_message(
                    "me", 
                    f"🚀 GitHub 发现新版本\n本地: {local[:7]}\n远程: {remote[:7]}\n开始自动更新"
                )
                
                # 执行更新
                subprocess.run(["git", "pull"], check=True)
                
                logger.info("♻️ 重启程序")
                await client.send_message("me", "✅ 更新完成，正在重启...")
                await asyncio.sleep(2)
                
                # 重启程序
                os.execv(sys.executable, [sys.executable] + sys.argv)
            
        except Exception as e:
            logger.error(f"GitHub 更新检查失败: {e}")
        
        await asyncio.sleep(config.AUTO_UPDATE_INTERVAL)

# ========= 转发消息 =========
async def forward_message(event, text):
    """转发消息到目标群组"""
    global forward_counter
    
    try:
        sender = await event.get_sender()
        chat = await event.get_chat()
        
        chat_title = safe_markdown(getattr(chat, "title", "群"))
        
        # 构建聊天链接
        if getattr(chat, "username", None):
            chat_link = f"https://t.me/{chat.username}"
        else:
            cid = str(event.chat_id)
            if cid.startswith("-100"):
                chat_link = f"https://t.me/c/{cid[4:]}"
            else:
                chat_link = "https://t.me"
        
        sender_name = safe_markdown(get_display_name(sender))
        
        # 发信人显示文本
        if sender.username:
            sender_text = f"@{sender.username}"
        else:
            sender_text = sender_name
        
        # 标记信息
        remark = ""
        if str(sender.id) in marked_users:
            remark = f"\n⚠️ 标记：{marked_users[str(sender.id)]}"
        
        # 原文链接
        original_link = ""
        if hasattr(event.message, 'id') and event.chat_id:
            chat_id_str = str(event.chat_id)
            if chat_id_str.startswith("-100"):
                original_link = f"\n[查看原文](https://t.me/c/{chat_id_str[4:]}/{event.message.id})"
        
        text = safe_markdown(text)
        
        msg = f"""【[{chat_title}]({chat_link})】
发信人：{sender_text}
内容：{text}{remark}{original_link}
"""
        # ========= 创建内联按钮 =========
        from telethon import types
        
        # 创建联系发信人的按钮
        contact_button = types.KeyboardButtonUrl(
            text="📞 点击联系发信人",
            url=f"tg://user?id={sender.id}"
        )
        
        # 如果有用户名，也可以添加一个备用按钮
        buttons = [[contact_button]]
        
        if sender.username:
            username_button = types.KeyboardButtonUrl(
                text="🔗 通过用户名联系",
                url=f"https://t.me/{sender.username}"
            )
            buttons.append([username_button])
        
        await asyncio.sleep(random.uniform(1, 3))
        
        # 发送带按钮的消息
        await client.send_message(
            config.FORWARD_CHAT_ID,
            msg,
            parse_mode="md",
            link_preview=False,
            buttons=buttons  # 添加内联按钮
        )
        
        forward_counter += 1
        logger.debug(f"转发消息: {text[:50]}...")
        
    except ChatRestrictedError:
        logger.warning("⚠️ 频道禁止发消息")
    except FloodWaitError as e:
        logger.warning(f"⚠️ FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logger.error(f"转发失败: {e}")

# ========= 新增：发送警示消息并@所有人 =========
async def send_alert_with_mention(chat_id, message):
    """发送警示消息并@所有人"""
    try:
        if config.ALERT_MENTION_ALL:
            # 尝试@所有人
            try:
                # 方法1：使用 @all 标签
                message_with_mention = f"@all {message}"
                await client.send_message(chat_id, message_with_mention)
                logger.info(f"已发送警示消息（含@all）到 {chat_id}")
            except Exception as e:
                logger.warning(f"@all 方式失败: {e}，使用普通消息")
                await client.send_message(chat_id, message)
        else:
            await client.send_message(chat_id, message)
            logger.info(f"已发送警示消息到 {chat_id}")
            
    except Exception as e:
        logger.error(f"发送警示消息失败: {e}")

# ========= 新增：检测群组是否暂停作业并发送警示 =========
async def check_and_alert(event):
    """检测群组是否暂停作业并发送警示"""
    try:
        # 获取群组信息
        chat = await event.get_chat()
        group_name = getattr(chat, "title", "未知群组")
        group_id = event.chat_id
        
        # 获取消息内容
        message_text = event.message.message if event.message else ""
        
        # 检查是否需要警示
        should_alert = False
        trigger_word = ""
        
        # 检查群名
        group_name_lower = group_name.lower()
        for kw in config.TRIGGER_KEYWORDS:
            if kw in group_name_lower:
                should_alert = True
                trigger_word = kw
                break
        
        # 检查消息内容
        if not should_alert and message_text:
            message_lower = message_text.lower()
            for kw in config.TRIGGER_KEYWORDS:
                if kw in message_lower:
                    should_alert = True
                    trigger_word = kw
                    break
        
        if not should_alert:
            return False
        
        # 检查冷却时间
        if not alert_manager.should_alert(group_id, group_name, message_text):
            return False
        
        # 记录警示
        alert_manager.record_alert(group_id)
        
        # 构建警示消息
        alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        alert_message = config.ALERT_MESSAGE.format(
            group_name=group_name,
            time=alert_time,
            trigger_word=trigger_word
        )
        
        # 添加消息来源信息
        if message_text:
            alert_message += f"\n\n📝 触发消息：{message_text[:100]}"
        
        # 发送警示到配置的群组
        target_chat_id = config.ALERT_FORWARD_CHAT_ID or config.FORWARD_CHAT_ID
        
        # 发送警示消息
        await send_alert_with_mention(target_chat_id, alert_message)
        
        # 同时发送给机器人主人
        try:
            await client.send_message("me", f"🔔 群组警示\n\n群组：{group_name}\n触发词：{trigger_word}\n时间：{alert_time}")
        except:
            pass
        
        logger.info(f"⚠️ 发送群组警示: {group_name} (触发词: {trigger_word})")
        return True
        
    except Exception as e:
        logger.error(f"check_and_alert 异常: {e}")
        return False

# ========= 私聊命令（带权限验证） =========
async def init_user_id():
    """初始化你的用户ID"""
    global YOUR_USER_ID
    me = await client.get_me()
    YOUR_USER_ID = me.id
    
    # 如果配置文件中没有设置，自动设置
    if config.YOUR_USER_ID is None:
        logger.info(f"⚠️ 配置文件中未设置your_user_id，自动设置为: {YOUR_USER_ID}")
        config.YOUR_USER_ID = YOUR_USER_ID
    
    logger.info(f"✅ 当前用户ID: {YOUR_USER_ID}")

def is_owner(event):
    """检查是否为机器人主人"""
    return event.sender_id == config.YOUR_USER_ID

@client.on(events.NewMessage(pattern=r'^/mark_id (\d+) (.+)'))
async def mark_user(event):
    """标记用户（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        await event.reply("❌ 权限不足，只有机器人主人可以使用此命令")
        return
    
    uid, remark = event.pattern_match.groups()
    marked_users[str(uid)] = remark
    save_marked_users(marked_users)
    
    await event.reply(f"✅ 标记成功\n{uid} → {remark}")
    logger.info(f"标记用户: {uid} -> {remark}")

@client.on(events.NewMessage(pattern=r'^/unmark_id (\d+)'))
async def unmark_user(event):
    """取消标记（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        await event.reply("❌ 权限不足，只有机器人主人可以使用此命令")
        return
    
    uid = event.pattern_match.group(1)
    
    if uid in marked_users:
        del marked_users[uid]
        save_marked_users(marked_users)
        await event.reply(f"❌ 已删除标记: {uid}")
        logger.info(f"取消标记: {uid}")
    else:
        await event.reply("❌ 未找到该用户标记")

@client.on(events.NewMessage(pattern=r'^/stats$'))
async def show_stats(event):
    """显示统计信息（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        return
    
    global message_counter, forward_counter, start_time
    
    uptime = int(time.time() - start_time)
    cache_stats = debounce_manager.get_stats()
    alert_stats = alert_manager.get_stats()
    
    stats = f"""📊 机器人统计

📈 监听消息: {message_counter}
📤 转发消息: {forward_counter}
📊 转发率: {(forward_counter/message_counter*100):.1f}%  (如果有消息)
⏱️ 运行时间: {uptime // 3600}小时 {(uptime % 3600) // 60}分钟

💾 缓存统计:
   - 缓存大小: {cache_stats['size']}
   - 命中次数: {cache_stats['hits']}
   - 未命中: {cache_stats['misses']}
   - 命中率: {(cache_stats['hits']/(cache_stats['hits']+cache_stats['misses'])*100):.1f}%  (如果有数据)

🔔 警示统计:
   - 功能状态: {'启用' if config.ALERT_ENABLED else '禁用'}
   - 触发关键词: {', '.join(config.TRIGGER_KEYWORDS)}
   - 冷却时间: {config.ALERT_COOLDOWN_MINUTES}分钟
   - 警示记录: {alert_stats['total']}个群组

🔧 配置信息:
   - 转发群组: {config.FORWARD_CHAT_ID}
   - 休眠时间: {config.SLEEP_START}:00 - {config.SLEEP_END}:00
   - 自动更新: {'开启' if config.ENABLE_AUTO_UPDATE else '关闭'}
"""
    
    await event.reply(stats)

@client.on(events.NewMessage(pattern=r'^/reload$'))
async def reload_config(event):
    """重载配置（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        return
    
    try:
        config.load_config()
        # 更新警示管理器的冷却时间
        alert_manager.cooldown = config.ALERT_COOLDOWN_MINUTES * 60
        await event.reply("✅ 配置重载成功")
        logger.info("配置重载成功")
    except Exception as e:
        await event.reply(f"❌ 配置重载失败: {e}")
        logger.error(f"配置重载失败: {e}")

@client.on(events.NewMessage(pattern=r'^/update$'))
async def force_update(event):
    """手动触发GitHub更新（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        await event.reply("❌ 权限不足，只有机器人主人可以使用此命令")
        return
    
    await event.reply("🔄 正在检查更新...")
    logger.info("手动触发更新检查")
    
    try:
        if not os.path.isdir(".git"):
            await event.reply("❌ 当前不是Git仓库，无法自动更新")
            return
        
        # 获取当前分支
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True
        ).strip()
        
        # 获取远程更新
        subprocess.run(
            ["git", "fetch", "origin"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30
        )
        
        # 比较版本
        local = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True
        ).strip()
        
        remote = subprocess.check_output(
            ["git", "rev-parse", f"origin/{branch}"],
            text=True
        ).strip()
        
        if local != remote:
            await event.reply(f"🚀 发现新版本\n本地: {local[:7]}\n远程: {remote[:7]}\n开始自动更新...")
            logger.info(f"发现新版本，开始更新: {local[:7]} -> {remote[:7]}")
            
            # 执行更新
            subprocess.run(["git", "pull"], check=True)
            
            await event.reply("✅ 更新完成，3秒后重启...")
            await asyncio.sleep(3)
            
            # 重启程序
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            await event.reply(f"✅ 已是最新版本\n当前版本: {local[:7]}")
            
    except Exception as e:
        await event.reply(f"❌ 更新失败: {e}")
        logger.error(f"更新失败: {e}")

# ========= 新增：警示统计命令 =========
@client.on(events.NewMessage(pattern=r'^/alert_stats$'))
async def show_alert_stats(event):
    """显示警示统计（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        return
    
    alert_stats = alert_manager.get_stats()
    
    stats = f"""🔔 警示系统统计

📊 功能状态: {'✅ 启用' if config.ALERT_ENABLED else '❌ 禁用'}
🔑 触发关键词: {', '.join(config.TRIGGER_KEYWORDS)}
⏰ 冷却时间: {config.ALERT_COOLDOWN_MINUTES} 分钟
📝 历史警示群组: {alert_stats['total']} 个
🟢 冷却中群组: {alert_stats['active']} 个

💡 当群名或消息包含触发关键词时，会自动发送警示并@所有人
"""
    
    await event.reply(stats)

# ========= 新增：手动触发警示命令 =========
@client.on(events.NewMessage(pattern=r'^/alert_group (.+)'))
async def manual_alert(event):
    """手动触发群组警示（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        await event.reply("❌ 权限不足")
        return
    
    group_name = event.pattern_match.group(1)
    
    alert_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_message = config.ALERT_MESSAGE.format(
        group_name=group_name,
        time=alert_time,
        trigger_word="手动触发"
    )
    
    target_chat_id = config.ALERT_FORWARD_CHAT_ID or config.FORWARD_CHAT_ID
    await send_alert_with_mention(target_chat_id, alert_message)
    
    await event.reply(f"✅ 已发送警示消息到群组\n群组：{group_name}")

@client.on(events.NewMessage(pattern=r'^/help$'))
async def show_help(event):
    """显示帮助信息（仅自己可用）"""
    if not event.is_private:
        return
    if not is_owner(event):
        return
    
    help_text = """🤖 **机器人命令帮助**

**管理命令：**
• `/stats` - 查看统计信息
• `/reload` - 重载配置文件
• `/update` - 手动检查并更新代码
• `/help` - 显示此帮助

**标记命令：**
• `/mark_id <用户ID> <备注>` - 标记用户
• `/unmark_id <用户ID>` - 取消标记

**警示命令：**
• `/alert_stats` - 查看警示系统统计
• `/alert_group <群组名>` - 手动发送群组警示

**警示功能说明：**
• 自动检测群名或消息中的暂停作业关键词
• 检测到后自动发送警示消息到指定群组
• 支持@所有人提醒（需要管理员权限）
• 每个群组有冷却时间，避免重复警示

**功能说明：**
• 自动监听群组消息
• 根据关键词过滤转发
• 支持配置文件热重载
• 支持GitHub自动更新
• 模拟真人在线/离线状态

**配置文件：** `config.json`
**日志文件：** `bot.log`
"""
    
    await event.reply(help_text, parse_mode="md")

# ========= 主监听 =========
@client.on(events.NewMessage)
async def handler(event):
    """主监听：优化过滤顺序，白名单优先转发"""
    # 休眠检查
    if is_sleep_time():
        return
    
    global message_counter
    
    try:
        # 基础过滤
        if not (event.is_group or event.is_channel):
            return
        if event.chat_id == config.FORWARD_CHAT_ID:
            return
        if not event.message or not event.message.message:
            return
        
        text = event.message.message.strip()
        if not text:
            return
        
        # ========= 新增：群组警示检测（优先执行） =========
        await check_and_alert(event)
        
        message_counter += 1
        
        # 定期清理缓存和重载配置
        if message_counter % 100 == 0:
            debounce_manager.clean_expired()
            alert_manager.clean_expired()
            if config.check_reload():
                logger.info("配置已热重载")
        
        # —— 高效过滤：先屏蔽广告与垃圾信息 —— #
        if is_block(text):
            return
        if is_ad(text):
            return
        
        # —— 白名单强制转发 —— #
        if is_white(text):
            await forward_message(event, text)
            return
        
        # —— 关键词 / 国家过滤 —— #
        if not is_target(text):
            return
        
        # —— 长度限制（超过则截断） —— #
        if len(text) > 300:
            text = text[:297] + "..."
        
        # —— 防抖 —— #
        if debounce_manager.is_duplicate(text):
            return
        
        # 满足条件，转发
        await forward_message(event, text)
        
    except Exception as e:
        logger.error(f"handler异常: {e}")

# ========= 日报 =========
async def daily_report():
    """每日报告"""
    global message_counter, forward_counter, start_time
    
    while True:
        try:
            await asyncio.sleep(86400)  # 24小时
            
            uptime = int(time.time() - start_time)
            cache_stats = debounce_manager.get_stats()
            alert_stats = alert_manager.get_stats()
            
            report = f"""📊 机器人运行日报

📅 日期: {datetime.now().strftime('%Y-%m-%d')}

📈 今日统计:
   - 监听消息: {message_counter}
   - 转发消息: {forward_counter}
   - 转发率: {(forward_counter/message_counter*100):.1f}% (如果有消息)

⏱️ 运行时长: {uptime // 3600}小时 {(uptime % 3600) // 60}分钟

💾 缓存效率:
   - 命中率: {(cache_stats['hits']/(cache_stats['hits']+cache_stats['misses'])*100):.1f}% (如果有数据)
   - 缓存大小: {cache_stats['size']}

🔔 警示统计:
   - 触发警示: {alert_stats['total']} 次

🔧 状态: {'运行中' if not is_sleep_time() else '休眠中'}
"""
            
            await client.send_message("me", report)
            logger.info("每日报告已发送")
            
        except Exception as e:
            logger.error(f"daily_report 异常: {e}")

# ========= 心跳 =========
async def heartbeat():
    """心跳检测"""
    global message_counter, forward_counter, start_time
    
    while True:
        try:
            await asyncio.sleep(config.HEARTBEAT_INTERVAL)
            
            uptime = int(time.time() - start_time)
            
            if is_sleep_time():
                status = "🌙 夜间休眠"
            else:
                status = "🟢 运行中"
            
            msg = f"""💓 心跳检测 [{datetime.now().strftime('%H:%M:%S')}]

状态: {status}
监听消息: {message_counter}
转发消息: {forward_counter}
运行时间: {uptime // 3600}小时 {(uptime % 3600) // 60}分钟
缓存大小: {debounce_manager.get_size()}
警示记录: {len(alert_manager.alerted_groups)}个群组
"""
            
            await client.send_message("me", msg)
            logger.debug("心跳已发送")
            
        except Exception as e:
            logger.error(f"心跳发送失败: {e}")

# ========= 新增：定时清理警示缓存 =========
async def alert_cache_cleaner():
    """定时清理警示缓存"""
    while True:
        await asyncio.sleep(3600)  # 每小时清理一次
        alert_manager.clean_expired()
        logger.debug("警示缓存已清理")

# ========= 主函数 =========
async def main():
    """主函数"""
    while True:
        try:
            await client.start()
            await client.get_dialogs()
            
            # 初始化用户ID
            await init_user_id()
            
            logger.info("✅ 机器人启动成功")
            await client.send_message("me", "🤖 监听机器人已启动\n状态：运行中\n输入 /help 查看帮助")
            
            # 创建所有后台任务
            tasks = [
                asyncio.create_task(heartbeat()),
                asyncio.create_task(daily_report()),
                asyncio.create_task(github_auto_update()),
                asyncio.create_task(simulate_human_offline()),
                asyncio.create_task(alert_cache_cleaner()),  # 新增
            ]
            
            await client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"❌ 连接异常: {e}")
            try:
                await client.send_message("me", f"⚠️ 机器人异常\n{str(e)[:200]}\n5秒后重连")
            except:
                pass
            await asyncio.sleep(5)

if __name__ == "__main__":
    logger.info("启动 Telegram 监听机器人...")
    
    with client:
        client.loop.run_until_complete(main())
