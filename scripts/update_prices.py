#!/usr/bin/env python3
"""每日更新股價：寫入 data/prices.json 並累積 data/history.json。

主來源：Yahoo Finance chart API（全球可用，上市用 .TW、上櫃用 .TWO）。
補漏：櫃買中心興櫃 API（僅台灣 IP 可用；海外會失敗但已容錯，不影響其他股票）。
之所以不用證交所/櫃買公開 API 當主來源：那些會擋海外 IP，GitHub Actions（美國）抓不到。"""
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=7d&interval=1d"
ESB_URL = "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics"  # 興櫃
HISTORY_KEEP = 90  # 每檔最多保留 90 個交易日
TZ = timezone(timedelta(hours=8))  # 台北


def fetch_json(url, retries=3):
    last_err = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # 憑證或網路問題時改用 curl 再試
            last_err = e
            try:
                out = subprocess.run(
                    ["curl", "-s", "--max-time", "60", "-A", "Mozilla/5.0", url],
                    capture_output=True, check=True,
                ).stdout
                return json.loads(out.decode("utf-8"))
            except Exception as e2:
                last_err = e2
                time.sleep(2)
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


def yahoo_quote(code):
    """回傳 {close, mavg, market, date} 或 None。上市試 .TW，上櫃試 .TWO。"""
    for suffix, market in ((".TW", "twse"), (".TWO", "tpex")):
        try:
            data = fetch_json(YF_URL.format(sym=code + suffix))
            res = (data.get("chart") or {}).get("result")
            if not res:
                continue
            res = res[0]
            meta = res.get("meta") or {}
            ts = res.get("timestamp") or []
            closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            close = date_epoch = None
            for t, c in zip(ts, closes):  # 取最後一個非 null 收盤
                if c is not None:
                    close, date_epoch = c, t
            if close is None:  # 退而求其次用即時價
                close = meta.get("regularMarketPrice")
                date_epoch = meta.get("regularMarketTime")
            if close is None or date_epoch is None:
                continue
            return {
                "close": round(float(close), 2),
                "mavg": None,
                "market": market,
                "date": datetime.fromtimestamp(date_epoch, TZ).strftime("%Y-%m-%d"),
            }
        except Exception:
            continue
    return None


def main():
    holdings = json.loads((DATA / "holdings.json").read_text("utf-8"))
    codes = [s["code"] for s in holdings["stocks"]]
    codes += [w["code"] for w in holdings.get("watchlist", [])]  # 想買觀察清單也一起抓價
    codes = list(dict.fromkeys(codes))  # 去重、保序（想買清單可能與持股重疊）

    quotes = {}
    trade_dates = set()

    # 主來源：Yahoo Finance（海外可用）
    for code in codes:
        q = yahoo_quote(code)
        if q:
            quotes[code] = q
            trade_dates.add(q["date"])
        time.sleep(0.25)  # 禮貌性間隔，避免被限流

    # 補漏：興櫃只有官方 API 有（僅台灣 IP 可成功，海外失敗則跳過）
    missing = [c for c in codes if c not in quotes]
    if missing:
        try:
            esb = {row["SecuritiesCompanyCode"]: row for row in fetch_json(ESB_URL)}
            for code in missing:
                row = esb.get(code)
                if not row:
                    continue
                close = to_float(row.get("LatestPrice")) or to_float(row.get("Average"))
                if close is None:
                    continue
                date = roc_to_iso(row["Date"])
                quotes[code] = {"close": close, "mavg": None, "market": "esb", "date": date}
                trade_dates.add(date)
        except Exception as e:
            print(f"興櫃補抓略過（多半是海外 IP 被擋）：{e}")

    if not quotes:
        raise SystemExit("所有來源都抓不到報價，中止（不覆蓋既有資料）")

    prices = {
        "updated": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "quotes": quotes,
    }
    (DATA / "prices.json").write_text(
        json.dumps(prices, ensure_ascii=False, indent=1), "utf-8"
    )

    # 累積歷史收盤價
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {}
    for code, q in quotes.items():
        rows = [r for r in history.get(code, []) if r[0] != q["date"]]
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
