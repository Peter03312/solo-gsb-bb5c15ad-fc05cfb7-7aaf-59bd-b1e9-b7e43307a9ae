#!/bin/sh
# 调用示例（curl）。前置：服务已在本机启动。
# 约定时间均为 UTC、ISO-8601；区间语义为 [起, 止)。
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"

# --- 1. 首次发布 ---------------------------------------------------------
curl -sS -X POST "$BASE_URL/api/v1/releases" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "body": "初版正文：进入车间必须佩戴护目镜。",
        "valid_start": "2026-01-01T00:00:00Z",
        "valid_end":   "2027-01-01T00:00:00Z"
      }'; echo

# --- 2. 更正（只覆盖 3 月子区间；published_at 由服务端记录，不可回填） ---
curl -sS -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "base_revision": 0,
        "valid_start": "2026-03-01T00:00:00Z",
        "valid_end":   "2026-04-01T00:00:00Z",
        "body": "更正正文：3 月检修期还须穿戴防割手套。"
      }'; echo

# --- 3. 回溯查询：作业时刻在 2 月（更正区间外） -> 初版 -----------------
curl -sS "$BASE_URL/api/v1/effective?work_time=2026-02-15T08:00:00Z"; echo

# --- 4. 回溯查询：作业时刻在 3 月（更正区间内） -> 更正正文 -------------
curl -sS "$BASE_URL/api/v1/effective?work_time=2026-03-15T08:00:00Z"; echo

# --- 5. 知悉时刻早于任何发布 -> 空结果 ----------------------------------
curl -sS "$BASE_URL/api/v1/effective?work_time=2026-03-15T08:00:00Z&known_as_of=2025-06-01T00:00:00Z"; echo

# --- 6a. 顺序请求却与"已发布"更正重叠 -> 409 过期基版，历史不变 ----------
curl -sS -i -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "base_revision": 0,
        "valid_start": "2026-03-20T00:00:00Z",
        "valid_end":   "2026-03-25T00:00:00Z",
        "body": "依据了旧版 r0 的已失效部分，应被拒绝"
      }'; echo

# --- 6b. 同一批次内并发更正相互冲突 -> 409，整批回滚 --------------------
curl -sS -i -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{ "items": [
        {"number": "SOP-CURL-001", "base_revision": 0,
         "valid_start": "2026-05-01T00:00:00Z", "valid_end": "2026-05-20T00:00:00Z",
         "body": "并发更正 A"},
        {"number": "SOP-CURL-001", "base_revision": 0,
         "valid_start": "2026-05-10T00:00:00Z", "valid_end": "2026-05-30T00:00:00Z",
         "body": "并发更正 B（与 A 冲突，整批拒绝）"}
      ]}'; echo

# --- 6c. 回填发布时间：published_at 为额外字段，422 拒绝 -----------------
curl -sS -i -X POST "$BASE_URL/api/v1/releases" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "body": "重复编号且回填发布时间",
        "valid_start": "2026-01-01T00:00:00Z",
        "valid_end":   "2027-01-01T00:00:00Z",
        "published_at": "2000-01-01T00:00:00Z"
      }'; echo

# --- 7. 批量更正：整批原子提交（任一条非法则全部不入库） -----------------
curl -sS -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{ "items": [
        {"number": "SOP-CURL-001", "base_revision": 0,
         "valid_start": "2026-05-01T00:00:00Z", "valid_end": "2026-06-01T00:00:00Z",
         "body": "5 月更正"},
        {"number": "SOP-CURL-001", "base_revision": 0,
         "valid_start": "2026-06-01T00:00:00Z", "valid_end": "2026-07-01T00:00:00Z",
         "body": "6 月更正（与上一条首尾相接，允许）"}
      ]}'; echo

# --- 8. 版本历史（审计视图，含已撤回版本与 withdrawn_at） ----------------
curl -sS "$BASE_URL/api/v1/procedures/SOP-CURL-001/history"; echo

# --- 10. 更正撤回（误发布撤回；withdrawn_at 由服务端记录，不可回填） -----
# 注：第 7 步已用掉 r2（5 月）、r3（6 月），故此处的链式子更正为 r4。

# 10a. 撤回首版 -> 409 revision_not_withdrawable / first_revision
curl -sS -i -X POST "$BASE_URL/api/v1/corrections/withdrawals" \
  -H 'Content-Type: application/json' \
  -d '{"number": "SOP-CURL-001", "revision": 0}'; echo

# 10b. 先给 3 月更正 r1 挂一个子更正（链式 r1 -> r4）
curl -sS -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "base_revision": 1,
        "valid_start": "2026-03-10T00:00:00Z",
        "valid_end":   "2026-03-20T00:00:00Z",
        "body": "再更正：3 月 10-20 日改为全套防割装备。"
      }'; echo

# 10c. r1 已有子更正 r4 -> 409 revision_not_withdrawable / has_children
curl -sS -i -X POST "$BASE_URL/api/v1/corrections/withdrawals" \
  -H 'Content-Type: application/json' \
  -d '{"number": "SOP-CURL-001", "revision": 1}'; echo

# 10d. 批量撤回：先叶子 r4 再 r1，整批原子（服务端记录同一撤回时刻）
curl -sS -X POST "$BASE_URL/api/v1/corrections/withdrawals" \
  -H 'Content-Type: application/json' \
  -d '{ "items": [
        {"number": "SOP-CURL-001", "revision": 4},
        {"number": "SOP-CURL-001", "revision": 1}
      ]}'; echo

# 10e. 重复撤回 r1 -> 409 revision_not_withdrawable / already_withdrawn
curl -sS -i -X POST "$BASE_URL/api/v1/corrections/withdrawals" \
  -H 'Content-Type: application/json' \
  -d '{"number": "SOP-CURL-001", "revision": 1}'; echo

# 10f. 撤回后 3 月子区间恢复父版本 r0 正文
curl -sS "$BASE_URL/api/v1/effective?work_time=2026-03-15T08:00:00Z"; echo

# 10g. 以已撤回的 r1 为基版更正 -> 409 base_version_withdrawn
curl -sS -i -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "base_revision": 1,
        "valid_start": "2026-03-01T00:00:00Z",
        "valid_end":   "2026-03-05T00:00:00Z",
        "body": "基于已撤回版本，应被拒绝"
      }'; echo

# 10h. 回填撤回时刻：withdrawn_at 为额外字段 -> 422 validation_error
curl -sS -i -X POST "$BASE_URL/api/v1/corrections/withdrawals" \
  -H 'Content-Type: application/json' \
  -d '{"number": "SOP-CURL-001", "revision": 1,
       "withdrawn_at": "2000-01-01T00:00:00Z"}'; echo

# 10i. 父版本 r0 在恢复区间上再次更正 -> 新版本号 r5（旧号不复用）
curl -sS -X POST "$BASE_URL/api/v1/corrections" \
  -H 'Content-Type: application/json' \
  -d '{
        "number": "SOP-CURL-001",
        "base_revision": 0,
        "valid_start": "2026-03-01T00:00:00Z",
        "valid_end":   "2026-04-01T00:00:00Z",
        "body": "撤回后重新发布的 3 月更正：穿戴防割手套并双人监护。"
      }'; echo

# 10j. 历史视图：r1/r4 为 withdrawn 并带 withdrawn_at，r0/r2/r3/r5 为 active
curl -sS "$BASE_URL/api/v1/procedures/SOP-CURL-001/history"; echo

# --- 9a. 两知悉时刻对比：前侧取发布之前，作业区间覆盖全年 ---------------
#       撤回并重发后，3 月子区间两侧为 null -> r5；5/6 月为 null -> r2/r3
curl -sS --get "$BASE_URL/api/v1/procedures/SOP-CURL-001/diff" \
  --data-urlencode 'known_before=2025-01-01T00:00:00Z' \
  --data-urlencode 'known_after=2030-01-01T00:00:00Z' \
  --data-urlencode 'work_start=2026-01-01T00:00:00Z' \
  --data-urlencode 'work_end=2027-01-01T00:00:00Z'; echo

# --- 9b. 收窄作业区间到 2-5 月：null->r0、null->r5（3月）、null->r2 段 ----
curl -sS --get "$BASE_URL/api/v1/procedures/SOP-CURL-001/diff" \
  --data-urlencode 'known_before=2025-01-01T00:00:00Z' \
  --data-urlencode 'known_after=2030-01-01T00:00:00Z' \
  --data-urlencode 'work_start=2026-02-01T00:00:00Z' \
  --data-urlencode 'work_end=2026-05-01T00:00:00Z'; echo

# --- 9c. 知悉顺序颠倒 -> 422 invalid_known_order（不写历史） -------------
curl -sS -i --get "$BASE_URL/api/v1/procedures/SOP-CURL-001/diff" \
  --data-urlencode 'known_before=2030-01-01T00:00:00Z' \
  --data-urlencode 'known_after=2025-01-01T00:00:00Z' \
  --data-urlencode 'work_start=2026-01-01T00:00:00Z' \
  --data-urlencode 'work_end=2027-01-01T00:00:00Z'; echo

# --- 9d. 空作业区间（起=止） -> 422 invalid_interval ---------------------
curl -sS -i --get "$BASE_URL/api/v1/procedures/SOP-CURL-001/diff" \
  --data-urlencode 'known_before=2025-01-01T00:00:00Z' \
  --data-urlencode 'known_after=2030-01-01T00:00:00Z' \
  --data-urlencode 'work_start=2026-03-01T00:00:00Z' \
  --data-urlencode 'work_end=2026-03-01T00:00:00Z'; echo
