# 待本人落笔文本（两份）｜go/no-go 签字 + 授权续期（2026-11-22）

> ⚠️ **本文件是空白模板，不是授权文件，不产生任何授权效力。**
> 执行方不代签、不代填、不代落盘；尖括号 `<...>` 项须由账户持有人本人填写。
> 意图来源：`SIGNATORY_AND_RENEW_statement_2026-11-22.md`（用户 16:18 表态"我给你签字和授权续期"逐字存证）。
> 角色分离：签署人同时是【go/no-go 签字人（承担资金后果）】与【审查方（程序性否决、无资金责任）】，两身份并存，见模板第二部分。

---

## 文本一：go/no-go 签字人指定 + 签署声明

> 用途：在 11-27 17:30 前填妥并签署，使 §5.1 决策点有合法主体。
> 若在 11-27 17:30 前未签署 ⇒ 按已登记规则**自动 no-go**，PROBE 结果按 B 分支复用，12-01 顺延。

```text
【DeepQuant go/no-go 签字人指定与签署｜2026-11-__】

我（账户持有人，署名：<本人署名>）确认：

1) 自本声明签署起，我担任 12-01 仿真调仓演练的 go/no-go 签字人，
   拥有对 11-27 17:30 决策点的 go / no-go 判定权，并承担相应资金后果。
2) 我同时以"审查方"身份书面接受该角色，行使程序性否决权：
   可无理由否决，但否决记录须标注触发的门/风险，或注明"无触发、自由裁量"。
   该否决不承担资金责任。签字人与审查方两身份并存、不冲突。
3) 判定依据：须见 DRYRUN_20261127 产物 + PROBE 结果 + 六门门控输出齐备；
   PROBE 未清空 / DRYRUN 失败 / 有 H 类 ⇒ 我判 no-go。
4) 本声明不解锁实盘：LIVE_ENABLED 保持 false，不得创建或修改 live_authorization.json。
5) 本声明不改变任何判据、不放宽任何门；不得解释为对策略有效性的确认。

签署时间：<YYYY-MM-DDTHH:MM:SS+08:00>（须 ≤ 2026-11-27T17:30:00+08:00）
署名：<本人署名>
```

---

## 文本二：`paper_authorization.json` 续期 / 新建

> ⚠️ 文件当前**不存在**（本地与执行机均无）。因此这是"新建"而非严格意义的"续期"。
> 唯一有效路径（执行机）：`/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json`
> 属主/权限 `deepquant:deepquant` / `600`；UTF-8 无 BOM / **LF**。
> **执行方不创建、不修改、不代填，即使把内容发给我也不会代写。**

### 2.1 字段模板（尖括号项由本人填写）

```json
{
  "schema_version": "paper_auth_v1",
  "channel": "gm-paper",
  "account_ref": "<本人填写：仿真账户标识，不要写 token/密码>",
  "max_notional": 100000,
  "not_after": "<本人填写：ISO 8601 带时区，见 2.2 有效期建议>",
  "scopes": ["query_cash", "query_positions", "query_execution_reports", "sim_rebalance_order"],
  "statement_ref": "3207bff41a58",
  "issued_by": "<本人署名>",
  "issued_at": "<签发时间，ISO 8601 带时区>",
  "sha256": "<先按 2.3 算出再填>"
}
```

- `scopes` 四项已冻结，增删任一会被守卫判 `DENY:SCOPE`；
- `statement_ref` = U-15 书面声明存证 sha256 前 12 位，用于绑定授权原文；
- `max_notional` 按既有书面声明 100000 登记；若要收紧，改数后**必须重算 sha256**。

### 2.2 有效期 `not_after`——三选一，由本人定（推荐第一个）

| 选项 | 取值 | 覆盖 |
|---|---|---|
| **推荐** | `2027-01-31T23:59:00+08:00` | 覆盖 A 分支 12-01 执行 + 1~2 月观察期，也覆盖 B 分支 12-31 决策 / 2027-01-04 执行 + 观察期 |
| 保守 | `2026-12-31T23:59:00+08:00` | 仅覆盖本月；若走 B 分支顺延到 1 月则**会断档** |
| 长周期 | `2027-03-31T23:59:00+08:00` | 覆盖到 Q1 末，给多次顺延留余量；授权期偏长 |

> 原模板默认 `2026-12-02T23:59`，在 A/B 分支下**不足**——它只到演练后一天，不覆盖观察期，也不覆盖顺延后日期。故不建议沿用默认值。

### 2.3 本人落盘与自检命令（在执行机执行）

```bash
F=/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json

# 1) 用本人编辑器按 2.1 写好（sha256 可暂留占位串），UTF-8/LF 保存
# 2) 算哈希（脚本忽略 sha256 字段）
/opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --compute-hash --file "$F"

# 3) 把 64 位 hex 填入 sha256 字段，其余字节不动
# 4) 自检（演练时点、单笔 4000、累计 0、可用 100000）
/opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --check --file "$F" \
  --action sim_rebalance_order --notional 4000 --cumulative 0 \
  --available 100000 --now "2026-12-01T09:35:00+08:00"
# 期望： [guard] ALLOW｜六项 6/6 通过｜未发单（rc=0）

# 5) 权限
chmod 600 "$F" && chown deepquant:deepquant "$F"
```

---

## 3. 时序提醒

| 时点 | 动作 | 主体 |
|---|---|---|
| 11-24 前 | 签署文本一（签字人指定） | 账户持有人本人 |
| 11-26 前 | 落盘 `paper_authorization.json`（文本二）+ guard 6/6 PASS | 账户持有人本人 |
| 11-26 | PROBE 执行（独立授权，不受本模板影响） | 执行方 |
| **11-27 17:30** | go/no-go 决策；未签 ⇒ 自动 no-go | 签字人（本人） |
| 12-01 09:35 | 仿真调仓演练（go 时） | 执行方 |

> 本模板本身不改变任何状态；签署与落盘完成后，执行方据实更新 STATUS.md 与主报告，并保留签署文本 sha256 存证。

---

## 4. 更正段（16:2x，原"文件不存在"表述有误，原文不改写）

实测执行机：`paper_authorization.json` **已存在**（477 B，`deepquant:deepquant` 600，`mtime=2026-11-14 22:53`；`issued_by=simon`、`issued_at=2026-11-14T22:45:00+08:00`、`not_after=2026-12-02T23:59:00+08:00`、`max_notional=100000`、`statement_ref=3207bff41a58`、scopes 四项、sha256 已设）。guard 脚本 `/opt/quant/scripts/paper_authorization_guard.py` 也在（6,583 B）。

⇒ **"续期"= 改现有文件的 `not_after`**，不是新建。正确步骤（本人执行）：

```bash
F=/opt/quant/research/stock_pick_2026-10-25/forward/paper_authorization.json
cp "$F" "$F.bak_20261122"          # 先备份原文件（含原 not_after=12-02）
# 本人编辑器：把 not_after 改为 §2.2 选定值；issued_at 若一并更新也由本人定
/opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --compute-hash --file "$F"
# 把输出的 hex 填入 sha256 字段，其余字节不动
/opt/quant/.venv/bin/python /opt/quant/scripts/paper_authorization_guard.py --check --file "$F" \
  --action sim_rebalance_order --notional 4000 --cumulative 0 \
  --available 100000 --now "2026-12-01T09:35:00+08:00"
# 期望 ALLOW 6/6；并另测 --now "2027-01-15T09:35:00+08:00" 仍 ALLOW（验证续期覆盖）
chmod 600 "$F" && chown deepquant:deepquant "$F"
# ⚠️ 凭证值（account_ref / sha256 全文）不回显、不写入任何报告
```

§2.1 的字段模板保留作为"若需重建"的参考，但**默认动作是在现有文件上改 `not_after` 并重算 sha256**。

