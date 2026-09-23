"""Offline loss/calibration diagnosis. No training, database access, or mutation."""
import argparse
import collections
import gzip
import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


def dt(s):
    return datetime.fromisoformat(s)


def outcome(market, home, away):
    if market.startswith(('over_', 'under_')):
        threshold = float(market.split('_', 1)[1])
        # Only binary half-goal markets have unambiguous settlement here.
        if threshold % 1 != .5:
            raise ValueError('nonbinary totals')
        return int(home + away > threshold) if market.startswith('over_') else int(home + away < threshold)
    rules = {'home_win': home > away, 'away_win': away > home, 'draw': home == away,
             'btts_yes': home > 0 and away > 0, 'btts_no': home == 0 or away == 0,
             'double_chance_1x': home >= away, 'double_chance_x2': away >= home,
             'double_chance_12': home != away}
    if market not in rules:
        raise ValueError('unsupported or push-sensitive market')
    return int(rules[market])


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def metrics(rows):
    if not rows:
        return {'n': 0}
    n = len(rows)
    wins = sum(r['y'] for r in rows)
    bins = collections.defaultdict(list)
    for r in rows:
        bins[min(9, int(r['p'] * 10))].append(r)
    priced = [r for r in rows if r['quote_valid']]
    snapshots = [r for r in rows if r['odds'] and math.isfinite(r['odds']) and r['odds'] > 1]
    brier = mean([(r['p'] - r['y']) ** 2 for r in rows])
    return dict(n=n, fixtures=len({r['match_id'] for r in rows}),
                days=len({r['day'] for r in rows}), wins=wins, losses=n-wins,
                mean_p=mean([r['p'] for r in rows]), hit_rate=wins/n,
                gap=mean([r['p']-r['y'] for r in rows]), brier=brier,
                log_loss=mean([-math.log(max(1e-15, min(1-1e-15, r['p'] if r['y'] else 1-r['p']))) for r in rows]),
                ece=sum(len(g)/n*abs(mean([r['p']-r['y'] for r in g])) for g in bins.values()),
                constant_half_brier=.25, same_sample_base_rate_brier=(wins/n)*(1-wins/n),
                priced_n=len(priced), paper_profit=sum(r['y']*r['odds']-1 for r in priced),
                paper_roi=mean([r['y']*r['odds']-1 for r in priced]),
                quoted_implied_brier=mean([(1/r['odds']-r['y'])**2 for r in priced]),
                model_brier_on_priced=mean([(r['p']-r['y'])**2 for r in priced]),
                snapshot_priced_n=len(snapshots),
                snapshot_paper_profit=sum(r['y']*r['odds']-1 for r in snapshots),
                snapshot_paper_roi=mean([r['y']*r['odds']-1 for r in snapshots]))


def grouped(rows, key):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[str(key(r))].append(r)
    return {k: metrics(v) for k, v in sorted(groups.items())}


def bootstrap(rows, key, iterations=1000):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    blocks = list(groups.values())
    if len(blocks) < 2:
        return {'clusters': len(blocks), 'interval': None}
    rng = random.Random(20260920)
    gaps = []
    for _ in range(iterations):
        sample = [r for _ in blocks for r in rng.choice(blocks)]
        gaps.append(mean([r['p']-r['y'] for r in sample]))
    gaps.sort()
    return {'clusters': len(blocks), 'gap_percentile_95': [gaps[24], gaps[974]],
            'iterations': iterations, 'seed': 20260920}


def analyze(data):
    tables = data['tables']
    matches = {r['id']: r for r in tables['matches']}
    predictions = {r['id']: r for r in tables['predictions']}
    comps = {r['id']: r['name'] for r in tables['competitions']}
    exclusions = collections.Counter()
    latest = {}
    for p in sorted(predictions.values(), key=lambda p: (p['created_at'], p['id'])):
        m = matches[p['match_id']]
        if p['as_of_at'] is not None:
            exclusions['historical_replay'] += 1
            continue
        if not dt(p['created_at']) < dt(m['kickoff_at']):
            exclusions['not_strictly_pre_kickoff'] += 1
            continue
        if not isinstance(p['model_probability'], (float, int)) or not 0 <= p['model_probability'] <= 1:
            exclusions['invalid_probability'] += 1
            continue
        if p['selection'] != p['market']:
            exclusions['selection_market_mismatch'] += 1
            continue
        key = (p['match_id'], p['market'], p['selection'], p['model_version'])
        if key in latest:
            exclusions['superseded_pre_match_prediction'] += 1
        latest[key] = p

    def record(p, probability=None, odds=None, quote_at=None, decision_at=None):
        m = matches[p['match_id']]
        if m['status'] != 'FINISHED' or m['home_goals'] is None or m['away_goals'] is None:
            raise ValueError('unresolved')
        y = outcome(p['market'], m['home_goals'], m['away_goals'])
        odds = p['source_decimal_odds'] if odds is None else odds
        quote_at = p['source_odds_at'] if quote_at is None else quote_at
        decision_at = decision_at or p['created_at']
        provenance = p['source_odds_provenance'] or {}
        quote_valid = bool(odds and math.isfinite(odds) and odds > 1 and quote_at
                           and dt(quote_at) <= dt(decision_at) < dt(m['kickoff_at'])
                           and provenance.get('source_type') == 'api_football'
                           and provenance.get('is_fallback') is False
                           and provenance.get('bookmaker') and p['source_odds_id'] is not None)
        return dict(prediction_id=p['id'], match_id=m['id'], market=p['market'],
                    version=p['model_version'], league=comps[m['competition_id']],
                    day=(dt(m['kickoff_at'])+timedelta(hours=2)).date().isoformat(),
                    p=p['model_probability'] if probability is None else probability,
                    y=y, odds=odds, quote_valid=quote_valid, q=p['q_score'],
                    edge=p['edge'], clv=p['clv_percentage'],
                    score=f"{m['home_goals']}-{m['away_goals']}",
                    source_odds_id_missing=p['source_odds_id'] is None,
                    components={k:p[k] for k in ('poisson_prob','zinb_prob','bayes_prob','elo_prob','xg_prob')})

    rows = []
    for p in latest.values():
        try:
            rows.append(record(p))
        except ValueError as e:
            exclusions[str(e)] += 1
    tickets = {t['id']: t for t in tables['accumulator_tickets']}
    selections = collections.defaultdict(list)
    for s in tables['ticket_selections']:
        selections[s['ticket_id']].append(s)
    latest_tickets = {}
    for t in sorted(tickets.values(), key=lambda t: (t['version'], t['id'])):
        if t['status'] in ('PUBLISHED','SETTLED','VOID'):
            latest_tickets[t['target_date'],t['ticket_type']] = t
    results = {}
    for r in sorted(tables['ticket_results'], key=lambda r: (r['version'], r['id'])):
        results[r['ticket_id']] = r
    ticket_rows = []
    allocation = collections.Counter()
    mismatches = []
    for t in latest_tickets.values():
        res = results.get(t['id'])
        if not res or res['result'] not in ('WON','LOST','VOID'):
            continue
        legs = selections[t['id']]
        failed = [s for s in legs if s['result'] == 'LOST']
        for s in legs:
            m = matches[s['match_id']]
            if m['status'] == 'FINISHED' and m['home_goals'] is not None and m['away_goals'] is not None:
                try:
                    expected = 'WON' if outcome(s['market'],m['home_goals'],m['away_goals']) else 'LOST'
                    if expected != s['result']:
                        mismatches.append(s['id'])
                except ValueError:
                    pass
        if res['result'] == 'LOST' and failed:
            for s in failed:
                allocation[s['market']] += -res['profit_loss']/len(failed)
        ticket_rows.append(dict(id=t['id'],type=t['ticket_type'],day=t['target_date'],
            version=t['model_version'],result=res['result'],p=t['adjusted_probability'],
            stake=res['stake'],profit=res['profit_loss'],legs=len(legs),failed_legs=len(failed),
            relaxed=t['relaxed_tier'],pre_match=all(dt(t['published_at'])<dt(matches[s['match_id']]['kickoff_at']) for s in legs)))
    published = {}
    pub_exclusions = collections.Counter()
    for s in sorted(tables['ticket_selections'],key=lambda s:(tickets[s['ticket_id']]['published_at'],s['id'])):
        t=tickets[s['ticket_id']]; p=predictions[s['prediction_id']]; m=matches[s['match_id']]
        if t['status'] not in ('PUBLISHED','SETTLED','VOID'):
            continue
        # Market-priced tickets publish the de-vigged bookmaker probability,
        # not the model's: counting them would make the model's published
        # calibration gap look closed when the model hasn't changed.
        if t.get('pricing', 'model') == 'market':
            pub_exclusions['market_priced_publication'] += 1
            continue
        if p['as_of_at'] is not None or not dt(p['created_at']) <= dt(t['published_at']) < dt(m['kickoff_at']):
            pub_exclusions['invalid_timing_or_replay'] += 1
            continue
        key=(s['match_id'],s['market'],s['selection'])
        if key in published:
            pub_exclusions['repeat_publication'] += 1
            continue
        # Reserve first publication even if unresolved; never select by outcome.
        published[key] = None
        try:
            r=record(p,s['probability_snapshot'],s['odds_snapshot'],s['source_odds_at'],t['published_at'])
            r.update(selection_id=s['id'],ticket_id=t['id'],q=s['q_score_snapshot'],edge=s['edge_snapshot'])
            published[key]=r
        except ValueError as e:
            pub_exclusions[str(e)] += 1
    pub=[r for r in published.values() if r is not None]
    loss_flags=collections.Counter()
    for r in pub:
        if not r['y']:
            for key,flag in [('p_ge_70',r['p']>=.7),('odds_ge_3',bool(r['odds'] and r['odds']>=3)),
                             ('q_lt_70',r['q']<70),('edge_lt_05',r['edge'] is not None and r['edge']<.05)]:
                if flag: loss_flags[key]+=1
    components={}
    for name in ('poisson_prob','zinb_prob','bayes_prob','elo_prob','xg_prob'):
        pairs=[r for r in rows if r['components'][name] is not None and 0<=r['components'][name]<=1]
        components[name]={'n':len(pairs),'component_brier':mean([(r['components'][name]-r['y'])**2 for r in pairs]),
                          'ensemble_brier_same_rows':mean([(r['p']-r['y'])**2 for r in pairs])}
    return dict(captured_at=data['captured_at'],table_counts={k:len(v) for k,v in tables.items()},
        exclusions=dict(exclusions),published_exclusions=dict(pub_exclusions),
        all_live=metrics(rows),by_version=grouped(rows,lambda r:r['version']),
        by_market=grouped(rows,lambda r:r['market']),by_league=grouped(rows,lambda r:r['league']),
        by_day=grouped(rows,lambda r:r['day']),
        by_version_market=grouped(rows,lambda r:r['version']+' / '+r['market']),
        bins=grouped(rows,lambda r:f"{min(9,int(r['p']*10))/10:.1f}-{(min(9,int(r['p']*10))+1)/10:.1f}"),
        published=metrics(pub),published_by_market=grouped(pub,lambda r:r['market']),
        published_bins=grouped(pub,lambda r:f"{min(9,int(r['p']*10))/10:.1f}-{(min(9,int(r['p']*10))+1)/10:.1f}"),
        published_by_league=grouped(pub,lambda r:r['league']),
        published_by_day=grouped(pub,lambda r:r['day']),
        published_by_version=grouped(pub,lambda r:r['version']),
        published_high_confidence=metrics([r for r in pub if r['p']>=.7]),
        published_loss_flags=dict(loss_flags),components=components,
        uncertainty={'all_fixture':bootstrap(rows,'match_id'),'all_day':bootstrap(rows,'day'),
                     'published_fixture':bootstrap(pub,'match_id'),'published_day':bootstrap(pub,'day')},
        ticket_rows=ticket_rows,latest_ticket_count=len(latest_tickets),
        gross_ticket_loss_allocation=dict(allocation),settlement_mismatches=mismatches,
        clv_coverage=sum(r['clv'] is not None for r in rows),
        rows=rows,published_rows=pub)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    raw=args.source.read_bytes()
    data=json.loads(gzip.decompress(raw) if args.source.suffix=='.gz' else raw)
    report=analyze(data)
    report['source_sha256']=hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k in ('all_live','published','exclusions','uncertainty','settlement_mismatches')},indent=2))


if __name__=='__main__':
    main()
