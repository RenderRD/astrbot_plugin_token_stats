"""Token 账本 —— 查看每天 QQ AI 消耗了多少 token。

数据来自 AstrBot 自己的 provider_stats 表（每次 LLM 调用一行），
所以这里只读数据库，不参与任何请求链路。

命令：
    /token            今天
    /token 昨天
    /token 7          最近 7 天（逐日）
    /token 30         最近 30 天
    /token 明细       今天按小时
    /token 会话       今天按会话
    /token 全部       历史每一天
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_token_stats"
PLUGIN_VERSION = "1.1.0"

# 日界时区偏移（小时），默认北京时间 +8；可由配置 timezone_offset 覆盖
TZ_OFFSET = 8.0

USAGE = (
    "用法：\n"
    "/token            今天\n"
    "/token 昨天\n"
    "/token 7          最近 7 天\n"
    "/token 30         最近 30 天\n"
    "/token 明细       今天按小时\n"
    "/token 会话       今天按会话\n"
    "/token 全部       历史每天"
)


def _tz() -> timezone:
    return timezone(timedelta(hours=TZ_OFFSET))


def _db_path() -> Path:
    """定位 AstrBot 的 data_v4.db。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        p = Path(get_astrbot_data_path()) / "data_v4.db"
        if p.exists():
            return p
    except Exception:
        pass
    # 兜底：容器内的默认数据目录
    for cand in ("/AstrBot/data/data_v4.db", "data/data_v4.db"):
        p = Path(cand)
        if p.exists():
            return p
    return Path("/AstrBot/data/data_v4.db")


def _has_table(db: Path) -> bool:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provider_stats'"
        ).fetchone()
        return bool(row)
    except Exception:
        return False
    finally:
        con.close()


def _query(db: Path, start_utc: str, end_utc: str):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        cur = con.execute(
            "SELECT created_at, umo, conversation_id,"
            " token_input_other, token_input_cached, token_output"
            " FROM provider_stats"
            " WHERE created_at >= ? AND created_at < ?"
            " ORDER BY created_at",
            (start_utc, end_utc),
        )
        return cur.fetchall()
    finally:
        con.close()


def _bounds(day_offset: int):
    """本地某一天 → UTC 起止字符串（数据库里存的是 UTC）。"""
    tz = _tz()
    now = datetime.now(tz)
    start_local = datetime(now.year, now.month, now.day, tzinfo=tz) - timedelta(
        days=day_offset
    )
    end_local = start_local + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        start_local.astimezone(timezone.utc).strftime(fmt),
        end_local.astimezone(timezone.utc).strftime(fmt),
        start_local.strftime("%m-%d"),
    )


def _local_dt(ts) -> datetime:
    """库里是 UTC naive 时间，转成配置的本地时区。"""
    try:
        d = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        return d.replace(tzinfo=timezone.utc).astimezone(_tz())
    except Exception:
        return datetime.now(_tz())


def _num(n: int) -> str:
    return format(int(n or 0), ",")


def _pct(cached: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return "%d%%" % round(100.0 * cached / total)


def _bucket(rows):
    miss = sum(int(r[3] or 0) for r in rows)
    cached = sum(int(r[4] or 0) for r in rows)
    out = sum(int(r[5] or 0) for r in rows)
    return miss, cached, out, len(rows)


def _umo_label(umo: str) -> str:
    parts = (umo or "").split(":")
    if len(parts) >= 3:
        kind = "群" if "GroupMessage" in parts[1] else "私聊"
        return "%s %s" % (kind, parts[2])
    return umo or "未知"


def build_day_report(db: Path, day_offset: int, title_suffix: str = "") -> str:
    start_utc, end_utc, day_label = _bounds(day_offset)
    rows = _query(db, start_utc, end_utc)
    miss, cached, out, calls = _bucket(rows)
    total = miss + cached + out
    head = "📊 Token 账本 · %s%s" % (
        day_label,
        (" " + title_suffix) if title_suffix else "",
    )
    if not calls:
        return "%s\n今天还没有调用记录。" % head
    lines = [
        head,
        "调用 %d 次 · 合计 %s" % (calls, _num(total)),
        "├ 输入合计 %s" % _num(miss + cached),
        "│   ├ 命中 %s（%s）" % (_num(cached), _pct(cached, miss + cached)),
        "│   └ 未命中 %s" % _num(miss),
        "└ 输出 %s" % _num(out),
        "── 本月对照 ──",
    ]
    # 本月合计（当月 1 号 → 下月 1 号）
    tz = _tz()
    now = datetime.now(tz)
    month_start = datetime(now.year, now.month, 1, tzinfo=tz)
    m_start = month_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    m_end = (month_start + timedelta(days=32)).replace(day=1)
    m_end_utc = m_end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    mrows = _query(db, m_start, m_end_utc)
    m_miss, m_cached, m_out, m_calls = _bucket(mrows)
    lines.append(
        "本月 %s · %d 次 · 命中 %s"
        % (_num(m_miss + m_cached + m_out), m_calls, _pct(m_cached, m_miss + m_cached))
    )
    return "\n".join(lines)


def build_range_report(db: Path, days: int) -> str:
    lines = ["📊 Token 账本 · 最近 %d 天" % days]
    tot_miss = tot_cached = tot_out = tot_calls = 0
    for offset in range(days - 1, -1, -1):
        start_utc, end_utc, day_label = _bounds(offset)
        rows = _query(db, start_utc, end_utc)
        if not rows:
            continue
        miss, cached, out, calls = _bucket(rows)
        tot_miss += miss
        tot_cached += cached
        tot_out += out
        tot_calls += calls
        lines.append(
            "%s  %8s  %d次  命中%s"
            % (day_label, _num(miss + cached + out), calls, _pct(cached, miss + cached))
        )
    if tot_calls:
        total = tot_miss + tot_cached + tot_out
        lines.append("────────────")
        lines.append(
            "合计 %s · %d 次 · 命中 %s"
            % (_num(total), tot_calls, _pct(tot_cached, tot_miss + tot_cached))
        )
    else:
        lines.append("这段时间没有调用记录。")
    return "\n".join(lines)


def build_hour_report(db: Path) -> str:
    start_utc, end_utc, day_label = _bounds(0)
    rows = _query(db, start_utc, end_utc)
    if not rows:
        return "📊 今天还没有调用记录。"
    buckets = {}
    for r in rows:
        ts = _local_dt(r[0]).strftime("%H")
        b = buckets.setdefault(ts, [0, 0, 0, 0])
        b[0] += int(r[3] or 0)
        b[1] += int(r[4] or 0)
        b[2] += int(r[5] or 0)
        b[3] += 1
    lines = ["📊 今日按小时 · %s" % day_label]
    for ts in sorted(buckets):
        miss, cached, out, calls = buckets[ts]
        lines.append(
            "%s时  %d次  miss %s / hit %s"
            % (ts, calls, _num(miss), _num(cached))
        )
    return "\n".join(lines)


def build_session_report(db: Path) -> str:
    start_utc, end_utc, day_label = _bounds(0)
    rows = _query(db, start_utc, end_utc)
    if not rows:
        return "📊 今天还没有调用记录。"
    buckets = {}
    for r in rows:
        key = _umo_label(r[1])
        b = buckets.setdefault(key, [0, 0, 0, 0])
        b[0] += int(r[3] or 0)
        b[1] += int(r[4] or 0)
        b[2] += int(r[5] or 0)
        b[3] += 1
    lines = ["📊 今日按会话 · %s" % day_label]
    for key in sorted(buckets, key=lambda k: -(buckets[k][0] + buckets[k][1])):
        miss, cached, out, calls = buckets[key]
        lines.append(
            "%s  %d次  miss %s / hit %s" % (key, calls, _num(miss), _num(cached))
        )
    return "\n".join(lines)


def build_all_report(db: Path) -> str:
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
    try:
        rows = con.execute(
            "SELECT created_at, token_input_other, token_input_cached, token_output"
            " FROM provider_stats ORDER BY created_at"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return "📊 还没有任何记录。"
    buckets = {}
    for created_at, miss, cached, out in rows:
        day = _local_dt(created_at).strftime("%m-%d")
        b = buckets.setdefault(day, [0, 0, 0, 0])
        b[0] += int(miss or 0)
        b[1] += int(cached or 0)
        b[2] += int(out or 0)
        b[3] += 1
    lines = ["📊 历史每天（本地日界）"]
    tot = 0
    for day in sorted(buckets):
        miss, cached, out, calls = buckets[day]
        tot += miss + cached + out
        lines.append(
            "%s  %8s  %d次  命中%s"
            % (day, _num(miss + cached + out), calls, _pct(cached, miss + cached))
        )
    lines.append("────────────")
    lines.append("总计 %s" % _num(tot))
    return "\n".join(lines)


@register(
    PLUGIN_NAME,
    "冬冬",
    "Token 账本：按天/按小时/按会话查看 QQ AI 的 token 消耗与缓存命中率（只读 provider_stats）",
    PLUGIN_VERSION,
)
class TokenStatsPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.conf = config or {}
        allowed = self.conf.get("allowed_users") or []
        self.allowed = {str(x) for x in allowed if str(x).strip()}
        # 时区偏移（小时），支持 8 / -5 / 5.5 这类写法
        global TZ_OFFSET
        try:
            off = float(self.conf.get("timezone_offset", 8))
            TZ_OFFSET = off
        except (TypeError, ValueError):
            TZ_OFFSET = 8.0

    def _db(self) -> Path:
        return _db_path()

    def _denied(self, event: AstrMessageEvent) -> bool:
        if not self.allowed:
            return False
        getter = getattr(event, "get_sender_id", None)
        sender = str(getter() or "") if callable(getter) else ""
        return sender not in self.allowed

    @filter.command("token")
    async def cmd_token(self, event: AstrMessageEvent, arg: str = "") -> None:
        """查看 token 消耗：/token [昨天|7|30|明细|会话|全部]"""
        if self._denied(event):
            yield event.plain_result("这个命令只对指定用户开放。")
            return
        arg = (arg or "").strip()
        db = self._db()
        if not db.exists():
            yield event.plain_result("找不到 AstrBot 数据库：%s" % db)
            return
        if not _has_table(db):
            yield event.plain_result(
                "这个 AstrBot 版本没有 provider_stats 表，统计不到用量。\n"
                "（该表由 AstrBot 核心维护，较新版本才有；建议升级到 4.29 以上）"
            )
            return
        try:
            if not arg:
                text = build_day_report(db, 0)
            elif arg in ("昨天", "yesterday", "1"):
                text = build_day_report(db, 1, "昨天")
            elif arg == "明细":
                text = build_hour_report(db)
            elif arg == "会话":
                text = build_session_report(db)
            elif arg == "全部":
                text = build_all_report(db)
            elif arg.isdigit() and 1 <= int(arg) <= 90:
                text = build_range_report(db, int(arg))
            elif arg in ("帮助", "help", "?", "-h"):
                text = USAGE
            else:
                text = USAGE
        except Exception as e:
            logger.error("[%s] 统计失败: %s" % (PLUGIN_NAME, e))
            yield event.plain_result("统计失败：%s" % e)
            return
        yield event.plain_result(text)

    @filter.command("token帮助")
    async def cmd_help(self, event: AstrMessageEvent) -> None:
        """查看 token 命令用法"""
        yield event.plain_result(USAGE)
