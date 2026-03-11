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

API_ID = 39566357
API_HASH = "28e543e71117bdfe3708595d584cf41e"

# ========= 夜间休眠 =========
SLEEP_START = 3   # 凌晨3点
SLEEP_END = 8     # 早上8点

# 修改后的转发群ID
FORWARD_CHAT_ID = -1003878983546
MARKED_FILE = "marked_users.json"

# ========= 白名单 =========
WHITE_KEYWORDS = {"入金金额", "银行卡号后四位"}

# ========= 关键词 =========
FILTER_KEYWORDS = {
"精聊","刷单","大区","泰铢","仲裁","卡主姓名","入金金额","香港","股票","换汇",
"公检法","通道","源头","进算","拖算","滲透","哈萨克","无视","尼日"
}

# ========= 国家 =========
COUNTRIES = {
"阿富汗","阿尔巴尼亚","阿尔及利亚","安道尔","安哥拉","阿根廷","亚美尼亚",
"澳大利亚","奥地利","阿塞拜疆","巴哈马","巴林","孟加拉国","白俄罗斯",
"比利时","巴西","文莱","保加利亚","柬埔寨","喀麦隆","加拿大","智利",
"哥伦比亚","古巴","塞浦路斯","捷克","丹麦","埃及","芬兰","法国","德国",
"希腊","印度","印度尼西亚","伊朗","伊拉克","爱尔兰","以色列","意大利",
"日本","约旦","哈萨克斯坦","韩国","科威特","老挝","马来西亚","马尔代夫",
"蒙古","摩洛哥","缅甸","尼泊尔","荷兰","新西兰","尼日利亚","挪威",
"巴基斯坦","菲律宾","波兰","葡萄牙","卡塔尔","罗马尼亚","俄罗斯",
"沙特阿拉伯","新加坡","南非","西班牙","瑞典","瑞士","叙利亚",
"泰国","土耳其","乌克兰","英国","美国","越南"
}

# ========= 屏蔽词 =========
BLOCK_KEYWORDS = {
"京东","淘宝","天猫","拼多多","支付宝","微信","Q币","苏宁","粉","能量","数据",
"反扫","跨境","飞机","资料","SPA","慈善","活跃","贷","码","油卡","实体卡",
"联系我","NFC","快手","抖音","抖币","香水","伟哥","机房","癌","币安","出海",
"社群","红包","广告","交友","信用","会员","短信","低价","案底","新闻",
"短视频","安全","电子","AG","小白","优惠","真人","茶","垫资","防骗助手",
"欢迎来到","大事件","奢侈品","珠宝","海外粉","一手","168","138","营业",
"赛车","飞艇","捡钱","澳门"
}

# ========= 广告关键词 =========
AD_KEYWORDS = {"买卖","拉群","招募","代理","广告","推广","加群","扫码","兼职"}

AD_PATTERNS = [
r"t\.me/",
r"telegram\.me/",
r"tg://join",
r"@\w+",
r"https?://"
]

FILTER_REGEXES = [
r"支付.*群",
r"换汇",
r"博彩"
]

AD_REGEX = [re.compile(p, re.I) for p in AD_PATTERNS]
FILTER_REGEX = [re.compile(p) for p in FILTER_REGEXES]

# ========= 防抖 =========
debounce_cache = {}
DEBOUNCE_TIME = 60
CACHE_EXPIRE = 3600

message_counter = 0
forward_counter = 0
start_time = time.time()

client = TelegramClient(
    "userbot_session",
    API_ID,
    API_HASH,
    connection=ConnectionTcpAbridged,
    auto_reconnect=True,
    retry_delay=5,
    request_retries=10
)

# ========= 工具 =========
# ========= 夜间休眠判断（北京时间） =========
def is_sleep_time():
    """
    判断是否为北京时间夜间休眠时间
    SLEEP_START 和 SLEEP_END 是北京时间小时（0-23）
    """
    # UTC 时间小时
    utc_hour = time.gmtime().tm_hour
    # 北京时间 = UTC +8
    bj_hour = (utc_hour + 8) % 24

    if SLEEP_START < SLEEP_END:
        return SLEEP_START <= bj_hour < SLEEP_END
    else:
        # 跨午夜情况，比如 23 ~ 8 点
        return bj_hour >= SLEEP_START or bj_hour < SLEEP_END

def safe_markdown(text):
    if not text:
        return ""

    replace_map = {
        "[":"【",
        "]":"】",
        "(":"（",
        ")":"）",
        "`":"",
        "_":"-",
        "*":"·"
    }

    for k,v in replace_map.items():
        text = text.replace(k,v)

    return text

def normalize_text(text):
    text = text.lower()
    text = re.sub(r"\s+", "", text)
    return text

# ========= 白名单 =========
def is_white(text):
    return any(k in text for k in WHITE_KEYWORDS)

# ========= 屏蔽 =========
def is_block(text):
    t = normalize_text(text)
    return any(k.lower() in t for k in BLOCK_KEYWORDS)

# ========= 广告 =========
def is_ad(text):
    if any(k in text for k in AD_KEYWORDS):
        return True
    return any(p.search(text) for p in AD_REGEX)

# ========= 关键词 =========
def is_target(text):
    if any(k in text for k in FILTER_KEYWORDS):
        return True
    if any(c in text for c in COUNTRIES):
        return True
    return any(p.search(text) for p in FILTER_REGEX)

# ========= 防抖 =========
def is_duplicate(text):
    now = time.time()
    key = hashlib.md5(normalize_text(text).encode()).hexdigest()

    if key in debounce_cache:
        if now - debounce_cache[key] < DEBOUNCE_TIME:
            return True

    debounce_cache[key] = now
    return False

def clean_cache():
    now = time.time()
    remove = [k for k,v in debounce_cache.items() if now - v > CACHE_EXPIRE]
    for k in remove:
        del debounce_cache[k]

# ========= 标记用户 =========
try:
    with open(MARKED_FILE,"r",encoding="utf-8") as f:
        marked_users=json.load(f)
except:
    marked_users={}

def save_marked():
    with open(MARKED_FILE,"w",encoding="utf-8") as f:
        json.dump(marked_users,f,ensure_ascii=False,indent=2)

# ========= 私聊命令 =========
@client.on(events.NewMessage(pattern=r'^/mark_id (\d+) (.+)'))
async def mark_user(event):
    if not event.is_private:
        return

    uid,remark=event.pattern_match.groups()
    marked_users[str(uid)] = remark  # ✅ 统一用 str 类型存储
    save_marked()

    await event.reply(f"✅ 标记成功\n{uid} → {remark}")

@client.on(events.NewMessage(pattern=r'^/unmark_id (\d+)'))
async def unmark_user(event):

    if not event.is_private:
        return

    uid=event.pattern_match.group(1)

    if uid in marked_users:
        del marked_users[uid]
        save_marked()
        await event.reply("❌ 已删除")

# ========= 模拟真人离线 =========

async def simulate_human_offline():
    while True:
        try:
            if is_sleep_time():
                await asyncio.sleep(600)
                continue

            online_time = random.randint(1800, 5400)
            print(f"🟢 模拟在线 {online_time // 60} 分钟")
            # ✅ 修复：恢复在线状态
            await client(functions.account.UpdateStatusRequest(offline=False))
            await asyncio.sleep(online_time)

            offline_time = random.randint(120, 360)
            print(f"🔴 模拟离线 {offline_time // 60} 分钟")

            await client(functions.account.UpdateStatusRequest(offline=True))
            await asyncio.sleep(offline_time)

            # ✅ 修复：离线结束后恢复在线
            await client(functions.account.UpdateStatusRequest(offline=False))

        except Exception as e:
            print(f"simulate_human_offline 异常: {e}")
            await asyncio.sleep(60)


# ========= GitHub自动更新 =========
async def github_auto_update():
    if not os.path.isdir(".git"):
        return

    while True:
        try:
            print("🔍 检查 GitHub 更新")

            subprocess.run(["git", "fetch", "origin"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

            local = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()

            # ✅ 修复：动态获取当前分支名，避免硬编码 main
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"]
            ).decode().strip()

            remote = subprocess.check_output(
                ["git", "rev-parse", f"origin/{branch}"]
            ).decode().strip()

            if local != remote:
                await client.send_message("me", "🚀 GitHub 发现新版本\n开始自动更新")
                print("🚀 发现新版本，自动更新")
                subprocess.run(["git", "pull"], check=True)
                print("♻️ 重启程序")
                os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as e:
            print(f"GitHub 更新检查失败: {e}")

        await asyncio.sleep(3600)

# ========= 转发 =========
async def forward_message(event,text):

    global forward_counter

    try:

        sender = await event.get_sender()
        chat = await event.get_chat()

        chat_title = safe_markdown(getattr(chat,"title","群"))

        if getattr(chat,"username",None):
            chat_link=f"https://t.me/{chat.username}"
        else:
            cid=str(event.chat_id)
            if cid.startswith("-100"):
                chat_link=f"https://t.me/c/{cid[4:]}"
            else:
                chat_link="https://t.me"

        sender_name = safe_markdown(get_display_name(sender))

        sender_username = getattr(sender, "username", None)

        if sender.username:
            sender_text=f"[{sender_name}](https://t.me/{sender.username})"
        else:
            sender_text=sender_name

        remark=""
        if str(sender.id) in marked_users:
            remark=f"\n⚠️ 标记：{marked_users[str(sender.id)]}"

        text = safe_markdown(text)

        msg=f"""【[{chat_title}]({chat_link})】
发信人：{sender_text}
内容：{text}{remark}
"""

        await asyncio.sleep(random.uniform(1,3))

        await client.send_message(
            FORWARD_CHAT_ID,
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
        print("转发失败:",e)

# ========= 主监听 =========
@client.on(events.NewMessage)
async def handler(event):
    """主监听：优化过滤顺序，白名单优先转发"""
    if is_sleep_time():
        return

    global message_counter

    try:
        if not (event.is_group or event.is_channel):
            return
        if event.chat_id == FORWARD_CHAT_ID:
            return
        if not event.message or not event.message.message:
            return

        text = event.message.message.strip()
        if not text:
            return

        message_counter += 1
        if len(debounce_cache) > 2000:
            clean_cache()

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

        # —— 长度限制 —— #
        if len(text) > 300:
            return

        # —— 防抖 —— #
        if is_duplicate(text):
            return

        # 满足条件，转发
        await forward_message(event, text)

    except Exception as e:
        print("handler异常:", e)

# ========= 日报 =========
async def daily_report():
    global message_counter, forward_counter, start_time

    while True:
        try:
            await asyncio.sleep(86400)
            uptime = int(time.time() - start_time)
            report = (
                f"📊 机器人运行报告\n\n"
                f"监听消息数：{message_counter}\n"
                f"转发消息数：{forward_counter}\n"
                f"运行时间：{uptime // 3600} 小时"
            )
            # ✅ 修复：加 try/except，发送失败不影响下次日报
            await client.send_message("me", report)
        except Exception as e:
            print(f"daily_report 异常: {e}")

# ========= 心跳 =========
async def heartbeat():

    global message_counter, forward_counter, start_time

    while True:

        uptime = int(time.time() - start_time)

        if is_sleep_time():
            status = "🌙 夜间休眠"
        else:
            status = "🟢 运行中"

        msg = f"""
💓 心跳检测

状态：{status}
监听消息：{message_counter}
转发消息：{forward_counter}
运行时间：{uptime//3600}小时
"""

        try:
            await client.send_message("me", msg)
        except Exception as e:
            print("心跳发送失败:", e)

        await asyncio.sleep(1800)

# ========= 启动 =========
heartbeat_task = None  # 全局
daily_task = None
update_task = None
offline_task = None

async def main():
    global heartbeat_task

    while True:
        try:
            await client.start()
            await client.get_dialogs()
            print("✅ 机器人启动成功")
            await client.send_message("me", "🤖 监听机器人已启动\n状态：运行中")

            # 只创建一次心跳
            if heartbeat_task is None or heartbeat_task.done():
                heartbeat_task = client.loop.create_task(heartbeat())

            # 其他任务
            global daily_task, update_task, offline_task

            if daily_task is None or daily_task.done():
                daily_task = client.loop.create_task(daily_report())

            if update_task is None or update_task.done():
                update_task = client.loop.create_task(github_auto_update())

            if offline_task is None or offline_task.done():
                offline_task = client.loop.create_task(simulate_human_offline())

            await client.run_until_disconnected()

        except Exception as e:
            print("❌ 连接异常:", e)
            try:
                await client.send_message("me", f"⚠️ 机器人异常\n{e}\n5秒后重连")
            except:
                pass
            await asyncio.sleep(5)

if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())
