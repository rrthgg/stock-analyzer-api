"""
Stock Analyzer API — Alpha Vantage (cours) + FMP v4 (fondamentaux)
Endpoints FMP mis à jour post-août 2025
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

AV_KEY  = os.environ.get("AV_KEY",  "")
FMP_KEY = os.environ.get("FMP_KEY", "")

AV_BASE  = "https://www.alphavantage.co/query"
FMP_BASE = "https://financialmodelingprep.com/stable"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockAnalyzer/1.0)"}

def safe(v, default=None):
    if v is None or v == "" or v == "N/A" or v == "-":
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default

def pct(v):
    f = safe(v)
    return round(f * 100, 2) if f is not None else None

def consensus_label(score):
    if score is None: return "Hold"
    s = float(score)
    if s <= 1.5: return "Strong Buy"
    if s <= 2.5: return "Buy"
    if s <= 3.5: return "Hold"
    return "Reduce"

def fmp_get(path, params={}):
    """Appel FMP avec gestion d'erreur."""
    try:
        r = requests.get(
            f"{FMP_BASE}/{path}",
            params={"apikey": FMP_KEY, **params},
            headers=HEADERS, timeout=15
        )
        data = r.json()
        if isinstance(data, dict) and "Error Message" in data:
            return None
        if isinstance(data, dict) and "message" in data:
            return None
        return data
    except Exception:
        return None

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Analyzer API en ligne"}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()

    # ── 1. COURS — Alpha Vantage ──────────────────
    try:
        r_q = requests.get(AV_BASE, params={
            "function": "GLOBAL_QUOTE",
            "symbol":   ticker,
            "apikey":   AV_KEY,
        }, headers=HEADERS, timeout=15)
        q = r_q.json().get("Global Quote", {})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Erreur réseau : {str(e)}")

    price = safe(q.get("05. price"))
    if not price:
        raise HTTPException(
            status_code=404,
            detail=f"'{ticker}' introuvable. EU : MC.PAR, AIR.PAR, ASML.AMS, SAP.FRK, TTE.PAR"
        )

    hi52_av = safe(q.get("03. high"))
    lo52_av = safe(q.get("04. low"))

    # ── 2. HISTORIQUE — Alpha Vantage (MM + RSI) ──
    closes = []
    try:
        r_h = requests.get(AV_BASE, params={
            "function":   "TIME_SERIES_DAILY",
            "symbol":     ticker,
            "outputsize": "compact",
            "apikey":     AV_KEY,
        }, headers=HEADERS, timeout=15)
        series = r_h.json().get("Time Series (Daily)", {})
        closes = [float(v["4. close"]) for v in list(series.values())[:200]]
    except Exception:
        closes = []

    ma200 = round(sum(closes)/len(closes), 2)     if len(closes) >= 100 else None
    ma50  = round(sum(closes[:50])/50, 2)          if len(closes) >= 50  else None
    ma200_signal = 1 if (price and ma200 and price > ma200) else -1
    ma50_signal  = 1 if (price and ma50  and price > ma50)  else -1

    rsi = None
    if len(closes) >= 15:
        try:
            deltas = [closes[i]-closes[i+1] for i in range(14)]
            gains  = [d if d>0 else 0 for d in deltas]
            losses = [-d if d<0 else 0 for d in deltas]
            ag, al = sum(gains)/14, sum(losses)/14
            if al > 0:
                rsi = round(100 - 100/(1 + ag/al), 1)
        except Exception:
            rsi = None

    # ── 3. PROFIL — FMP stable endpoint ───────────
    name=ticker; sector="—"; industry="—"; country="—"
    currency="USD"; exchange="—"; summary=""; website=None
    market_cap=None; beta=None; hi52=hi52_av; lo52=lo52_av

    profile = fmp_get(f"profile/{ticker}")
    if profile and isinstance(profile, list) and len(profile) > 0:
        p = profile[0]
        name       = p.get("companyName") or ticker
        sector     = p.get("sector")   or "—"
        industry   = p.get("industry") or "—"
        country    = p.get("country")  or "—"
        currency   = p.get("currency") or "USD"
        exchange   = p.get("exchangeShortName") or "—"
        summary    = (p.get("description") or "")[:400]
        website    = p.get("website")
        market_cap = safe(p.get("mktCap"))
        beta       = safe(p.get("beta"))
        price_fmp  = safe(p.get("price"))
        if price_fmp and not price:
            price = price_fmp
        rng = str(p.get("range") or "")
        if "-" in rng:
            parts = rng.split("-")
            lo52 = safe(parts[0]) or lo52_av
            hi52 = safe(parts[-1]) or hi52_av

    # ── 4. RATIOS — FMP stable ────────────────────
    pe=pb=ps=ev_ebitda=peg=forward_pe=None
    roe=roa=net_margin=gross_margin=op_margin=None
    rev_growth=eps_growth=debt_eq=current_ratio=None
    div_rate=div_yield=payout=fcf_yield=None
    insider=institution=None

    ratios = fmp_get(f"ratios/{ticker}", {"period": "annual", "limit": 1})
    if ratios and isinstance(ratios, list) and len(ratios) > 0:
        r = ratios[0]
        pe            = safe(r.get("priceEarningsRatio"))
        forward_pe    = safe(r.get("priceEarningsRatio"))
        pb            = safe(r.get("priceToBookRatio"))
        ps            = safe(r.get("priceToSalesRatio"))
        ev_ebitda     = safe(r.get("enterpriseValueMultiple"))
        peg           = safe(r.get("priceEarningsToGrowthRatio"))
        roe_r         = safe(r.get("returnOnEquity"))
        roa_r         = safe(r.get("returnOnAssets"))
        nm_r          = safe(r.get("netProfitMargin"))
        gm_r          = safe(r.get("grossProfitMargin"))
        om_r          = safe(r.get("operatingProfitMargin"))
        roe           = round(roe_r * 100, 2) if roe_r is not None else None
        roa           = round(roa_r * 100, 2) if roa_r is not None else None
        net_margin    = round(nm_r  * 100, 2) if nm_r  is not None else None
        gross_margin  = round(gm_r  * 100, 2) if gm_r  is not None else None
        op_margin     = round(om_r  * 100, 2) if om_r  is not None else None
        debt_eq       = safe(r.get("debtEquityRatio"))
        current_ratio = safe(r.get("currentRatio"))
        dy_r          = safe(r.get("dividendYield"))
        div_yield     = round(dy_r * 100, 2) if dy_r is not None else None
        pr_r          = safe(r.get("payoutRatio"))
        payout        = round(pr_r * 100, 2) if pr_r is not None else None

    # Croissance
    growth = fmp_get(f"income-statement-growth/{ticker}", {"limit": 1})
    if growth and isinstance(growth, list) and len(growth) > 0:
        g = growth[0]
        rg = safe(g.get("growthRevenue"))
        eg = safe(g.get("growthEPS"))
        rev_growth = round(rg * 100, 2) if rg is not None else None
        eps_growth = round(eg * 100, 2) if eg is not None else None

    # Key metrics (FCF yield, market cap affiné)
    km = fmp_get(f"key-metrics/{ticker}", {"limit": 1})
    if km and isinstance(km, list) and len(km) > 0:
        k = km[0]
        market_cap = safe(k.get("marketCap")) or market_cap
        fcy = safe(k.get("freeCashFlowYield"))
        fcf_yield = round(fcy * 100, 2) if fcy is not None else None
        div_rate  = safe(k.get("dividendPerShare")) or div_rate

    # ── 5. CONSENSUS — FMP stable ─────────────────
    cons_note="Hold"; num_ana=None; tgt=None; tgt_h=None; tgt_l=None

    pt = fmp_get(f"price-target-summary/{ticker}")
    if pt and isinstance(pt, list) and len(pt) > 0:
        tgt   = safe(pt[0].get("targetConsensus"))
        tgt_h = safe(pt[0].get("targetHigh"))
        tgt_l = safe(pt[0].get("targetLow"))
        num_ana = safe(pt[0].get("numberOfAnalysts"))

    ratings = fmp_get(f"analyst-stock-recommendations/{ticker}", {"limit": 20})
    if ratings and isinstance(ratings, list) and len(ratings) > 0:
        buy  = sum(1 for r in ratings if "buy" in str(r.get("newGrade","")).lower())
        hold = sum(1 for r in ratings if "hold" in str(r.get("newGrade","")).lower() or "neutral" in str(r.get("newGrade","")).lower())
        sell = sum(1 for r in ratings if "sell" in str(r.get("newGrade","")).lower() or "reduce" in str(r.get("newGrade","")).lower())
        total = buy + hold + sell
        num_ana = num_ana or total
        if total > 0:
            score = (buy*1.5 + hold*3 + sell*4.5) / total
            cons_note = consensus_label(score)

    upside = round((tgt/price - 1)*100, 2) if tgt and price and price > 0 else None

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
        "beta":                 beta,
        "rsi":                  rsi,
        "ma200":                ma200,
        "ma50":                 ma50,
        "ma200Signal":          ma200_signal,
        "ma50Signal":           ma50_signal,
        "avgVolume":            None,
        "shortRatio":           None,
        "consensusNote":        cons_note,
        "numAnalysts":          int(num_ana) if num_ana else None,
        "recommendationKey":    cons_note.lower().replace(" ", ""),
        "insiderOwnership":     insider,
        "institutionOwnership": institution,
        "peers":                [],
    })
