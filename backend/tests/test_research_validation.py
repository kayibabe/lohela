from scripts.prospective_validation import outcome, selector_path_status


def finished(home, away):
    return {'status': 'FINISHED', 'home_goals': home, 'away_goals': away}


def test_binary_market_outcomes_are_settled_from_finished_match():
    match = finished(2, 1)
    assert outcome('over_2.5', match) == 1
    assert outcome('under_3.5', match) == 1
    assert outcome('home_win', match) == 1
    assert outcome('draw', match) == 0
    assert outcome('double_chance_1x', match) == 1


def test_unfinished_match_is_not_a_label():
    assert outcome('over_2.5', {'status': 'SCHEDULED', 'home_goals': None, 'away_goals': None}) is None


def test_market_pricing_makes_q_score_selector_ablation_inapplicable():
    payload = {
        'tables': {
            'ticket_generations': [{
                'config_snapshot': {
                    'pricing': 'market',
                    'ticket_specs': [{'min_q_score': 0.0}],
                }
            }]
        }
    }
    result = selector_path_status(payload)
    assert result['status'] == 'NOT_APPLICABLE_CURRENT_SELECTOR'
    assert result['observed_min_q_scores'] == [0.0]


def test_missing_generation_records_fail_closed():
    result = selector_path_status({'tables': {}})
    assert result['status'] == 'NOT_VERIFIED'
