from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import requests
import math
import os

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

AV_KEY = os.environ.get("AV_KEY", "VOTRE_CLE_ICI")
BASE   = "https://www.alphavantage.co/query"

def safe(v):
    if v is None or v == "None" or v == "-" or v == "":
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return None

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

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Analyzer API en ligne"}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()
    try:
        # Cours + métadonnées
        r1 = requests.get(BASE, params={
            "function": "GLOBAL_QUOTE",
            "symbol": ticker,
            "apikey": AV_KEY
        }, timeout=15)
        d1 = r1.json()
        q  = d1.get("Global Quote", {})
        if not q or not q.get("05. price"):
            raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' introuvable. Vérifiez le symbole (ex: AAPL, MC.PAR, SAP.FRK).")

        price = safe(q.get("05. price"))
        hi52  = safe(q.get("03. high"))
        lo52  = safe(q.get("04. low"))

        # Informations société
        r2 = requests.get(BASE, params={
            "function": "OVERVIEW",
            "symbol": ticker,
            "apikey": AV_KEY
        }, timeout=15)
        ov = r2.json()

        name     = ov.get("Name", ticker)
        sector   = ov.get("Sector", "—")
        industry = ov.get("Industry", "—")
        country  = ov.get("Country", "—")
        currency = ov.get("Currency", "USD")
        exchange = ov.get("Exchange", "—")
        summary  = (ov.get("Description") or "")[:400]

        pe          = safe(ov.get("TrailingPE"))
        forward_pe  = safe(ov.get("ForwardPE"))
        pb          = safe(ov.get("PriceToBookRatio"))
        ps          = safe(ov.get("PriceToSalesRatioTTM"))
        ev_ebitda   = safe(ov.get("EVToEBITDA"))
        peg         = safe(ov.get("PEGRatio"))
        market_cap  = safe(ov.get("MarketCapitalization"))
        beta        = safe(ov.get("Beta"))
        div_rate    = safe(ov.get("DividendPerShare"))
        div_yield   = safe(ov.get("DividendYield"))
        payout      = safe(ov.get("PayoutRatio"))
        roe         = pct(ov.get("ReturnOnEquityTTM"))
        roa         = pct(ov.get("ReturnOnAssetsTTM"))
        net_margin  = pct(ov.get("ProfitMargin"))
        op_margin   = pct(ov.get("OperatingMarginTTM"))
        rev_growth  = pct(ov.get("QuarterlyRevenueGrowthYOY"))
        eps_growth  = pct(ov.get("QuarterlyEarningsGrowthYOY"))
        debt_eq     = safe(ov.get("DebtToEquityRatio") or ov.get("BookValue"))
        tgt         = safe(ov.get("AnalystTargetPrice"))
        insider     = pct(ov.get("PercentInsiders"))
        institution = pct(ov.get("PercentInstitutions"))
        hi52_ov     = safe(ov.get("52WeekHigh"))
        lo52_ov     = safe(ov.get("52WeekLow"))
        analyst_n   = safe(ov.get("AnalystRatingStrongBuy"))

        # Consensus approximatif
        sb  = safe(ov.get("AnalystRatingStrongBuy"))  or 0
        b   = safe(ov.get("AnalystRatingBuy"))        or 0
        h   = safe(ov.get("AnalystRatingHold"))       or 0
        s   = safe(ov.get("AnalystRatingSell"))        or 0
        ss  = safe(ov.get("AnalystRatingStrongSell")) or 0
        total_ana = sb + b + h + s + ss
        cons_note = "Hold"
        num_ana   = int(total_ana) if total_ana else None
        if total_ana > 0:
            score = (sb*1 + b*2 + h*3 + s*4 + ss*5) / total_ana
            cons_note = consensus_label(score)

        upside = round((tgt / price - 1) * 100, 2) if tgt and price and price > 0 else None

        # Historique 200 jours pour MM
        r3 = requests.get(BASE, params={
            "function": "TIME_SERIES_DAILY",
            "symbol": ticker,
            "outputsize": "compact",
            "apikey": AV_KEY
        }, timeout=15)
        d3     = r3.json()
        series = d3.get("Time Series (Daily)", {})
        closes = [float(v["4. close"]) for v in list(series.values())[:200]]

        ma200 = round(sum(closes) / len(closes), 2)       if len(closes) >= 100 else None
        ma50  = round(sum(closes[:50]) / 50, 2)           if len(closes) >= 50  else None
        ma200_signal = 1 if (price and ma200 and price > ma200) else -1
        ma50_signal  = 1 if (price and ma50  and price > ma50)  else -1

        rsi = None
        if len(closes) >= 15:
            deltas = [closes[i] - closes[i+1] for i in range(14)]
            gains  = [d if d > 0 else 0 for d in deltas]
            losses = [-d if d < 0 else 0 for d in deltas]
            avg_g  = sum(gains) / 14
            avg_l  = sum(losses) / 14
            if avg_l > 0:
                rsi = round(100 - 100 / (1 + avg_g / avg_l), 1)

        fcf_yield = None
        if market_cap and market_cap > 0:
            fcf_raw = safe(ov.get("OperatingCashflowTTM"))
            capex   = safe(ov.get("CapitalExpenditures"))
            if fcf_raw and capex:
                fcf = fcf_raw - abs(capex)
                fcf_yield = round(fcf / market_cap * 100, 2)

        return JSONResponse(content={
            "ticker":               ticker,
            "name":                 name,
            "sector":               sector,
            "industry":             industry,
            "country":              country,
            "currency":             currency,
            "exchange":             exchange,
            "summary":              summary,
            "price":                round(price, 2) if price else None,
            "price52wHigh":         hi52_ov or hi52,
            "price52wLow":          lo52_ov or lo52,
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
            "dividendYield":        round(float(div_yield)*100, 2) if div_yield else None,
            "payoutRatio":          round(float(payout)*100, 2) if payout else None,
            "beta":                 beta,
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")
