"""配置：``~/.linknote/agent.json`` 的读写。

设计要点：
- token 只存在本机；文件用原子写 + 尽量 0600 权限（Windows 上 chmod 支持有限，
  后续可接 DPAPI，见 docs/architecture.md §3.2）。
- 支持环境变量覆盖（``LINKNOTE_`` 前缀），便于自测与 CI：
  例如 ``LINKNOTE_RELAY_BASE_URL=http://127.0.0.1:8787``、
  ``LINKNOTE_AGENT_TOKEN=agt_...``、``LINKNOTE_HOME=D:\\tmp\\linknote``。
- 未知字段原样保留（``_extra``），旧版本 Agent 不会把新版本写下的配置吃掉。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

from . import __version__
from ._jsonio import atomic_write_json, read_json

ENV_PREFIX = "LINKNOTE_"

#: Relay 生产地址（契约里的生产示例）；本地开发用 ``--relay http://127.0.0.1:8787``。
DEFAULT_RELAY_BASE_URL = "https://relay.xykong.xyz"

#: BiliNote 后端默认地址与可执行文件（打包版默认安装位置）。
DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8483/api"
DEFAULT_BACKEND_EXE = r"C:\app\bilinote\BiliNoteBackend.exe"
DEFAULT_BACKEND_WORKDIR = r"C:\app\bilinote"


class ConfigError(RuntimeError):
    """配置文件损坏 / 字段非法。"""


class NotPairedError(RuntimeError):
    """本机还没有完成配对（缺少 agent_token）。"""


def default_home() -> Path:
    """本机数据目录（~/.linknote），可用 ``LINKNOTE_HOME`` 覆盖（测试用）。"""
    override = os.environ.get(ENV_PREFIX + "HOME", "").strip()
    return Path(override) if override else Path.home() / ".linknote"


def default_config_path() -> Path:
    override = os.environ.get(ENV_PREFIX + "CONFIG_PATH", "").strip()
    return Path(override) if override else default_home() / "agent.json"


def default_log_dir() -> Path:
    return default_home() / "logs"


def default_outbox_dir() -> Path:
    return default_home() / "outbox"


def default_notes_dir() -> Path:
    return default_home() / "notes"


@dataclass
class AgentConfig:
    """Agent 全部可配置项。字段名即 agent.json 里的 key。"""

    # ---- 配对身份（pair 命令写入，run 命令读取）----
    relay_base_url: str = DEFAULT_RELAY_BASE_URL
    agent_token: str = ""
    agent_id: str = ""
    agent_name: str = "我的电脑"
    platform: str = "windows"
    version: str = __version__
    pairing_id: str = ""
    pairing_code: str = ""
    pairing_expires_at: str = ""
    paired_at: str = ""
    device_id: str = ""
    device_name: str = ""
    device_platform: str = ""

    # ---- 本机 BiliNote 后端 ----
    backend_base_url: str = DEFAULT_BACKEND_BASE_URL
    backend_exe: str = DEFAULT_BACKEND_EXE
    backend_workdir: str = DEFAULT_BACKEND_WORKDIR
    auto_start_backend: bool = True
    backend_start_timeout: float = 90.0

    # ---- 生成参数（默认值与 BiliNote 前端/wecom_bot 一致）----
    provider_id: str = "deepseek"
    model_name: str = "deepseek-v4-flash"
    quality: str = "fast"  # fast / medium / slow
    style: str = "minimal"  # 笔记风格；显式 null 会被后端 422，必须是字符串
    screenshot: bool = False
    insert_video_links: bool = True

    # ---- 运行时节奏（REST 轮询 / 旧客户端兜底）----
    poll_interval_seconds: float = 4.0  # 空闲时拉取任务间隔（契约建议 3–5 秒）
    fallback_poll_interval_seconds: float = 4.0  # WS 断开时 REST 兜底领取任务的间隔
    task_status_poll_interval_seconds: float = 3.0  # 轮询 BiliNote 任务状态间隔
    progress_min_interval_seconds: float = 8.0  # 同状态进度回传最小间隔（契约 ≤10 秒）
    heartbeat_interval_seconds: float = 30.0  # REST Agent 心跳间隔（契约 30 秒）
    lease_heartbeat_interval_seconds: float = 300.0  # 长任务租约续期间隔（契约 5 分钟）
    task_timeout_seconds: float = 3600.0  # 单任务最长等待，超时判定失败
    pair_poll_interval_seconds: float = 2.0  # pair 命令轮询配对状态间隔
    max_status_errors: int = 20  # 连续查询 BiliNote 状态失败多少次判定后端失联

    # ---- WebSocket 实时推送（docs/realtime-protocol.md / v0.3-protocol-delta.md）----
    use_websocket: bool = True  # run 默认走 WS 推送；false 或 CLI --poll 时退回 REST 轮询
    ws_url: str = ""  # 留空由 relay_base_url 推导；本地联调可显式 ws://127.0.0.1:8798/api/v1/agent/ws
    ws_ping_interval_seconds: float = 25.0  # v0.3：WebSocket 协议层 ping 间隔（不再发应用层 JSON ping）
    ws_ping_timeout_seconds: float = 20.0  # v0.3：协议层 ping 未回 pong 的超时（由 websockets 判定断线）
    ws_dead_timeout_seconds: float = 60.0  # 已废弃：v0.3 起不再按「无消息」判死连接，保留字段兼容旧配置
    ws_hello_timeout_seconds: float = 10.0  # 连接后等待 hello 的超时
    ws_reconnect_delays: list[float] = field(
        default_factory=lambda: [1.0, 2.0, 4.0, 8.0, 15.0, 30.0]
    )  # 断线重连退避基数；可配置 "1,2,4,8,15,30"
    ws_jitter_ratio: float = 0.2  # 每次退避附加 0–20% 抖动
    rest_fallback: bool = True  # WS 断开期间用 REST /agent/tasks/next 兜底
    rest_heartbeat_enabled: bool = True  # WS 模式是否同时发 REST 心跳（兼容旧 REST 在线状态）
    note_retention_days: float = 7.0  # 已生成笔记在 outbox 的保留天数（未 ack 也不无限涨）

    # ---- v0.3 readiness（agent_ready）与 ack persisted 重试 ----
    readiness_health_cache_seconds: float = 30.0  # BiliNote 引擎健康探测缓存（避免每次 ping 都探测）
    readiness_health_timeout_seconds: float = 2.0  # BiliNote /api/health 探测超时
    note_ack_retry_delays_seconds: list[float] = field(
        default_factory=lambda: [30.0, 120.0, 600.0]
    )  # ack persisted=false 时最多重发 task_note 的 3 次延迟（30s / 2min / 10min）

    # ---- 目录（留空 = ~/.linknote/ 下的默认子目录）----
    outbox_dir: str = ""
    notes_dir: str = ""
    log_dir: str = ""

    verbose: bool = False

    # 保留配置文件里本版本不认识的字段，save 时原样写回。
    _extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    _KNOWN: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        type(self)._KNOWN = frozenset(
            f.name for f in fields(self) if f.name != "_extra"
        )

    # ---------------------------------------------------------------- 路径

    @property
    def path(self) -> Path:
        """当前配置来源路径（可能是默认路径，也可能被显式指定）。"""
        return getattr(self, "_path", None) or default_config_path()

    def resolved_outbox_dir(self) -> Path:
        return Path(self.outbox_dir) if self.outbox_dir else default_outbox_dir()

    def resolved_notes_dir(self) -> Path:
        return Path(self.notes_dir) if self.notes_dir else default_notes_dir()

    def resolved_log_dir(self) -> Path:
        return Path(self.log_dir) if self.log_dir else default_log_dir()

    # ---------------------------------------------------------------- 状态

    @property
    def is_paired(self) -> bool:
        return bool(self.agent_token)

    @property
    def masked_token(self) -> str:
        token = self.agent_token or ""
        if not token:
            return "（未配对）"
        if len(token) <= 12:
            return token[:4] + "…"
        return f"{token[:8]}…{token[-4:]}"

    def require_paired(self) -> None:
        if self.is_paired:
            return
        raise NotPairedError(
            "本机还没有配对（~/.linknote/agent.json 里没有 agent_token）。\n"
            "请先运行：\n"
            f'  .venv\\Scripts\\python.exe -m linknote_agent pair --relay {self.relay_base_url}\n'
            "然后用手机 App 输入终端里显示的 8 位配对码。"
        )

    # ---------------------------------------------------------------- 读写

    @classmethod
    def load(cls, path: Path | str | None = None) -> AgentConfig:
        cfg_path = Path(path) if path else default_config_path()
        try:
            raw = read_json(cfg_path, default=None)
        except (OSError, ValueError) as exc:  # JSONDecodeError 是 ValueError 的子类
            raise ConfigError(
                f"配置文件无法解析：{cfg_path}\n{exc}\n"
                "可以删除该文件后重新运行 pair 命令生成。"
            ) from exc
        if raw is None:
            cfg = cls()
            cfg._path = cfg_path  # type: ignore[attr-defined]
            _apply_env_overrides(cfg)
            return cfg
        if not isinstance(raw, dict):
            raise ConfigError(
                f"配置文件格式错误（应为 JSON 对象）：{cfg_path}\n"
                "可以删除该文件后重新运行 pair 命令生成。"
            )

        cfg = cls()
        unknown: dict[str, Any] = {}
        for key, value in raw.items():
            if key in cls._KNOWN:
                try:
                    setattr(cfg, key, _coerce(getattr(cfg, key), value))
                except (TypeError, ValueError) as exc:
                    raise ConfigError(f"配置项 {key} 的值非法：{value!r}（{exc}）") from exc
            else:
                unknown[key] = value
        cfg._extra = unknown
        cfg._path = cfg_path  # type: ignore[attr-defined]
        _apply_env_overrides(cfg)
        return cfg

    def save(self, path: Path | str | None = None) -> Path:
        """原子写配置文件，返回写入路径。"""
        cfg_path = Path(path) if path else self.path
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_extra"}
        data.update(self._extra)
        return atomic_write_json(cfg_path, data, private=True)

    def update(self, **values: Any) -> AgentConfig:
        for key, value in values.items():
            if key not in self._KNOWN:
                self._extra[key] = value
            else:
                setattr(self, key, value)
        return self

    def to_dict(self, *, include_token: bool = True) -> dict[str, Any]:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_extra"}
        if not include_token:
            data["agent_token"] = self.masked_token
        return data

    def copy(self) -> AgentConfig:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_extra"}
        cfg = AgentConfig(**data)
        cfg._extra = dict(self._extra)
        cfg._path = self.path  # type: ignore[attr-defined]
        return cfg


def load_config(path: Path | str | None = None) -> AgentConfig:
    return AgentConfig.load(path)


def save_config(cfg: AgentConfig, path: Path | str | None = None) -> Path:
    return cfg.save(path)


def current_platform() -> str:
    """契约里平台只有 windows / macos / linux 三种。"""
    import sys

    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _coerce(current: Any, raw: Any) -> Any:
    """按字段默认值类型做尽量宽松的转换（用户可能手改 agent.json）。"""
    if isinstance(current, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on", "y")
        return bool(raw)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list) and not isinstance(current, str):
        # 例：ws_reconnect_delays 支持 ["1","2"] 或 "1,2,4,8,15,30"
        if isinstance(raw, str):
            items: list[Any] = [part.strip() for part in raw.split(",") if part.strip()]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            raise TypeError(f"应为列表或逗号分隔字符串，实际 {raw!r}")
        template = current[0] if current else 0.0
        try:
            return [type(template)(item) for item in items]
        except (TypeError, ValueError):
            return items
    if isinstance(current, str):
        return "" if raw is None else str(raw)
    return raw


def _apply_env_overrides(cfg: AgentConfig) -> None:
    """环境变量覆盖：``LINKNOTE_<字段名大写>``，空字符串视为未设置。"""
    for f in fields(cfg):
        if f.name == "_extra":
            continue
        env_key = ENV_PREFIX + f.name.upper()
        value = os.environ.get(env_key)
        if value is None or value == "":
            continue
        try:
            setattr(cfg, f.name, _coerce(getattr(cfg, f.name), value))
        except (TypeError, ValueError):
            # 环境变量写错不值得让整个进程起不来：忽略并继续。
            continue


__all__ = [
    "AgentConfig",
    "ConfigError",
    "NotPairedError",
    "DEFAULT_RELAY_BASE_URL",
    "current_platform",
    "default_config_path",
    "default_home",
    "default_log_dir",
    "default_notes_dir",
    "default_outbox_dir",
    "load_config",
    "save_config",
]
