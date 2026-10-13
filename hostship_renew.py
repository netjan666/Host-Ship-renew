#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Host-Ship 纯 API 自动续期脚本 (升级版)
- 无需加载网页，彻底绕过 Cloudflare Turnstile 验证码
- 直接调用 Host-Ship 客户端原生 API 接口: /api/client/servers/{SERVER_ID}/renew
- 支持 10 天周期智能过滤与 Telegram 结果推送
"""

import os
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ---------- 配置区 ----------
SERVER_URL = os.getenv("SERVER_URL", "https://panel.host-ship.com/server/11325742").strip()
SERVER_ID = os.getenv("SERVER_ID", "").strip()
if not SERVER_ID and SERVER_URL:
    SERVER_ID = SERVER_URL.rstrip("/").split("/")[-1]

# 优先读取 API_KEY，如果 workflow 未显式注入，则自动从已注入的 HOSTSHIP_PASSWORD 或 HOSTSHIP_LOGIN 中识别 ptlc_ 凭证
API_KEY = os.getenv("API_KEY", "").strip()
if not API_KEY:
    for candidate in [os.getenv("HOSTSHIP_PASSWORD", "").strip(), os.getenv("HOSTSHIP_LOGIN", "").strip()]:
        if candidate.startswith("ptlc_"):
            API_KEY = candidate
            log("💡 成功从 HOSTSHIP_PASSWORD 中识别出 ptlc_ 凭证！")
            break


TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
MANUAL_RUN = os.getenv("MANUAL_RUN", "false").lower() == "true"

MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
RENEW_INTERVAL = int(os.getenv("RENEW_INTERVAL", "61"))

BJ_TZ = ZoneInfo("Asia/Shanghai")
PANEL_URL = "https://panel.host-ship.com"

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def beijing_now():
    return datetime.now(BJ_TZ)

def tg_notify(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过推送")
        return False
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            log("✅ Telegram 消息推送成功")
            return True
    except Exception as e:
        log(f"❌ Telegram 消息推送异常: {e}")
        return False

def main():
    log("======================================")
    log(" Host-Ship 原生 API 自动续期引擎")
    log(f" 面板地址: {PANEL_URL}")
    log(f" 服务器ID: {SERVER_ID}")
    log(f" 运行模式: {'手动触发' if MANUAL_RUN else '定时自动调度'}")
    log("======================================")

    # 10 天周期调度过滤 (每月 1, 11, 21, 31 号)
    now_bj = beijing_now()
    if not MANUAL_RUN and now_bj.day not in (1, 11, 21, 31):
        log(f"📅 今日为 {now_bj.strftime('%Y-%m-%d')}，非 10 天周期续期日，自动跳过。")
        return 0

    if not API_KEY:
        msg = "❌ 缺失 API_KEY (ptlc_xxxx)，请在 GitHub Secrets 中配置 API_KEY！"
        log(msg)
        tg_notify(f"❌ Host-Ship 续期失败\n\n原因: 缺失 API_KEY (ptlc_xxxx)\n请在 GitHub Secrets 中添加变量 API_KEY！")
        return 1

    renew_url = f"{PANEL_URL}/api/client/servers/{SERVER_ID}/renew"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HostShipAutoRenew/2.0"
    }

    attempt = 0
    success = False
    maxed_out = False
    last_response_text = ""

    while attempt < MAX_ATTEMPTS:
        attempt += 1
        log(f"----- 第 {attempt}/{MAX_ATTEMPTS} 次发起续期 API 请求 -----")
        req = urllib.request.Request(renew_url, headers=headers, method="POST")

        status_code = None
        body = ""

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status_code = resp.status
                body = resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            status_code = e.code
            body = e.read().decode("utf-8", errors="ignore")
        except Exception as e:
            body = str(e)

        log(f"HTTP 响应状态码: {status_code}")
        log(f"响应内容: {body}")
        last_response_text = body

        # 检查是否已达到最大续期天数限制 (例如30天)
        if "You cannot add more than 30 days" in body or "limit" in body.lower():
            log("🎉 已达最大续期上限 (30天)，无需继续增加天数。")
            maxed_out = True
            success = True
            break

        if status_code in (200, 204):
            log(f"✅ 第 {attempt} 次续期成功 (HTTP {status_code})")
            success = True
        else:
            log(f"⚠️ 第 {attempt} 次请求返回非 200/204 状态: {status_code}")

        if attempt < MAX_ATTEMPTS:
            log(f"[*] 等待 {RENEW_INTERVAL} 秒后进行下次请求尝试...")
            time.sleep(RENEW_INTERVAL)

    # 发送 Telegram 结果报告
    time_str = beijing_now().strftime("%Y/%m/%d %H:%M")
    if success:
        if maxed_out:
            tg_text = (
                f"🎉 Host-Ship 续期检查完成\n\n"
                f"🖥️ 服务器：#{SERVER_ID}\n"
                f"📅 状态：已达最大天数上限 (30天)\n"
                f"🕗 时间：{time_str}\n"
                f"⏰ 调度：每 10 天自动检测一次"
            )
        else:
            tg_text = (
                f"🎉 Host-Ship 续期成功！\n\n"
                f"🖥️ 服务器：#{SERVER_ID}\n"
                f"✅ 状态：已成功执行 API 续期\n"
                f"🕗 时间：{time_str}\n"
                f"⏰ 调度：每 10 天自动检测一次"
            )
        tg_notify(tg_text)
        return 0
    else:
        tg_text = (
            f"❌ Host-Ship 续期异常\n\n"
            f"🖥️ 服务器：#{SERVER_ID}\n"
            f"⚠️ 响应：{last_response_text[:200]}\n"
            f"🕗 时间：{time_str}"
        )
        tg_notify(tg_text)
        return 1

if __name__ == "__main__":
    sys.exit(main())
