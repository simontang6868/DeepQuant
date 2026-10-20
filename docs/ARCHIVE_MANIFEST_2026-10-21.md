进度报告（给外部专家审阅）+ 数据存档清单

docs/PROGRESS_REPORT_FOR_REVIEW_2026-10-21.md：10 节完整进度报告，含治理纪律、
本轮修掉的两个致命数据缺陷（东财分红表对 249 只退市股全空 / 股改对价未登记）、
五个 Gate 的完整对照与 2016 分段、4.1 资金门槛、4.2 三条风控规则全部未验证、
PIT 决策（先自建暂不采购）、本轮撤回的两条结论、希望被挑战的五个点、存档与复现命令。

存档（只把清单与 sha256 入库，77MB 的 zip 不入库）：
- dist/DeepQuant-data-core-2026-10-21.zip 77MB / 2781 文件
  sha256 23cb38e6201ec71fa2d016289135a7b66fc54630de6bd4db1982671ecfc482c8
  内容：exec_long(549 真不复权 2004-2026) + qfq_rebuilt_broad_long(549 ADJ) + exec(旧批次 549)
  + 东财/新浪分红缓存(各 549) + 全 A 代码表(5223) + Gate1~5 全部产物
- dist/DeepQuant-docs-2026-10-20_21.zip 0.2MB sha256 d2360b366e22662ac886995efd66a0e750a547dd0e3b5d9ea24d07752de501c2

已知缺口（报告已如实写明）：云端上传失败（LibraryUpload fetch failed）；
Agent Mail 未开通无法发邮件；全量测试本会话未回归且有 1 个收集错误未定位；
data/exec_all（全 A 5223 只）抓取中未验收，不在存档内。
