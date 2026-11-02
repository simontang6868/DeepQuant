# -*- coding: utf-8 -*-
"""阶段 1 复权核验 Part A：本地自检（不需要通达信）

原理（可证）：
  前复权  P_adj(t) = P_raw(t) × f(t)
  因子 f 只在除权日跳变，其余日子为常数。
  ⇒ 在非除权日，两套价格的日收益率必须逐位相等（相对误差 ~1e-12）。
  ⇒ 若在非除权日出现差异，说明 ADJ 构建有问题（对齐错、因子穿到别的日子、或调多次）。

Part A 做三件事：
  1. 抽样统计「非除权日」两套日收益的不一致率与幅度
  2. 在除权日，比较 qfq 隐含的因子跳变 vs 分红记录隐含的应然跳变
  3. 找出「qfq 跳变但无分红记录」与「有分红记录但 qfq 未跳变」的异常

输出：out/adj_check_local.json
"""
import os, csv, json, glob, random
from collections import defaultdict

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
EXEC = os.path.join(ROOT, "data", "exec_all")
QFQ = os.path.join(ROOT, "data", "qfq_all")
DIV = os.path.join(ROOT, "data", "div_all_em")
OUT = os.path.join(ROOT, "research", "stock_pick_2026-10-25", "out")


def read_prices(path, col="close"):
    out = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = str(r.get("date", "")).replace("-", "").strip()
            if len(d) != 8 or not d.isdigit():
                continue
            try:
                v = float(r[col])
            except (KeyError, ValueError, TypeError):
                continue
            if v > 0:
                out[d] = v
    return out


def read_div(code_suffix):
    p = os.path.join(DIV, code_suffix + ".div.json")
    if not os.path.exists(p):
        return None
    try:
        recs = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    out = {}
    for r in recs:
        d = str(r.get("EX_DIVIDEND_DATE") or "")[:10].replace("-", "")
        if len(d) != 8:
            continue
        bonus = r.get("PRETAX_BONUS_RMB")
        br = r.get("BONUS_RATIO")
        bit = r.get("BONUS_IT_RATIO")
        try:
            bonus = float(bonus) if bonus is not None else 0.0
        except (TypeError, ValueError):
            bonus = 0.0
        # 送股/转增：字段单位是「每 10 股送/转 X 股」→ 每股比例 = X/10
        try:
            br = float(br) if br is not None else 0.0
        except (TypeError, ValueError):
            br = 0.0
        try:
            bit = float(bit) if bit is not None else 0.0
        except (TypeError, ValueError):
            bit = 0.0
        out[d] = {"bonus_per_share": bonus / 10.0,
                  "share_ratio": (br + bit) / 10.0,
                  "profile": r.get("IMPL_PLAN_PROFILE")}
    return out


def main():
    random.seed(20261026)
    files = sorted(glob.glob(os.path.join(EXEC, "*.exec.csv")))
    codes = [os.path.basename(p).replace(".exec.csv", "") for p in files]
    print(f"全 A 文件 {len(codes)} 只")

    # 先扫一遍分红，找有送转的（最可能出 bug）
    print("扫描分红记录，挑出有送股/转增的样本…")
    with_share = []
    for c in codes:
        dv = read_div(c)
        if not dv:
            continue
        if any(v["share_ratio"] > 0.01 for v in dv.values()) and len(dv) >= 3:
            with_share.append(c)
    print(f"  有送转且分红记录≥3 条：{len(with_share)} 只")

    sample = random.sample(with_share, min(30, len(with_share)))
    also_plain = random.sample([c for c in codes if c not in with_share], 20)
    sample = sample + also_plain
    print(f"  抽样 {len(sample)} 只（30 有送转 + 20 普通）")

    results = []
    for c in sample:
        pe = read_prices(os.path.join(EXEC, c + ".exec.csv"))
        pq_path = os.path.join(QFQ, c + ".qfq.csv")
        if not os.path.exists(pq_path):
            results.append({"code": c, "skip": "qfq 缺失"})
            continue
        pq = read_prices(pq_path)
        dv = read_div(c) or {}
        dates = sorted(set(pe) & set(pq))
        if len(dates) < 300:
            results.append({"code": c, "skip": f"共同日期仅 {len(dates)}"})
            continue

        bad_non_ex = []      # 非除权日但两套收益不等
        ex_days = []
        prev = None
        for d in dates:
            if prev is not None:
                re_ = pe[d] / pe[prev] - 1
                rq_ = pq[d] / pq[prev] - 1
                # 相对差（用绝对值归一，避免小收益放大）
                diff = abs(rq_ - re_)
                scale = max(abs(re_), 0.005)
                rel = diff / scale
                is_ex = d in dv
                if is_ex:
                    # qfq 隐含的因子跳变：(1+rq)/(1+re)
                    implied = (1 + rq_) / (1 + re_) if (1 + re_) > 0 else None
                    # 应然跳变：除息除权后价格 = (P - D) / (1 + 送转)
                    # 前复权因子比 = P/(P-D)*(1+送转)  … 用 P 近似取 prev 日不复权价
                    D = dv[d]["bonus_per_share"]
                    S = dv[d]["share_ratio"]
                    P = pe[prev]
                    theo = P / (P - D) * (1 + S) if (P - D) > 0 else None
                    ex_days.append({"date": d, "implied": implied, "theo": theo,
                                    "D": D, "share": S, "profile": dv[d]["profile"],
                                    "raw_ret": re_, "adj_ret": rq_})
                elif rel > 0.02:
                    bad_non_ex.append({"date": d, "raw_ret": re_, "adj_ret": rq_, "rel": rel})
            prev = d

        results.append({"code": c, "n_days": len(dates),
                        "n_ex": len(ex_days), "bad_non_ex": bad_non_ex[:10],
                        "n_bad_non_ex": len(bad_non_ex),
                        "ex_days": ex_days[-6:]})

    # 汇总
    print("\n" + "=" * 92)
    print("Part A 结果：非除权日两套日收益不一致（阈值：相对差 >2%）")
    print("=" * 92)
    tot_bad, tot_days, clean = 0, 0, 0
    worst = sorted([r for r in results if r.get("n_bad_non_ex")],
                   key=lambda x: -x["n_bad_non_ex"])
    print(f"{'代码':<12}{'交易日':>8}{'非除权日不一致':>16}{'不一致率':>10}{'除权日':>8}")
    for r in sorted(results, key=lambda x: -(x.get("n_bad_non_ex") or 0))[:12]:
        if r.get("skip"):
            print(f"{r['code']:<12}{'—':>8}{r['skip']:>16}")
            continue
        rate = r["n_bad_non_ex"] / max(1, r["n_days"] - r["n_ex"] - 1)
        print(f"{r['code']:<12}{r['n_days']:>8}{r['n_bad_non_ex']:>16}{rate*100:>9.2f}%{r['n_ex']:>8}")
    for r in results:
        if r.get("skip"):
            continue
        tot_bad += r["n_bad_non_ex"]
        tot_days += r["n_days"] - r["n_ex"] - 1
        if r["n_bad_non_ex"] == 0:
            clean += 1
    n_valid = sum(1 for r in results if not r.get("skip"))
    print(f"\n合计：{clean}/{n_valid} 只完全干净｜非除权日不一致 {tot_bad}/{tot_days} = "
          f"{tot_bad/max(1,tot_days)*100:.4f}%")

    print("\n" + "=" * 92)
    print("除权日：qfq 隐含因子跳变 vs 分红记录应然跳变（比值≈1 为正确）")
    print("=" * 92)
    print(f"{'代码':<10}{'除权日':>10}{'送转':>8}{'派息/股':>9}{'隐含':>8}{'应然':>8}{'误差':>9}  方案")
    rows = []
    for r in results:
        if r.get("skip"):
            continue
        for e in r["ex_days"]:
            if e["implied"] and e["theo"] and e["theo"] > 0:
                err = e["implied"] / e["theo"] - 1
                rows.append((r["code"], e["date"], e["share"], e["D"], e["implied"],
                             e["theo"], err, e["profile"]))
    rows.sort(key=lambda x: -abs(x[6]))
    for c, d, s, D, im, th, err, prof in rows[:18]:
        print(f"{c:<10}{d:>10}{s*100:>7.1f}%{D:>9.3f}{im:>8.4f}{th:>8.4f}{err*100:>8.2f}%  {str(prof)[:22]}")
    big = [x for x in rows if abs(x[6]) > 0.10]
    print(f"\n除权日样本 {len(rows)} 个｜误差 >10% 的 {len(big)} 个 = {len(big)/max(1,len(rows))*100:.1f}%")

    os.makedirs(OUT, exist_ok=True)
    json.dump({"n_sample": len(sample), "results": results,
               "summary": {"clean": clean, "n_valid": n_valid,
                           "bad_non_ex_total": tot_bad, "non_ex_days_total": tot_days,
                           "ex_days_total": len(rows), "ex_days_big_err": len(big)}},
              open(os.path.join(OUT, "adj_check_local.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("saved out/adj_check_local.json")


if __name__ == "__main__":
    main()
