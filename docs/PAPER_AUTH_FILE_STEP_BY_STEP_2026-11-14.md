# `paper_authorization.json` 手把手操作（2026-11-14）

生成时间：2026-11-14 22:3x｜适用：账户持有人本人
任务性质：**操作执行指引**，不是建模研究 —— 不调用六阶段技能链（无新预测、无新数据、无模型比较），只把已冻结的模板与校验器用法展开成可照抄的命令。
配套文件：`docs/PAPER_AUTH_FILE_TEMPLATE_2026-11-09.md`（模板）、`docs/PAPER_AUTH_GUARD_PREREG_2026-11-09.md`（sha `1e48091a…`，哈希口径冻结）、`scripts/paper_authorization_guard.py`（校验器）。

> ⚠️ 为什么必须你本人做：U-15 书面声明 ⑦ 与 R6-1 硬边界写明 **agent 不创建、不修改、不代填授权文件**。我这轮只读核查，不碰这个文件、不读它的内容、不索取凭证值。下面全部命令由你在云主机上执行。

## 0. 先登录云主机

本地 Windows 打开 **Git Bash**（或 PowerShell），执行：

```bash
# Git Bash
ssh -i "C:/Users/77436/.ssh/fk_server_ed25519" root@1.92.141.140

# PowerShell 等价写法
ssh -i "$env:USERPROFILE\.ssh\fk_server_ed25519" root@1.92.141.140
```

没有这把私钥就用云厂商控制台的 **WebShell** 登 root，效果一样。之后的命令**都在云主机 Linux shell 里执行**。

⭐ **不要在 Windows 记事本写好再传上去**：记事本默认 CRLF、可能带 BOM，全文哈希口径会失效。直接在 Linux shell 里用下面的 heredoc 生成最稳。

## 1. 一次性粘贴这段（只需改 3 个占位）

把 `account_ref` / `issued_by` / `issued_at` 三处改成你自己的，其余**一字不动**，然后整段粘贴：

```bash
PY=/opt/quant/.venv/bin/python
G=/opt/quant/scripts/paper_authorization_guard.py
D=/opt/quant/research/stock_pick_2026-10-25/forward
F="$D/paper_authorization.json"

cat > "$F" <<'JSON'
{
  "schema_version": "paper_auth_v1",
  "channel": "gm-paper",
  "account_ref": "改成你的仿真账户标识（不要写 token/密码）",
  "max_notional": 100000,
  "not_after": "2026-12-02T23:59:00+08:00",
  "scopes": ["query_cash", "query_positions", "query_execution_reports", "sim_rebalance_order"],
  "statement_ref": "3207bff41a58",
  "issued_by": "改成你的署名",
  "issued_at": "2026-11-14T22:40:00+08:00",
  "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
}
JSON

chown deepquant:deepquant "$F"
chmod 600 "$F"

# 2) 算哈希（脚本会忽略 sha256 字段，只对其余字段规范化）
H=$($PY "$G" --compute-hash --file "$F")
echo "哈希 = $H"

# 3) 只替换 sha256 那一行的值，其余字节不动
$PY - "$F" "$H" <<'PY'
import re, sys
p, h = sys.argv[1], sys.argv[2]
s = open(p, encoding="utf-8").read()
s2, n = re.subn(r'("sha256"\s*:\s*")[0-9a-f]{64}(")',
                 lambda m: m.group(1) + h + m.group(2), s, count=1)
assert n == 1, "sha256 占位串未找到"
open(p, "w", encoding="utf-8", newline="\n").write(s2)
PY

chown deepquant:deepquant "$F"
chmod 600 "$F"

# 4) 自检（--now 是显式注入的演练时点，脚本不读系统时钟）
$PY "$G" --check --file "$F" --action sim_rebalance_order \
  --notional 4000 --cumulative 0 --available 100000 \
  --now "2026-12-01T09:35:00+08:00"
echo "rc=$?"
```

**期望输出**：

```text
哈希 = <64 位小写 hex>
[guard] ALLOW｜六项 6/6 通过｜未发单
rc=0
```

## 2. 三个容易踩死的点

| 项 | 必须这样 | 错了会怎样 |
| --- | --- | --- |
| 编码 / 换行 | UTF-8 **无 BOM**、**LF** | BOM ⇒ `DENY:NO_AUTH`；CRLF 会让哈希口径失效 |
| `scopes` | 恰好上面 4 个，**不增不减** | `DENY:SCOPE` |
| `not_after` / `issued_at` | ISO 8601 **带时区**（`+08:00`） | 不带时区 ⇒ `DENY:NO_AUTH` |
| `sha256` | 64 位小写 hex；先用 64 个 `0` 占位 | 填回后又改了别的字段 ⇒ `DENY:HASH` |
| `--action` | 必须 `sim_rebalance_order` | 写 `place_order` 等 ⇒ `DENY:SCOPE` |
| `--now` | 必须显式给，且 ≤ `not_after` | 不给我就报错；过期 ⇒ `DENY:EXPIRY` |

- `max_notional` 按你书面声明的**字面值 100000** 登记；想收紧成 2–3 万也可以，但**改完必须重算 `sha256`**。运行时另有「≤ 实时 `available`」+ 单笔/累计双校，`--check` 里 `--notional 4000 --cumulative 0 --available 100000` 就是模拟这个校验。
- `statement_ref` = U-15 书面声明存证 sha256 前 12 位 `3207bff41a58`，用来把文件与授权原文绑定，别改。
- `account_ref` 只写**账户标识**；**token / 密码一律不写进这个文件**（它们仍在 `/opt/quant/env`）。

## 3. 交叉验算（不依赖我的脚本，建议在自检后跑一次）

```bash
$PY -c 'import json,hashlib,sys;d=json.load(open(sys.argv[1],encoding="utf-8"));d.pop("sha256",None);print(hashlib.sha256(json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest())' "$F"
```

输出必须和 `--compute-hash` 的 64 位 hex **逐位相同**。规范化口径是冻结的：除 `sha256` 外全部字段 `sort_keys=True` / `separators=(",",":")` / `ensure_ascii=False` → UTF-8 → sha256 小写 hex。

再核一下格式与权限：

```bash
stat -c '%n %U:%G %a' "$F"        # 期望 deepquant:deepquant / 600
od -An -tx1 -N3 "$F"               # 前 3 字节应为 7b 0a 20 之类，不能是 ef bb bf
grep -c $'\r' "$F"                 # 期望 0
```

## 4. 出现 `DENY:*` 怎么排查

| 输出 | 优先怀疑 |
| --- | --- |
| `NO_AUTH` | BOM / CRLF / 字段缺失或拼错 / `sha256` 不是 64 位小写 hex / 时间不带时区 / `max_notional` 不是正数 |
| `SCHEMA` | `schema_version` 不是 `paper_auth_v1` |
| `CHANNEL` | `channel` 不是 `gm-paper` |
| `EXPIRY` | `--now` 晚于 `not_after` |
| `SCOPE` | `scopes` 增删了，或 `--action` 不在四个枚举里 |
| `NOTIONAL` | 单笔/累计超 `max_notional`，或 `max_notional > available` |
| `HASH` | 填回哈希后又动了任何字段 ⇒ 重跑第 2 步算哈希再填 |

⚠️ **任何 DENY 都先怀疑文件本身，不要改判据、不要让我「放宽校验」**。六项校验和 10/10 真变异检出是在预登记冻结后实现的，改判据等于绕过你自己的安全边界。

## 5. 做完之后

把 `[guard] ALLOW｜六项 6/6 通过｜未发单` 那一行回我一句即可。我会做**只读**复核：确认云端该文件命中数从 0 → 1、跑 `check_channel_isolation.py --remote` 看 FAIL 项数；**我不读取文件内容、不回显凭证、不修改这个文件**。

⚠️ **文件 `ALLOW` ≠ 可以报单**。仍缺：11-30 产出 `mode=FORWARD` 且含 `target_holds_d4` 的台账、`--remote` 通道隔离 PASS、以及你 11-30 之后给的 **U-16 书面放行**。`LIVE_ENABLED=false` 不变，11-30 前不向任何账户报单。

## 6. 脚本已离线自测（2026-11-14 22:3x）

为避免给你一段没验证过的命令，我在**本地临时目录**（非云端、非授权路径）用同一份 `paper_authorization_guard.py` 跑通了上面的逻辑，自测文件已即时删除：

| 检查 | 结果 |
| --- | --- |
| `--compute-hash` 输出 | 64 位小写 hex（示例 `f2fd09d3…`） |
| `sha256` 占位串替换脚本 | 命中并替换成功；随后 `--check` 的 `hash` 项通过 |
| 故意用非授权 channel 的 DEMO | `[guard] DENY｜原因 CHANNEL｜六项 5/6 通过｜未发单` ⇒ 闸门按预期拒绝，且哈希链条是通的 |
| 独立验算（不依赖脚本） | 与 `--compute-hash` **逐位一致** |
| 纯内存验证正确字段组合 | `ALLOW`，六项 `schema/channel/expiry/scope/notional/hash` 全 `True`（**未落盘任何文件**） |
| 本地 vs 云端校验器 sha256 | `325d6b1d15ed26644486217943fdeda1949937651183d729cdbee9d619f91202` **逐位一致** ⇒ 云端跑同样命令结果相同 |

⚠️ 自测里**没有**产出过任何 `ALLOW` 的落盘文件，也没有对 `/opt/quant/.../paper_authorization.json` 执行任何命令。

## 7. 边界

本卡只是操作指引；我没有在云主机执行其中任何一条命令，没有创建/修改/读取正式 `paper_authorization.json`，没有回显 token、账户 ID 或授权文件内容。既有模板与预登记原文均未改动。
