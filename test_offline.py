#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线测试脚本：不连接 Telegram，验证所有核心逻辑
运行: python3 test_offline.py
"""
import sys
import os
import asyncio
import re

# 设置不自动启动 client 的环境标记
os.environ["TEST_MODE"] = "1"

print("=" * 60)
print("🧪 开始离线测试")
print("=" * 60)

# ========= 测试 1：模块导入 =========
print("\n[1/5] 测试模块导入...")
try:
    import userbot_forward as ub
    print("  ✅ 模块导入成功")
    print(f"  ✅ Config 加载: API_ID={ub.config.API_ID}, FORWARD_CHAT_ID={ub.config.FORWARD_CHAT_ID}")
    print(f"  ✅ 白名单关键词数: {len(ub.config.WHITE_KEYWORDS)}")
    print(f"  ✅ 屏蔽词数: {len(ub.config.BLOCK_KEYWORDS)}")
    print(f"  ✅ 国家词数: {len(ub.config.COUNTRIES)}")
    print(f"  ✅ 转发黑名单用户: {ub.config.FORWARD_BLACKLIST_USERS}")
except Exception as e:
    print(f"  ❌ 导入失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ========= 测试 2：文本标准化 =========
print("\n[2/5] 测试 normalize_text 标准化...")
cases_norm = [
    ("微信", "微信"),
    ("微 信", "微信"),                       # 去空格
    ("微\u00ad信", "微信"),                  # 去软连字符
    ("微\u200b信", "微信"),                  # 去零宽空格
    ("ｖｘ", "vx"),                          # 全角转半角 + 小写
    ("VX", "vx"),                            # 小写
    ("  Hello  World  ", "helloworld"),      # 去首尾及中间空格 + 小写
    ("", ""),                                # 空串
    (None, ""),                              # None
]
ok = True
for inp, expected in cases_norm:
    result = ub.normalize_text(inp)
    status = "✅" if result == expected else "❌"
    if result != expected:
        ok = False
    disp = (inp or "None").replace("\u00ad", "<SHY>").replace("\u200b", "<ZWSP>")
    print(f"  {status} {disp!r:<25} -> {result!r:<15} (预期 {expected!r})")
print("  结果:", "通过 ✅" if ok else "失败 ❌")

# ========= 测试 3：屏蔽词/白名单/目标 过滤 =========
print("\n[3/5] 测试过滤函数...")

# 屏蔽词：用真实配置里的词
cases_block = [
    ("我用微信支付", True),                  # "微信" 在黑名单
    ("我 用 微 信 支付", True),              # 插空格也应屏蔽
    ("我用ｖｘ支付", True),                  # 全角 vx
    ("今天天气不错", False),
    ("京东淘宝", True),                      # 多个屏蔽词
    ("普通聊天内容", False),
]
print("  -- is_block 屏蔽词 --")
ok_block = True
for text, expected in cases_block:
    result = ub.is_block(text)
    status = "✅" if result == expected else "❌"
    if result != expected:
        ok_block = False
    print(f"  {status} {text!r:<20} -> {result} (预期 {expected})")

# 白名单
cases_white = [
    ("请问担保关闭了吗", True),              # "担保关闭" 白名单
    ("入金金额是100", True),                 # "入金金额" 白名单
    ("普通消息", False),
]
print("  -- is_white 白名单 --")
ok_white = True
for text, expected in cases_white:
    result = ub.is_white(text)
    status = "✅" if result == expected else "❌"
    if result != expected:
        ok_white = False
    print(f"  {status} {text!r:<25} -> {result} (预期 {expected})")

# 目标词
cases_target = [
    ("需要换汇", True),                      # filter_keywords
    ("美国渠道", True),                      # countries + filter
    ("今天吃什么", False),
    ("支付交流群", True),                    # filter_regexes: 支付.*群
]
print("  -- is_target 目标词 --")
ok_target = True
for text, expected in cases_target:
    result = ub.is_target(text)
    status = "✅" if result == expected else "❌"
    if result != expected:
        ok_target = False
    print(f"  {status} {text!r:<25} -> {result} (预期 {expected})")

# 广告词
cases_ad = [
    ("扫码加群", True),                      # ad_keywords
    ("访问 https://example.com", True),      # ad_patterns
    ("普通内容", False),
]
print("  -- is_ad 广告 --")
ok_ad = True
for text, expected in cases_ad:
    result = ub.is_ad(text)
    status = "✅" if result == expected else "❌"
    if result != expected:
        ok_ad = False
    print(f"  {status} {text!r:<30} -> {result} (预期 {expected})")

# ========= 测试 4：防抖 & 警示管理器 =========
print("\n[4/5] 测试防抖 & 警示管理器...")

# 防抖：相同文本短时间内重复
ok_debounce = True
ub.debounce_manager.cache.clear()
ub.debounce_manager.hits = 0
ub.debounce_manager.misses = 0
r1 = ub.debounce_manager.is_duplicate("test message")
r2 = ub.debounce_manager.is_duplicate("test message")  # 应该命中
r3 = ub.debounce_manager.is_duplicate("another message")
if r1 == False and r2 == True and r3 == False:
    print(f"  ✅ 防抖逻辑: 首次={r1}, 重复={r2}, 不同={r3}")
    print(f"  ✅ 防抖统计: {ub.debounce_manager.get_stats()}")
else:
    print(f"  ❌ 防抖逻辑异常: {r1}, {r2}, {r3}")
    ok_debounce = False

# 警示：冷却期内不重复
ok_alert = True
ub.alert_manager.alerted_groups.clear()
# 模拟触发
triggered = ub.alert_manager.should_alert(
    group_id=-100123,
    group_name="测试群",
    message_text="已暂停作业",
    check_group_name=False
)
ub.alert_manager.record_alert(-100123)
# 冷却期内再次触发，应该返回 False
triggered_again = ub.alert_manager.should_alert(
    group_id=-100123,
    group_name="测试群",
    message_text="已暂停作业",
    check_group_name=False
)
if triggered == True and triggered_again == False:
    print(f"  ✅ 警示冷却: 首次={triggered}, 冷却期内={triggered_again}")
else:
    print(f"  ❌ 警示冷却异常: 首次={triggered}, 冷却期内={triggered_again}")
    ok_alert = False

# 群名扫描触发
ub.alert_manager.alerted_groups.clear()
triggered_by_name = ub.alert_manager.should_alert(
    group_id=-100456,
    group_name="XX暂停作业XX",
    message_text="",
    check_group_name=True
)
if triggered_by_name == True:
    print(f"  ✅ 群名扫描触发: {triggered_by_name}")
else:
    print(f"  ❌ 群名扫描未触发: {triggered_by_name}")
    ok_alert = False

# ========= 测试 5：命令正则匹配 =========
print("\n[5/5] 测试命令正则匹配...")

# 列出所有注册的命令及其正则
command_patterns = {
    r'^/mark_id (\d+) (.+)': ["/mark_id 12345 可疑用户", "/mark_id abc x"],
    r'^/unmark_id (\d+)': ["/unmark_id 12345", "/unmark_id abc"],
    r'^/scan$': ["/scan", "/scan extra"],
    r'^/stats$': ["/stats", "/stats x"],
    r'^/reload$': ["/reload"],
    r'^/update$': ["/update"],
    r'^/alert_stats$': ["/alert_stats"],
    r'^/alert_group (.+)': ["/alert_group 测试群", "/alert_group"],
    r'^/sleep$': ["/sleep"],
    r'^/ping$': ["/ping"],
    r'^/status$': ["/status"],
    r'^/add_mention (\w+)': ["/add_mention alice", "/add_mention alice bob"],
    r'^/remove_mention (\w+)': ["/remove_mention alice"],
    r'^/list_mention$': ["/list_mention"],
    r'^/add_blacklist (\w+)$': ["/add_blacklist spammer", "/add_blacklist a b"],
    r'^/remove_blacklist (\w+)$': ["/remove_blacklist spammer"],
    r'^/list_blacklist$': ["/list_blacklist"],
    r'^/help$': ["/help"],
}

ok_cmd = True
for pattern, test_inputs in command_patterns.items():
    compiled = re.compile(pattern)
    for inp in test_inputs:
        m = compiled.match(inp)
        # 第一个用例应当匹配，第二个（如果有）应当不匹配
        should_match = (test_inputs.index(inp) == 0)
        if should_match:
            if m:
                print(f"  ✅ {pattern:<35} 匹配 {inp!r}")
            else:
                print(f"  ❌ {pattern:<35} 未匹配 {inp!r}")
                ok_cmd = False
        else:
            # 第二个用例用于检查边界，匹配与否不强制报错，只提示
            if m:
                print(f"  ⚠️ {pattern:<35} 边界匹配 {inp!r}")

# ========= 测试 6：safe_markdown =========
print("\n[6/6] 测试 safe_markdown...")
cases_md = [
    ("hello[world]", "hello【world】"),
    ("a(b)c", "a（b）c"),
    ("code`block`", "codeblock"),
    ("", ""),
    (None, ""),
]
ok_md = True
for inp, expected in cases_md:
    result = ub.safe_markdown(inp)
    status = "✅" if result == expected else "❌"
    if result != expected:
        ok_md = False
    print(f"  {status} {inp!r:<18} -> {result!r}")

# ========= 测试 7：休眠时间判断 =========
print("\n[7/7] 测试休眠时间判断（仅验证函数不抛异常）...")
try:
    s = ub.is_sleep_time()
    r = ub.get_sleep_remaining()
    print(f"  ✅ is_sleep_time() = {s}")
    print(f"  ✅ get_sleep_remaining() = {r} 分钟")
except Exception as e:
    print(f"  ❌ 休眠函数异常: {e}")

# ========= 总结 =========
print("\n" + "=" * 60)
results = {
    "标准化 normalize_text": ok,
    "屏蔽词 is_block": ok_block,
    "白名单 is_white": ok_white,
    "目标词 is_target": ok_target,
    "广告 is_ad": ok_ad,
    "防抖 DebounceManager": ok_debounce,
    "警示 AlertManager": ok_alert,
    "命令正则": ok_cmd,
    "safe_markdown": ok_md,
}
all_pass = True
for name, passed in results.items():
    mark = "✅ 通过" if passed else "❌ 失败"
    print(f"  {mark}  {name}")
    if not passed:
        all_pass = False

print("=" * 60)
if all_pass:
    print("🎉 所有测试通过！代码逻辑正常。")
    print("   要完整验证 Telegram 连接，请运行: python3 userbot_forward.py")
else:
    print("⚠️  部分测试未通过，请检查上面的输出。")
    sys.exit(1)
