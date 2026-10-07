from ishti.skills.ledger import skill as ledger


def test_planning_request_with_a_budget_is_not_a_ledger_command():
    assert not ledger.match("help me plan a 3 day trip to Swat valley with a budget of 40000 rupees")
    assert ledger.match("set a budget of 20000 for groceries") and ledger.match("I spent 500 on groceries")
