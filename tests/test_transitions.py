"""The state machine's transition table IS the safety model — pin it."""
from schemas import AppState, transition_allowed


def test_happy_path_is_legal():
    path = [AppState.DISCOVERED, AppState.MATCHED, AppState.DRAFTED,
            AppState.IN_REVIEW]
    for a, b in zip(path, path[1:]):
        assert transition_allowed(a, b), f"{a} -> {b} must be legal"
    assert transition_allowed(AppState.APPROVED, AppState.QUEUED)
    assert transition_allowed(AppState.QUEUED, AppState.SUBMITTING)
    assert transition_allowed(AppState.SUBMITTING, AppState.SUBMITTED)


def test_submitting_can_roll_back_to_queued():
    """The worker's retry path: SUBMITTING -> QUEUED re-arms the
    idempotency gate for the redelivered Cloud Task."""
    assert transition_allowed(AppState.SUBMITTING, AppState.QUEUED)


def test_submitting_failure_routes():
    assert transition_allowed(AppState.SUBMITTING, AppState.NEEDS_HUMAN)
    assert transition_allowed(AppState.SUBMITTING, AppState.FAILED)


def test_human_approval_is_not_an_engine_transition():
    """approve/reject are human-only, enforced in firestore.rules — the
    engine must never be able to approve its own draft."""
    assert not transition_allowed(AppState.IN_REVIEW, AppState.APPROVED)
    assert not transition_allowed(AppState.DRAFTED, AppState.APPROVED)


def test_terminal_states_have_no_engine_exits():
    for terminal in (AppState.SUBMITTED, AppState.FAILED, AppState.NEEDS_HUMAN):
        for to in AppState:
            assert not transition_allowed(terminal, to), \
                f"{terminal} -> {to} must not be engine-legal"


def test_no_skipping_review():
    """QUEUED is only reachable from APPROVED (human) or SUBMITTING
    (retry rollback) — never straight from a draft."""
    for state in (AppState.DISCOVERED, AppState.MATCHED, AppState.DRAFTED,
                  AppState.IN_REVIEW):
        assert not transition_allowed(state, AppState.QUEUED)
        assert not transition_allowed(state, AppState.SUBMITTING)
