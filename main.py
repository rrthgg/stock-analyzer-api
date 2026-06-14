"""
Stock Analyzer API — Alpha Vantage (cours) + FMP (fondamentaux)
Alpha Vantage : cours temps réel, RSI, moyennes mobiles
FMP           : PER, marges, ROE, consensus analystes, objectif de cours
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests
import math
import os

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AV_KEY  = os.environ.get("AV_KEY",  "VOTRE_CLE_ALPHAVANTAGE")
FMP_KEY = os.environ.get("FMP_KEY", "VOTRE_CLE_FMP")

AV_BASE  = "https://www.alphavantage.co/query"
FMP_BASE = "https://financialmodelingprep.com/api/v3"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockAnalyzer/1.0)",
    "Accept": "application/json",
}

def safe(v, default=None):
    if v is None or v == "None" or v == "-" or v == "" or v == "N/A":
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default

def pct(v):
    f = safe(v)
    return round(f * 100, 2) if f is not None else None

def consensus_from_score(score):
    if score is None: return "Hold"
    s = float(score)
    if s <= 1.5: return "Strong Buy"
    if s <= 2.5: return "Buy"
    if s <= 3.5: return "Hold"
    return "Reduce"

def consensus_from_fmp(label):
    if not label: return "Hold"
    l = label.lower()
    if "strong buy" in l or "strongbuy" in l: return "Strong Buy"
    if "buy" in l:                             return "Buy"
    if "hold" in l or "neutral" in l:          return "Hold"
    return "Reduce"

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Analyzer API en ligne"}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()

    # ─────────────────────────────────────────────
    # 1. COURS — Alpha Vantage (fiable, pas de blocage)
    # ─────────────────────────────────────────────
    try:
        r_quote = requests.get(AV_BASE, params={
            "function": "GLOBAL_QUOTE",
            "symbol":   ticker,
            "apikey":   AV_KEY,
        }, headers=HEADERS, timeout=15)
        q = r_quote.json().get("Global Quote", {})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Erreur Alpha Vantage : {str(e)}")

    price = safe(q.get("05. price"))
    if not price:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{ticker}' introuvable sur Alpha Vantage. "
                   f"Pour les actions EU utilisez : MC.PAR, AIR.PAR, ASML.AMS, SAP.FRK, TTE.PAR, BNP.PAR"
        )

    hi52_av = safe(q.get("03. high"))
    lo52_av = safe(q.get("04. low"))

    # ─────────────────────────────────────────────
    # 2. HISTORIQUE — Alpha Vantage (MM50, MM200, RSI)
    # ─────────────────────────────────────────────
    closes = []
    try:
        r_hist = requests.get(AV_BASE, params={
            "function":   "TIME_SERIES_DAILY",
            "symbol":     ticker,
            "outputsize": "compact",
            "apikey":     AV_KEY,
        }, headers=HEADERS, timeout=15)
        series = r_hist.json().get("Time Series (Daily)", {})
        closes = [float(v["4. close"]) for v in list(series.values())[:200]]
    except Exception:
        closes = []

    ma200 = round(sum(closes) / len(closes), 2)        if len(closes) >= 100 else None
    ma50  = round(sum(closes[:50]) / 50, 2)            if len(closes) >= 50  else None
    ma200_signal = 1 if (price and ma200 and price > ma200) else -1
    ma50_signal  = 1 if (price and ma50  and price > ma50)  else -1

    rsi = None
    if len(closes) >= 15:
        try:
            deltas = [closes[i] - closes[i+1] for i in range(14)]
            gains  = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            avg_g  = sum(gains) / 14
            avg_l  = sum(losses) / 14
            if avg_l > 0:
                rsi = round(100 - 100 / (1 + avg_g / avg_l), 1)
        except Exception:
            rsi = None

    # ─────────────────────────────────────────────
    # 3. PROFIL SOCIÉTÉ — FMP
    # ─────────────────────────────────────────────
    name = ticker; sector = "—"; industry = "—"; country = "—"
    currency = "USD"; exchange = "—"; summary = ""; website = None
    market_cap = None; beta_fmp = None
    hi52 = hi52_av; lo52 = lo52_av

    try:
        r_profile = requests.get(
            f"{FMP_BASE}/profile/{ticker}",
            params={"apikey": FMP_KEY},
            headers=HEADERS, timeout=15
        )
        profiles = r_profile.json()
        if profiles and isinstance(profiles, list) and len(profiles) > 0:
            p = profiles[0]
            name       = p.get("companyName", ticker)
            sector     = p.get("sector", "—") or "—"
            industry   = p.get("industry", "—") or "—"
            country    = p.get("country", "—") or "—"
            currency   = p.get("currency", "USD") or "USD"
            exchange   = p.get("exchangeShortName", "—") or "—"
            summary    = (p.get("description") or "")[:400]
            website    = p.get("website")
            market_cap = safe(p.get("mktCap"))
            beta_fmp   = safe(p.get("beta"))
            hi52       = safe(p.get("range", "").split("-")[1]) if p.get("range") and "-" in str(p.get("range")) else hi52_av
            lo52       = safe(p.get("range", "").split("-")[0]) if p.get("range") and "-" in str(p.get("range")) else lo52_av
    except Exception:
        pass

    # ─────────────────────────────────────────────
    # 4. RATIOS FINANCIERS — FMP
    # ─────────────────────────────────────────────
    pe = forward_pe = pb = ps = ev_ebitda = peg = None
    roe = roa = net_margin = gross_margin = op_margin = None
    rev_growth = eps_growth = debt_eq = current_ratio = fcf_yield = None
    div_rate = div_yield = payout = None
    insider = institution = None

    try:
        r_ratios = requests.get(
            f"{FMP_BASE}/ratios-ttm/{ticker}",
            params={"apikey": FMP_KEY},
            headers=HEADERS, timeout=15
        )
        ratios_data = r_ratios.json()
        if ratios_data and isinstance(ratios_data, list):
            r = ratios_data[0]
            pe            = safe(r.get("peRatioTTM"))
            pb            = safe(r.get("priceToBookRatioTTM"))
            ps            = safe(r.get("priceToSalesRatioTTM"))
            ev_ebitda     = safe(r.get("enterpriseValueOverEBITDATTM"))
            peg           = safe(r.get("priceEarningsToGrowthRatioTTM"))
            roe           = round(safe(r.get("returnOnEquityTTM"), 0) * 100, 2)
            roa           = round(safe(r.get("returnOnAssetsTTM"), 0) * 100, 2)
            net_margin    = round(safe(r.get("netProfitMarginTTM"), 0) * 100, 2)
            gross_margin  = round(safe(r.get("grossProfitMarginTTM"), 0) * 100, 2)
            op_margin     = round(safe(r.get("operatingProfitMarginTTM"), 0) * 100, 2)
            debt_eq       = safe(r.get("debtEquityRatioTTM"))
            current_ratio = safe(r.get("currentRatioTTM"))
            div_yield     = round(safe(r.get("dividendYielTTM"), 0) * 100, 2)
            payout        = round(safe(r.get("payoutRatioTTM"), 0) * 100, 2)
            fcf_raw       = safe(r.get("freeCashFlowPerShareTTM"))
    except Exception:
        pass

    # Croissance — FMP income statement growth
    try:
        r_growth = requests.get(
            f"{FMP_BASE}/income-statement-growth/{ticker}",
            params={"limit": 1, "apikey": FMP_KEY},
            headers=HEADERS, timeout=15
        )
        growth_data = r_growth.json()
        if growth_data and isinstance(growth_data, list):
            g = growth_data[0]
            rev_growth = round(safe(g.get("growthRevenue"), 0) * 100, 2)
            eps_growth = round(safe(g.get("growthEPS"), 0) * 100, 2)
    except Exception:
        pass

    # Dividende
    try:
        r_div = requests.get(
            f"{FMP_BASE}/stock_dividend_history/{ticker}",
            params={"apikey": FMP_KEY},
            headers=HEADERS, timeout=10
        )
        divs = r_div.json()
        if divs and isinstance(divs, list) and len(divs) > 0:
            div_rate = round(sum(safe(d.get("dividend"), 0) for d in divs[:4]), 2)
    except Exception:
        pass

    # Actionnariat
    try:
        r_inst = requests.get(
            f"{FMP_BASE}/institutional-holder/{ticker}",
            params={"apikey": FMP_KEY},
            headers=HEADERS, timeout=10
        )
        inst_data = r_inst.json()
        if inst_data and isinstance(inst_data, list):
            total_shares = sum(safe(h.get("shares"), 0) for h in inst_data[:20])
            if market_cap and price and total_shares:
                institution = round(total_shares * price / market_cap * 100, 1)
    except Exception:
        pass

    # ─────────────────────────────────────────────
    # 5. CONSENSUS ANALYSTES — FMP
    # ─────────────────────────────────────────────
    cons_note = "Hold"; num_ana = None; tgt = None; tgt_h = None; tgt_l = None

    try:
        r_price_tgt = requests.get(
            f"{FMP_BASE}/price-target-consensus/{ticker}",
            params={"apikey": FMP_KEY},
            headers=HEADERS, timeout=15
        )
        pt = r_price_tgt.json()
        if pt and isinstance(pt, list) and len(pt) > 0:
            tgt   = safe(pt[0].get("targetConsensus"))
            tgt_h = safe(pt[0].get("targetHigh"))
            tgt_l = safe(pt[0].get("targetLow"))
    except Exception:
        pass

    try:
        r_rating = requests.get(
            f"{FMP_BASE}/analyst-stock-recommendations/{ticker}",
            params={"limit": 10, "apikey": FMP_KEY},
            headers=HEADERS, timeout=15
        )
        ratings = r_rating.json()
        if ratings and isinstance(ratings, list):
            buy_count  = sum(1 for r in ratings if "buy" in str(r.get("newGrade","")).lower())
            hold_count = sum(1 for r in ratings if "hold" in str(r.get("newGrade","")).lower() or "neutral" in str(r.get("newGrade","")).lower())
            sell_count = sum(1 for r in ratings if "sell" in str(r.get("newGrade","")).lower() or "reduce" in str(r.get("newGrade","")).lower())
            num_ana    = len(ratings)
            total      = buy_count + hold_count + sell_count
            if total > 0:
                score = (buy_count * 1.5 + hold_count * 3 + sell_count * 4.5) / total
                cons_note = consensus_from_score(score)
    except Exception:
        pass

    upside = round((tgt / price - 1) * 100, 2) if tgt and price and price > 0 else None

    # Forward PE via FMP key metrics
    try:
        r_km = requests.get(
            f"{FMP_BASE}/key-metrics-ttm/{ticker}",
            params={"apikey": FMP_KEY},
            headers=HEADERS, timeout=15
        )
        km = r_km.json()
        if km and isinstance(km, list):
            forward_pe  = safe(km[0].get("peRatioTTM")) if not pe else forward_pe
            market_cap  = safe(km[0].get("marketCapTTM")) or market_cap
            ev_ebitda   = safe(km[0].get("evToEbitdaTTM")) or ev_ebitda
            fcf_yield   = round(safe(km[0].get("freeCashFlowYieldTTM"), 0) * 100, 2)
    except Exception:
        pass

    return JSONResponse(content={
        "ticker":               ticker,
        "name":                 name,
        "sector":               sector,
        "industry":             industry,
        "country":              country,
        "currency":             currency,
        "exchange":             exchange,
        "summary":              summary,
        "website":              website,
        "price":                round(price, 2),
        "price52wHigh":         hi52,
        "price52wLow":          lo52,
        "targetMean":           tgt,
        "targetHigh":           tgt_h,
        "targetLow":            tgt_l,
        "upside":               upside,
        "pe":                   pe,
        "forwardPE":            forward_pe,
        "pb":                   pb,
        "ps":                   ps,
        "evEbitda":             ev_ebitda,
        "peg":                  peg,
        "marketCap":            market_cap,
        "roe":                  roe,
        "roa":                  roa,
        "netMargin":            net_margin,
        "grossMargin":          gross_margin,
        "opMargin":             op_margin,
        "revGrowth":            rev_growth,
        "epsGrowth":            eps_growth,
        "fcfYield":             fcf_yield,
        "debtEquity":           debt_eq,
        "currentRatio":         current_ratio,
        "cashPerShare":         None,
        "dividendRate":         div_rate,
        "dividendYield":        div_yield,
        "payoutRatio":          payout,
        "beta":                 beta_fmp,
        "rsi":                  rsi,
        "ma200":                ma200,
        "ma50":                 ma50,
        "ma200Signal":          ma200_signal,
        "ma50Signal":           ma50_signal,
        "avgVolume":            None,
        "shortRatio":           None,
        "consensusNote":        cons_note,
        "numAnalysts":          num_ana,
        "recommendationKey":    cons_note.lower().replace(" ", ""),
        "insiderOwnership":     insider,
        "institutionOwnership": institution,
        "peers":                [],
    })
