"""命令行入口：``python -m linknote_agent <command>``。

子命令：
- ``pair``              创建配对、保存 agent_token、显示 8 位配对码，轮询到手机领取后退出
- ``run``               常驻轮询 Relay 任务并调用本机 BiliNote 生成笔记
- ``status``            查看配对/后端/Relay 状态
- ``install-autostart`` 安装（或 ``--remove`` 移除）Windows 登录自启
- ``unpair``            清除本机配对凭据（可选先撤销手机设备）
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

import httpx

from . import __version__
from .backend import BackendSupervisor
from .bilinote_client import BiliNoteClient
from .bots.runner import BotRunner
from .config import (
    RELAY_NOT_CONFIGURED_MESSAGE,
    AgentConfig,
    ConfigError,
    NotPairedError,
    current_platform,
    default_config_path,
)
from .logging_setup import setup_logging
from .outbox import Outbox
from .relay_client import RelayClient, RelayError
from .relay_ws import build_ws_url
from .runner import AgentRunner
from .ws_runner import RealtimeAgentRunner

PROG = "linknote_agent"
PAIR_EXPIRED_EXIT = 3
NEED_PAIR_EXIT = 2

TIPS_LOCAL_RELAY = "（本地开发可加：--relay http://127.0.0.1:8787）"


# ================================================================ 参数解析


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="LinkNote PC Agent：把手机发来的视频链接交给本机 BiliNote 生成 Markdown 笔记。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用流程：\n"
            "  1) 配对：python -m linknote_agent pair --relay https://your-relay.example.com\n"
            "  2) 启动：python -m linknote_agent run\n"
            "  3) 检查：python -m linknote_agent status\n"
            "\n"
            "配置与日志默认在 ~/.linknote/ 下（agent.json / logs/ / outbox/ / notes/）。"
        ),
    )
    parser.add_argument(
        "--config", metavar="PATH", default=None, help="配置文件路径（默认 ~/.linknote/agent.json）"
    )
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    pair = sub.add_parser("pair", help="创建配对并在终端显示 8 位配对码")
    pair.add_argument(
        "--relay",
        default=None,
        help="你自己的 Relay 地址（未配置时必须指定，例如 https://your-relay.example.com）",
    )
    pair.add_argument("--name", default="我的电脑", help="电脑显示名（1–40 字符，默认「我的电脑」）")
    pair.add_argument(
        "--platform",
        default=current_platform(),
        choices=["windows", "macos", "linux"],
        help="平台标识（默认按当前系统）",
    )
    pair.add_argument(
        "--no-wait", action="store_true", help="只创建配对并保存凭据，不等待手机领取"
    )
    pair.add_argument(
        "--wait-timeout",
        type=float,
        default=0.0,
        help="最多等待多少秒（默认 0 = 等到配对码过期）",
    )
    pair.add_argument(
        "--poll-interval", type=float, default=None, help="轮询配对状态间隔秒数（默认 2）"
    )

    run = sub.add_parser("run", help="常驻运行 Agent（默认 WebSocket 推送；REST 兜底）")
    run.add_argument("--relay", default=None, help="临时覆盖 Relay 地址（不写入配置）")
    run.add_argument(
        "--poll", action="store_true", help="强制使用旧 REST 轮询模式（排障/兼容用）"
    )
    run.add_argument(
        "--once", action="store_true", help="只走一次 REST 领取并处理（调试；等同于旧行为）"
    )

    status = sub.add_parser("status", help="查看配对、Relay、BiliNote 后端状态")
    status.add_argument("--json", action="store_true", help="以 JSON 输出（方便脚本解析）")

    auto = sub.add_parser("install-autostart", help="安装 Windows 登录自启（启动文件夹快捷方式）")
    auto.add_argument("--remove", action="store_true", help="移除已安装的自启快捷方式")
    auto.add_argument(
        "--startup-folder", default=None, help="覆盖启动文件夹路径（一般不需要，测试用）"
    )
    auto.add_argument("--script", default=None, help="覆盖 install_autostart.ps1 路径（测试用）")

    unpair = sub.add_parser("unpair", help="清除本机配对凭据")
    unpair.add_argument("--yes", action="store_true", help="不询问，直接确认")
    unpair.add_argument(
        "--revoke-devices", action="store_true", help="同时撤销该电脑下所有已配对手机"
    )

    return parser


# ================================================================ main


def _ensure_std_streams() -> None:
    """pythonw.exe（开机自启用）没有 stdout/stderr，把 print 安全地接到 devnull。

    否则每条 ``print`` 都会抛 ``AttributeError: 'NoneType' object has no attribute
    'write'``，自启实例会在启动时直接崩掉。
    """
    import os

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    bilinote_transport: httpx.AsyncBaseTransport | None = None,
    run_command: Callable[..., Any] | None = None,
    ws_client: Any | None = None,
) -> int:
    """同步入口（console_scripts / ``python -m`` 都走这里）。

    ``transport`` / ``bilinote_transport`` / ``run_command`` / ``ws_client``
    仅供测试注入，正常使用时不传。
    """
    _ensure_std_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "pair":
            return asyncio.run(cmd_pair(args, transport=transport))
        if args.command == "run":
            return asyncio.run(
                cmd_run(
                    args,
                    relay_transport=transport,
                    bilinote_transport=bilinote_transport,
                    ws_client=ws_client,
                )
            )
        if args.command == "status":
            return asyncio.run(
                cmd_status(args, transport=transport, bilinote_transport=bilinote_transport)
            )
        if args.command == "install-autostart":
            return cmd_install_autostart(args, run_command=run_command)
        if args.command == "unpair":
            return asyncio.run(cmd_unpair(args, transport=transport))
        parser.print_help()
        return 0
    except KeyboardInterrupt:
        print("\n已中断（配置/配对码按上面提示保留）。", file=sys.stderr)
        return 130
    except NotPairedError as exc:
        print(str(exc), file=sys.stderr)
        return NEED_PAIR_EXIT
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except RelayError as exc:
        print(f"Relay 错误：{exc.message}", file=sys.stderr)
        return 1


# ================================================================ pair


async def cmd_pair(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> int:
    cfg_path = Path(args.config) if getattr(args, "config", None) else None
    cfg = AgentConfig.load(cfg_path)
    relay_url = (args.relay or cfg.relay_base_url).strip()
    if not relay_url:
        print(RELAY_NOT_CONFIGURED_MESSAGE, file=sys.stderr)
        return NEED_PAIR_EXIT
    agent_name = (args.name or cfg.agent_name or "我的电脑").strip()[:40] or "我的电脑"
    platform = args.platform or cfg.platform or current_platform()
    if cfg.is_paired:
        print(
            "注意：本机已有配对凭据；继续会创建新的电脑记录并覆盖本地 agent_token，"
            "原先配对的手机需要重新配对。",
            file=sys.stderr,
        )

    client = RelayClient(relay_url, "", transport=transport)
    try:
        print(f"正在创建配对（Relay：{relay_url}）…")
        try:
            data = await client.create_pairing(agent_name, platform, __version__)
        except RelayError as exc:
            print(f"创建配对失败：{exc.message}", file=sys.stderr)
            if "127.0.0.1" in relay_url or "localhost" in relay_url:
                print(
                    "提示：本地 Relay 需要先启动（npm --prefix relay run dev），"
                    "或改用你自己的 Relay 地址 --relay https://your-relay.example.com",
                    file=sys.stderr,
                )
            return 1

        # 契约要求立刻保存 agent_token，关闭窗口后拿不到。
        token = str(data.get("agent_token") or "")
        expires_at = str(data.get("expires_at") or "")
        code = _format_pair_code(str(data.get("code") or ""))
        pairing_id = str(data.get("pairing_id") or "")
        cfg.relay_base_url = relay_url.rstrip("/")
        cfg.agent_token = token
        cfg.agent_id = str(data.get("agent_id") or "")
        cfg.agent_name = agent_name
        cfg.platform = platform
        cfg.version = __version__
        cfg.pairing_id = pairing_id
        cfg.pairing_code = code
        cfg.pairing_expires_at = expires_at
        cfg.paired_at = ""
        cfg.device_id = ""
        cfg.device_name = ""
        cfg.device_platform = ""
        config_path = cfg.save(cfg_path)
        client.set_token(token)

        print()
        print(f"配对码：{code}")
        print(f"有效期至：{_format_local_time(expires_at) or expires_at}（本地时间，约 10 分钟）")
        print(f"凭据已保存：{config_path}")
        print("请在手机 LinkNote App 输入上面的配对码完成配对。")
        if relay_url.rstrip("/") == cfg.relay_base_url and args.relay is None:
            print(TIPS_LOCAL_RELAY)

        if args.no_wait:
            print()
            print("已跳过等待。手机领取后直接运行：python -m linknote_agent run")
            return 0

        print()
        print("等待手机领取配对码…（Ctrl+C 退出后配对码在有效期内仍然有效）")
        poll_interval = (
            float(args.poll_interval)
            if getattr(args, "poll_interval", None)
            else max(0.5, float(cfg.pair_poll_interval_seconds))
        )
        wait_timeout = float(getattr(args, "wait_timeout", 0.0) or 0.0)
        started = asyncio.get_running_loop().time()
        expiry_deadline = _iso_to_timestamp(expires_at)
        last_notice = 0.0

        while True:
            now = asyncio.get_running_loop().time()
            if wait_timeout > 0 and (now - started) >= wait_timeout:
                print(
                    f"等待配对超时（{int(wait_timeout)} 秒）。配对码在 "
                    f"{_format_local_time(expires_at) or expires_at} 前仍可用；"
                    "重新运行 pair 可生成新码。",
                    file=sys.stderr,
                )
                return PAIR_EXPIRED_EXIT
            if expiry_deadline is not None and _epoch_now() > expiry_deadline:
                print("配对码已过期。请重新运行 pair 命令生成新的配对码。", file=sys.stderr)
                return PAIR_EXPIRED_EXIT

            try:
                pairing = await client.get_pairing(pairing_id)
            except RelayError as exc:
                if exc.retriable:
                    print(f"查询配对状态失败（将重试）：{exc.message}", file=sys.stderr)
                else:
                    print(f"查询配对状态失败：{exc.message}", file=sys.stderr)
                    return 1
            else:
                status = str(pairing.get("status") or "").strip().lower()
                if status == "claimed":
                    device = pairing.get("device")
                    if not isinstance(device, dict):
                        device = {}
                    cfg.device_id = str(device.get("id") or "")
                    cfg.device_name = str(device.get("name") or "")
                    cfg.device_platform = str(device.get("platform") or "")
                    cfg.paired_at = str(device.get("paired_at") or "")
                    cfg.save(cfg_path)
                    device_label = cfg.device_name or cfg.device_id or "未知设备"
                    print()
                    print(f"配对成功！手机设备：{device_label}（{cfg.device_platform or '未知平台'}）")
                    print("现在可以启动 Agent：python -m linknote_agent run")
                    return 0
                if status == "expired":
                    print("配对码已过期。请重新运行 pair 命令生成新的配对码。", file=sys.stderr)
                    return PAIR_EXPIRED_EXIT
                if status != "pending":
                    print(f"收到未知配对状态 {status!r}，继续等待…", file=sys.stderr)

            elapsed = asyncio.get_running_loop().time() - started
            if elapsed - last_notice >= 30:
                last_notice = elapsed
                print(f"仍在等待…（已等待 {int(elapsed)} 秒）")
            await sleep(poll_interval)
    finally:
        await client.aclose()


# ================================================================ run


async def cmd_run(
    args: argparse.Namespace,
    *,
    relay_transport: httpx.AsyncBaseTransport | None = None,
    bilinote_transport: httpx.AsyncBaseTransport | None = None,
    stop_event: asyncio.Event | None = None,
    ws_client: Any | None = None,
    bot_runner: Any | None = None,
) -> int:
    cfg_path = Path(args.config) if getattr(args, "config", None) else None
    cfg = AgentConfig.load(cfg_path)
    if getattr(args, "relay", None):
        cfg.relay_base_url = str(args.relay).rstrip("/")

    if not cfg.relay_configured:
        print(RELAY_NOT_CONFIGURED_MESSAGE, file=sys.stderr)
        return NEED_PAIR_EXIT

    if not cfg.is_paired:
        cfg.require_paired()  # 抛 NotPairedError，由 main() 打印并返回 2
        return NEED_PAIR_EXIT

    once = bool(getattr(args, "once", False))
    force_poll = bool(getattr(args, "poll", False))
    use_websocket = bool(cfg.use_websocket) and not once and not force_poll

    verbose = bool(getattr(args, "verbose", False) or cfg.verbose)
    log_path = setup_logging(verbose=verbose, log_dir=cfg.resolved_log_dir())
    print("LinkNote PC Agent 启动中…")
    print(f"  配置文件：{cfg_path or default_config_path()}")
    print(f"  Relay：{cfg.relay_base_url}")
    try:
        ws_url = build_ws_url(cfg.relay_base_url, ws_url=cfg.ws_url)
    except Exception:  # noqa: BLE001 - 地址非法时退回 REST
        ws_url = "（地址非法）"
        use_websocket = False
    mode = "WebSocket 推送" if use_websocket else ("REST 单次" if once else "REST 轮询")
    print(f"  传输模式：{mode}" + (f"（{ws_url}）" if use_websocket else ""))
    print(f"  BiliNote 后端：{cfg.backend_base_url}（exe：{cfg.backend_exe}）")
    print(f"  模型：{cfg.provider_id} / {cfg.model_name}（quality={cfg.quality} style={cfg.style}）")
    bot_channels = cfg.bot_channels_enabled
    if bot_channels:
        label = "Telegram" + (f" (@{cfg.telegram_bot_username})" if cfg.telegram_bot_username else "")
        print(f"  Bot 入口：{label}")
    else:
        print("  Bot 入口：未启用")
    if use_websocket:
        print(
            f"  重连退避：{','.join(str(int(d)) for d in cfg.ws_reconnect_delays)} 秒 + "
            f"{int(cfg.ws_jitter_ratio * 100)}% 抖动；心跳 {cfg.ws_ping_interval_seconds:.0f} 秒"
        )
    if log_path:
        print(f"  日志：{log_path}")
    print("按 Ctrl+C 退出。")

    relay = RelayClient(cfg.relay_base_url, cfg.agent_token, transport=relay_transport)
    bilinote = BiliNoteClient(cfg.backend_base_url, transport=bilinote_transport)
    backend = BackendSupervisor(cfg, bilinote=bilinote)
    outbox = Outbox(cfg.resolved_outbox_dir(), notes_dir=cfg.resolved_notes_dir())
    runner: Any = None
    try:
        if once:
            runner = AgentRunner(cfg, relay, bilinote, backend=backend, outbox=outbox)
            try:
                processed = await runner.poll_once()
            except RelayError as exc:
                print(f"Relay 错误：{exc.message}", file=sys.stderr)
                return 1
            print("已处理一个任务。" if processed else "当前没有待处理任务。")
            return 0
        if use_websocket:
            runner = RealtimeAgentRunner(
                cfg,
                relay,
                bilinote,
                backend=backend,
                outbox=outbox,
                ws_client=ws_client,
            )
        else:
            runner = AgentRunner(cfg, relay, bilinote, backend=backend, outbox=outbox)

        stop = stop_event or asyncio.Event()
        bot: Any = None
        bot_task: Optional[asyncio.Task] = None
        if bot_channels:
            bot = bot_runner or BotRunner(cfg, relay, outbox=outbox)
            bot_task = asyncio.create_task(bot.run(stop), name="bot-runner")
            print(f"  Bot Runner 已随 Agent 启动（{'、'.join(bot_channels)}）")
        try:
            await runner.run_forever(stop)
            return 0
        finally:
            stop.set()
            if bot_task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    bot_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await bot_task
            if bot is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await bot.close()
    finally:
        if runner is not None:
            await runner.close()
        else:
            await backend.close()
        await relay.aclose()
        await bilinote.aclose()


# ================================================================ status


async def cmd_status(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    bilinote_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    cfg_path = Path(args.config) if getattr(args, "config", None) else None
    cfg = AgentConfig.load(cfg_path)
    config_path = cfg_path or default_config_path()

    bilinote = BiliNoteClient(cfg.backend_base_url, transport=bilinote_transport)
    backend_alive = False
    try:
        backend_alive = await bilinote.is_alive()
    finally:
        await bilinote.aclose()

    result: dict[str, Any] = {
        "config_path": str(config_path),
        "version": __version__,
        "paired": cfg.is_paired,
        "relay_configured": cfg.relay_configured,
        "relay_base_url": cfg.relay_base_url,
        "agent_id": cfg.agent_id,
        "agent_name": cfg.agent_name,
        "platform": cfg.platform,
        "token": cfg.masked_token,
        "paired_at": cfg.paired_at,
        "device_id": cfg.device_id,
        "device_name": cfg.device_name,
        "device_platform": cfg.device_platform,
        "backend_base_url": cfg.backend_base_url,
        "backend_exe": cfg.backend_exe,
        "backend_alive": backend_alive,
        "provider_id": cfg.provider_id,
        "model_name": cfg.model_name,
        "quality": cfg.quality,
        "style": cfg.style,
        "screenshot": cfg.screenshot,
        "insert_video_links": cfg.insert_video_links,
        "log_dir": str(cfg.resolved_log_dir()),
        "outbox_dir": str(cfg.resolved_outbox_dir()),
        "outbox_pending": len(_status_outbox(cfg).pending()),
        "notes_pending_ack": len(_status_outbox(cfg).pending_notes()),
        "transport_mode": (
            "websocket"
            if cfg.relay_configured and cfg.use_websocket
            else ("rest" if cfg.relay_configured else "unconfigured")
        ),
        "ws_url": _safe_ws_url(cfg) if cfg.relay_configured else "",
        "relay": None,
        "relay_error": "",
    }

    if cfg.is_paired and cfg.relay_configured:
        relay = RelayClient(cfg.relay_base_url, cfg.agent_token, transport=transport)
        try:
            result["relay"] = await relay.me()
        except RelayError as exc:
            result["relay_error"] = exc.message
        finally:
            await relay.aclose()

    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("LinkNote PC Agent 状态")
    print(f"  版本：{result['version']}    配置文件：{result['config_path']}")
    if not cfg.relay_configured:
        print("  Relay：未配置")
        print(f"  {RELAY_NOT_CONFIGURED_MESSAGE}")
    if not cfg.is_paired:
        print("  配对：未配对")
        pair_relay = cfg.relay_base_url or "https://your-relay.example.com"
        print(f"  下一步：python -m linknote_agent pair --relay {pair_relay}")
    else:
        print("  配对：已配对")
        if cfg.relay_configured:
            print(f"    Relay：{cfg.relay_base_url}")
        print(f"    Agent：{cfg.agent_name}（{cfg.agent_id or '未知 ID'}）")
        print(f"    Token：{cfg.masked_token}    配对时间：{cfg.paired_at or '未知'}")
        if cfg.device_name or cfg.device_id:
            print(f"    最近配对的手机：{cfg.device_name or cfg.device_id}（{cfg.device_platform}）")

    backend_state = "运行中" if backend_alive else "未运行（run 时会尝试自动拉起）"
    print(f"  BiliNote 后端：{cfg.backend_base_url} — {backend_state}")
    print(f"    程序路径：{cfg.backend_exe}")
    if result["transport_mode"] == "unconfigured":
        print("  传输模式：未配置（配置 Relay 后由它决定）")
    else:
        transport_label = (
            "WebSocket 推送" if result["transport_mode"] == "websocket" else "REST 轮询"
        )
        print(
            f"  传输模式：{transport_label}"
            + (f"  {result['ws_url']}" if result["transport_mode"] == "websocket" else "")
        )
    print(
        f"  生成参数：{cfg.provider_id} / {cfg.model_name}  quality={cfg.quality} "
        f"style={cfg.style} screenshot={cfg.screenshot} link={cfg.insert_video_links}"
    )
    print(
        f"  本地数据：outbox {result['outbox_pending']} 条；待手机 ack 笔记 "
        f"{result['notes_pending_ack']} 条；日志目录 {result['log_dir']}"
    )

    relay_info = result.get("relay")
    if result.get("relay_error"):
        print(f"  Relay 状态：不可用 — {result['relay_error']}")
    elif isinstance(relay_info, dict):
        agent = relay_info.get("agent") if isinstance(relay_info.get("agent"), dict) else {}
        queue = relay_info.get("queue") if isinstance(relay_info.get("queue"), dict) else {}
        devices = relay_info.get("devices") if isinstance(relay_info.get("devices"), list) else []
        online = "在线" if agent.get("online") else "离线/未心跳"
        print(
            f"  Relay 状态：{online}；队列 queued={queue.get('queued', 0)} "
            f"running={queue.get('running', 0)}；已配对手机 {len(devices)} 台"
        )
        for device in devices:
            if isinstance(device, dict):
                print(
                    f"    - {device.get('name') or device.get('id')} "
                    f"（{device.get('platform') or '未知'}）"
                )
    return 0


# ================================================================ install-autostart


def cmd_install_autostart(
    args: argparse.Namespace,
    *,
    run_command: Callable[..., Any] | None = None,
) -> int:
    if not sys.platform.startswith("win"):
        print("install-autostart 只支持 Windows；其它系统请自行配置 systemd/launchd。", file=sys.stderr)
        return 2

    script = Path(args.script) if getattr(args, "script", None) else (
        Path(__file__).resolve().parent.parent / "packaging" / "install_autostart.ps1"
    )
    if not script.exists():
        print(
            f"找不到自启脚本：{script}\n"
            "请直接用仓库里的 pc-agent/packaging/install_autostart.ps1，"
            "或手动[Win+R]输入 shell:startup 放入快捷方式。",
            file=sys.stderr,
        )
        return 2

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    if getattr(args, "remove", False):
        command.append("-Remove")
    if getattr(args, "startup_folder", None):
        command.extend(["-StartupFolder", str(args.startup_folder)])

    runner = run_command or subprocess.run
    print(("移除" if getattr(args, "remove", False) else "安装") + "登录自启…")
    completed = runner(command)
    return int(getattr(completed, "returncode", 0) or 0)


# ================================================================ unpair


async def cmd_unpair(
    args: argparse.Namespace,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    cfg_path = Path(args.config) if getattr(args, "config", None) else None
    cfg = AgentConfig.load(cfg_path)
    if not cfg.is_paired:
        print("本机尚未配对，无需解除。")
        return 0

    if not getattr(args, "yes", False):
        if sys.stdin is not None and sys.stdin.isatty():
            answer = input("确定要清除本机配对凭据吗？[y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("已取消。")
                return 0
        else:
            print("非交互环境请加 --yes 确认。", file=sys.stderr)
            return NEED_PAIR_EXIT

    relay = RelayClient(cfg.relay_base_url, cfg.agent_token, transport=transport)
    try:
        if getattr(args, "revoke_devices", False):
            try:
                me = await relay.me()
                devices = me.get("devices") if isinstance(me.get("devices"), list) else []
                for device in devices:
                    if not isinstance(device, dict) or not device.get("id"):
                        continue
                    try:
                        await relay.revoke_device(str(device["id"]))
                        print(f"已撤销设备：{device.get('name') or device['id']}")
                    except RelayError as exc:
                        print(
                            f"撤销设备 {device.get('name') or device['id']} 失败：{exc.message}",
                            file=sys.stderr,
                        )
            except RelayError as exc:
                print(f"获取设备列表失败（继续清除本地凭据）：{exc.message}", file=sys.stderr)
    finally:
        await relay.aclose()

    cfg.agent_token = ""
    cfg.agent_id = ""
    cfg.pairing_id = ""
    cfg.pairing_code = ""
    cfg.pairing_expires_at = ""
    cfg.paired_at = ""
    cfg.device_id = ""
    cfg.device_name = ""
    cfg.device_platform = ""
    path = cfg.save(cfg_path)
    print(f"已清除本机配对凭据：{path}")
    re_pair_relay = cfg.relay_base_url or "https://your-relay.example.com"
    print(f"重新配对：python -m linknote_agent pair --relay {re_pair_relay}")
    return 0


# ================================================================ 工具


def _status_outbox(cfg: AgentConfig) -> Outbox:
    return Outbox(cfg.resolved_outbox_dir(), notes_dir=cfg.resolved_notes_dir())


def _safe_ws_url(cfg: AgentConfig) -> str:
    try:
        return build_ws_url(cfg.relay_base_url, ws_url=cfg.ws_url)
    except Exception:  # noqa: BLE001 - status 输出不因地址非法而失败
        return ""


def _format_pair_code(code: str) -> str:
    """规范成契约里的 ``XXXX-XXXX`` 显示形式（大小写/横线不敏感）。"""
    compact = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    if len(compact) == 8:
        return f"{compact[:4]}-{compact[4:]}"
    return str(code or "")


def _parse_iso(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso_to_timestamp(value: str) -> Optional[float]:
    parsed = _parse_iso(value)
    return parsed.timestamp() if parsed else None


def _epoch_now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _format_local_time(value: str) -> str:
    parsed = _parse_iso(value)
    if parsed is None:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")


__all__ = [
    "build_parser",
    "cmd_install_autostart",
    "cmd_pair",
    "cmd_run",
    "cmd_status",
    "cmd_unpair",
    "main",
]
