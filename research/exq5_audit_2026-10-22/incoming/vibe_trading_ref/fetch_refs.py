import json, base64, subprocess, sys, os

FILES = [
    "schema/tool_contracts/paper_trade_step-v1.1.json",
    "agent/src/tools/paper_trade/README.md",
    "agent/src/tools/paper_trade/rules.py",
    "agent/src/tools/paper_trade/limits_cn_a.py",
    "agent/src/tools/paper_trade/engine.py",
    "agent/src/tools/paper_trade/state_hash.py",
    "agent/src/tools/paper_trade/calendar.py",
    "agent/src/tools/paper_trade/corporate_actions.py",
    "agent/backtest/engines/china_a.py",
    "agent/backtest/run_card.py",
]
API = "https://api.github.com/repos/HKUDS/Vibe-Trading/contents/"
for path in FILES:
    url = API + path
    try:
        raw = subprocess.run(["curl", "-sL", "--max-time", "60", url],
                             capture_output=True, check=True).stdout
        obj = json.loads(raw)
        if "content" not in obj:
            print(f"MISS {path}: keys={list(obj)[:5]}")
            continue
        data = base64.b64decode(obj["content"])
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print(f"OK   {path}  {len(data)} bytes  sha={obj.get('sha','')[:12]}")
    except Exception as e:
        print(f"ERR  {path}: {type(e).__name__} {e}")
