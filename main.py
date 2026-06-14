"""
Stock Analyzer API — Alpha Vantage uniquement
GLOBAL_QUOTE  : cours temps réel
OVERVIEW      : tous les fondamentaux (PER, marges, ROE, consensus, objectif)
TIME_SERIES_DAILY : historique pour MM50, MM200, RSI
= 3 appels par ticker, plan gratuit 25/jour
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests, math, os

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AV_KEY = os.environ.get("AV_KEY", "")
BASE   = "https://www.alphavantage.co/query"
HDR    = {"User-Agent": "Mozilla/5.0 (compatible; StockAnalyzer/1.0)"}

def safe(v, default=None):
    if v is None or str(v).strip() in ("", "None", "-", "N/A", "0.0000"):
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default

def pct(v):
    f = safe(v)
    return round(f * 100, 2) if f is not None else None

def av(function, symbol, extra={}):
    try:
        r = requests.get(BASE, params={"function": function, "symbol": symbol, "apikey": AV_KEY, **extra}, headers=HDR, timeout=15)
        d = r.json()
        if "Information" in d or "Note" in d:
            return None  # rate limit atteint
        return d
    except Exception:
        return None

def consensus_from_av(ov):
    """Reconstruit le consensus depuis les comptages analystes AV."""
    sb  = safe(ov.get("AnalystRatingStrongBuy"),  0)
    b   = safe(ov.get("AnalystRatingBuy"),         0)
    h   = safe(ov.get("AnalystRatingHold"),        0)
    s   = safe(ov.get("AnalystRatingSell"),         0)
    ss  = safe(ov.get("AnalystRatingStrongSell"),  0)
    total = (sb or 0) + (b or 0) + (h or 0) + (s or 0) + (ss or 0)
    if total == 0:
        return "Hold", None
    score = ((sb or 0)*1 + (b or 0)*2 + (h or 0)*3 + (s or 0)*4 + (ss or 0)*5) / total
    if score <= 1.5: label = "Strong Buy"
    elif score <= 2.5: label = "Buy"
    elif score <= 3.5: label = "Hold"
    else: label = "Reduce"
    return label, int(total)

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Analyzer API en ligne"}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()

    # ── 1. COURS ──────────────────────────────────
    q_data = av("GLOBAL_QUOTE", ticker)
    if not q_data:
        raise HTTPException(status_code=503, detail="Limite Alpha Vantage atteinte (25/jour). Réessayez demain ou utilisez une 2ème clé.")
    q = q_data.get("Global Quote", {})
    price = safe(q.get("05. price"))
    if not price:
        raise HTTPException(status_code=404, detail=f"'{ticker}' introuvable. Exemples EU : MC.PAR · AIR.PAR · ASML.AMS · SAP.FRK · TTE.PAR · BNP.PAR")

    hi52_q = safe(q.get("03. high"))
    lo52_q = safe(q.get("04. low"))
    vol    = safe(q.get("06. volume"))

    # ── 2. FONDAMENTAUX (OVERVIEW) ────────────────
    ov = av("OVERVIEW", ticker) or {}

    name         = ov.get("Name") or ticker
    sector       = ov.get("Sector")   or "—"
    industry     = ov.get("Industry") or "—"
    country      = ov.get("Country")  or "—"
    currency     = ov.get("Currency") or "USD"
    exchange     = ov.get("Exchange") or "—"
    summary      = (ov.get("Description") or "")[:400]
    market_cap   = safe(ov.get("MarketCapitalization"))
    beta         = safe(ov.get("Beta"))
    pe           = safe(ov.get("TrailingPE"))
    forward_pe   = safe(ov.get("ForwardPE"))
    pb           = safe(ov.get("PriceToBookRatio"))
    ps           = safe(ov.get("PriceToSalesRatioTTM"))
    ev_ebitda    = safe(ov.get("EVToEBITDA"))
    peg          = safe(ov.get("PEGRatio"))
    tgt          = safe(ov.get("AnalystTargetPrice"))
    hi52         = safe(ov.get("52WeekHigh"))  or hi52_q
    lo52         = safe(ov.get("52WeekLow"))   or lo52_q
    roe          = pct(ov.get("ReturnOnEquityTTM"))
    roa          = pct(ov.get("ReturnOnAssetsTTM"))
    net_margin   = pct(ov.get("ProfitMargin"))
    op_margin    = pct(ov.get("OperatingMarginTTM"))
    rev_growth   = pct(ov.get("QuarterlyRevenueGrowthYOY"))
    eps_growth   = pct(ov.get("QuarterlyEarningsGrowthYOY"))
    debt_eq      = safe(ov.get("DebtToEquityRatio"))
    div_rate     = safe(ov.get("DividendPerShare"))
    div_yield_r  = safe(ov.get("DividendYield"))
    div_yield    = round(div_yield_r * 100, 2) if div_yield_r else None
    payout_r     = safe(ov.get("PayoutRatio"))
    payout       = round(payout_r * 100, 2) if payout_r else None
    insider      = pct(ov.get("PercentInsiders"))
    institution  = pct(ov.get("PercentInstitutions"))

    cons_note, num_ana = consensus_from_av(ov)
    upside = round((tgt/price - 1)*100, 2) if tgt and price and price > 0 else None

    # FCF yield approx
    fcf_yield = None
    fcf = safe(ov.get("OperatingCashflowTTM"))
    capex = safe(ov.get("CapitalExpenditures"))
    if fcf and capex and market_cap and market_cap > 0:
        fcf_yield = round((fcf - abs(capex)) / market_cap * 100, 2)

    # ── 3. HISTORIQUE → MM50, MM200, RSI ─────────
    closes = []
    h_data = av("TIME_SERIES_DAILY", ticker, {"outputsize": "compact"})
    if h_data:
        series = h_data.get("Time Series (Daily)", {})
        closes = [float(v["4. close"]) for v in list(series.values())[:200] if v.get("4. close")]

    ma200 = round(sum(closes)/len(closes), 2)   if len(closes) >= 100 else None
    ma50  = round(sum(closes[:50])/50, 2)        if len(closes) >= 50  else None
    ma200_sig = 1 if (price and ma200 and price > ma200) else -1
    ma50_sig  = 1 if (price and ma50  and price > ma50)  else -1

    rsi = None
    if len(closes) >= 15:
        try:
            deltas = [closes[i]-closes[i+1] for i in range(14)]
            ag = sum(d for d in deltas if d > 0) / 14
            al = sum(-d for d in deltas if d < 0) / 14
            if al > 0:
                rsi = round(100 - 100/(1 + ag/al), 1)
        except Exception:
            rsi = None

    return JSONResponse(content={
        "ticker":               ticker,
        "name":                 name,
        "sector":               sector,
        "industry":             industry,
        "country":              country,
        "currency":             currency,
        "exchange":             exchange,
        "summary":              summary,
        "website":              None,
        "price":                round(price, 2),
        "price52wHigh":         hi52,
        "price52wLow":          lo52,
        "targetMean":           tgt,
        "targetHigh":           None,
        "targetLow":            None,
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
        "grossMargin":          None,
        "opMargin":             op_margin,
        "revGrowth":            rev_growth,
        "epsGrowth":            eps_growth,
        "fcfYield":             fcf_yield,
        "debtEquity":           debt_eq,
        "currentRatio":         None,
        "cashPerShare":         None,
        "dividendRate":         div_rate,
        "dividendYield":        div_yield,
        "payoutRatio":          payout,
        "beta":                 beta,
        "rsi":                  rsi,
        "ma200":                ma200,
        "ma50":                 ma50,
        "ma200Signal":          ma200_sig,
        "ma50Signal":           ma50_sig,
        "avgVolume":            vol,
        "shortRatio":           None,
        "consensusNote":        cons_note,
        "numAnalysts":          num_ana,
        "recommendationKey":    cons_note.lower().replace(" ", ""),
        "insiderOwnership":     insider,
        "institutionOwnership": institution,
        "peers":                [],
    })
