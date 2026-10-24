"""LinkNote Desktop 视觉规范：浅蓝底 + 白卡片 + 品牌蓝。

集中定义颜色常量与全局 QSS。所有页面/控件都从这里取色，避免散落的魔法值。
"""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------- 颜色
PAPER = "#F4F7FC"           # 纸色背景
PAPER_DEEP = "#EAF0FA"      # 更深一层的纸
SIDEBAR = "#EDF2FB"         # 侧栏
CARD = "#FFFFFF"            # 卡片
CARD_ALT = "#F7FAFF"        # 卡片内的次级块
CARD_HOVER = "#FAFCFF"
INK = "#0F1B30"             # 炭黑主文字
INK_SOFT = "#334155"        # 正文
MUTED = "#64748B"           # 次要文字
LINE = "#DDE7F5"            # 细边框
LINE_STRONG = "#C9D7EA"
WHITE = "#FFFFFF"
ON_CORAL = "#FFFFFF"

CORAL = "#1E4DFD"
CORAL_STRONG = "#173ED6"
CORAL_INK = "#1A3FC4"
CORAL_SOFT = "#E8EDFF"
CORAL_SOFT_STRONG = "#D6E0FF"

OK = "#16A34A"
WARN = "#B45309"
DANGER = "#DC2626"

OK_BG = "#ECFDF3"
OK_BORDER = "#BBF7D0"
WARN_BG = "#FFFBEB"
WARN_BORDER = "#FDE68A"
DANGER_BG = "#FEF2F2"
DANGER_BORDER = "#FECACA"
INFO_BG = "#EFF6FF"
INFO_BORDER = "#BFDBFE"

CODE_BG = "#0F1B30"
CODE_INK = "#E8F0FF"
TOAST_BG = "#0F1B30"
TOAST_INK = "#EAF0FF"

IDLE = "#94A3B8"            # 未运行/未知状态点

FONT_STACK = '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", sans-serif'
MONO_STACK = '"Consolas", "Cascadia Mono", "Courier New", monospace'

STATE_COLORS = {
    "ok": OK,
    "warn": WARN,
    "error": DANGER,
    "idle": IDLE,
    "unknown": MUTED,
    "info": CORAL,
}


def base_font() -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSizeF(10.0)
    return font


APP_QSS = f"""
/* ---------------------------------------------------------------- 全局 */
QWidget {{
    color: {INK};
    font-family: {FONT_STACK};
    font-size: 13px;
}}

QMainWindow, QWidget#Root, QWidget#PageContent {{
    background-color: {PAPER};
}}

QStackedWidget#Pages {{
    background: transparent;
    border: none;
}}

QScrollArea#PageScroll {{
    background: transparent;
    border: none;
}}
QScrollArea#PageScroll > QWidget > QWidget {{
    background: transparent;
}}

QLabel {{
    background: transparent;
}}
QLabel#H1 {{
    font-size: 22px;
    font-weight: bold;
    color: {INK};
}}
QLabel#H2 {{
    font-size: 15px;
    font-weight: bold;
    color: {INK};
}}
QLabel#CardTitle {{
    font-size: 14px;
    font-weight: bold;
    color: {INK};
}}
QLabel#FieldLabel {{
    font-size: 12px;
    font-weight: bold;
    color: {INK_SOFT};
}}
QLabel#Muted {{
    font-size: 12px;
    color: {MUTED};
}}
QLabel#Help {{
    font-size: 11.5px;
    color: {MUTED};
}}
QLabel#Mono {{
    font-family: {MONO_STACK};
    font-size: 11.5px;
    color: {INK_SOFT};
}}
QLabel#MonoMuted {{
    font-family: {MONO_STACK};
    font-size: 11.5px;
    color: {MUTED};
}}
QLabel#PairCode {{
    font-size: 30px;
    font-weight: bold;
    color: {CORAL_INK};
}}
QLabel#Value {{
    font-size: 12.5px;
    color: {INK_SOFT};
}}
QLabel#Body {{
    font-size: 12.5px;
    color: {INK_SOFT};
}}
QLabel#Danger {{
    color: {DANGER};
}}

/* ---------------------------------------------------------------- 卡片 */
QFrame#Card {{
    background-color: {CARD};
    border: 1px solid {LINE};
    border-radius: 12px;
}}
QFrame#SoftCard {{
    background-color: {CARD_ALT};
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QFrame#HoverCard {{
    background-color: {CARD_ALT};
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QFrame#SidebarCard {{
    background-color: {CARD};
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QFrame#Divider {{
    background-color: {LINE};
    border: none;
    min-height: 1px;
    max-height: 1px;
}}
QFrame#QRFrame {{
    background-color: #FFFFFF;
    border: 1px solid {LINE};
    border-radius: 10px;
}}

/* ---------------------------------------------------------------- 按钮 */
QPushButton {{
    background-color: {CARD};
    color: {INK};
    border: 1px solid {LINE_STRONG};
    border-radius: 9px;
    padding: 0 14px;
    min-height: 34px;
    font-size: 12.5px;
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {CARD_HOVER};
    border-color: #B8C7E0;
}}
QPushButton:pressed {{
    background-color: {PAPER_DEEP};
}}
QPushButton:disabled {{
    color: #94A3B8;
    background-color: #F1F5FB;
    border-color: #E2E8F0;
}}
QPushButton#Primary {{
    background-color: {CORAL};
    color: {ON_CORAL};
    border: 1px solid {CORAL_STRONG};
}}
QPushButton#Primary:hover {{
    background-color: #3B6BFF;
}}
QPushButton#Primary:pressed {{
    background-color: {CORAL_STRONG};
}}
QPushButton#Primary:disabled {{
    background-color: #BFD3FF;
    color: #FFFFFF;
    border-color: #BFD3FF;
}}
QPushButton#Danger {{
    background-color: {CARD};
    color: {DANGER};
    border: 1px solid {DANGER_BORDER};
}}
QPushButton#Danger:hover {{
    background-color: {DANGER_BG};
}}
QPushButton#Ghost {{
    background-color: transparent;
    color: {CORAL_INK};
    border: 1px solid transparent;
}}
QPushButton#Ghost:hover {{
    background-color: {CORAL_SOFT};
}}
QPushButton#Link {{
    background: transparent;
    border: none;
    color: {CORAL_INK};
    padding: 0 2px;
    min-height: 20px;
    font-size: 12px;
    text-decoration: underline;
}}
QPushButton#Link:hover {{
    color: {CORAL};
}}

/* ---------------------------------------------------------------- 输入 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {CARD};
    color: {INK};
    border: 1px solid {LINE_STRONG};
    border-radius: 8px;
    padding: 0 10px;
    min-height: 34px;
    font-size: 12.5px;
    selection-background-color: {CORAL};
    selection-color: {ON_CORAL};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {CORAL};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: #94A3B8;
    background-color: #F1F5FB;
    border-color: #E2E8F0;
}}
QLineEdit[readOnly="true"] {{
    background-color: {CARD_ALT};
    color: {INK_SOFT};
}}
QComboBox {{
    padding-right: 24px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {MUTED};
    margin-right: 8px;
}}
QComboBox::down-arrow:disabled {{
    border-top-color: #94A3B8;
}}
QComboBox QAbstractItemView {{
    background-color: {CARD};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 4px;
    outline: 0;
    selection-background-color: {CORAL_SOFT};
    selection-color: {INK};
}}

/* ---------------------------------------------------------------- 进度 / 滚动条 */
QProgressBar {{
    background-color: #EAF0FA;
    border: none;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {CORAL};
    border-radius: 4px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 1px;
}}
QScrollBar::handle:vertical {{
    background: #C7D5EA;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #AFC2DD;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: transparent;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background: #C7D5EA;
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
    background: transparent;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

/* ---------------------------------------------------------------- 日志/等宽块 */
QPlainTextEdit#CodeView {{
    background-color: {CODE_BG};
    color: {CODE_INK};
    border: 1px solid #0F1B30;
    border-radius: 10px;
    padding: 10px 12px;
    font-family: {MONO_STACK};
    font-size: 11.5px;
    selection-background-color: {CORAL};
    selection-color: {WHITE};
}}
QPlainTextEdit#CodeView QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QPlainTextEdit#CodeView QScrollBar::handle:vertical {{
    background: #334155;
    border-radius: 4px;
}}
QPlainTextEdit#CodeView QScrollBar::handle:vertical:hover {{
    background: #475569;
}}
QPlainTextEdit#CodeView QScrollBar::add-line:vertical,
QPlainTextEdit#CodeView QScrollBar::sub-line:vertical {{
    height: 0;
}}
QPlainTextEdit#CodeView QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QPlainTextEdit#CodeView QScrollBar::handle:horizontal {{
    background: #334155;
    border-radius: 4px;
}}

/* ---------------------------------------------------------------- Tab */
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}
QTabWidget::tab-bar {{
    alignment: left;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 6px 14px;
    margin-right: 6px;
    font-size: 12.5px;
    font-weight: bold;
}}
QTabBar::tab:hover {{
    color: {INK_SOFT};
    background: {CARD_ALT};
}}
QTabBar::tab:selected {{
    background: {CARD};
    color: {CORAL_INK};
    border: 1px solid {LINE};
}}

/* ---------------------------------------------------------------- 菜单 / 提示 */
QMenu {{
    background-color: {CARD};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 28px 7px 12px;
    border-radius: 6px;
    font-size: 12.5px;
}}
QMenu::item:selected {{
    background-color: {CORAL_SOFT};
    color: {CORAL_INK};
}}
QMenu::item:disabled {{
    color: #94A3B8;
}}
QMenu::separator {{
    height: 1px;
    background: {LINE};
    margin: 5px 8px;
}}

QToolTip {{
    background-color: {TOAST_BG};
    color: {TOAST_INK};
    border: none;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11.5px;
}}

QMessageBox {{
    background-color: {CARD};
}}
QMessageBox QLabel {{
    color: {INK};
    font-size: 13px;
}}
QMessageBox QPushButton {{
    min-width: 76px;
}}

/* ---------------------------------------------------------------- 自定义控件补充 */
QWidget#Sidebar {{
    background-color: {SIDEBAR};
    border-right: 1px solid {LINE};
}}
QFrame#Toast {{
    background-color: {TOAST_BG};
    border: none;
    border-radius: 10px;
}}
QFrame#Toast QLabel {{
    color: {TOAST_INK};
    font-size: 12.5px;
}}

/* ---------------------------------------------------------------- 首次配置向导 */
QDialog#Onboarding {{
    background-color: {PAPER};
}}
QWidget#WizardHeader {{
    background: transparent;
}}
QWidget#WizardRail {{
    background-color: {SIDEBAR};
    border-right: 1px solid {LINE};
}}
QWidget#WizardFooter {{
    background: transparent;
    border-top: 1px solid {LINE};
}}
QLabel#WizardTitle {{
    font-size: 16px;
    font-weight: bold;
    color: {INK};
}}
QLabel#WizardStepTitle {{
    font-size: 20px;
    font-weight: bold;
    color: {INK};
}}
QLabel#WizardStepSub {{
    font-size: 12.5px;
    color: {MUTED};
}}

/* ---------------------------------------------------------------- 历史笔记 / 当前任务 */
QFrame#StatTile {{
    background-color: {CARD_ALT};
    border: 1px solid {LINE};
    border-radius: 10px;
}}
QLabel#StatValue {{
    font-size: 20px;
    font-weight: bold;
    color: {INK};
}}
QTextBrowser#MarkdownView {{
    background-color: transparent;
    border: none;
    color: {INK};
    font-size: 13px;
    selection-background-color: {CORAL_SOFT};
    selection-color: {INK};
}}
QListWidget#HistoryList {{
    background-color: transparent;
    border: none;
    outline: 0;
}}
QListWidget#HistoryList::item {{
    border: none;
    margin: 0;
    padding: 0;
}}
QListWidget#HistoryList::item:selected {{
    background: transparent;
}}
QListWidget#HistoryList QScrollBar:vertical {{
    background: transparent;
    width: 8px;
}}
QTabWidget#HistoryTabs::pane {{
    border: none;
}}
QScrollArea#WizardScroll {{
    background: transparent;
    border: none;
}}
QScrollArea#WizardScroll > QWidget > QWidget {{
    background: transparent;
}}
"""


def apply_theme(app: QApplication) -> None:
    """应用 Fusion 基础样式 + 全局 QSS + 字体（幂等，可重复调用）。"""
    if app is None:
        return
    app.setStyle("Fusion")
    app.setFont(base_font())
    app.setStyleSheet(APP_QSS)

