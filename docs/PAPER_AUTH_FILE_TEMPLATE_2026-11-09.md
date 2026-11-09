# `paper_authorization.json` 填写模板（**由账户持有人本人落盘**）

**2026-11-09**｜配套预登记：`docs/PAPER_AUTH_GUARD_PREREG_2026-11-09.md`（sha256 `1e48091a…`）
⚠️ **本文件是模板，不是授权文件，不产生任何授权效力。agent 不创建、不修改、不代填 `paper_authorization.json`——即使你把内容发给我，我也不会替你写这个文件**（R6-1 硬边界、书面声明 ⑦）。

## 一、唯一有效路径与格式

| 项 | 值 |
| --- | --- |
| 路径 | `/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json` |
| 属主 / 权限 | `deepquant:deepquant` / `600` |
| 编码 / 换行 | UTF-8 **无 BOM** / **LF**（Windows 记事本默认的 CRLF 会让全文哈希口径失效） |
| 顶层 | 单个 JSON 对象 |

## 二、字段模板（复制后由你本人填写尖括号项）

```json
{
  "schema_version": "paper_auth_v1",
  "channel": "gm-paper",
  "account_ref": "<由你本人填写：仿真账户标识；不要写 token/密码>",
  "max_notional": 100000,
  "not_after": "2026-12-02T23:59:00+08:00",
  "scopes": ["query_cash", "query_positions", "query_execution_reports", "sim_rebalance_order"],
  "statement_ref": "3207bff41a58",
  "issued_by": "<你的署名>",
  "issued_at": "<签发时间，ISO 8601 带时区，如 2026-11-09T11:16:00+08:00>",
  "sha256": "<见第三节：先算再填>"
}
```

- `max_notional` **按你书面声明的字面值 100000 登记**（模板曾建议 20,000–30,000，我未擅自压低）。若你想收紧，改这个数并**重算 sha256** 即可；不论取何值，运行时仍受「≤ 实时 `available`」与单笔＋累计双校约束。
- `scopes` 四个枚举已在预登记冻结；**增删任一项都会导致 `--check` 判 `DENY:SCOPE` 或 `--compute-hash` 口径变化**。
- `statement_ref` = U-15 书面声明存证 sha256 前 12 位，用于把文件与授权原文绑定。

## 三、`sha256` 怎么算（口径已在预登记冻结）

规范化字节＝把**除 `sha256` 外**的全部字段按 `sort_keys=True`、`separators=(",", ":")`、`ensure_ascii=False` 序列化为 UTF-8；取其 sha256 小写 hex。校验器用 `hmac.compare_digest` 比对。

三步（在云主机上执行；`paper_authorization_guard.py` 已部署到 `/opt/quant/scripts/`）：

```bash
F=/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json

# 1) 先把不含 sha256 的字段写好（sha256 可暂留占位串），UTF-8/LF 保存
# 2) 算哈希：脚本会忽略 sha256 字段，只对其余字段规范化
/opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --compute-hash --file "$F"

# 3) 把输出的 64 位 hex 填进 sha256 字段；其余字节一个都不要动
# 4) 自检（示例：以演练时点、单笔 4000 元、累计 0、实时可用资金 100000 校验）
/opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --check --file "$F" \
  --action sim_rebalance_order --notional 4000 --cumulative 0 \
  --available 100000 --now "2026-12-01T09:35:00+08:00"
# 期望： [guard] ALLOW｜六项 6/6 通过｜未发单   （rc=0）
```

**独立验算**（不依赖我的脚本，用于交叉核对）：

```bash
/opt/quant/.venv/bin/python -c 'import json,hashlib,sys;d=json.load(open(sys.argv[1],encoding="utf-8"));d.pop("sha256",None);print(hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest())' "$F"
```

两个结果必须一致。**自检返回任何 `DENY:*` 都先怀疑文件本身**（BOM/CRLF/字段拼写/哈希填错），不要改判据、不要让我「放宽校验」。

## 四、文件创建 ≠ 可以报单

落盘并 `ALLOW` 只是**第 2 个前置条件**。仍需：

1. `check_channel_isolation.py --remote` 为 `FAIL 0 项`；
2. **11-30** 产出 `mode=FORWARD` 且含 `target_holds_d4` 的台账；
3. 你给出 **U-16** 明确书面放行「开始 12-01 演练」。

四者缺一不报单。声明 ⑧ 明确本授权「不是 12-01 演练的执行许可」。**`LIVE_ENABLED` 仍为 false**，四道实盘门全未过；仿真成交与净值不得用于经济性判断。
