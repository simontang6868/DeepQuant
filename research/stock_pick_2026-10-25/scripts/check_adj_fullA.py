# -*- coding: utf-8 -*-
"""阶段 1 复权核验 Part B：全 A 除权日误差扫描

对每一只股票、每一个除权日，比较：
  隐含 = (1+r_adj) / (1+r_raw)        # 前复权隐含的因子跳变
  应然 = P_prev / (P_prev − D) × (1 + 送转)
若 隐含/应然 − 1 明显偏离 0，说明该股该次分红的前复权调整有误。

这是判定 qfq_all 能否用于本课题的核心证据（不是抽样，是全量）。
输出：out/adj_check_fulA.json
"""
import os, csv, json, glob, sys

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
EXEC = os.path.join(ROOT, "data", "exec_all")
QFQ = os.path.join(ROOT, "data", "qfq_all")
DIV = os.path.join(ROOT, "data", "div_all_em")
OUT = os.path.join(ROOT, "research", "stock_pick_2026-10-25", "out")
ERR_BIG = 0.10


def read_close(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = str(r.get("date", "")).replace("-", "").strip()
                if len(d) != 8 or not d.isdigit():
                    continue
                try:
                    v = float(r["close"])
                except (KeyError, ValueError, TypeError):
                    continue
                if v > 0:
                    out[d] = v
    except Exception:
        return None
    return out


def read_div(code_with_suffix):
    p = os.path.join(DIV, code_with_suffix + ".div.json")
    if not os.path.exists(p):
        return {}
    try:
        recs = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for r in recs:
        d = str(r.get("EX_DIVIDEND_DATE") or "")[:10].replace("-", "")
        if len(d) != 8:
            continue

        def fnum(x):
            try:
                return float(x) if x is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        D = fnum(r.get("PRETAX_BONUS_RMB")) / 10.0
        # 送转比例：两字段在「只送」时为同值（相加会翻倍），在「送+转」时 IT_RATIO 已是合计
        # 实测三种情形下 max 均正确：只送 6/6->6、只转 0/8->8、送5转5 5/10->10
        S = max(fnum(r.get("BONUS_RATIO")), fnum(r.get("BONUS_IT_RATIO"))) / 10.0
        if D == 0 and S == 0:
            continue
        out[d] = (D, S, r.get("IMPL_PLAN_PROFILE"))
    return out


def main():
    files = sorted(glob.glob(os.path.join(EXEC, "*.exec.csv")))
    print(f"全 A {len(files)} 只，开始扫描…", flush=True)
    per_stock = []
    ex_total = 0
    ex_big = 0
    big_list = []
    n_ok = 0

    for i, p in enumerate(files):
        cw = os.path.basename(p).replace(".exec.csv", "")
        pq = os.path.join(QFQ, cw + ".qfq.csv")
        if not os.path.exists(pq):
            continue
        pe = read_close(p)
        pqd = read_close(pq)
        if not pe or not pqd:
            continue
        dv = read_div(cw)
        if not dv:
            n_ok += 1
            continue
        dates = sorted(set(pe) & set(pqd))
        if len(dates) < 60:
            continue
        idx = {d: k for k, d in enumerate(dates)}
        errs = []
        for d, (D, S, prof) in dv.items():
            k = idx.get(d)
            if k is None or k == 0:
                continue
            prev = dates[k - 1]
            re_ = pe[d] / pe[prev] - 1
            rq_ = pqd[d] / pqd[prev] - 1
            if 1 + re_ <= 0:
                continue
            implied = (1 + rq_) / (1 + re_)
            P = pe[prev]
            if P - D <= 0:
                continue
            theo = P / (P - D) * (1 + S)
            err = implied / theo - 1
            errs.append((d, err, D, S, prof, implied, theo))
            ex_total += 1
            if abs(err) > ERR_BIG:
                ex_big += 1
                if len(big_list) < 400:
                    big_list.append({"code": cw, "date": d, "err": err, "D": D, "S": S,
                                     "implied": implied, "theo": theo, "profile": prof})
        if errs:
            mx = max(errs, key=lambda x: abs(x[1]))
            per_stock.append({"code": cw, "n_ex": len(errs), "max_abs_err": abs(mx[1]),
                              "worst_date": mx[0]})
        else:
            n_ok += 1
        if (i + 1) % 500 == 0:
            print(f"  {i+1}/{len(files)}  除权日累计 {ex_total} 误差>10% {ex_big}", flush=True)

    per_stock.sort(key=lambda x: -x["max_abs_err"])
    print("\n" + "=" * 92)
    print(f"全 A 扫描完成：{len(files)} 只｜可核验除权日 {ex_total} 个｜误差 >10% 的 {ex_big} 个 "
          f"= {ex_big/max(1,ex_total)*100:.3f}%")
    print("=" * 92)
    print(f"{'代码':<14}{'除权日':>10}{'隐含':>9}{'应然':>9}{'误差':>9}{'派息':>8}{'送转':>8}")
    for b in big_list[:25]:
        print(f"{b['code']:<14}{b['date']:>10}{b['implied']:>9.4f}{b['theo']:>9.4f}"
              f"{b['err']*100:>8.2f}%{b['D']:>8.3f}{b['S']*100:>7.1f}%")

    with_err = [s for s in per_stock if s["max_abs_err"] > ERR_BIG]
    print(f"\n有过大误差的股票 {len(with_err)} 只 / {len(per_stock)} 只有除权日的 = "
          f"{len(with_err)/max(1,len(per_stock))*100:.2f}%")
    for s in with_err[:20]:
        print(f"  {s['code']:<14} 除权 {s['n_ex']} 次，最差 {s['worst_date']} 误差 {s['max_abs_err']*100:.1f}%")

    os.makedirs(OUT, exist_ok=True)
    json.dump({"n_files": len(files), "ex_total": ex_total, "ex_big": ex_big,
               "big_list": big_list, "stocks_with_err": with_err,
               "top_stocks_by_err": per_stock[:200]},
              open(os.path.join(OUT, "adj_check_fullA_max.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("saved out/adj_check_fullA_max.json")


if __name__ == "__main__":
    main()
