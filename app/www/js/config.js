/**
 * 链记 LinkNote — 全局常量
 * 纯静态模块，不依赖任何第三方库。改品牌名只需改这里 + manifest + HTML title。
 */

export const APP_NAME = '链记';
export const APP_NAME_EN = 'LinkNote';
export const APP_TAGLINE = '手机发链接，电脑做笔记';
export const APP_VERSION = '0.3.0';

/** 接口前缀（docs/api-contract.md 约定：所有路径以 /api/v1 开头） */
export const API_PREFIX = '/api/v1';

/** 原生 App（Capacitor 壳）没有同源 Relay 时的默认地址 */
export const NATIVE_DEFAULT_RELAY = 'https://relay.xykong.xyz';

/** localStorage 命名空间 */
export const STORAGE_PREFIX = 'linknote.';

/** 离线队列 / 列表分页 */
export const QUEUE_RETRY_MS = 15000;
export const LIST_LIMIT = 20;

/** 实时连接参数（docs/realtime-protocol.md §3 / §5） */
export const REALTIME_HEARTBEAT_MS = 25000;
export const REALTIME_SILENCE_MS = 70000;
export const REALTIME_BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
export const REALTIME_TICKET_TIMEOUT_MS = 12000;
export const REALTIME_NOTE_TIMEOUT_MS = 12000;
export const REALTIME_LEGACY_PROBE_MS = 60000;

/** 契约限制 */
export const MAX_URL_LENGTH = 2048;
export const CODE_LENGTH = 8;
export const CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
export const DEFAULT_DEVICE_NAME = '我的手机';

/** App 侧限制（超出后不再缓存，避免撑爆本地存储） */
export const MAX_SHARE_TEXT = 4000;
export const MAX_CACHED_TASKS = 40;
/** v0.3：最多 30 篇笔记 / 6MB 字节预算（固定/收藏的笔记不参与淘汰） */
export const MAX_CACHED_NOTES = 30;
export const NOTE_CACHE_BUDGET_BYTES = 6 * 1024 * 1024;
/** localStorage 兜底时的保守预算（localStorage 约 5MB 且与其它状态共用） */
export const NOTE_LEGACY_BUDGET_BYTES = 2.5 * 1024 * 1024;

/** 契约 §8：任务阶段（顺序即时间线顺序） */
export const STAGES = [
  { key: 'pending', label: '排队中', hint: '等待电脑领取任务' },
  { key: 'parsing', label: '解析链接', hint: '识别视频信息' },
  { key: 'downloading', label: '下载音视频', hint: '在电脑上下载视频' },
  { key: 'transcribing', label: '语音转写', hint: 'Whisper 识别字幕' },
  { key: 'summarizing', label: 'AI 总结', hint: '使用你自己的模型' },
  { key: 'formatting', label: '整理格式', hint: '排版为 Markdown' },
  { key: 'saving', label: '保存笔记', hint: '写入生成结果' },
  { key: 'done', label: '已完成', hint: '可以阅读与导出了' },
];

export const STAGE_LABELS = {
  pending: '排队中',
  parsing: '正在解析链接',
  downloading: '正在下载音视频',
  transcribing: '正在语音转写',
  summarizing: 'AI 正在总结',
  formatting: '正在整理格式',
  saving: '正在保存',
  done: '已完成',
  failed: '失败',
};

export const STAGE_HINTS = Object.fromEntries(STAGES.map((stage) => [stage.key, stage.hint]));

/** v0.3 任务页三态摘要（等待电脑 → 获取内容 → 生成笔记） */
export const TASK_PHASES = [
  { key: 'waiting', label: '等待电脑', hint: '电脑领取任务后开始' },
  { key: 'fetching', label: '获取内容', hint: '下载视频并转写字幕' },
  { key: 'generating', label: '生成笔记', hint: 'AI 总结与排版' },
];

export const STAGE_PHASE = {
  pending: 'waiting',
  parsing: 'fetching',
  downloading: 'fetching',
  transcribing: 'fetching',
  summarizing: 'generating',
  formatting: 'generating',
  saving: 'generating',
  done: 'generating',
  failed: 'generating',
};

/** readiness（电脑在线 ≠ 可生成）issues 的兜底中文文案 */
export const READINESS_ISSUE_LABELS = {
  model_not_configured: '未配置 AI 模型',
  engine_unreachable: 'BiliNote 引擎不可用',
  ffmpeg_missing: '缺少 ffmpeg',
  agent_paused: 'Agent 已暂停',
};

/** 契约 §8：任务状态 */
export const STATUS_META = {
  queued: { label: '排队中', tone: 'waiting' },
  running: { label: '进行中', tone: 'active' },
  succeeded: { label: '已完成', tone: 'ok' },
  failed: { label: '失败', tone: 'danger' },
};

export const TERMINAL_STATUSES = ['succeeded', 'failed'];

/** 契约 §6.1：只接受这些平台的链接 */
export const PLATFORM_META = {
  bilibili: { label: 'B 站', color: '#fb7299', hosts: ['bilibili.com', 'b23.tv'] },
  youtube: { label: 'YouTube', color: '#ff0033', hosts: ['youtube.com', 'youtu.be'] },
  douyin: { label: '抖音', color: '#1f1f1f', hosts: ['douyin.com', 'iesdouyin.com'] },
  kuaishou: { label: '快手', color: '#ff4906', hosts: ['kuaishou.com'] },
};

export const SUPPORTED_PLATFORMS_TEXT = 'B 站 / YouTube / 抖音 / 快手';

export const STORAGE_KEYS = {
  settings: `${STORAGE_PREFIX}settings.v1`,
  auth: `${STORAGE_PREFIX}auth.v1`,
  ui: `${STORAGE_PREFIX}ui.v1`,
  draft: `${STORAGE_PREFIX}draft.v1`,
  afterPair: `${STORAGE_PREFIX}after-pair.v1`,
};

