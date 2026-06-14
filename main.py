"""
Stock Analyzer API - Backend FastAPI
Déployable sur Render.com ou Railway.app (gratuit)
Données via yfinance (Yahoo Finance, délai ~15 min)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import yfinance as yf
import math

app = FastAPI(title="Stock Analyzer API", version="1.0.0")

# CORS : autorise tous les domaines (à restreindre en production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

def safe(v, default=None):
    """Retourne None si la valeur est NaN ou infinie."""
    if v is None:
        return default
    try:
        if math.isnan(v) or math.isinf(v):
            return default
    except (TypeError, ValueError):
        pass
    return v

def pct(v):
    """Convertit un ratio décimal en pourcentage."""
    if v is None:
        return None
    try:
        if math.isnan(v):
            return None
        return round(v * 100, 2)
    except (TypeError, ValueError):
        return None

def consensus_label(key):
    """Traduit la clé de recommandation Yahoo en label français."""
    mapping = {
        "strongBuy": "Strong Buy",
        "buy": "Buy",
        "hold": "Hold",
        "sell": "Reduce",
        "strongSell": "Reduce",
    }
    return mapping.get(str(key).lower(), "Hold") if key else "Hold"

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Analyzer API en ligne"}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    """
    Retourne les données fondamentales, techniques et consensus d'un ticker.
    Compatible US (AAPL, NVDA…) et EU (MC.PA, AIR.PA, ASML.AS…)
    """
    ticker = ticker.upper().strip()
    try:
        t = yf.Ticker(ticker)
        info = t.info

        # Vérification que le ticker existe
        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' introuvable ou non supporté.")

        price = safe(info.get("currentPrice") or info.get("regularMarketPrice"))
        tgt   = safe(info.get("targetMeanPrice"))
        tgt_h = safe(info.get("targetHighPrice"))
        tgt_l = safe(info.get("targetLowPrice"))

        upside = None
        if price and tgt and price > 0:
            upside = round((tgt / price - 1) * 100, 2)

        # Historique 1 an pour MM200 / MM50
        hist = t.history(period="1y")
        ma200 = ma50 = None
        if not hist.empty and "Close" in hist.columns:
            closes = hist["Close"]
            if len(closes) >= 200:
                ma200 = round(float(closes.tail(200).mean()), 2)
            elif len(closes) >= 2:
                ma200 = round(float(closes.mean()), 2)
            if len(closes) >= 50:
                ma50 = round(float(closes.tail(50).mean()), 2)
            elif len(closes) >= 2:
                ma50 = round(float(closes.mean()), 2)

        ma200_signal = 1 if (price and ma200 and price > ma200) else -1
        ma50_signal  = 1 if (price and ma50  and price > ma50)  else -1

        # RSI simplifié sur 14 jours
        rsi = None
        if not hist.empty and len(hist) >= 15:
            delta = hist["Close"].diff()
            gains = delta.clip(lower=0).tail(14)
            losses = (-delta.clip(upper=0)).tail(14)
            avg_gain = gains.mean()
            avg_loss = losses.mean()
            if avg_loss and avg_loss != 0:
                rs = avg_gain / avg_loss
                rsi = round(100 - (100 / (1 + rs)), 1)

        # Récupération des recommandations analystes
        try:
            recs = t.recommendations
            if recs is not None and not recs.empty:
                recent = recs.tail(1)
                cons_note = consensus_label(recent.get("To Grade", recent.get("Action", pd.Series(["hold"]))).iloc[0])
            else:
                cons_note = consensus_label(info.get("recommendationKey"))
        except Exception:
            cons_note = consensus_label(info.get("recommendationKey"))

        # Données peers (limité — Yahoo ne fournit pas nativement les peers)
        peers_raw = []
        try:
            peers_raw = info.get("industryPeers", []) or []
        except Exception:
            peers_raw = []

        result = {
            # Identification
            "ticker":    ticker,
            "name":      info.get("longName") or info.get("shortName", ticker),
            "sector":    info.get("sector", "—"),
            "industry":  info.get("industry", "—"),
            "country":   info.get("country", "—"),
            "currency":  info.get("currency", "USD"),
            "exchange":  info.get("exchange", "—"),
            "website":   info.get("website"),
            "summary":   (info.get("longBusinessSummary") or "")[:400],

            # Prix & objectif
            "price":       round(price, 2) if price else None,
            "price52wHigh": safe(info.get("fiftyTwoWeekHigh")),
            "price52wLow":  safe(info.get("fiftyTwoWeekLow")),
            "targetMean":  round(tgt, 2) if tgt else None,
            "targetHigh":  round(tgt_h, 2) if tgt_h else None,
            "targetLow":   round(tgt_l, 2) if tgt_l else None,
            "upside":      upside,

            # Valorisation
            "pe":          safe(info.get("trailingPE")),
            "forwardPE":   safe(info.get("forwardPE")),
            "pb":          safe(info.get("priceToBook")),
            "ps":          safe(info.get("priceToSalesTrailing12Months")),
            "evEbitda":    safe(info.get("enterpriseToEbitda")),
            "evRevenue":   safe(info.get("enterpriseToRevenue")),
            "peg":         safe(info.get("pegRatio")),
            "marketCap":   safe(info.get("marketCap")),

            # Rentabilité & croissance
            "roe":         pct(info.get("returnOnEquity")),
            "roa":         pct(info.get("returnOnAssets")),
            "netMargin":   pct(info.get("profitMargins")),
            "grossMargin": pct(info.get("grossMargins")),
            "opMargin":    pct(info.get("operatingMargins")),
            "revGrowth":   pct(info.get("revenueGrowth")),
            "epsGrowth":   pct(info.get("earningsGrowth")),
            "revenueYoY":  safe(info.get("totalRevenue")),
            "fcfYield":    pct(info.get("freeCashflow") / info.get("marketCap"))
                           if info.get("freeCashflow") and info.get("marketCap") else None,

            # Bilan
            "debtEquity":  safe(info.get("debtToEquity")),
            "currentRatio": safe(info.get("currentRatio")),
            "cashPerShare": safe(info.get("totalCashPerShare")),

            # Dividende
            "dividendRate":  safe(info.get("dividendRate")),
            "dividendYield": pct(info.get("dividendYield")),
            "payoutRatio":   pct(info.get("payoutRatio")),

            # Technique
            "beta":       safe(info.get("beta")),
            "rsi":        rsi,
            "ma200":      ma200,
            "ma50":       ma50,
            "ma200Signal": ma200_signal,
            "ma50Signal":  ma50_signal,
            "avgVolume":   safe(info.get("averageVolume")),
            "shortRatio":  safe(info.get("shortRatio")),

            # Consensus
            "consensusNote":      cons_note,
            "numAnalysts":        safe(info.get("numberOfAnalystOpinions")),
            "recommendationKey":  info.get("recommendationKey", "hold"),

            # Actionnariat
            "insiderOwnership":   pct(info.get("heldPercentInsiders")),
            "institutionOwnership": pct(info.get("heldPercentInstitutions")),

            "peers": peers_raw[:5] if peers_raw else [],
        }

        return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors de la récupération des données : {str(e)}")


@app.get("/search/{query}")
def search_ticker(query: str):
    """Recherche approximative de tickers (basique — utilise yfinance)."""
    try:
        results = yf.Search(query, max_results=8)
        quotes = results.quotes if hasattr(results, "quotes") else []
        return {"results": [
            {"ticker": q.get("symbol"), "name": q.get("longname") or q.get("shortname"), "exchange": q.get("exchange")}
            for q in quotes if q.get("symbol")
        ]}
    except Exception as e:
        return {"results": [], "error": str(e)}
