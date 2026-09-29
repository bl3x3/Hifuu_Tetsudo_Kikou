"""Analyze room snapshots using user-supplied calendar-time anchors.

Example (PowerShell):
  python tools/analyze_runtime.py --date 2026-01-01 --start 09:30 --end 16:00 `
    --anchor ABCDE=12:00 --anchor FGHIJ=13:00

Only reads runtime. Outputs an allowlisted JSON summary and Markdown report.
created is monotonic milliseconds: calibration assumes a shared clock origin.
The anchor envelope is a sensitivity check, not a statistical confidence interval.
"""

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))
OUTCOMES = {"hifuu": "秘封胜", "yukari": "紫胜", "aborted": "中止"}


def calendar_time(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ)


def analyze(directory, day, start, end, anchors):
    rooms = {}
    for path in sorted(directory.glob("room-*.json")):
        room = json.loads(path.read_text(encoding="utf-8-sig"))
        code = room["code"].upper()
        if code in rooms:
            raise ValueError(f"重复房间编号：{code}")
        rooms[code] = room
    if not rooms:
        raise ValueError("未找到 room-*.json")
    lower, upper = calendar_time(day, start), calendar_time(day, end)
    if lower >= upper:
        raise ValueError("结束时间必须晚于开始时间")
    offsets, calibration = [], []
    for spec in anchors:
        code, clock = spec.split("=", 1)
        code = code.upper()
        if code not in rooms:
            raise ValueError(f"找不到参照房间：{code}")
        wall = calendar_time(day, clock)
        offset = wall.timestamp() - rooms[code]["created"] / 1000
        offsets.append(offset)
        calibration.append(
            {
                "room": code,
                "supplied_time": wall.isoformat(),
                "clock_origin": datetime.fromtimestamp(offset, TZ).isoformat(),
            }
        )
    central_offset = median(offsets)
    successors = {}
    for room in rooms.values():
        if room.get("previousRoom"):
            successors.setdefault(room["previousRoom"], []).append(room)
    records = []
    for code, room in sorted(rooms.items(), key=lambda item: (item[1]["created"], item[0])):
        seconds = room["created"] / 1000
        estimates = [datetime.fromtimestamp(seconds + offset, TZ) for offset in offsets]
        earliest, latest = min(estimates), max(estimates)
        status = (
            "selected"
            if lower <= earliest and latest <= upper
            else "excluded" if latest < lower or earliest > upper else "boundary_uncertain"
        )
        events = room.get("events", [])
        counts = Counter(e["type"] for e in events)
        result = room.get("result") or {}
        tick = result.get("tick", room.get("tick", 0))
        later_rooms = [r for r in successors.get(code, []) if r["created"] > room["created"]]
        next_room = min(later_rooms, key=lambda r: r["created"]) if later_rooms else None
        upper_seconds = (next_room["created"] - room["created"]) / 1000 if next_room else None
        records.append(
            {
                "room": code,
                "selection": status,
                "estimated_created": datetime.fromtimestamp(
                    seconds + central_offset, TZ
                ).isoformat(),
                "anchor_range": [earliest.isoformat(), latest.isoformat()],
                "phase": room["phase"],
                "launched": bool(counts["launch"]),
                "outcome": result.get("outcome"),
                "reason": result.get("reason"),
                "tick": tick,
                "game_hours": tick * 15 / 60,
                "event_counts": dict(counts),
                "previous_room": room.get("previousRoom"),
                "rail_progress_seconds": tick * room.get("tickMs", 2000) / 1000,
                "elapsed_upper_bound_seconds": upper_seconds,
                "upper_bound_next_room": next_room["code"] if next_room else None,
            }
        )
    selected = [r for r in records if r["selection"] == "selected"]
    launched = [r for r in selected if r["launched"]]
    completed = [r for r in launched if r["outcome"] in ("hifuu", "yukari", "timeout")]
    aborted = [r for r in launched if r["outcome"] == "aborted"]
    events = Counter()
    for record in launched:
        events.update(record["event_counts"])
    hours = [r["game_hours"] for r in completed]
    return {
        "window": {"date": day.isoformat(), "start": start, "end": end, "timezone": "UTC+08:00"},
        "method": "按房间 created 筛选；采用各参照点独立换算的时间范围，全部落入窗口才纳入；点估计使用偏移中位数。",
        "assumptions": [
            "参照房间与待筛选房间共用同一单调时钟起点；旧系统启动或外部设备的快照无法据此定年。",
            "参照时间为约数；范围仅反映参照点之间的偏差，不是误差上限。",
            "筛选的是窗口内创建的房间，不是窗口内发生的全部事件。",
            "每个文件仅保留当前一局；同房间重开覆盖的旧局无法恢复。",
            "updated 与文件时间不能用于计算结束时间；没有可靠的现实总耗时。",
        ],
        "calibration": calibration,
        "anchor_spread_seconds": round(max(offsets) - min(offsets), 3),
        "counts": {
            "files": len(records),
            "selected": len(selected),
            "excluded": sum(r["selection"] == "excluded" for r in records),
            "boundary_uncertain": sum(r["selection"] == "boundary_uncertain" for r in records),
            "launched": len(launched),
            "completed": len(completed),
            "aborted_after_launch": len(aborted),
            "not_launched": len(selected) - len(launched),
            "outcomes_completed": dict(Counter(r["outcome"] for r in completed)),
            "abort_reasons_after_launch": dict(Counter(r["reason"] for r in aborted)),
            "abort_reasons_before_launch": dict(
                Counter(
                    r["reason"] for r in selected if not r["launched"] and r["outcome"] == "aborted"
                )
            ),
            "paused_rooms": sum(r["event_counts"].get("technical_pause", 0) > 0 for r in launched),
            "event_counts_launched": dict(events),
        },
        "game_hours_completed": {
            "median": median(hours) if hours else None,
            "min": min(hours) if hours else None,
            "max": max(hours) if hours else None,
        },
        "rooms": records,
    }


def markdown(report):
    w, c = report["window"], report["counts"]
    rows = [r for r in report["rooms"] if r["selection"] == "selected"]
    lines = [
        f"# {w['date']} {w['start']}–{w['end']} 游玩分析（北京时间）",
        "",
        "按用户提供的创建时间校准，以下结果以快照共用单调时钟起点为前提。",
        "",
        f"- 共扫描 {c['files']} 个房间，纳入 {c['selected']} 个，排除 {c['excluded']} 个，边界待定 {c['boundary_uncertain']} 个。",
        f"- 纳入房间中实际发车 {c['launched']} 局：正常结束 {c['completed']} 局，中止 {c['aborted_after_launch']} 局；另有 {c['not_launched']} 个房间未发车。",
    ]
    if c["completed"]:
        wins = "、".join(
            f"{OUTCOMES.get(k, k)} {v} 局（{v / c['completed']:.1%}）"
            for k, v in c["outcomes_completed"].items()
        )
        h = report["game_hours_completed"]
        lines += [
            f"- 正常结束结果：{wins}。样本胜率不足以单独判断阵营平衡。",
            f"- 正常结束的游戏内时长中位数 {h['median']:g} 小时，范围 {h['min']:g}–{h['max']:g} 小时；不是现实耗时。",
        ]
    if c["launched"]:
        lines += [
            f"- 已发车对局完成率 {c['completed'] / c['launched']:.1%}；{c['paused_rooms']} 局出现连接暂停，共 {c['event_counts_launched'].get('technical_pause', 0)} 次。"
        ]
    for label, key in [
        ("发车后中止", "abort_reasons_after_launch"),
        ("发车前中止", "abort_reasons_before_launch"),
    ]:
        for reason, count in c[key].items():
            lines.append(f"- {label} {count} 局：{reason}")
    lines += [
        "",
        "## 筛选依据",
        "",
        report["method"],
        "",
        f"三个参照点的时钟偏移跨度为 {report['anchor_spread_seconds'] / 60:.2f} 分钟。",
        "",
        "| 参照房间 | 用户提供的创建时间 | 换算的时钟起点 |",
        "|---|---|---|",
    ]
    for a in report["calibration"]:
        lines.append(f"| {a['room']} | {a['supplied_time']} | {a['clock_origin']} |")
    lines += [
        "",
        "## 纳入房间",
        "",
        "时间段为各参照点分别换算后的最早／最晚值，不代表实际持续时间。",
        "",
        "| 房间 | 创建时间估算范围 | 是否发车 | 结果 | 游戏内时长（小时） |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        earliest, latest = [
            datetime.fromisoformat(t).strftime("%H:%M:%S") for t in r["anchor_range"]
        ]
        outcome = OUTCOMES.get(r["outcome"], r["outcome"] or r["phase"])
        lines.append(
            f"| {r['room']} | {earliest}–{latest} | {'是' if r['launched'] else '否'} | {outcome} | {r['game_hours']:g} |"
        )
    lines += ["", "## 范围限制", ""] + [f"- {s}" for s in report["assumptions"]]
    launched = [r for r in rows if r["launched"]]
    bounded = [r for r in launched if r["elapsed_upper_bound_seconds"] is not None]
    lines += [
        "",
        "## 现实耗时的可恢复信息",
        "",
        f"{len(launched)} 局已发车对局中，{len(bounded)} 局保留后续房间关系，可计算现实耗时上限。",
        "上限＝后续房间创建时钟－本房间创建时钟。服务要求上一局结束后才能创建后续房间；差值包含开局前等待及局间空档，不能作为实际游玩时长或用于计算平均局长。仍以同一时钟起点、无同房间重开造成关系失效为前提。",
        "纯推进时间＝tick × tickMs，只包含游戏时钟推进，不含讨论、选路及连接暂停；它是配置折算值。",
        "",
        "| 房间 | 纯推进时间 | 现实耗时上限 | 后续房间 |",
        "|---|---|---|---|",
    ]

    def duration(seconds):
        if seconds is None:
            return "无法确定"
        return f"{seconds // 60:.0f}分{seconds % 60:.1f}秒"

    for r in launched:
        lines.append(
            f"| {r['room']} | {duration(r['rail_progress_seconds'])} | {duration(r['elapsed_upper_bound_seconds'])} | {r['upper_bound_next_room'] or '—'} |"
        )
    lines += [
        "",
        "排除房间："
        + "、".join(r["room"] for r in report["rooms"] if r["selection"] == "excluded"),
        "",
    ]
    uncertain = [r["room"] for r in report["rooms"] if r["selection"] == "boundary_uncertain"]
    if uncertain:
        lines += ["边界待定房间（不计入统计）：" + "、".join(uncertain), ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--start", default="09:30")
    parser.add_argument("--end", default="16:00")
    parser.add_argument("--anchor", action="append", required=True, metavar="ROOM=HH:MM")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis")
    args = parser.parse_args()
    runtime, output = args.runtime.resolve(), args.output.resolve()
    if output == runtime or runtime in output.parents:
        parser.error("输出目录不能位于 runtime 内")
    try:
        report = analyze(runtime, args.date, args.start, args.end, args.anchor)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    output.mkdir(parents=True, exist_ok=True)
    stem = f"runtime-{args.date}-{args.start.replace(':', '')}-{args.end.replace(':', '')}"
    (output / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / f"{stem}.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(f"Report: {output / (stem + '.md')}")


if __name__ == "__main__":
    main()
