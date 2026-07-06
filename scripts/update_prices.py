#!/usr/bin/env python3
"""每日更新股價：抓證交所/櫃買公開 API，寫入 data/prices.json 並累積 data/history.json。
資料來源：openapi.twse.com.tw、tpex.org.tw（皆為前一交易日/當日收盤資料）"""
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
ESB_URL = "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"  # 興櫃
HISTORY_KEEP = 90  # 每檔最多保留 90 個交易日


def fetch_json(url, retries=3):
    last_err = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 憑證或網路問題時改用 curl 再試
            last_err = e
            try:
                out = subprocess.run(
                    ["curl", "-s", "--max-time", "120", url],
                    capture_output=True, check=True,
                ).stdout
                return json.loads(out.decode("utf-8"))
            except Exception as e2:
                last_err = e2
                time.sleep(3)
    raise last_err


def roc_to_iso(roc):
    """1150703 或 115/07/03 → 2026-07-03"""
    roc = roc.replace("/", "")
    y = int(roc[:3]) + 1911
    return f"{y}-{roc[3:5]}-{roc[5:7]}"


def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def main():
    holdings = json.loads((DATA / "holdings.json").read_text("utf-8"))
    codes = [s["code"] for s in holdings["stocks"]]
    codes += [w["code"] for w in holdings.get("watchlist", [])]  # 想買觀察清單也一起抓價
    codes = list(dict.fromkeys(codes))  # 去重、保序（想買清單可能與持股重疊）

    twse = {row["Code"]: row for row in fetch_json(TWSE_URL)}
    tpex = {row["SecuritiesCompanyCode"]: row for row in fetch_json(TPEX_URL)}
    esb = {row["SecuritiesCompanyCode"]: row for row in fetch_json(ESB_URL)}

    quotes = {}
    trade_dates = set()
    for code in codes:
        if code in twse:
            row = twse[code]
            close = to_float(row.get("ClosingPrice"))
            if close is None:
                continue
            date = roc_to_iso(row["Date"])
            quotes[code] = {
                "close": close,
                "mavg": to_float(row.get("MonthlyAveragePrice")),
                "market": "twse",
                "date": date,
            }
            trade_dates.add(date)
        elif code in tpex:
            row = tpex[code]
            close = to_float(row.get("Close"))
            if close is None:
                continue
            date = roc_to_iso(row["Date"])
            quotes[code] = {
                "close": close,
                "mavg": None,
                "market": "tpex",
                "date": date,
            }
            trade_dates.add(date)
        elif code in esb:
            row = esb[code]
            close = to_float(row.get("LatestPrice")) or to_float(row.get("Average"))
            if close is None:
                continue
            date = roc_to_iso(row["Date"])
            quotes[code] = {
                "close": close,
                "mavg": to_float(row.get("PreviousAveragePrice")),
                "market": "esb",
                "date": date,
            }
            trade_dates.add(date)

    tz = timezone(timedelta(hours=8))
    prices = {
        "updated": datetime.now(tz).strftime("%Y-%m-%d %H:%M"),
        "quotes": quotes,
    }
    (DATA / "prices.json").write_text(
        json.dumps(prices, ensure_ascii=False, indent=1), "utf-8"
    )

    # 累積歷史收盤價
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {}
    for code, q in quotes.items():
        rows = history.setdefault(code, [])
        rows = [r for r in rows if r[0] != q["date"]]
        rows.append([q["date"], q["close"]])
        rows.sort(key=lambda r: r[0])
        history[code] = rows[-HISTORY_KEEP:]
    hist_path.write_text(json.dumps(history, ensure_ascii=False), "utf-8")

    missing = [c for c in codes if c not in quotes]
    print(f"更新 {len(quotes)} 檔，交易日 {sorted(trade_dates)}")
    if missing:
        print(f"查無報價（可能為興櫃或代號變更）：{missing}")


if __name__ == "__main__":
    main()
