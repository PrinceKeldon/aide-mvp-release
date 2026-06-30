"""
AIDE Onboarding - CLI Interface
Terminal-based onboarding flow
"""
from onboarding.flow import OnboardingFlow, OnboardingStep


def run_cli_onboarding():
    """Run onboarding in terminal"""
    
    flow = OnboardingFlow()
    
    # Check if already completed
    if flow.get_current_step() == OnboardingStep.COMPLETE:
        print("\n✓ You've already completed onboarding")
        print(f"  User: {flow.data.user_display_name or '(not set)'}")
        print(f"  Operator: {flow.data.operator_name}")
        return flow.data
    
    # Step 1: Welcome
    print("\n" + "="*60)
    print("Hi, I'm your personal private operator.")
    print("="*60)
    print("\nI live on your device, work for you, and keep your")
    print("information private.")
    print()
    input("Press Enter to get started...")
    
    # Step 2: User Name
    print("\n" + "="*60)
    print("What should I call you?")
    print("="*60)
    print("This helps me prepare things more personally.")
    print()
    user_name = input("Your name (or press Enter to skip): ").strip()
    flow.set_user_name(user_name if user_name else None)
    
    # Step 3: Operator Name
    print("\n" + "="*60)
    print("Would you like to name me?")
    print("="*60)
    print("Default: AIDE")
    print()
    operator_name = input("Operator name (or press Enter to keep AIDE): ").strip()
    flow.set_operator_name(operator_name if operator_name else None)
    
    # Step 4: Confirmation
    print("\n" + "="*60)
    print(flow.get_greeting())
    print("="*60)
    print(f"\n{flow.get_intro_message()}")
    print()
    input("Press Enter to open AIDE...")
    
    # Complete
    flow.complete_onboarding()
    
    return flow.data


if __name__ == "__main__":
    run_cli_onboarding()
