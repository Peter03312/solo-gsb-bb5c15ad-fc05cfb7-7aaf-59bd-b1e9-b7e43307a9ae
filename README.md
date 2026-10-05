# 安全作业规程历史回溯服务（SOP History Service）

基于 **Python 3.12 + FastAPI + SQLite** 的双时间维（bitemporal）规程版本库，
提供可调用的 HTTP API：

- **首次发布**：录入规程编号、正文及 UTC 适用区间 `[起, 止)`；
- **更正提交**：编号 + 被替代版本 + 该版本当前生效区间内的子区间 + 新正文，
  发布时间由服务端记录、不可回填；
- **回溯查询**：输入**作业时刻**与**知悉时刻**，返回截至知悉时刻已发布、
  且在作业时刻有效的正文与版本；无匹配返回空列表。
- **两知悉时刻对比**：输入规程编号、先后两个带时区的知悉时刻与非空作业区间，
  逐段比较同一规程在两侧知悉时刻下对该作业区间的实际适用内容，返回发生
  变化的最大片段（起止 + 两侧版本号/正文，无适用版本一侧为 `null`）。
- **更正撤回**：发现更正误发布时，值班员凭规程编号 + 更正版本号撤回；撤回
  时刻由服务端记录、不可回填。仅当前仍生效、尚无子更正的更正版本可撤回，
  撤回后该子区间恢复父版本正文并可再次更正；撤回同样是只追加事件，撤回前
  知悉时刻的回溯与区间对比仍按原版本链还原。

更正只新增行、不改写任何旧行（撤回也只是追加一条撤回事件），因此"更正发布前
/撤回前的查询结果"天然保持原样。

---

## 一、快速开始（一条命令）

前置：已安装 Docker 与 Docker Compose v2（`docker compose version` 可用）。

```bash
docker compose up --build
```

该命令依次完成：**安装依赖 → 初始化数据库（幂等建表）→ 启动 HTTP 服务**。
数据库文件位于命名卷 `sop-data` 内（容器路径 `/data/sop.db`），容器重建后数据保留。

服务启动后：

| 用途 | 地址 |
| --- | --- |
| API 根（端点列表） | <http://localhost:8080/> |
| 健康检查 | <http://localhost:8080/health> |
| Swagger 交互文档 | <http://localhost:8080/docs> |
| OpenAPI 描述 | <http://localhost:8080/openapi.json> |

后台运行 / 停止 / 清理：

```bash
docker compose up --build -d      # 后台
docker compose logs -f            # 查看日志
docker compose down               # 停止并移除容器（保留数据卷）
docker compose down -v            # 连同数据卷一起删除（清空历史）
```

### 不用 Docker 的本地运行方式

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
SOP_DB_PATH=./sop.db uvicorn app.main:app --host 0.0.0.0 --port 8080
# 数据库表在服务启动时自动创建，无需额外初始化步骤
```

---

## 二、配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SOP_DB_PATH` | `/data/sop.db` | SQLite 数据库文件路径，启动时自动建表 |
| `SOP_HOST` | `0.0.0.0` | HTTP 监听地址 |
| `SOP_PORT` | `8080` | **容器内**监听端口 |
| `SOP_BUSY_TIMEOUT_MS` | `5000` | SQLite 写锁等待毫秒数 |

修改宿主机映射端口（例如改用 18080）：

```bash
SOP_PORT=18080 docker compose up --build
# 此时访问 http://localhost:18080
```

> 说明：`docker-compose.yml` 中端口映射写作 `"${SOP_PORT:-8080}:8080"`，
> 即该变量改变的是宿主机侧端口；容器内始终监听 8080。

---

## 三、API 说明

所有时间均为 **ISO-8601 且必须带时区**（如 `2026-03-01T00:00:00Z` 或
`2026-03-01T08:00:00+08:00`），服务端统一规范化为 UTC 微秒串：
`YYYY-MM-DDTHH:MM:SS.ffffffZ`。区间一律为**左闭右开 `[起, 止)`**。

### 1. 首次发布 `POST /api/v1/releases`

请求体支持单条对象，或 `{"items": [...]}` 批量提交（整批原子，任一非法全部不入库）。

```json
{
  "number": "SOP-001",
  "body": "初版正文：进入车间必须佩戴护目镜。",
  "valid_start": "2026-01-01T00:00:00Z",
  "valid_end":   "2027-01-01T00:00:00Z"
}
```

- 成功 `201`，返回服务端记录的 `published_at` 与版本号（首版 `revision=0`）。
- 编号已存在 → `409 duplicate_number`。
- `起 >= 止`、缺时区等 → `422 invalid_interval / invalid_time`。
- 请求中出现 `published_at` 等额外字段 → `422 validation_error`（禁止回填）。

### 2. 提交更正 `POST /api/v1/corrections`

```json
{
  "number": "SOP-001",
  "base_revision": 0,
  "valid_start": "2026-03-01T00:00:00Z",
  "valid_end":   "2026-04-01T00:00:00Z",
  "body": "更正正文：3 月检修期还须穿戴防割手套。"
}
```

语义与校验（任一不满足则**整体拒绝、不写任何历史**）：

1. 子区间必须整体落在被替代版本的适用区间内，否则 `422 invalid_interval`；
2. 子区间必须落在被替代版本的**当前仍生效区间**（自身区间减去其已发布子更正）
   内；落在已被覆盖处 → `409 expired_base_version`（过期基版，响应中带
   `remaining_intervals` 提示当前可更正区间；应改以最新正文版本为基版）；
3. 同一批次中对同一被替代版本的多条更正子区间不得相互重叠，
   否则 `409 concurrent_correction_conflict`，整批回滚；
4. 编号不存在 → `404 not_found`；基版版本号不存在 → `404 base_version_not_found`。

更正版本号 `revision` 在该规程内递增；`supersedes_revision` 标明被替代版本，
支持"更正之更正"的版本链。同批条目按顺序处理，可在同一批中先更正 r0 得到 r1、
再以 r1 为基版继续更正。

### 3. 撤回误发布的更正 `POST /api/v1/corrections/withdrawals`

值班员发现更正误发布时，可提交**规程编号 + 更正版本号**撤回。请求体同样支持
单条对象或 `{"items": [...]}` 批量（整批原子，任一非法全部不入库）：

```json
{
  "number": "SOP-001",
  "revision": 1
}
```

成功 `201`，返回服务端记录的撤回时刻与被撤回版本信息（**不接受、也不需要
`withdrawn_at` 入参**，出现即 `422 validation_error`）：

```json
{
  "withdrawn_at": "2026-06-02T09:30:00.123456Z",
  "items": [
    {
      "number": "SOP-001",
      "revision": 1,
      "supersedes_revision": 0,
      "valid_start": "2026-03-01T00:00:00.000000Z",
      "valid_end": "2026-04-01T00:00:00.000000Z",
      "body": "更正正文：3 月检修期还须穿戴防割手套。",
      "withdrawn_at": "2026-06-02T09:30:00.123456Z"
    }
  ]
}
```

语义与校验（任一不满足则**整体拒绝、只回滚本次操作，不改变任何历史**）：

1. **仅当前仍生效、尚无"生效中"子更正的更正版本可撤回**。以下三种一律
   `409 revision_not_withdrawable`，`details.reason` 区分：
   - `first_revision`：首版 `revision=0` 只能被更正、不能撤回；
   - `has_children`：该版本当前还有生效中的直接子更正，`details.child_revisions`
     列出子版本号；**子更正撤回后即不再占位**——误发的更正链可自叶子向上
     逐级撤回（单条按 r2→r1 顺序调用；批量时把子版本条目排在前面，批次按
     条目顺序处理，颠倒会整批拒绝）；
   - `already_withdrawn`：该版本已撤回（不可重复撤回）；
     `details.withdrawn_at` 给出首次撤回时刻；同批次内重复目标也按此拒绝。
2. 编号不存在 → `404 not_found`；版本号不存在 → `404 base_version_not_found`。
3. **只追加事件**：撤回写入独立的撤回事件表（含服务端撤回时刻），原版本行
   永不删除、永不修改。

撤回的生效规则（双时间维）：

- 撤回是一个带服务端时刻的事件：仅当 **`withdrawn_at <= known_as_of`** 时，
  查询/对比才视该更正失效（撤回时刻含等号，与发布时刻一致）；知悉时刻更早时
  撤回事件尚不可见，**撤回前的回溯与区间对比仍按原版本链**，看到的还是更正正文；
- 撤回后，该更正的适用子区间**恢复父版本正文**；父版本可在恢复的区间上再次
  更正，新版本号继续递增（旧版本号不复用、不重用）；
- 不能再以已撤回版本为被替代版本提交更正 → `409 base_version_withdrawn`
  （其区间已恢复父版本正文，应改以父版本为基版）；
- 撤回与并发更正共用同一把写锁、按服务端事件先后**确定性串行处理**：先撤回
  则与之冲突的更正因基版失效被拒，先更正则该版本有了子更正、撤回被拒，
  同一区间任何时刻至多存在一份当前正文；
- 历史接口仍能看到被撤回版本（`status="withdrawn"`，附 `withdrawn_at`），
  其余版本为 `status="active"`、`withdrawn_at=null`。

### 4. 回溯查询 `GET /api/v1/effective`

| 查询参数 | 必填 | 说明 |
| --- | --- | --- |
| `work_time` | 是 | 作业时刻（ISO-8601 带时区） |
| `known_as_of` | 否 | 知悉时刻；缺省取服务端当前时刻 |

```bash
curl 'http://localhost:8080/api/v1/effective?work_time=2026-03-15T08:00:00Z&known_as_of=2026-05-01T00:00:00Z'
```

返回（按规程编号排序）：

```json
{
  "work_time": "2026-03-15T08:00:00.000000Z",
  "known_as_of": "2026-05-01T00:00:00.000000Z",
  "count": 1,
  "items": [
    {
      "number": "SOP-001",
      "revision": 1,
      "body": "更正正文：……",
      "valid_start": "2026-03-01T00:00:00.000000Z",
      "valid_end":   "2026-04-01T00:00:00.000000Z",
      "supersedes_revision": 0,
      "published_at": "2026-02-10T03:12:44.918203Z"
    }
  ]
}
```

- 只返回满足 `published_at <= known_as_of` 且 `valid_start <= work_time < valid_end`
  的版本；同一规程存在版本链时返回最深（最新）的有效版本；
- 更正仅覆盖指定子区间，其余作业时刻仍返回父版本正文；
- 无任何匹配时返回 `200` 与 `{"count": 0, "items": []}`。

### 5. 版本历史（审计视图）`GET /api/v1/procedures/{number}/history`

返回该规程的全部版本（**含已撤回版本，版本行从不删除**），不做知悉时刻裁剪，
供审计/排障使用。编号不存在 → `404`。每个版本除既有字段外另带：

| 字段 | 说明 |
| --- | --- |
| `status` | `active`（当前可作为正文/可作基版）或 `withdrawn`（已被撤回） |
| `withdrawn_at` | 撤回时刻（UTC 微秒串）；未撤回为 `null` |

### 6. 两知悉时刻对比 `GET /api/v1/procedures/{number}/diff`

值班人员核对"同一段作业、先后两次知悉时看到的规程是否一致"。查询参数全部必填、
均为 ISO-8601 **带时区**时间：

| 参数 | 说明 |
| --- | --- |
| `known_before` | 较早的知悉时刻（严格早于 `known_after`） |
| `known_after` | 较晚的知悉时刻 |
| `work_start` | 作业区间起点（含） |
| `work_end` | 作业区间终点（不含） |

```bash
curl -sS --get 'http://localhost:8080/api/v1/procedures/SOP-001/diff' \
  --data-urlencode 'known_before=2026-02-01T00:00:00Z' \
  --data-urlencode 'known_after=2026-06-01T00:00:00Z' \
  --data-urlencode 'work_start=2026-01-01T00:00:00Z' \
  --data-urlencode 'work_end=2027-01-01T00:00:00Z'
```

语义：

- 各侧状态沿**版本链按事件时刻**还原：仅纳入 `published_at <= 知悉时刻`
  的版本，并剔除 `withdrawn_at <= 知悉时刻` 的已撤回更正；作业时刻落在哪些
  版本区间内就沿 `supersedes` 链取最深（最新）者；无任何可见版本覆盖时该侧
  记为 `null`。因此撤回前后对比会出现"更正正文 ↔ 父版本正文"的变化片段；
- 用两侧可见版本的全部区间端点对作业区间 `[work_start, work_end)` 做左闭右开
  切分，片段内两侧版本各自恒定；再**合并首尾相接且两侧版本相同的相邻片段**；
- 只返回两侧版本不同的**最大片段**：逐段给出 `start/end` 与
  `before/after`（各自 `{"revision", "body"}` 或 `null`）；两侧版本相同
  （含两侧均无版本）的片段不返回；
- **是否变化只看版本号**：即使新版本正文与旧版完全相同，只要版本不同就算变化。

响应（沿用上面"3 月更正"的数据；前侧知悉时刻早于一切发布）：

```json
{
  "number": "SOP-001",
  "known_before": "2025-01-01T00:00:00.000000Z",
  "known_after": "2026-06-01T00:00:00.000000Z",
  "work_start": "2026-01-01T00:00:00.000000Z",
  "work_end": "2027-01-01T00:00:00.000000Z",
  "count": 3,
  "segments": [
    {"start": "2026-01-01T00:00:00.000000Z", "end": "2026-03-01T00:00:00.000000Z",
     "before": null, "after": {"revision": 0, "body": "初版正文：……"}},
    {"start": "2026-03-01T00:00:00.000000Z", "end": "2026-04-01T00:00:00.000000Z",
     "before": null, "after": {"revision": 1, "body": "更正正文：……"}},
    {"start": "2026-04-01T00:00:00.000000Z", "end": "2027-01-01T00:00:00.000000Z",
     "before": null, "after": {"revision": 0, "body": "初版正文：……"}}
  ]
}
```

无变化时 `200` 且 `{"count": 0, "segments": []}`。错误均为只读、不写任何历史：

| 情况 | 状态码 | error.code |
| --- | --- | --- |
| 规程编号不存在（或为空） | `404` | `not_found` |
| 任一时刻缺时区 / 不可解析 | `422` | `invalid_time` |
| `work_start >= work_end`（空作业区间） | `422` | `invalid_interval` |
| `known_before >= known_after`（知悉顺序颠倒或相等） | `422` | `invalid_known_order` |
| 缺少必填查询参数 | `422` | `validation_error` |

---

## 四、调用示例

- Python（仅标准库）：`python examples/python_client.py`
  （可带参数指定地址：`python examples/python_client.py http://localhost:8080`）
- curl：`sh examples/curl-examples.sh`（可用 `BASE_URL` 环境变量改地址）

示例覆盖：首次发布 → 子区间更正 → 区间外/内查询 → 知悉时刻早于发布返回空 →
过期基版拒绝 → 同批并发更正冲突整批回滚 → 回填发布时间被拒 → 链式更正 →
**误发布更正撤回（含首版/重复撤回/以撤回版为基版被拒、撤回后父版本可再次
更正、撤回前知悉时刻仍见原更正）** → 历史查看 → 两知悉时刻对比（含撤回前后
片段、`null` 一侧与顺序颠倒报错）。

---

## 五、设计要点

- **双时间维**：每条版本行同时带适用区间（valid time）与发布时间
  （transaction time）。发布时间由服务端单调时钟生成，接口不接受该字段；
  所有写操作只 `INSERT`、从不 `UPDATE/DELETE` 业务行。
- **撤回即事件**：撤回不改动 versions 行，只向 `correction_withdrawals`
  追加一条 `withdrawn_at`（同一单调时钟，不可回填）。可见性 =
  `published_at <= t` 且不存在 `withdrawn_at <= t` 的撤回事件；撤回后子区间
  恢复父版本正文，撤回前知悉时刻的视图按原版本链保持不变。
- **版本树**：首版 `revision=0`；更正以 `supersedes_id` 指向被替代版本。
  父版本的"仍生效区间"= 自身区间 − 其全部**未撤回**子更正区间。查询时在
  候选版本中沿替代链取最深者。撤回资格仅限"生效树的叶子"：尚有生效中子
  更正的版本不可撤回；子更正被撤回后不再占位，误发链可自叶子向上逐级撤回；
  再更正版本号继续递增。
- **原子拒绝**：写操作在单条 `BEGIN IMMEDIATE` 事务内完成"校验 + 插入"，
  进程内写锁串行化；校验失败即回滚，绝不留下半截历史。撤回与并发更正由此
  按服务端事件先后确定地决出胜负，同一区间永不同时存在两份当前正文。
- **时间存储**：统一存为定长 UTC 字符串，字典序与时间序一致，区间过滤直接用
  字符串比较；WAL 模式下读写不互斥。

## 六、测试

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

共 53 个用例，覆盖双时间维可见性边界（含微秒）、时区偏移规范化、更正仅覆盖
子区间、链式更正、四类整体拒绝、批量原子回滚、**更正撤回的三类资格拒绝与
双时间维生效边界、撤回后恢复父版本并可再次更正、撤回与并发更正的确定性次序、
撤回前后的区间对比**、两知悉时刻区间对比及完整 HTTP 流程。

## 七、项目结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由与错误处理
│   ├── service.py       # 领域服务：发布 / 更正 / 撤回 / 回溯查询 / 对比
│   ├── db.py            # SQLite 连接、建表（含撤回事件表）、写事务
│   ├── schemas.py       # 请求模型（额外字段一律拒绝）
│   ├── errors.py        # 领域错误（稳定 code）
│   ├── time_utils.py    # UTC 时间解析与规范化
│   └── config.py        # 环境变量配置
├── examples/
│   ├── python_client.py # 标准库 Python 调用示例
│   └── curl-examples.sh # curl 调用示例
├── tests/test_service.py
├── Dockerfile
├── docker-entrypoint.sh
├── docker-compose.yml
├── requirements.txt
└── requirements-dev.txt
```
