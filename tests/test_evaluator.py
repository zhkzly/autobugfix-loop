from autobugfix.evaluator import parse_evaluator_decision


def test_parse_evaluator_yaml():
    decision = parse_evaluator_decision("decision: pass\nreason: ok\n")
    assert decision.passed


def test_parse_evaluator_accepts_exact_contract_with_plain_colon_reason():
    decision = parse_evaluator_decision(
        "decision: pass\nreason: Correct: the configured verifier passed.\n"
    )

    assert decision.passed
    assert decision.reason == "Correct: the configured verifier passed."


def test_parse_evaluator_fails_closed_on_ambiguous_text_and_schema():
    ambiguous = parse_evaluator_decision("The patch does not pass semantic review")
    extra = parse_evaluator_decision("decision: pass\nreason: ok\nscore: 1\n")
    empty_reason = parse_evaluator_decision("decision: pass\nreason: ''\n")

    assert ambiguous.decision == "needs_changes"
    assert extra.decision == "needs_changes"
    assert empty_reason.decision == "needs_changes"
