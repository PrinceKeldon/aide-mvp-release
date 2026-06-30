"""
AIDE Onboarding - User Identity Storage
Stores user display name and operator name
"""
from pathlib import Path
import json
from datetime import datetime


class OnboardingData:
    """Stores user identity and preferences"""
    
    def __init__(
        self,
        user_display_name: str = None,
        operator_name: str = "AIDE",
        operator_name_customized: bool = False,
        onboarding_completed: bool = False,
        completed_at: datetime = None
    ):
        self.user_display_name = user_display_name
        self.operator_name = operator_name
        self.operator_name_customized = operator_name_customized
        self.onboarding_completed = onboarding_completed
        self.completed_at = completed_at
    
    def to_dict(self):
        return {
            "user_display_name": self.user_display_name,
            "operator_name": self.operator_name,
            "operator_name_customized": self.operator_name_customized,
            "onboarding_completed": self.onboarding_completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        completed_at = None
        if data.get("completed_at"):
            completed_at = datetime.fromisoformat(data["completed_at"])
        
        return cls(
            user_display_name=data.get("user_display_name"),
            operator_name=data.get("operator_name", "AIDE"),
            operator_name_customized=data.get("operator_name_customized", False),
            onboarding_completed=data.get("onboarding_completed", False),
            completed_at=completed_at
        )
    
    def save(self, path: Path = None):
        """Save to disk"""
        if path is None:
            path = Path.home() / ".aide" / "onboarding.json"
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, path: Path = None):
        """Load from disk"""
        if path is None:
            path = Path.home() / ".aide" / "onboarding.json"
        
        if not path.exists():
            return cls()  # Return default
        
        with open(path, 'r') as f:
            data = json.load(f)
        
        return cls.from_dict(data)


def get_onboarding_data() -> OnboardingData:
    """Get current onboarding state"""
    return OnboardingData.load()


def is_onboarding_complete() -> bool:
    """Check if user has completed onboarding"""
    data = get_onboarding_data()
    return data.onboarding_completed
