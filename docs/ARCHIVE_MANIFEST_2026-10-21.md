# 数据存档清单与校验（2026-10-21 更新）

> 本文件是**唯一权威**的存档说明。任何"数据存哪了、怎么验"都以这里为准。
> 生成时间：2026-10-21 09:48

---

## 1. 两层存档：小包（可发）+ 大目录（原地 + 指纹）

| 层 | 位置 | 体积 | 用途 |
|---|---|---:|---|
| **A. 小包** | `dist/DeepQuant-data-core-2026-10-21.zip` | 77 MB / 2,781 文件 | 549 只池的完整数据 + Gate1~5 全部产物，**可邮件发送** |
| **A. 小包** | `dist/DeepQuant-docs-2026-10-20_21.zip` | 0.2 MB | 全部文档 |
| **B. 全 A 数据层** | `data/exec_all`、`data/qfq_all`、`data/div_all_em`、`data/div_all_sina` | **1.65 GB** | 研究层主力数据，**原地不复制** |
| **B. 校验** | `dist/ARCHIVE_FINGERPRINT.json` + `dist/MANIFEST_*.json` | ~1.3 MB | 逐文件 sha256，可校验任何一份拷贝 |

### 1.1 为什么全 A 数据不打包

1.65 GB 既发不了邮件，云端上传在本机也反复失败（`LibraryUpload fetch failed`）。
而"存档"的实际用途是**能证明手里这份没坏、能被别人核对、能重建**——不是再复制一份。

所以 B 层采取：**原地保留 + 逐文件 sha256 + 目录级指纹 + 行数**。

- 只有哈希，证明不了"没有被截断"（有人砍掉 200 行，哈希会变，但你不知道少了什么）；
- 只有行数，证明不了"内容没被改"。
- **两者都要。**

---

## 2. 全 A 数据层指纹（2026-10-21 09:48 实测）

| 目录 | 文件数 | 行数 | 体积 | dir_sha256 |
|---|---:|---:|---:|---|
| `data/exec_all` | 5,472 | 15,734,081 | 749.8 MB | `b52ef0b3f1098d10152be57485afa0801c29a890e9854c266e8f72cbe5bd602b` |
| `data/qfq_all` | 5,444 | 15,677,200 | 925.8 MB | `53f43f396d311bd59dde551721f43a3dad8420c16a6799216f07446d9508c4ab` |
| `data/div_all_em` | 5,472 | （JSON） | 19.9 MB | `bb66c71223c4e8a87c114b9e15320a45e0a46841ca39bcbb1d0ed80631373fa1` |
| `data/div_all_sina` | 5,458 | （JSON） | 9.0 MB | `3607a1a6a7b1a353a8e86ca6c1cd16be37e60f6123c806e6ba33d684cd6bf9cb` |

**`dir_sha256` 的算法**（重要，否则校验结果会不一样）：

```
对目录内全部文件按文件名排序，
把 "文件名:文件sha256\n" 逐行串接，
再对整个字符串取 sha256。
```

换名、换序、换内容都会变。**不能用"各文件哈希相加"之类可交换的运算。**

### 2.1 覆盖关系（不是巧合，是逐个核过的）

- `exec_all` 5,472 ⊇ `qfq_all` 5,444，差 **28 只** = 14 只 B 股（本不该在 A 股宇宙）+ 14 只新股（数据不足）
  → **真正"该进 ADJ 却进不去"的 = 0**
- `div_all_sina` 5,458 比 `div_all_em` 5,472 少 14 只；两源是 **merge 关系**（东财优先，空则新浪），
  不是替代关系。**东财对退市股全空**，这是 D-1 事故的根源，已修复。

---

## 3. 阶段 C 产物（本轮新增）

| 位置 | 内容 |
|---|---|
| `phasec_out/3a01759a37f0/` | 正式结果：five_cells.csv / paired_diffs.csv / summary.json / manifest.json |
| `phasec_out/3a01759a37f0_pre_costfix/` | 成本口径修正前（保留，供对照） |
| `phasec_out/3a01759a37f0_pre_auxdirfix/` | 辅助检验方向修正前（保留，供对照） |

每份 `manifest.json` 都记录了：预登记 sha256、**脚本自身 sha256**、三个输出文件的 sha256、
以及数据验收状态。**产物与代码的对应关系可核。**

---

## 4. 复现步骤

```bash
cd "D:/DeepQuant power by txm/DeepQuant-main"

# 环境：默认 python 没有 pandas，必须用 dq_clean
PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"

# 校验数据完整性（对照 §2 的 dir_sha256）
"$PY" scripts/make_archive_manifest.py

# 工程基线
cd backend && PYTHONPATH=src "$PY" -m pytest tests/ -q      # 期望 1997 passed / 1 skipped
cd .. && "$PY" scripts/property_checks.py                    # 期望 PC1~PC9 全 PASS
PYTHONPATH=scripts "$PY" -m pytest scripts/tests/ -q         # 期望 12+19+34 passed

# 阶段 C
"$PY" scripts/run_phase_c.py
```

⚠️ Windows 下 `PYTHONPATH` 分隔符是 `;` 不是 `:`。

---

## 5. 邮件发送状态（如实记录）

**未完成。** `mcp__agent-mail__GetMe` 返回 `status: not_bound`——Agent 邮箱尚未开通，
发送工具直接拒绝，不是网络问题，重试无用。

需要用户在「更多 - 我的邮箱」或对话窗左下角加号菜单里完成开通。
开通后可直接附件发送 `dist/DeepQuant-docs-2026-10-20_21.zip`（0.2 MB）；
77 MB 的数据包超出常规附件限制，建议只发**校验指纹 + 领取方式**。

---

## 6. 已知缺口

| 项 | 状态 |
|---|---|
| 全 A 数据云端备份 | **无**。仅本机 `data/` 原地 + 指纹。异地备份未做 |
| Agent 邮箱 | 未开通 |
| 77 MB 小包的上传 | 本机 `LibraryUpload` 反复 `fetch failed` |
| `data/qfq_rebuilt`（52 只，served） | 在 git 内，未单独打包 |
| GitHub 推送 | 最近两次靠 Git Data API 造同树异 sha 提交；网络恢复后需 `git fetch && git reset --hard origin/<branch>` 对齐 |
