"""
Authors: Claire Liu, Yu-Jing Wei
Description: Unit tests for apply_threat_overrides — the rule-based safety net
that corrects the VLM when it misclassifies threat level. These are the highest-ROI
tests in the project: a regression here is a real-world security failure.

Run: pytest test_threat_overrides.py -v
"""
import pytest
from worker import apply_threat_overrides


class TestDowngrade:
    """Alert → safe when description mentions an ordinary item."""

    @pytest.mark.parametrize("item", ["pen", "pencil", "stylus", "marker"])
    def test_alert_with_ordinary_item_downgrades_to_safe(self, item):
        level, reason = apply_threat_overrides(
            "alert", f"person holding a {item}", "VLM said weapon"
        )
        assert level == "safe"
        assert item in reason
        assert "downgraded" in reason.lower()

    def test_alert_with_actual_weapon_stays_alert_even_if_pen_nearby(self):
        # 'pen' appears but a real weapon is also there → escalate wins via ALERT pass.
        # Order: downgrade runs first, would zap to safe; then ALERT pass re-escalates.
        level, _ = apply_threat_overrides(
            "alert", "person holding a knife and a pen", ""
        )
        assert level == "alert"


class TestEscalateToAlert:
    """Safe/watch → alert when a weapon keyword appears."""

    @pytest.mark.parametrize("weapon", [
        "knife", "gun", "pistol", "firearm", "bat", "crowbar", "weapon", "blade", "umbrella"
    ])
    def test_weapon_in_description_forces_alert(self, weapon):
        level, reason = apply_threat_overrides(
            "safe", f"person carrying a {weapon}", ""
        )
        assert level == "alert"
        assert weapon in reason

    def test_already_alert_no_double_reason(self):
        # If VLM already said alert AND a weapon is mentioned, reason shouldn't
        # be needlessly mutated (avoid noisy log accumulation).
        level, reason = apply_threat_overrides(
            "alert", "holding a knife", "original reason"
        )
        assert level == "alert"
        assert "auto-escalated" not in reason  # original reason preserved


class TestEscalateToWatch:
    """Safe → watch when face/head covering is detected."""

    @pytest.mark.parametrize("item", [
        "hat", "cap", "hood", "mask", "scarf", "sunglasses", "beanie", "helmet"
    ])
    def test_covering_in_description_escalates_to_watch(self, item):
        level, reason = apply_threat_overrides(
            "safe", f"a person wearing a {item}", ""
        )
        assert level == "watch"
        assert item in reason

    def test_watch_with_hat_stays_watch_not_alert(self):
        # Already watch and only a hat → no escalation past watch.
        level, _ = apply_threat_overrides("watch", "wearing a cap", "")
        assert level == "watch"


class TestInvalidInput:
    """Defensive: garbage level → safe; everything still works."""

    def test_unknown_level_normalised_to_safe(self):
        level, _ = apply_threat_overrides("danger", "empty hands", "")
        assert level == "safe"

    def test_empty_description_keeps_level(self):
        level, _ = apply_threat_overrides("watch", "", "ok")
        assert level == "watch"


class TestPrecedence:
    """Multiple keywords: alert > watch > downgrade, and order matters."""

    def test_weapon_beats_hat(self):
        # Both 'mask' and 'knife' present — must end up alert.
        level, _ = apply_threat_overrides(
            "safe", "masked person holding a knife", ""
        )
        assert level == "alert"

    def test_pen_alone_from_safe_stays_safe(self):
        # Downgrade only fires when starting from alert. A safe 'pen' stays safe.
        level, _ = apply_threat_overrides("safe", "holding a pen", "")
        assert level == "safe"


class TestWordBoundary:
    """Avoid false positives from substrings inside larger words."""

    def test_pencilcase_does_not_match_pencil_substring(self):
        # 'pencilcase' should NOT trigger the 'pencil' downgrade because it's
        # not a whole word. (Regression guard for the space-padding logic.)
        level, _ = apply_threat_overrides(
            "alert", "person with a pencilcase", "weapon"
        )
        assert level == "alert"  # downgrade should NOT fire
