#!/usr/bin/env python3
"""
Test onboarding flow
"""
from onboarding.ui.cli import run_cli_onboarding
from onboarding.storage import get_onboarding_data

if __name__ == "__main__":
    print("\n🎯 AIDE Onboarding Test")
    print("="*60)
    
    # Run onboarding
    data = run_cli_onboarding()
    
    # Verify it saved
    print("\n" + "="*60)
    print("Verifying persistence...")
    print("="*60)
    
    loaded_data = get_onboarding_data()
    
    print(f"\n✓ User name: {loaded_data.user_display_name or '(not set)'}")
    print(f"✓ Operator name: {loaded_data.operator_name}")
    print(f"✓ Completed: {loaded_data.onboarding_completed}")
    print(f"✓ Stored in: ~/.aide/onboarding.json")
    
    print("\n✅ Onboarding module working!\n")
