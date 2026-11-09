# 部署遗留压缩包清理记录（2026-11-09 23:22）

**授权来源**：用户引用上一轮容量诊断的建议 ②「想腾空间，先删云端+本地两处部署 tar（合计约 1.5G，最安全）」并明确指令「执行清理」。
**范围**：**仅 4 个文件**，不动 `data/`、`research/`、`backend/`、git 仓库、任何运行脚本或证据。
**性质**：⚠️ **永久删除（不走回收站）**。未做 1:1 字节备份——那会再复制 1.5G，正好抵消本次清理；替代保障是「删除前验证内容可重建 + 留存 sha256 + 写明重建命令」。

## 一、删除前核查（只读）

| 项 | 结论 |
| --- | --- |
| 文件身份 | 11-03 05:12–05:16 部署时上传的**解包源**，部署完成后按设计不再使用 |
| 本地/云端一致性 | 四个文件 sha256 **两两完全相同**（见下表），是同一批上传包的两份副本 |
| 运行期引用 | 全仓库 `grep` 只命中 `deploy/bootstrap.sh:62` 与历史交接副本——那是 `die` 的**提示文案**（"先把代码传上来：scp code.tar.gz…"），仅在 `STATUS.md` 缺失时打印，**不读取 tar**；`/etc/cron.d`、`systemd unit`、日更链脚本均无引用 |
| `data.tar.gz` 内容边界 | 顶层仅 `exec_all/` + `qfq_all/` ⇒ 与云端 `/opt/quant/data/`（1.7G）和本地 `data/` 完全对应 |
| `code.tar.gz` 内容边界 | 仓库根目录快照（`./backend/`、`./.github/` 等），**不含 `.git/`**；代码全历史另存于 cloud 裸仓库（28M）与 GitHub，本地工作副本完整 |
| 解包结果 | `/opt/quant/data` 1.7G、`backend` 18M、`scripts` 3.2M 均在位 |

## 二、四个目标文件

| 路径 | 字节 | sha256 |
| --- | --- | --- |
| `var/deploy/data.tar.gz`（本地） | 524,324,935 | `6eb41c533027ccf018ebe6b98a5736279d7b6787512d42a46db0ad318b55c9ad` |
| `var/deploy/code.tar.gz`（本地） | 276,622,654 | `c8ec7bead7d0f28684f6286d12dba7eb9ef4eada1dd52d3497013443f21446f2` |
| `/opt/quant/var/data.tar.gz`（云端） | 524,324,935 | 同上 `6eb41c53…` |
| `/opt/quant/var/code.tar.gz`（云端） | 276,622,654 | 同上 `c8ec7bea…` |

合计 **1,600,947,178 字节 ≈ 1.49 GiB**（本地 765M + 云端 765M）。指纹另存 `var/deploy/tarballs.sha256`（保留，不删）。

## 三、恢复路径（若日后需要）

```bash
# 行情包：直接用解包后的目录重新打包（内容等价，gzip 时间戳不同 ⇒ sha256 不会相同）
tar czf data.tar.gz -C /opt/quant/data exec_all qfq_all

# 代码包：从 git 全历史恢复仓库快照（cloud 裸仓库或 GitHub）
git clone /opt/quant-backup/repo.git restored && cd restored && \
  git checkout workbuddy/2026-10-20-consolidated && tar czf code.tar.gz .
```

**注意**：重新打包得到的 sha256 与原包不同（gzip 头含时间戳），因此上表指纹只能用于识别「原包」，不能用于校验重建包；重建后应重新比对**内容清单**而非哈希。

## 四、执行回执（2026-11-09 23:22 完成）

分两批各 2 个文件、每批删后立即验证，**无失败、无扩大范围**。

| 批次 | 动作 | 验证 |
| --- | --- | --- |
| 1 本地 | `rm var/deploy/{data,code}.tar.gz` | 两个路径均 `GONE`；`var/deploy/` **768M → 3.9M**；仓库 **4.7G → 3.9G**；`tarballs.sha256` 保留 |
| 2 云端 | `rm -f /opt/quant/var/{data,code}.tar.gz` | 两个路径均 `GONE`；`/opt/quant/var/` **788M → 24M**；磁盘已用 **11,474,732 KB → 10,692,552 KB**（释放 **782,180 KB ≈ 764 MiB**），使用率 **22% → 21%** |

**删除后完整性复验**：`/opt/quant/data` 1.7G、`backend` 18M、`scripts` 3.2M 均在位；每日备份 7 个、日志 10 个文件数未变；`deploy_manifest.py --verify` 本地与 `--verify --remote` **均 45/45 PASS**；`check_channel_isolation.py --remote` **FAIL 0 项**。日更链、定时器、证据链未受任何影响。

**未做的事**：没有清空回收站、没有删除 `tarballs.sha256`、没有动 `data/` 或 git、没有改任何运行脚本或清理策略、没有重启任何服务。

⚠️ **恢复方式提示**：这是**永久删除**，不能从回收站还原；若需要原包，按第 三 节命令重新打包，且重建包的 sha256 会与原包不同（gzip 头含时间戳），应比对**内容清单**而非哈希。
