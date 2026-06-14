"""
Stock Analyzer API — Yahoo Finance direct (pas de limite, pas de clé)
Contourne le blocage Render via session + cookies Yahoo authentifiés
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests, math, time

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session persistante avec cookies Yahoo
_session = None

def get_session():
    global _session
    if _session is not None:
        return _session
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://finance.yahoo.com",
        "Origin": "https://finance.yahoo.com",
    })
    # Obtenir un cookie crumb valide
    try:
        s.get("https://finance.yahoo.com", timeout=10)
        time.sleep(0.5)
        crumb_r = s.get("https://query1.finance.yahoo.com/v1/test/csrfToken", timeout=10)
        crumb = crumb_r.json().get("crumb", "")
        s.headers.update({"crumb": crumb})
        s._crumb = crumb
    except Exception:
        s._crumb = ""
    _session = s
    return s

def yf_get(url, params={}):
    s = get_session()
    if hasattr(s, "_crumb") and s._crumb:
        params = {**params, "crumb": s._crumb}
    try:
        r = s.get(url, params=params, timeout=15)
        if r.status_code == 401 or r.status_code == 403:
            # Session expirée — on recrée
            global _session
            _session = None
            s = get_session()
            r = s.get(url, params={**params, "crumb": s._crumb}, timeout=15)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def safe(v, default=None):
    if v is None or str(v).strip() in ("", "None", "-", "N/A"):
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return v if default is None else default

def pct(v):
    f = safe(v)
    if f is None: return None
    try: return round(float(f) * 100, 2)
    except: return None

def consensus_label(key):
    if not key: return "Hold"
    k = str(key).lower().replace(" ", "")
    if k in ("strongbuy",): return "Strong Buy"
    if k in ("buy",):       return "Buy"
    if k in ("hold", "neutral", "marketperform", "sectorperform"): return "Hold"
    return "Reduce"

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Analyzer API en ligne"}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()

    # ── 1. CHART (cours + historique) ─────────────
    chart = yf_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        {"interval": "1d", "range": "1y", "includePrePost": "false"}
    )
    if not chart:
        chart = yf_get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
            {"interval": "1d", "range": "1y"}
        )
    if not chart:
        raise HTTPException(status_code=404, detail=f"Impossible de récupérer '{ticker}' depuis Yahoo Finance.")

    result = (chart.get("chart") or {}).get("result") or []
    if not result:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' introuvable sur Yahoo Finance. EU : MC.PA · AIR.PA · ASML.AS · SAP.DE · TTE.PA")

    meta   = result[0].get("meta", {})
    price  = safe(meta.get("regularMarketPrice") or meta.get("previousClose"))
    if not price:
        raise HTTPException(status_code=404, detail=f"Aucun cours disponible pour '{ticker}'.")

    currency = meta.get("currency", "USD")
    exchange = meta.get("exchangeName", "—")

    closes_raw = (result[0].get("indicators") or {}).get("quote", [{}])[0].get("close", [])
    closes = [c for c in closes_raw if c is not None]

    ma200 = round(sum(closes[-200:])/len(closes[-200:]), 2) if len(closes) >= 200 else (round(sum(closes)/len(closes), 2) if len(closes) >= 20 else None)
    ma50  = round(sum(closes[-50:])/len(closes[-50:]),   2) if len(closes) >= 50  else None
    ma200_sig = 1 if (price and ma200 and price > ma200) else -1
    ma50_sig  = 1 if (price and ma50  and price > ma50)  else -1

    rsi = None
    if len(closes) >= 15:
        try:
            deltas = [closes[i+1]-closes[i] for i in range(-15, -1)]
            ag = sum(d for d in deltas if d > 0) / 14
            al = sum(-d for d in deltas if d < 0) / 14
            rsi = round(100 - 100/(1 + ag/al), 1) if al > 0 else 100.0
        except Exception:
            rsi = None

    # ── 2. QUOTE SUMMARY (tous les fondamentaux) ──
    modules = "summaryDetail,defaultKeyStatistics,financialData,recommendationTrend,assetProfile,price"
    qs = yf_get(
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
        {"modules": modules, "corsDomain": "finance.yahoo.com"}
    )
    if not qs:
        qs = yf_get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
            {"modules": modules}
        )

    def g(d, k):
        """Extrait une valeur brute d'un dict Yahoo (format {raw, fmt})."""
        if not d: return None
        val = d.get(k)
        if isinstance(val, dict): return val.get("raw")
        return val

    sd={}; ks={}; fd={}; ap={}; pr={}; rt=[]
    if qs:
        qsr = (qs.get("quoteSummary") or {}).get("result") or []
        if qsr:
            data = qsr[0]
            sd = data.get("summaryDetail")    or {}
            ks = data.get("defaultKeyStatistics") or {}
            fd = data.get("financialData")    or {}
            ap = data.get("assetProfile")     or {}
            pr = data.get("price")            or {}
            rt = (data.get("recommendationTrend") or {}).get("trend") or []

    name      = pr.get("longName") or pr.get("shortName") or ap.get("longName") or ap.get("shortName") or ticker
    sector    = ap.get("sector")   or "—"
    industry  = ap.get("industry") or "—"
    country   = ap.get("country")  or "—"
    website   = ap.get("website")
    summary   = (ap.get("longBusinessSummary") or "")[:400]

    market_cap   = safe(g(pr,  "marketCap")                 or g(sd, "marketCap"))
    beta         = safe(g(sd,  "beta")                       or g(ks, "beta"))
    pe           = safe(g(sd,  "trailingPE"))
    forward_pe   = safe(g(sd,  "forwardPE"))
    pb           = safe(g(ks,  "priceToBook"))
    ps           = safe(g(sd,  "priceToSalesTrailing12Months"))
    ev_ebitda    = safe(g(ks,  "enterpriseToEbitda"))
    peg          = safe(g(ks,  "pegRatio"))
    roe          = pct(g(fd,   "returnOnEquity"))
    roa          = pct(g(fd,   "returnOnAssets"))
    net_margin   = pct(g(fd,   "profitMargins"))
    op_margin    = pct(g(fd,   "operatingMargins"))
    gross_margin = pct(g(fd,   "grossMargins"))
    rev_growth   = pct(g(fd,   "revenueGrowth"))
    eps_growth   = pct(g(fd,   "earningsGrowth"))
    debt_eq      = safe(g(fd,  "debtToEquity"))
    current_r    = safe(g(fd,  "currentRatio"))
    tgt          = safe(g(fd,  "targetMeanPrice"))
    tgt_h        = safe(g(fd,  "targetHighPrice"))
    tgt_l        = safe(g(fd,  "targetLowPrice"))
    num_ana      = safe(g(fd,  "numberOfAnalystOpinions"))
    cons_key     = fd.get("recommendationKey") or ""
    cons_note    = consensus_label(cons_key)
    hi52         = safe(g(sd,  "fiftyTwoWeekHigh"))
    lo52         = safe(g(sd,  "fiftyTwoWeekLow"))
    div_rate     = safe(g(sd,  "dividendRate"))
    div_yield    = pct(g(sd,   "dividendYield"))
    payout       = pct(g(sd,   "payoutRatio"))
    insider      = pct(g(ks,   "heldPercentInsiders"))
    institution  = pct(g(ks,   "heldPercentInstitutions"))

    fcf_yield = None
    fcf = safe(g(fd, "freeCashflow"))
    if fcf and market_cap and market_cap > 0:
        fcf_yield = round(fcf / market_cap * 100, 2)

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
        "currentRatio":         current_r,
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
        "avgVolume":            safe(g(pr, "averageVolume")),
        "shortRatio":           safe(g(ks, "shortRatio")),
        "consensusNote":        cons_note,
        "numAnalysts":          int(num_ana) if num_ana else None,
        "recommendationKey":    cons_key,
        "insiderOwnership":     insider,
        "institutionOwnership": institution,
        "peers":                [],
    })
