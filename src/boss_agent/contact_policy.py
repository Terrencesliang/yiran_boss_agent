"""Recommendation contact policy shared by the CLI and legacy entry points."""

LIVE_CONTACT_DISABLED = "live_recommendation_contact_disabled_phase_01"
CONFIRMED_CONTACT_STATES = ("已打招呼", "继续沟通", "已沟通", "沟通中", "已联系")


def blocked_contact() -> dict:
    return {"clicked": False, "greeted": False, "sent": False, "reason": LIVE_CONTACT_DISABLED}
