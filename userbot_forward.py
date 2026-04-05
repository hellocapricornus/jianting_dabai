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
            
            # 休眠配置
            self.SLEEP_START = config.get("sleep_start", 3)
            self.SLEEP_END = config.get("sleep_end", 8)
            
            # 防抖配置
            self.DEBOUNCE_TIME = config.get("debounce_time", 60)
            self.CACHE_EXPIRE = config.get("cache_expire", 3600)
            
            # 心跳间隔
            self.HEARTBEAT_INTERVAL = config.get("heartbeat_interval", 1800)
            
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
            
            print(f"✅ 配置文件加载成功: {self.config_path}")
            
        except Exception as e:
            print(f"❌ 配置文件加载失败: {e}")
            raise
    
    def check_reload(self):
        """检查是否需要重载配置"""
        if time.time() - self.last_load_time > self.reload_interval:
            self.load_config()
            self.last_load_time = time.time()

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
        "*": "·"
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
    
    def is_duplicate(self, text):
        """检查是否重复"""
        now = time.time()
        key = hashlib.md5(normalize_text(text).encode()).hexdigest()
        
        if key in self.cache:
            if now - self.cache[key] < self.debounce_time:
                return True
        
        self.cache[key] = now
        return False
    
    def clean_expired(self):
        """清理过期缓存"""
        now = time.time()
        remove_keys = [k for k, v in self.cache.items() if now - v > self.cache_expire]
        for k in remove_keys:
            del self.cache[k]
        return len(remove_keys)
    
    def get_size(self):
        return len(self.cache)

debounce_manager = DebounceManager()

# ========= 标记用户管理 =========
MARKED_FILE = "marked_users.json"

def load_marked_users():
    """加载标记用户"""
    try:
        with open(MARKED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_marked_users(users):
    """保存标记用户"""
    with open(MARKED_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

marked_users = load_marked_users()

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

# ========= 修复后的模拟真人离线 =========
async def simulate_human_offline():
    """模拟真人离线状态（修复版）"""
    print("🟢 启动模拟在线/离线任务")
    
    while True:
        try:
            # 休眠期间不执行
            if is_sleep_time():
                await asyncio.sleep(300)
                continue
            
            # 在线时段
            online_time = random.randint(1800, 5400)
            print(f"🟢 模拟在线 {online_time // 60} 分钟")
            await client(functions.account.UpdateStatusRequest(offline=False))
            await asyncio.sleep(online_time)
            
            # 再次检查是否进入休眠时间
            if is_sleep_time():
                continue
            
            # 离线时段
            offline_time = random.randint(120, 360)
            print(f"🔴 模拟离线 {offline_time // 60} 分钟")
            await client(functions.account.UpdateStatusRequest(offline=True))
            await asyncio.sleep(offline_time)
            
        except Exception as e:
            print(f"simulate_human_offline 异常: {e}")
            await asyncio.sleep(60)

# ========= GitHub自动更新 =========
async def github_auto_update():
    """GitHub自动更新"""
    if not os.path.isdir(".git"):
        return
    
    while True:
        try:
            print("🔍 检查 GitHub 更新")
            
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
                await client.send_message("me", "🚀 GitHub 发现新版本\n开始自动更新")
                print("🚀 发现新版本，自动更新")
                subprocess.run(["git", "pull"], check=True)
                print("♻️ 重启程序")
                os.execv(sys.executable, [sys.executable] + sys.argv)
            
        except Exception as e:
            print(f"GitHub 更新检查失败: {e}")
        
        await asyncio.sleep(3600)

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
        
        # 发信人链接
        if sender.username:
            sender_text = f"[{sender_name}](https://t.me/{sender.username})"
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
        
        await asyncio.sleep(random.uniform(1, 3))
        
        await client.send_message(
            config.FORWARD_CHAT_ID,
            msg,
            parse_mode="md",
            link_preview=False
        )
        
        forward_counter += 1
        
    except ChatRestrictedError:
        print("⚠️ 频道禁止发消息")
    except FloodWaitError as e:
        print(f"⚠️ FloodWait {e.seconds}s")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        print("转发失败:", e)

# ========= 私聊命令（带权限验证） =========
YOUR_USER_ID = None  # 设置你的用户ID

async def init_user_id():
    """初始化你的用户ID"""
    global YOUR_USER_ID
    me = await client.get_me()
    YOUR_USER_ID = me.id
    print(f"✅ 当前用户ID: {YOUR_USER_ID}")

@client.on(events.NewMessage(pattern=r'^/mark_id (\d+) (.+)'))
async def mark_user(event):
    """标记用户（仅自己可用）"""
    if not event.is_private:
        return
    if event.sender_id != YOUR_USER_ID:
        await event.reply("❌ 权限不足，只有机器人主人可以使用此命令")
        return
    
    uid, remark = event.pattern_match.groups()
    marked_users[str(uid)] = remark
    save_marked_users(marked_users)
    
    await event.reply(f"✅ 标记成功\n{uid} → {remark}")

@client.on(events.NewMessage(pattern=r'^/unmark_id (\d+)'))
async def unmark_user(event):
    """取消标记（仅自己可用）"""
    if not event.is_private:
        return
    if event.sender_id != YOUR_USER_ID:
        await event.reply("❌ 权限不足，只有机器人主人可以使用此命令")
        return
    
    uid = event.pattern_match.group(1)
    
    if uid in marked_users:
        del marked_users[uid]
        save_marked_users(marked_users)
        await event.reply("❌ 已删除标记")
    else:
        await event.reply("❌ 未找到该用户标记")

@client.on(events.NewMessage(pattern=r'^/stats$'))
async def show_stats(event):
    """显示统计信息（仅自己可用）"""
    if not event.is_private:
        return
    if event.sender_id != YOUR_USER_ID:
        return
    
    global message_counter, forward_counter, start_time
    
    uptime = int(time.time() - start_time)
    cache_size = debounce_manager.get_size()
    
    stats = f"""📊 机器人统计

📈 监听消息: {message_counter}
📤 转发消息: {forward_counter}
⏱️ 运行时间: {uptime // 3600}小时 {(uptime % 3600) // 60}分钟
💾 缓存大小: {cache_size}
🔧 配置重载间隔: {config.reload_interval}秒
"""
    
    await event.reply(stats)

@client.on(events.NewMessage(pattern=r'^/reload$'))
async def reload_config(event):
    """重载配置（仅自己可用）"""
    if not event.is_private:
        return
    if event.sender_id != YOUR_USER_ID:
        return
    
    try:
        config.load_config()
        await event.reply("✅ 配置重载成功")
    except Exception as e:
        await event.reply(f"❌ 配置重载失败: {e}")

# ========= 主监听 =========
message_counter = 0
forward_counter = 0
start_time = time.time()

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
        
        message_counter += 1
        
        # 定期清理缓存和重载配置
        if message_counter % 100 == 0:
            cleaned = debounce_manager.clean_expired()
            config.check_reload()
            if cleaned:
                print(f"🧹 清理了 {cleaned} 条过期缓存")
        
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
        print("handler异常:", e)

# ========= 日报 =========
async def daily_report():
    """每日报告"""
    global message_counter, forward_counter, start_time
    
    while True:
        try:
            await asyncio.sleep(86400)
            uptime = int(time.time() - start_time)
            report = (
                f"📊 机器人运行报告\n\n"
                f"监听消息数：{message_counter}\n"
                f"转发消息数：{forward_counter}\n"
                f"运行时间：{uptime // 3600} 小时 { (uptime % 3600) // 60 } 分钟"
            )
            await client.send_message("me", report)
        except Exception as e:
            print(f"daily_report 异常: {e}")

# ========= 心跳 =========
async def heartbeat():
    """心跳检测"""
    global message_counter, forward_counter, start_time
    
    while True:
        try:
            uptime = int(time.time() - start_time)
            
            if is_sleep_time():
                status = "🌙 夜间休眠"
            else:
                status = "🟢 运行中"
            
            msg = f"""💓 心跳检测

状态：{status}
监听消息：{message_counter}
转发消息：{forward_counter}
运行时间：{uptime // 3600}小时 {(uptime % 3600) // 60}分钟
缓存大小：{debounce_manager.get_size()}
"""
            
            await client.send_message("me", msg)
            
        except Exception as e:
            print("心跳发送失败:", e)
        
        await asyncio.sleep(config.HEARTBEAT_INTERVAL)

# ========= 主函数 =========
async def main():
    """主函数"""
    while True:
        try:
            await client.start()
            await client.get_dialogs()
            
            # 初始化用户ID
            await init_user_id()
            
            print("✅ 机器人启动成功")
            await client.send_message("me", "🤖 监听机器人已启动\n状态：运行中")
            
            # 创建所有后台任务
            tasks = [
                asyncio.create_task(heartbeat()),
                asyncio.create_task(daily_report()),
                asyncio.create_task(github_auto_update()),
                asyncio.create_task(simulate_human_offline()),
            ]
            
            await client.run_until_disconnected()
            
        except Exception as e:
            print("❌ 连接异常:", e)
            try:
                await client.send_message("me", f"⚠️ 机器人异常\n{str(e)[:200]}\n5秒后重连")
            except:
                pass
            await asyncio.sleep(5)

if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
