#!/usr/bin/env python3
"""一次性回補歷史收盤價（近兩個月），供反彈判斷用。平常不需執行。"""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MONTHS = ["20260601", "20260701"]  # 要回補的月份（西元每月1日）


def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # 憑證問題改用 curl
        out = subprocess.run(
            ["curl", "-s", "--max-time", "60", url], capture_output=True, check=True
        ).stdout
        return json.loads(out.decode("utf-8"))


def roc_to_iso(roc):
    roc = roc.replace("/", "")
    y = int(roc[:3]) + 1911
    return f"{y}-{roc[3:5]}-{roc[5:7]}"


def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def twse_month(code, ym):
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={ym}&stockNo={code}&response=json"
    d = fetch_json(url)
    if d.get("stat") != "OK":
        return []
    out = []
    for row in d.get("data", []):
        close = to_float(row[6])
        if close:
            out.append([roc_to_iso(row[0]), close])
    return out


def tpex_month(code, ym):
    date = f"{ym[:4]}/{ym[4:6]}/{ym[6:]}"
    url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={code}&date={date}&response=json"
    d = fetch_json(url)
    out = []
    for table in d.get("tables", []):
        for row in table.get("data", []):
            close = to_float(row[6])
            if close:
                out.append([roc_to_iso(row[0]), close])
    return out


def main():
    prices = json.loads((DATA / "prices.json").read_text("utf-8"))
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {}

    for code, q in prices["quotes"].items():
        rows = {r[0]: r[1] for r in history.get(code, [])}
        for ym in MONTHS:
            try:
                month_rows = (
                    twse_month(code, ym) if q["market"] == "twse" else tpex_month(code, ym)
                )
                for d, c in month_rows:
                    rows[d] = c
            except Exception as e:
                print(f"  {code} {ym} 失敗：{e}")
            time.sleep(0.4)
        history[code] = sorted([[d, c] for d, c in rows.items()])[-90:]
        print(f"{code}: {len(history[code])} 筆")

    hist_path.write_text(json.dumps(history, ensure_ascii=False), "utf-8")
    print("回補完成")


if __name__ == "__main__":
    main()
