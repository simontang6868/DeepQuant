# -*- coding: utf-8 -*-
"""SP-4：低流动性档(<20%) + 5 只 + F3 卖出（决定性检验）

按 SP4_预登记.md 执行。三组：
  C2  F3 触发卖出
  C1  同换手次数随机换（配对）
  C0  买入不换
另报：档内等权（1000+ 只，benchmark）
"""
import os, sys, json, hashlib
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
OUT = os.path.join(RD, "out")

K, THRESH, LIQ_MAX = 5, 0.70, 0.20
N_PATHS, SEED = 200, 20261026
COST = 0.000204
SEGS = [("200501","201712","IS 2005-2017"),("201801","202112","OOS1 2018-2021"),
        ("202201","202612","OOS2 2022-2026"),("200501","202612","全样本")]

def check():
    want = open(os.path.join(RD,"sp4_prereg.sha256"),encoding="utf-8").read().split()[0]
    got = hashlib.sha256(open(os.path.join(RD,"SP4_预登记.md"),"rb").read()).hexdigest()
    print(f"预登记校验 {want[:16]}… {'一致' if want==got else '不一致!'}")
    if want != got: sys.exit(1)

def perf(r):
    r=np.asarray(r,float); eq=np.cumprod(1+r)
    ann=eq[-1]**(12/len(r))-1 if eq[-1]>0 else -1
    peak=np.maximum.accumulate(eq)
    return float(ann), float(np.min(eq/peak-1)), float(np.std(r,ddof=1)*np.sqrt(12))

def main():
    check()
    panel = pd.read_parquet(PANEL)
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"]=p.groupby("date")["f3"].rank(pct=True,method="average")
    p["liqpct"]=p.groupby("date")["amt20"].rank(pct=True,method="average")
    p=p[p["f3pct"].notna()&p["liqpct"].notna()]
    by={}
    for d,g in p.groupby("date"):
        if len(g)<100: continue
        codes=g["code"].to_numpy()
        f3p=g["f3pct"].to_numpy(float); lq=g["liqpct"].to_numpy(float)
        by[d]={"codes":codes,"fwd":g["fwd20"].to_numpy(float),"pct":f3p,
               "pool":np.where((f3p<=THRESH)&(lq<LIQ_MAX))[0],
               "cmap":{c:i for i,c in enumerate(codes)},
               "band_fwd":g["fwd20"].to_numpy(float)[lq<LIQ_MAX].mean() if (lq<LIQ_MAX).sum()>0 else np.nan,
               "n_band":int((lq<LIQ_MAX).sum())}
    months=sorted(by)
    print(f"低流动性档（liq<20%）：{len(months)} 月（{months[0]}~{months[-1]}），档内中位 {int(np.median([by[d]['n_band'] for d in months]))} 只/月")

    def sim(mode, rng):
        b0=by[months[0]]; pool=b0["pool"]
        if len(pool)<K: return None,None
        hold=[b0["codes"][i] for i in rng.choice(pool,K,replace=False)]
        rets,ns=[],[]
        for t,d in enumerate(months):
            b=by[d]; n=0
            if t>0 and mode!="hold":
                if mode=="f3":
                    sell=[c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]]>THRESH]
                    pool_b=b["pool"]
                else:
                    k=ns_fixed[t] if ns_fixed is not None else 0
                    csel=[c for c in hold if c in b["cmap"]]
                    sell=list(rng.choice(csel,min(k,len(csel)),replace=False)) if k else []
                    pool_b=b["pool"]
                n=len(sell)
                if n:
                    held=[b["cmap"][c] for c in hold if c in b["cmap"]]
                    cand=pool_b[~np.isin(pool_b,held)] if len(pool_b) else np.array([],int)
                    if len(cand)>=n:
                        hold=([c for c in hold if c not in sell]
                              +[b["codes"][i] for i in rng.choice(cand,n,replace=False)])
                    else: n=0
            vals=[b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
            rets.append((float(np.mean(vals)) if vals else 0.0)-n*COST); ns.append(n)
        return rets,ns

    A={"c2":[],"c1":[],"c0":[]}; M={"c2":[],"c1":[],"c0":[]}; V={"c2":[],"c1":[],"c0":[]}
    NS=[]; R={"c2":[],"c1":[],"c0":[]}
    for path in range(N_PATHS):
        global ns_fixed
        rng=np.random.default_rng(SEED+path)
        ns_fixed=None
        r2,ns2=sim("f3",rng)
        if r2 is None: continue
        ns_fixed=ns2
        r1,_=sim("random",np.random.default_rng(SEED+path))
        r0,_=sim("hold",np.random.default_rng(SEED+path))
        for key,r in (("c2",r2),("c1",r1),("c0",r0)):
            a,m,v=perf(r); A[key].append(a); M[key].append(m); V[key].append(v); R[key].append(r)
        NS.append(np.mean(ns2)/K)
    for k in A: A[k]=np.array(A[k]); M[k]=np.array(M[k]); R[k]=np.array(R[k])

    print("\n"+"="*84)
    print(f"200 条并行 5 只路径（低流动性档）")
    print("="*84)
    print(f"{'组':<24}{'年化中位':>10}{'5%':>9}{'95%':>9}{'波动':>9}{'MDD':>9}")
    for k,nm in (("c2","C2 F3触发卖出"),("c1","C1 同换手随机"),("c0","C0 买入不换")):
        print(f"{nm:<24}{np.median(A[k])*100:>+9.2f}%{np.percentile(A[k],5)*100:>+8.2f}%"
              f"{np.percentile(A[k],95)*100:>+8.2f}%{np.median(V[k])*100:>8.2f}%{np.median(M[k])*100:>+8.2f}%")
    band=[by[d]["band_fwd"] for d in months]
    ba,bm,bv=perf(band)
    print(f"{'档内等权(1000+只)':<24}{ba*100:>+9.2f}%{'':>9}{'':>9}{bv*100:>8.2f}%{bm*100:>+8.2f}%")
    print(f"\n  月换手 {np.median(NS)*100:.1f}%｜年成本 {np.median(NS)*K*COST*12*100:.2f}%")
    print(f"  C2>C1 胜率 {float((A['c2']>A['c1']).mean())*100:.1f}%｜C2−C1 中位 {(np.median(A['c2'])-np.median(A['c1']))*100:+.2f}pp")

    print("\n"+"="*84)
    print("分段（三组 + 档内等权）")
    print("="*84)
    print(f"{'区间':<20}{'C2':>10}{'C1':>10}{'C0':>10}{'档内等权':>11}{'C2−C1':>10}")
    seg={}
    for lo,hi,lab in SEGS:
        ii=[i for i,d in enumerate(months) if lo<=d<=hi]
        if len(ii)<12: continue
        c2=np.array([perf(R["c2"][k,ii])[0] for k in range(len(R["c2"]))])
        c1=np.array([perf(R["c1"][k,ii])[0] for k in range(len(R["c1"]))])
        c0=np.array([perf(R["c0"][k,ii])[0] for k in range(len(R["c0"]))])
        m2=np.array([perf(R["c2"][k,ii])[1] for k in range(len(R["c2"]))])
        bb=perf([band[i] for i in ii])[0]
        print(f"{lab:<20}{np.median(c2)*100:>+9.2f}%{np.median(c1)*100:>+9.2f}%"
              f"{np.median(c0)*100:>+9.2f}%{bb*100:>+10.2f}%{(np.median(c2)-np.median(c1))*100:>+9.2f}pp")
        seg[lab]={"c2":float(np.median(c2)),"c1":float(np.median(c1)),"c0":float(np.median(c0)),
                  "band":float(bb),"c2_mdd":float(np.median(m2)),
                  "win_rate":float((c2>c1).mean())}

    R1=np.median(A["c2"])>0.08
    R2=seg.get("OOS2 2022-2026",{}).get("c2",-1)>0.08
    R3=np.median(A["c2"])>np.median(A["c1"])
    R4=np.median(M["c2"])>=-0.20
    R5=np.median(NS)<=0.30
    print("\n"+"="*84)
    print("判据")
    print("="*84)
    for lab,v in (("R1 全样本年化>8%",R1),("R2 OOS2年化>8%",R2),("R3 F3优于同档随机",R3),
                  ("R4 MDD≥−20%",R4),("R5 月换手≤30%",R5)):
        print(f"  {lab:<24}{'过' if v else '不过'}")
    print(f"\n  ⚠️ 保守口径（回测年化 − 2pp 成本低估）：全样本 {np.median(A['c2'])*100-2:.2f}%"
          f"｜OOS2 {seg.get('OOS2 2022-2026',{}).get('c2',0)*100-2:.2f}%")
    verdict = "SP4_PASS" if (R1 and R2 and R3 and R5) else ("SP4_PASS_DD_BREACH" if (R1 and R2 and R3) else "SP4_TARGET_MISSED")
    print(f"\n【判定】{verdict}")

    os.makedirs(OUT,exist_ok=True)
    json.dump({"prereg_sha256":open(os.path.join(RD,"sp4_prereg.sha256"),encoding="utf-8").read().split()[0],
               "months":len(months),"n_paths":len(A["c2"]),
               "c2":{"ann":float(np.median(A["c2"])),"p5":float(np.percentile(A["c2"],5)),
                     "p95":float(np.percentile(A["c2"],95)),"mdd":float(np.median(M["c2"])),"vol":float(np.median(V["c2"]))},
               "c1":{"ann":float(np.median(A["c1"])),"mdd":float(np.median(M["c1"]))},
               "c0":{"ann":float(np.median(A["c0"])),"mdd":float(np.median(M["c0"]))},
               "band":{"ann":ba,"mdd":bm,"vol":bv},
               "turnover":float(np.median(NS)),
               "segments":seg,
               "gates":{"R1":bool(R1),"R2":bool(R2),"R3":bool(R3),"R4":bool(R4),"R5":bool(R5)},
               "verdict":verdict},open(os.path.join(OUT,"sp4_result.json"),"w",encoding="utf-8"),
              ensure_ascii=False,indent=1)
    print("saved out/sp4_result.json")

if __name__=="__main__":
    main()
