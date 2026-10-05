#!/usr/bin/env python3
"""调用示例：仅依赖标准库，直接运行即可走一遍完整业务场景。

前置：服务已在本机启动（docker compose up --build 或 uvicorn app.main:app）。
用法：python examples/python_client.py [BASE_URL]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def show(title: str, status: int, payload: dict) -> None:
    print(f"\n=== {title} -> HTTP {status} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    number = "SOP-DEMO-001"

    # 1) 首次发布：全年适用的初版正文
    status, res = call("POST", "/api/v1/releases", {
        "number": number,
        "body": "初版正文：进入车间必须佩戴护目镜。",
        "valid_start": "2026-01-01T00:00:00Z",
        "valid_end": "2027-01-01T00:00:00Z",
    })
    show("1. 首次发布", status, res)

    # 2) 更正：仅覆盖 3 月这一子区间；发布时间由服务端记录
    status, res = call("POST", "/api/v1/corrections", {
        "number": number,
        "base_revision": 0,
        "valid_start": "2026-03-01T00:00:00Z",
        "valid_end": "2026-04-01T00:00:00Z",
        "body": "更正正文：3 月检修期还须穿戴防割手套。",
    })
    show("2. 提交更正（子区间）", status, res)
    published_r1 = res["items"][0]["published_at"]

    # 3) 查询：2 月（更正区间之外）-> 仍是初版
    status, res = call("GET", "/api/v1/effective?work_time=2026-02-15T08:00:00Z")
    show("3. 作业时刻 2026-02-15（更正区间外）", status, res)

    # 4) 查询：3 月（更正区间之内）-> 更正正文
    status, res = call("GET", "/api/v1/effective?work_time=2026-03-15T08:00:00Z")
    show("4. 作业时刻 2026-03-15（更正区间内）", status, res)

    # 5) 回溯：知悉时刻取 2025 年（任何版本都尚未发布）-> 空结果
    status, res = call("GET",
                       "/api/v1/effective?work_time=2026-03-15T08:00:00Z"
                       "&known_as_of=2025-06-01T00:00:00Z")
    show("5. 知悉时刻早于发布 -> 空", status, res)

    # 6a) 顺序请求，却与"已发布"更正区间重叠 -> 409 过期基版，历史不变
    status, res = call("POST", "/api/v1/corrections", {
        "number": number,
        "base_revision": 0,
        "valid_start": "2026-03-20T00:00:00Z",
        "valid_end": "2026-03-25T00:00:00Z",
        "body": "依据了旧版 r0 的已失效部分，必须被拒绝。",
    })
    show("6a. 过期基版（顺序重叠已发布更正，整体拒绝）", status, res)

    # 6b) 同一批次内两条对 r0 的并发更正相互重叠 -> 409，整批回滚
    status, res = call("POST", "/api/v1/corrections", {"items": [
        {"number": number, "base_revision": 0,
         "valid_start": "2026-05-01T00:00:00Z",
         "valid_end": "2026-05-20T00:00:00Z", "body": "并发更正 A"},
        {"number": number, "base_revision": 0,
         "valid_start": "2026-05-10T00:00:00Z",
         "valid_end": "2026-05-30T00:00:00Z", "body": "并发更正 B（与 A 冲突）"},
    ]})
    show("6b. 并发更正冲突（同批重叠，整批回滚）", status, res)

    # 6c) 试图回填发布时间：额外字段被拒绝（published_at 只由服务端记录）
    status, res = call("POST", "/api/v1/releases", {
        "number": number,
        "body": "重复编号且回填发布时间",
        "valid_start": "2026-01-01T00:00:00Z",
        "valid_end": "2027-01-01T00:00:00Z",
        "published_at": "2000-01-01T00:00:00Z",
    })
    show("6c. 回填发布时间/重复编号被拒绝", status, res)

    # 7) 以 r1 为基版做"更正之更正"（链式）
    status, res = call("POST", "/api/v1/corrections", {
        "number": number,
        "base_revision": 1,
        "valid_start": "2026-03-10T00:00:00Z",
        "valid_end": "2026-03-20T00:00:00Z",
        "body": "再更正：3 月 10-20 日改为全套防割装备。",
    })
    show("7. 链式更正 r1 -> r2", status, res)

    # 8) 撤回误发布的更正（见文件下方 _withdrawal_examples）
    _withdrawal_examples(number)

    status, res = call("GET",
                       "/api/v1/procedures/" + number + "/history")
    show("9. 版本历史（含已撤回版本与撤回时刻）", status, res)

    # 10) 两知悉时刻作业区间对比（见文件下方 _diff_examples）
    _diff_examples(number, published_r1)


def _withdrawal_examples(number: str) -> None:
    """撤回示例：资格拒绝 → 撤回 → 双时间维效果 → 恢复后再次更正。"""
    # 8a) 撤回首版 -> 409 revision_not_withdrawable / first_revision
    status, res = call("POST", "/api/v1/corrections/withdrawals",
                       {"number": number, "revision": 0})
    show("8a. 撤回首版被拒绝（first_revision，历史不变）", status, res)

    # 8b) r1 已有子更正 r2（第 7 步），撤回 r1 -> 409 has_children
    status, res = call("POST", "/api/v1/corrections/withdrawals",
                       {"number": number, "revision": 1})
    show("8b. 撤回有子更正的版本被拒绝（has_children）", status, res)

    # 8c) 先撤回叶子 r2，再撤回 r1 —— 整批原子提交
    status, res = call("POST", "/api/v1/corrections/withdrawals",
                       {"items": [{"number": number, "revision": 2},
                                  {"number": number, "revision": 1}]})
    show("8c. 批量撤回 r2、r1（撤回时刻由服务端记录）", status, res)
    r1_withdrawn = res["withdrawn_at"]

    # 8d) 重复撤回 r1 -> 409 already_withdrawn
    status, res = call("POST", "/api/v1/corrections/withdrawals",
                       {"number": number, "revision": 1})
    show("8d. 重复撤回被拒绝（already_withdrawn）", status, res)

    # 8e) 撤回后 3 月子区间恢复父版本 r0 正文
    status, res = call("GET",
                       "/api/v1/effective?work_time=2026-03-15T08:00:00Z")
    show("8e. 撤回后查询 3 月（恢复为父版本 r0 正文）", status, res)

    # 8f) 撤回前知悉时刻的回溯仍按原版本链：取 r1 撤回时刻前 1 微秒查询
    #     3 月 25 日（r2 只覆盖 3/10-3/20），此刻 r1 仍"已发布未撤回"，
    #     看到的还是 r1 更正正文
    import datetime as _dt
    dt = _dt.datetime.strptime(r1_withdrawn, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=_dt.timezone.utc)
    just_before = (dt - _dt.timedelta(microseconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")
    status, res = call("GET",
                       "/api/v1/effective?work_time=2026-03-25T08:00:00Z"
                       "&known_as_of=" + just_before)
    show("8f. 撤回前 1 微秒的回溯（原版本链，仍见 r1 更正正文）", status, res)

    # 8g) 以已撤回的 r1 为基版 -> 409 base_version_withdrawn
    status, res = call("POST", "/api/v1/corrections", {
        "number": number,
        "base_revision": 1,
        "valid_start": "2026-03-01T00:00:00Z",
        "valid_end": "2026-03-05T00:00:00Z",
        "body": "试图基于已撤回版本，必须被拒绝。",
    })
    show("8g. 以已撤回版本为基版被拒绝（base_version_withdrawn）", status, res)

    # 8h) 回填撤回时刻：额外字段 -> 422 validation_error
    status, res = call("POST", "/api/v1/corrections/withdrawals", {
        "number": number, "revision": 1,
        "withdrawn_at": "2000-01-01T00:00:00Z"})
    show("8h. 回填撤回时刻被拒绝（422 validation_error）", status, res)

    # 8i) 父版本在恢复的区间上可再次更正：新版本号 r3（旧号不复用）
    status, res = call("POST", "/api/v1/corrections", {
        "number": number,
        "base_revision": 0,
        "valid_start": "2026-03-01T00:00:00Z",
        "valid_end": "2026-04-01T00:00:00Z",
        "body": "撤回后重新发布的 3 月更正：穿戴防割手套并双人监护。",
    })
    show("8i. 恢复区间上以 r0 为基版再次更正（新版本号 r3）", status, res)
    status, res = call("GET",
                       "/api/v1/effective?work_time=2026-03-15T08:00:00Z")
    show("8j. 再次更正后查询 3 月（当前正文为 r3）", status, res)


def _diff_examples(number: str, published_r1: str) -> None:
    """对比接口示例。

    走到这里时版本状态为：r1、r2 已撤回，r3 是撤回后在 3 月区间重新发布的
    更正（基于 r0）。以"撤回前 / 很久以后"两个知悉时刻对比，3 月段呈现
    r1 → r3；另演示只跨撤回事件时的 r1 → 父版本 r0 片段。
    """
    import urllib.parse
    from datetime import datetime, timedelta, timezone

    def us_iso(canonical: str, delta_microseconds: int) -> str:
        dt = datetime.strptime(canonical, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc)
        return (dt + timedelta(microseconds=delta_microseconds)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")

    # 取 r1 的发布/撤回时刻（均由服务端记录）
    _, hist = call("GET", "/api/v1/procedures/" + number + "/history")
    by_rev = {v["revision"]: v for v in hist["items"]}
    pub1 = by_rev[1]["published_at"]
    wd1 = by_rev[1]["withdrawn_at"]

    # 9a) 前侧卡在 r1 已发布、撤回之前；后侧取很久以后（r1/r2 已撤回、
    #     r3 生效）：全年区间只有 3 月段两侧不同，为 r1 -> r3
    q = urllib.parse.urlencode({
        "known_before": us_iso(pub1, 1),
        "known_after": us_iso(pub1, 10_000_000),
        "work_start": "2026-01-01T00:00:00Z",
        "work_end": "2027-01-01T00:00:00Z",
    })
    status, res = call("GET", f"/api/v1/procedures/{number}/diff?{q}")
    show("9a. 两知悉时刻对比（撤回前 r1 → 撤回并重发后 r3，仅 3 月段）",
         status, res)

    # 9b) 只跨 r1 撤回事件（撤回前 1 微秒 -> 撤回时刻本身）：r1 -> 父版本 r0
    q = urllib.parse.urlencode({
        "known_before": us_iso(wd1, -1),
        "known_after": wd1,
        "work_start": "2026-02-01T00:00:00Z",
        "work_end": "2026-05-01T00:00:00Z",
    })
    status, res = call("GET", f"/api/v1/procedures/{number}/diff?{q}")
    show("9b. 只跨撤回事件（3 月段 r1 → 恢复父版本 r0）", status, res)

    # 9c) 前侧取很早以前（一切版本均未发布）：before=null 逐段列出，
    #     3 月段后侧为撤回后重发的 r3
    q = urllib.parse.urlencode({
        "known_before": "2025-01-01T00:00:00Z",
        "known_after": "2030-01-01T00:00:00Z",
        "work_start": "2026-02-01T00:00:00Z",
        "work_end": "2026-05-01T00:00:00Z",
    })
    status, res = call("GET", f"/api/v1/procedures/{number}/diff?{q}")
    show("9c. 前侧无任何可见版本（before 为 null 也逐段列出）", status, res)

    # 9d) 知悉顺序颠倒 -> 422 invalid_known_order，不写历史
    q = urllib.parse.urlencode({
        "known_before": "2030-01-01T00:00:00Z",
        "known_after": "2025-01-01T00:00:00Z",
        "work_start": "2026-01-01T00:00:00Z",
        "work_end": "2027-01-01T00:00:00Z",
    })
    status, res = call("GET", f"/api/v1/procedures/{number}/diff?{q}")
    show("9d. 知悉顺序颠倒被拒绝", status, res)


if __name__ == "__main__":
    main()
