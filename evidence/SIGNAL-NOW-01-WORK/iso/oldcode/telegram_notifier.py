#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram 实时通知模块 (Telegram Notifier)
=======================================
职责：
  1. 订阅策略信号生成、实盘/模拟盘挂单成交、FT-LOW4H 动态跟踪止损抬升、平仓退出事件；
  2. 异步非阻塞队列发送：网络抖动或超时绝不拖累量化 2 秒轮询主循环；
  3. 支持通过 框架/backend/config/telegram.json 或环境变量 (TG_BOT_TOKEN, TG_CHAT_ID, TG_PROXY) 配置；
  4. 支持标准 Markdown 消息格式排版。
"""

import os
import sys
import json
import time
import queue
import logging
import threading
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any

logger = logging.getLogger("telegram_notifier")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "telegram.json")


class TelegramNotifier:
    """Telegram 异步推送守护器"""

    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self._queue = queue.Queue(maxsize=1000)
        self._worker_thread = None
        self._stop_event = threading.Event()
        self._last_heartbeat = 0
        self.reload_config()
        self._ensure_worker()

    def reload_config(self) -> dict:
        """加载配置（支持环境变量动态覆盖）"""
        cfg = {
            "enabled": False,
            "bot_token": "",
            "chat_id": "",
            "proxy": "",
            "events": {
                "signal": True,
                "entry": True,
                "trailing_stop": True,
                "exit": True,
                "error": True,
                "heartbeat": False
            }
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    file_cfg = json.load(f)
                    cfg.update(file_cfg)
            except Exception as e:
                logger.warning(f"读取 Telegram 配置文件失败: {e}")

        # 环境变量优先级最高
        env_token = os.environ.get("TG_BOT_TOKEN")
        env_chat = os.environ.get("TG_CHAT_ID")
        env_proxy = os.environ.get("TG_PROXY")
        if env_token:
            cfg["bot_token"] = env_token
            cfg["enabled"] = True
        if env_chat:
            cfg["chat_id"] = env_chat
        if env_proxy:
            cfg["proxy"] = env_proxy

        self.cfg = cfg
        return cfg

    @property
    def is_configured(self) -> bool:
        return bool(self.cfg.get("bot_token") and self.cfg.get("chat_id"))

    @property
    def is_enabled(self) -> bool:
        return bool(self.cfg.get("enabled") and self.is_configured)

    def _ensure_worker(self):
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="tg_notifier_worker",
                daemon=True
            )
            self._worker_thread.start()

    def _worker_loop(self):
        """后台异步消费队列发送，绝不阻塞主量化进程"""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            text, parse_mode = item
            try:
                self._send_http(text, parse_mode=parse_mode)
            except Exception as e:
                logger.error(f"Telegram 发送异常: {e}")
            finally:
                self._queue.task_done()

    def _send_http(self, text: str, parse_mode: str = "Markdown") -> bool:
        """底层 HTTP POST 到 Telegram API"""
        if not self.is_configured:
            return False

        token = self.cfg["bot_token"].strip()
        chat_id = str(self.cfg["chat_id"]).strip()
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        data = json.dumps(payload).encode("utf-8")

        # 代理设置
        handlers = []
        proxy = self.cfg.get("proxy", "").strip()
        if proxy:
            handlers.append(urllib.request.ProxyHandler({
                "http": proxy,
                "https": proxy
            }))
        opener = urllib.request.build_opener(*handlers)

        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "WyckoffResonanceBot/1.0"
            }
        )

        try:
            with opener.open(req, timeout=8.0) as resp:
                res_body = resp.read().decode("utf-8")
                res_json = json.loads(res_body)
                return bool(res_json.get("ok", False))
        except Exception as e:
            logger.warning(f"Telegram 发送失败 (HTTP): {e}")
            return False

    def send_raw(self, text: str, parse_mode: str = "Markdown"):
        """投递消息到队列"""
        if not self.is_enabled:
            return
        try:
            self._queue.put_nowait((text, parse_mode))
        except queue.Full:
            logger.warning("Telegram 发送队列已满，丢弃消息")

    # ────────────────────────── 业务格式化消息 ──────────────────────────

    def notify_signal(self, cand_dict: Dict[str, Any]):
        """新信号推送"""
        if not self.is_enabled or not self.cfg.get("events", {}).get("signal", True):
            return

        sym = cand_dict.get("symbol", "UNKNOWN")
        stage = cand_dict.get("stage", "BREAKOUT")
        score = cand_dict.get("score", 0.0)
        price = cand_dict.get("close", cand_dict.get("price", "N/A"))
        direction = cand_dict.get("direction", "LONG")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        msg = (
            f"🔔 *【威科夫共振信号生成】*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• *标的*: `{sym}`\n"
            f"• *阶段*: `{stage}` ({direction})\n"
            f"• *综合共振评分*: `{score:.2f}`\n"
            f"• *当前基准价*: `{price}`\n"
            f"• *状态机*: 已达成共振条件，正移交风控闸门\n"
            f"• *触发时间*: `{now_str}`"
        )
        self.send_raw(msg)

    def notify_entry(self, order_dict: Dict[str, Any]):
        """入场成交通知"""
        if not self.is_enabled or not self.cfg.get("events", {}).get("entry", True):
            return

        sym = order_dict.get("symbol", "UNKNOWN")
        price = order_dict.get("price", order_dict.get("fill_price", 0.0))
        qty = order_dict.get("quantity", 0.0)
        stop = order_dict.get("stop_loss", order_dict.get("stop_price", 0.0))
        notional = order_dict.get("notional", float(price) * float(qty) if price and qty else 0.0)
        arm = order_dict.get("arm", "3a")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        msg = (
            f"🚀 *【模拟实盘订单成交 / 入场开仓】*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• *标的*: `{sym}` (臂 `{arm}`)\n"
            f"• *成交均价*: `{price}`\n"
            f"• *持仓数量*: `{qty}`\n"
            f"• *开仓名义额*: `${notional:.2f}`\n"
            f"• *初始止损价*: `{stop}` (FT-LOW4H -5%)\n"
            f"• *成交时间*: `{now_str}`"
        )
        self.send_raw(msg)

    def notify_trailing_stop(self, sym: str, old_stop: float, new_stop: float, current_price: float = 0.0):
        """FT-LOW4H 动态跟踪止损抬升通知"""
        if not self.is_enabled or not self.cfg.get("events", {}).get("trailing_stop", True):
            return

        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        pct_lift = ((new_stop - old_stop) / old_stop * 100) if old_stop else 0.0

        msg = (
            f"🛡️ *【FT-LOW4H 跟踪止损抬升】*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• *标的*: `{sym}`\n"
            f"• *原保护价*: `{old_stop:.5f}`\n"
            f"• *最新保护价*: `{new_stop:.5f}` (+{pct_lift:.2f}%)\n"
            f"{f'• 当前市价: `{current_price}`' if current_price else ''}\n"
            f"• *机制*: 基于最新 4h 低点保护向上抬升，绝不后退\n"
            f"• *维护时间*: `{now_str}`"
        )
        self.send_raw(msg)

    def notify_exit(self, pos_dict: Dict[str, Any]):
        """平仓出场通知"""
        if not self.is_enabled or not self.cfg.get("events", {}).get("exit", True):
            return

        sym = pos_dict.get("symbol", "UNKNOWN")
        entry_p = pos_dict.get("entry_price", 0.0)
        exit_p = pos_dict.get("exit_price", 0.0)
        pnl = pos_dict.get("pnl", pos_dict.get("realized_pnl", 0.0))
        fee = pos_dict.get("fee", 0.0)
        funding = pos_dict.get("funding", 0.0)
        reason = pos_dict.get("reason", "TRAILING_STOP")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        pnl_symbol = "🟢 盈利" if float(pnl) >= 0 else "🔴 亏损"
        msg = (
            f"🏁 *【持仓结案离场 / 平仓通知】*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• *标的*: `{sym}`\n"
            f"• *离场原因*: `{reason}`\n"
            f"• *入场价格*: `{entry_p}`\n"
            f"• *离场价格*: `{exit_p}`\n"
            f"• *净盈亏*: `{pnl_symbol} ${float(pnl):.2f}`\n"
            f"• *手续费*: `${float(fee):.4f}` | *累计资金费*: `${float(funding):.4f}`\n"
            f"• *结案时间*: `{now_str}`"
        )
        self.send_raw(msg)

    def notify_system(self, title: str, detail: str):
        """系统健康/运行警报"""
        if not self.is_enabled:
            return
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        msg = (
            f"⚠️ *【系统事件提示】*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• *事件*: *{title}*\n"
            f"• *详情*: `{detail}`\n"
            f"• *时间*: `{now_str}`"
        )
        self.send_raw(msg)


# 全局单例
notifier = TelegramNotifier()


def get_notifier() -> TelegramNotifier:
    return notifier


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Telegram 通知测试工具")
    parser.add_argument("--test", action="store_true", help="发送一条测试消息")
    parser.add_argument("--token", help="临时指定 bot token")
    parser.add_argument("--chat", help="临时指定 chat id")
    parser.add_argument("--proxy", help="临时指定 http proxy")
    args = parser.parse_args()

    n = get_notifier()
    if args.token:
        n.cfg["bot_token"] = args.token
        n.cfg["enabled"] = True
    if args.chat:
        n.cfg["chat_id"] = args.chat
    if args.proxy:
        n.cfg["proxy"] = args.proxy

    print(f"当前配置状态: configured={n.is_configured}, enabled={n.is_enabled}")
    print(f"配置文件路径: {n.config_path}")

    if args.test:
        if not n.is_configured:
            print("❌ 无法发送测试消息：未配置 bot_token 或 chat_id！")
            print("👉 请在 框架/backend/config/telegram.json 中填入你的 Telegram 机器人参数，或通过 --token 与 --chat 参数传入。")
            sys.exit(1)
        print("正在向 Telegram 发送测试消息...")
        res = n._send_http("🧪 *【威科夫因子共振量化系统】*\nTelegram 通知推送通道已成功接通！\n当前时间：" + time.strftime("%Y-%m-%d %H:%M:%S"))
        if res:
            print(" 测试消息发送成功！Telegram 已连通。")
        else:
            print("❌ 发送失败，请检查 token、chat_id 或网络代理。")
