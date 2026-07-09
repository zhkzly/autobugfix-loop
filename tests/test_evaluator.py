from autobugfix.evaluator import parse_evaluator_decision


def test_parse_evaluator_yaml():
    decision = parse_evaluator_decision("decision: pass\nreason: ok\n")
    assert decision.passed
