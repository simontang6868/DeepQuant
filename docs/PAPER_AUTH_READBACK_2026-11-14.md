# `paper_authorization.json` 落盘复核记录（2026-11-14 22:4x）

性质：**只读复核**（云端元数据 + 校验器 + 本地通道隔离）；作者：DQ Agent
触发：账户持有人本人回贴终端输出 `哈希 = 7989f2e4…`、`[guard] ALLOW｜六项 6/6 通过｜未发单`、`rc=0`。

## 1. 复核结果（结论先行）

**技术闸门已过**：授权文件已落盘、格式合规、哈希自洽、校验器判 `ALLOW`、通道隔离 `FAIL 0 项`。

| 复核项 | 命令 | 结果 |
| --- | --- | --- |
| 文件命中数 | `find /opt/quant -name paper_authorization.json` | **1**（原为 0） |
| 属主 / 权限 / 大小 | `stat` | `deepquant:deepquant` / `600` / 507 bytes |
| BOM | `od -An -tx1 -N3` | `7b 0a 20` ⇒ **无 BOM**（`{` + LF） |
| CRLF | `grep -c $'\r'` | **0** ⇒ 纯 LF |
| 哈希（校验器） | `--compute-hash` | `7989f2e46c3a613946dcd2f6fbd43f91d4f5e6247dd024421f15a81601ff69ce` |
| 哈希（独立验算，不依赖脚本） | `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` → sha256 | **逐位一致** |
| 干净复跑（避开粘贴错乱） | `--check --now 2026-11-14T22:45:00+08:00 --action sim_rebalance_order …` | **`ALLOW｜六项 6/6 通过｜未发单`，rc=0** |
| 通道隔离 | 本地 `check_channel_isolation.py --remote` | **FAIL 0 项｜PASS**；`[INFO] paper_authorization.json 存在且 channel=gm-paper` |

隔离检查六项细节：真实通道授权/适配器文件命中 0、真实通道 env 变量命中 0、`LIVE_ENABLED` 未设置、broker 模块白名单 5 个全在册、只读适配器 `order_*` 远端 grep 计数 **0**。

## 2. ⭐ 但有一处「ALLOW 也覆盖不到」的问题，必须如实报

回贴的 JSON 里这两个字段**仍是模板占位文字**，没有替换成你本人的值：

```text
"account_ref": "改成你的仿真账户标识（不要写 token/密码）"
"issued_by":   "改成你的署名"
```

- 六项校验查的是 `schema / channel / expiry / scope / notional / hash`，**不查 `account_ref` 与 `issued_by` 的实际内容** ⇒ 所以照样 `ALLOW 6/6`。这是「**测试全绿 ≠ 验收项覆盖**」的又一个实例，不能把 ALLOW 读成「文件内容已合格」。
- 按 U-15 书面声明 ⑦，授权文件应由**账户持有人本人填写并署名**；`issued_by` 写成「改成你的署名」不构成有效署名，`account_ref` 也不是账户标识。文件内容仍在对话里由你本人贴出，我没有代改、也不代签。
- ⚠️ 当前哈希 `7989f2e4…` **正是基于这两个占位字符串算出来的**。**只要改这两个字段（或 `issued_at`），哈希就会变，必须重算并重新填回**，否则 `--check` 会判 `DENY:HASH`。

## 3. 若要补齐（你本人执行，可选但建议）

整段粘贴；只改三处 `←` 的值，脚本会自动重算哈希、写回、恢复权限并自检：

```bash
PY=/opt/quant/.venv/bin/python
G=/opt/quant/scripts/paper_authorization_guard.py
F=/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json

$PY - "$F" <<'PY'
import json, sys
sys.path.insert(0, "/opt/quant/scripts")
import paper_authorization_guard as g
p = sys.argv[1]
d = json.loads(open(p, encoding="utf-8").read())
d["account_ref"] = "你的仿真账户标识"          # ← 改这里，不要写 token/密码
d["issued_by"]   = "你的署名"                  # ← 改这里
d["issued_at"]   = "2026-11-14T22:45:00+08:00" # ← 改成实际签发时间（带时区）
d["sha256"] = g.compute_hash({k: v for k, v in d.items() if k != "sha256"})
open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
print("新哈希 =", d["sha256"])
PY

chown deepquant:deepquant "$F"; chmod 600 "$F"
$PY "$G" --check --file "$F" --action sim_rebalance_order \
  --notional 4000 --cumulative 0 --available 100000 \
  --now "2026-11-14T22:50:00+08:00"
```

哈希口径按**解析后的对象**规范化（`sort_keys` / `separators=(",",":")` / `ensure_ascii=False`），与文件排版无关，重新 `json.dumps(indent=2)` 不会破坏一致性（本轮独立验算已证）。改完把新的 `ALLOW 6/6` 回我一句，我再只读复核一次哈希与隔离。

## 4. 仍然不能报单

`ALLOW` 只是四道前置里的**第 2 道**。仍缺：

1. **11-30** 产出 `mode=FORWARD` 且含 `target_holds_d4` 的台账（现在还是 `LEGACY_NO_D4` 老台账）；
2. 11-27 月末 DRYRUN、日历缺口记录；
3. 你 **11-30 之后**给出的 **U-16 书面放行**「开始 12-01 演练」；
4. 演练驱动 / 成交回报对账器按方案 §6 **尚未实现**，授权后第一步才写，且实现前先冻结预登记。

`LIVE_ENABLED=false` 不变；11-30 前不向任何账户报单；仿真成交与净值不得用于经济性判断。

## 5. 边界

本轮**只读**：未创建 / 修改 / 代填授权文件，未把文件内容或凭证值从云端取回对话（内容是你本人贴出的），未发任何委托，未改代码与配置。复核命令只输出元数据、哈希与判定结果。

## 6. 追加：22:45 第二次修订后的复核（原文不改写）

用户按第 3 节命令又跑了一次，回贴 `新哈希 = 37494c97…`、`[guard] ALLOW｜六项 6/6 通过｜未发单`。

**只读复核（2026-11-14 22:46）**：

| 项 | 结果 |
| --- | --- |
| 权限 / 大小 | `deepquant:deepquant` / `600` / **487 B**（上一版 507 B ⇒ 文件确实被改写） |
| BOM / CRLF | `7b 0a 20`（无 BOM）、CR 行 0 |
| 哈希 | `--compute-hash` = `37494c97754e3e28599cb405a4c5b0d6b0d1dfe10a53cc2c36e89a0c2e533498`，与独立验算**逐位一致**；与上一版 `7989f2e4…` 不同 ⇒ 内容已变 |
| 干净复跑 | `ALLOW｜六项 6/6 通过｜未发单`，rc=0（`--now 2026-11-14T22:46:00+08:00`） |
| 通道隔离 | 本地 `--remote` 再跑一次：**FAIL 0 项｜PASS** |

⭐ **但第 2 节的问题没有解决**：从回贴的命令可见，三处又原样粘贴了我给的模板字符串 —— `account_ref="你的仿真账户标识"`、`issued_by="你的署名"`，仍不是账户持有人本人的值；`issued_at="2026-11-14T22:45:00+08:00"` 与当前时刻吻合，**这一项可以保留不用再改**。

⇒ 用**交互式输入**替代复制占位，避免第三次把模板字符串粘进去（本人执行，agent 不代填）：

```bash
read -r -p "账户标识（不写 token/密码）: " AREC
read -r -p "署名: " IB
$PY - "$F" "$AREC" "$IB" <<'PY'
import json, sys
sys.path.insert(0, "/opt/quant/scripts")
import paper_authorization_guard as g
p, arec, ib = sys.argv[1], sys.argv[2], sys.argv[3]
assert arec.strip() and ib.strip(), "两项都不能为空"
d = json.loads(open(p, encoding="utf-8").read())
d["account_ref"] = arec.strip()
d["issued_by"] = ib.strip()
d["sha256"] = g.compute_hash({k: v for k, v in d.items() if k != "sha256"})
open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
print("新哈希 =", d["sha256"])
PY
chown deepquant:deepquant "$F"; chmod 600 "$F"
$PY "$G" --check --file "$F" --action sim_rebalance_order \
  --notional 4000 --cumulative 0 --available 100000 --now "2026-11-14T22:50:00+08:00"
```

**或者明确选择保留占位**：技术闸门已全部通过，`ALLOW 6/6` 与隔离 PASS 都成立；若账户持有人认为这两个字段用占位值即可，回一句「保持现状」，本卡即记为「占位值保留、本人已知情」，不再追问。两者都不改变「ALLOW ≠ 可报单」的结论（仍缺 11-30 FORWARD、U-16、演练驱动）。

边界同 §5：本轮只读，未创建/修改/代填文件、未取回文件内容或凭证、未发委托。

## 7. 追加：22:51 交互式尝试失败（按 R5 保留，原文不改写）

用户回贴的终端里发生了两件事：

```text
read -r -p "虚拟盘测试: " AREC -p "署名: " IB
-bash: read: `-p': not a valid identifier
read -r -p "simon " IB
...
AssertionError: 两项都不能为空
[guard] ALLOW｜六项 6/6 通过｜未发单
```

- `read` 的 `-p` 只能带**一个**提示、且必须写在变量名之前；把两组合并到一行后，多余参数被当成变量名 ⇒ `-p` 不是合法标识符，`AREC` 为空。随后 `IB` 拿到了 `simon`，但 `AREC` 仍空。
- `assert arec.strip() and ib.strip()` 位于**写文件之前** ⇒ 脚本中止，**文件没有被改写**（这是保护生效，不是数据损坏）。最后那行 `ALLOW` 是对**未变的文件**跑的，不能当作「修改成功」的证据。

**只读复核（22:52）**：`deepquant:deepquant / 600 / 487 B`（与 §6 完全一致）、`7b 0a 20` 无 BOM、CR 行 0、`--compute-hash` 仍是 `37494c97…`、干净复跑 `ALLOW｜6/6` rc=0 ⇒ 文件完好，占位值仍保留。

⇒ 换成**显式赋值**，不再用 `read`（最不容易粘错）。按用户刚才敲的两个值预填，不对就改引号里的字：

```bash
PY=/opt/quant/.venv/bin/python
G=/opt/quant/scripts/paper_authorization_guard.py
F=/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json

AREC="虚拟盘测试"     # ← 账户标识，改成你要的字符串（别写 token/密码）
IB="simon"            # ← 署名

$PY - "$F" "$AREC" "$IB" <<'PY'
import json, sys
sys.path.insert(0, "/opt/quant/scripts")
import paper_authorization_guard as g
p, arec, ib = sys.argv[1], sys.argv[2], sys.argv[3]
assert arec.strip() and ib.strip(), "两项都不能为空"
d = json.loads(open(p, encoding="utf-8").read())
d["account_ref"] = arec.strip()
d["issued_by"] = ib.strip()
d["sha256"] = g.compute_hash({k: v for k, v in d.items() if k != "sha256"})
open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
print("新哈希 =", d["sha256"])
PY
chown deepquant:deepquant "$F"; chmod 600 "$F"
$PY "$G" --check --file "$F" --action sim_rebalance_order \
  --notional 4000 --cumulative 0 --available 100000 --now "2026-11-14T22:55:00+08:00"
```

若 `read` 一定要用，正确写法是**一行一个变量**：`read -r -p "账户标识: " AREC` 然后 `read -r -p "署名: " IB`。

「保持现状」选项依旧有效：技术闸门全过，回一句即可记为「占位值保留、本人已知情」。两种都不改变 **ALLOW ≠ 可报单**。

边界同 §5：本轮只读，未创建/修改/代填文件、未取回文件内容或凭证、未发委托。

## 8. 追加：22:54 授权文件定稿复核（占位已替换，原文不改写）

用户回贴命令显示两个字段已换成其本人的值（`AREC="虚拟盘测试202611"`、`IB="simon"`），输出 `新哈希 = 3a3c35a3…`、`[guard] ALLOW｜六项 6/6 通过｜未发单`。

**只读复核（2026-11-14 22:54）**：

| 项 | 结果 |
| --- | --- |
| 命中数 | **1** |
| 属主 / 权限 / 大小 | `deepquant:deepquant` / `600` / **477 B**（§7 为 487 B ⇒ 内容确实改写） |
| BOM / CRLF | `7b 0a 20`（无 BOM）、CR 行 **0** |
| 哈希 | `--compute-hash` = `3a3c35a35c5c8a922a0d13167318541c37062014ef185681d4ed8a1347e026b0`，与独立验算**逐位一致**；与 §7 的 `37494c97…` 不同 |
| 干净复跑 | `ALLOW｜六项 6/6 通过｜未发单`，rc=0（`--now 2026-11-14T22:54:00+08:00`） |
| 通道隔离 | 本地 `--remote` 第三次跑：**FAIL 0 项｜PASS** |

⇒ **第 2 道前置闭环**：授权文件由账户持有人本人填写、哈希自洽、六项校验通过、通道隔离无 FAIL。§2 / §6 / §7 的占位与失败尝试按「不删失败」纪律全部保留，原文未改写。

⚠️ 我方**没有把文件内容从云端取回对话**（两个字段值来自用户本人回贴的命令），只核元数据、哈希与判定结果；凭证值始终未回显。

### 四道前置当前状态

| # | 前置 | 状态 |
| --- | --- | --- |
| 1 | U-15 书面声明（sha `3207bff41a58…`） | ✅ 11-09 已存证 |
| 2 | `paper_authorization.json` + `--check ALLOW` + `--remote` 隔离 PASS | ✅ **本日闭环** |
| 3 | 11-30 产出 `mode=FORWARD` 且含 `target_holds_d4` 台账（另需 11-27 DRYRUN、日历补刷） | ⏳ 未到时点 |
| 4 | 11-30 之后 U-16 书面放行「开始 12-01 演练」 | ⏳ 未给 |

另外：`docs/SIM_REBALANCE_DRILL_2026-12-01.md` §6 的演练驱动 / 成交回报对账器**尚未实现**，收到 U-16 后第一步才实现，且实现前先冻结预登记 + sha256。

**结论不变：`ALLOW` ≠ 可报单**。`LIVE_ENABLED=false`、11-30 前不向任何账户报单；仿真成交与净值不得用于经济性判断。

边界同 §5：本轮只读，未创建/修改/代填文件、未取回内容或凭证、未发委托、未改代码配置。
