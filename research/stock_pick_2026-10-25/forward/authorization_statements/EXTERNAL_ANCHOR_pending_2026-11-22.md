# 外部锚待核对存证｜授权续期 hash 前 12 位（2026-11-22）

> ⚠️ **本文件是"待核对"存证，不是"已核对"证明。核对结果栏为空，须账户持有人本人回填。**
> 触发：外部专家第 8 轮 §一——guard 只证明文件内部自洽，不能证明 `not_after` 是用户授权的那个值；执行方"读哈希→写哈希→跑 guard"是同一信任闭环，**需要独立于执行方的外部锚**。
> 原文不改写；核对完成后另立段落，不回填本段。

## 1. 为什么需要外部锚（结构缺陷，非执行方过失）

当前事实链：`执行方改 not_after → 执行方重算 sha256 → 执行方跑 guard → guard 只查内部一致性 → ALLOW`。
若执行方把 `not_after` 改到 2099 再重算，guard 同样 ALLOW 6/6。**guard 证明"自洽"，不证明"授权值真实"。**

⇒ 外部锚 = 账户持有人**本人**用独立路径核对 hash 前 12 位 + 核对时间，作为"人在意向上确认过这个具体值"的证据。

## 2. 待核对值（执行方给出，**未经用户核对**）

| 项 | 值 | 生成方式 |
|---|---|---|
| 授权文件 | 执行机 `/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json` | — |
| `not_after`（已改） | `2027-01-31T23:59:00+08:00` | 执行方按签署件 §4 改 |
| **待核对 hash 前 12 位** | **`213a5b1dbf78`** | guard `--compute-hash` 输出 + 独立实现 `hashlib.sha256(canonical_bytes)` **两路一致** |
| 独立重算 `match` | `True` | 不依赖 guard 脚本，执行方第二次自证（**仍非外部锚**） |
| 存储 vs 重算 | 前 12 位一致；完整值未在本文回显 | 凭证值不写入报告 |

## 3. 账户持有人本人核对方法（二选一，执行方不代做）

**方法 A（推荐，本地可执行）**：在您自己的机器/可信终端，用独立实现重算并**只对比前 12 位**：

```bash
ssh -i ~/.ssh/fk_server_ed25519 root@1.92.141.140 \
'/opt/quant/.venv/bin/python -c "
import json,hashlib
d=json.load(open(\"/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json\",encoding=\"utf-8\"))
body={k:v for k,v in d.items() if k!=\"sha256\"}
print(hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(\",\",\":\")).encode()).hexdigest()[:12])
print(d[\"not_after\"])
"'
# 期望输出第一行 = 213a5b1dbf78，第二行 = 2027-01-31T23:59:00+08:00
```

**方法 B（手工）**：在执行机上只读打开文件，核对 `not_after` 字段；hash 前 12 位用方法 A 或 guard `--compute-hash` 取。

## 4. 外部锚回填栏（**留空，须本人填写**）

| 字段 | 值（本人填） |
|---|---|
| 本人核对 hash 前 12 位 | `______________` |
| 本人核对 `not_after` | `______________` |
| 核对时间（人核对的时点，非文件 mtime） | `______________` |
| 核对方式（A / B） | `__________` |
| 本人署名 | `______________` |

> 执行方**不代填本栏**。回填后另立"已核对"段并冻结新 sha256；本"待核对"段原文保留。

## 5. 未来修改的硬规则（专家 §一第 3 点）

此后任何对 `paper_authorization.json` 的再次修改，必须重走三步：**执行方改 → 账户持有人独立核对 hash 前 12 位 → 回填外部锚存证**。缺任一步，该次修改不视为有效授权。
