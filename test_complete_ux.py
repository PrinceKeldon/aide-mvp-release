from pathlib import Path

from onboarding.storage import OnboardingData, get_onboarding_data, is_onboarding_complete
from daily_brief.context import add_manual_notes, load_manual_notes


def test_onboarding_and_daily_notes_are_hermetic(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    onboarding = OnboardingData(
        user_display_name="TopD",
        operator_name="VERA",
        onboarding_completed=True,
    )
    onboarding.save()

    assert is_onboarding_complete() is True
    assert get_onboarding_data().user_display_name == "TopD"

    notes = [
        "Reply to John about Friday meeting",
        "Prepare slides for client presentation",
        "Follow up on invoice from last week",
    ]
    add_manual_notes(notes)

    assert load_manual_notes() == notes
