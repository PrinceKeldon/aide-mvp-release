"""
AIDE Onboarding - Flow State Machine
Manages the 4-step onboarding process
"""
from enum import Enum
from typing import Optional
from onboarding.storage import OnboardingData, get_onboarding_data


class OnboardingStep(Enum):
    WELCOME = "welcome"
    USER_NAME = "user_name"
    OPERATOR_NAME = "operator_name"
    CONFIRMATION = "confirmation"
    COMPLETE = "complete"


class OnboardingFlow:
    """
    Manages onboarding state and transitions
    """
    
    def __init__(self):
        self.data = get_onboarding_data()
        self.current_step = OnboardingStep.WELCOME
    
    def get_current_step(self) -> OnboardingStep:
        """Get current step in flow"""
        if self.data.onboarding_completed:
            return OnboardingStep.COMPLETE
        return self.current_step
    
    def set_user_name(self, name: Optional[str]):
        """Set user display name (or skip)"""
        self.data.user_display_name = name.strip() if name else None
        self.current_step = OnboardingStep.OPERATOR_NAME
    
    def set_operator_name(self, name: Optional[str]):
        """Set operator name (or keep default)"""
        if name and name.strip():
            self.data.operator_name = name.strip()
            self.data.operator_name_customized = True
        else:
            self.data.operator_name = "AIDE"
            self.data.operator_name_customized = False
        
        self.current_step = OnboardingStep.CONFIRMATION
    
    def complete_onboarding(self):
        """Mark onboarding as complete"""
        from datetime import datetime
        
        self.data.onboarding_completed = True
        self.data.completed_at = datetime.now()
        self.data.save()
        
        self.current_step = OnboardingStep.COMPLETE
        
        print(f"\n✅ Onboarding complete!")
        if self.data.user_display_name:
            print(f"   User: {self.data.user_display_name}")
        print(f"   Operator: {self.data.operator_name}")
    
    def get_greeting(self) -> str:
        """Get personalized greeting"""
        if self.data.user_display_name:
            return f"Nice to meet you, {self.data.user_display_name}."
        return "Nice to meet you."
    
    def get_intro_message(self) -> str:
        """Get confirmation message"""
        name = self.data.operator_name
        return f"I'm {name}. I'll help prepare your day privately on this device."
