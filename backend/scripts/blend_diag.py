"""Diagnostic: model vs de-vigged market vs logit blends; selection (winner's-curse) gap.

Reads the frozen production archive (diagnosis only — no parameter is written
anywhere) and, separately, the local DB. Blend weights are evaluated with
leave-one-date-out so no reported score uses its own date's outcomes.
"""
import asyncio, gzip, json, logging, math, sys
from collections import defaultdict
from datetime import datetime
logging.disable(50)

PAIRS = {"over_1.5": "under_1.5", "over_2.5": "under_2.5", "over_3.5": "under_3.5", "over_4.5": "under_4.5",
         "btts_yes": "btts_no"}
PAIRS.update({v: k for k, v in list(PAIRS.items())})
THREE = ("home_win", "draw", "away_win")


def outcome(market, h, a):
    t = h + a
    if market.startswith("over_"): return float(t > float(market[5:]))
    if market.startswith("under_"): return float(t < float(market[6:]))
    return {"btts_yes": float(h > 0 and a > 0), "btts_no": float(not (h > 0 and a > 0)),
            "home_win": float(h > a), "draw": float(h == a), "away_win": float(h < a),
            "double_chance_1x": float(h >= a), "double_chance_x2": float(h <= a)}.get(market)


def fair_probs(group):
    """group: {market: implied_prob} from one run+match -> {market: de-vigged prob}."""
    fair = {}
    for m, p in group.items():
        o = PAIRS.get(m)
        if o and o in group and p and group[o]:
            fair[m] = p / (p + group[o])
    if all(group.get(m) for m in THREE):
        s = sum(group[m] for m in THREE)
        f = {m: group[m] / s for m in THREE}
        fair.update(f)
        fair["double_chance_1x"] = f["home_win"] + f["draw"]
        fair["double_chance_x2"] = f["away_win"] + f["draw"]
    return fair


def build(preds, matches):
    """Latest pre-kickoff non-replay prediction per (match, market); returns rows."""
    groups = defaultdict(dict)
    for p in preds:
        if p.get("source_implied_probability"):
            groups[(p["model_run_id"], p["match_id"])][p["market"]] = p["source_implied_probability"]
    latest = {}
    for p in preds:
        if p.get("as_of_at") or p.get("model_probability") is None: continue
        m = matches.get(p["match_id"])
        if not m or m["home_goals"] is None or m["status"] != "FINISHED": continue
        if str(p["created_at"]) >= str(m["kickoff_at"]): continue
        key = (p["match_id"], p["market"])
        if key not in latest or str(p["created_at"]) > str(latest[key]["created_at"]):
            latest[key] = p
    rows = []
    for (mid, market), p in latest.items():
        y = outcome(market, matches[mid]["home_goals"], matches[mid]["away_goals"])
        fair = fair_probs(groups[(p["model_run_id"], mid)]).get(market)
        if y is None or fair is None: continue
        rows.append({"date": str(matches[mid]["kickoff_at"])[:10], "p": p["model_probability"], "q": fair, "y": y,
                     "market": market, "odds": p.get("source_decimal_odds")})
    return rows


lg = lambda x: math.log(x / (1 - x))
sg = lambda z: 1 / (1 + math.exp(-z))
clip = lambda x: min(max(x, 1e-4), 1 - 1e-4)


def blend(p, q, w):
    return sg(w * lg(clip(p)) + (1 - w) * lg(clip(q)))


def scores(rows, key):
    n = len(rows)
    brier = sum((r[key] - r["y"]) ** 2 for r in rows) / n
    ll = -sum(r["y"] * math.log(clip(r[key])) + (1 - r["y"]) * math.log(1 - clip(r[key])) for r in rows) / n
    return round(brier, 4), round(ll, 4)


def report(name, rows):
    print(f"\n=== {name}: {len(rows)} scored forecasts, {len({r['date'] for r in rows})} dates ===")
    for r in rows: r["market_p"] = r["q"]
    print("model   brier/logloss:", scores(rows, "p"))
    print("market  brier/logloss:", scores(rows, "market_p"))
    grid = [i / 10 for i in range(11)]
    dates = sorted({r["date"] for r in rows})
    # leave-one-date-out: choose w on other dates, score held-out date
    for r in rows: r["cv"] = None
    chosen = []
    for d in dates:
        train = [r for r in rows if r["date"] != d]
        best = min(grid, key=lambda w: sum(-(r["y"] * math.log(blend(r["p"], r["q"], w)) + (1 - r["y"]) * math.log(1 - blend(r["p"], r["q"], w))) for r in train))
        chosen.append(best)
        for r in rows:
            if r["date"] == d: r["cv"] = blend(r["p"], r["q"], best)
    print("LODO blend brier/logloss:", scores(rows, "cv"), "weights chosen per fold:", chosen)
    for w in (0.0, 0.2, 0.3, 0.5, 1.0):
        for r in rows: r["b"] = blend(r["p"], r["q"], w)
        print(f"  fixed w={w}: brier/logloss", scores(rows, "b"))
    # Selection effect: legs the optimiser would consider (edge vs quoted price >= 3%, p >= 0.5)
    for label, key in (("raw model", "p"), ("LODO blend", "cv")):
        sel = [r for r in rows if r["odds"] and r[key] - 1 / r["odds"] >= 0.03]
        if not sel: print(f"  selected by {label}: none"); continue
        mp = sum(r[key] for r in sel) / len(sel); wr = sum(r["y"] for r in sel) / len(sel)
        pnl = sum((r["odds"] - 1) if r["y"] else -1 for r in sel)
        print(f"  selected by {label} (edge>=3%): n={len(sel)} predicted={mp:.3f} observed={wr:.3f} "
              f"gap={100*(mp-wr):+.1f}pp flat-stake ROI={100*pnl/len(sel):+.1f}%")


def main_archive(path):
    d = json.load(gzip.open(path))
    t = d["tables"]
    matches = {m["id"]: m for m in t["matches"]}
    report("PRODUCTION ARCHIVE (frozen 2026-09-20, diagnosis only)", build(t["predictions"], matches))


async def main_local():
    from sqlalchemy import text
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        preds = [dict(r._mapping) for r in (await db.execute(text(
            "select id, model_run_id, match_id, market, model_probability, source_implied_probability, source_decimal_odds, created_at, as_of_at from predictions"))).all()]
        matches = {r.id: dict(r._mapping) for r in (await db.execute(text(
            "select id, home_goals, away_goals, status::text as status, kickoff_at from matches where status='FINISHED'"))).all()}
    report("LOCAL DATABASE (independent cohort)", build(preds, matches))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main_archive(sys.argv[1])
    else:
        asyncio.run(main_local())
