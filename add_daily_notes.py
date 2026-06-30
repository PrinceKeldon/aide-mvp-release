#!/usr/bin/env python3
"""
Add notes for today's daily brief
"""
from daily_brief.context import add_manual_notes

print("\n📝 Add Notes for Today's Brief")
print("="*60)
print("Enter notes (one per line, empty line to finish):")
print()

notes = []
while True:
    note = input("> ").strip()
    if not note:
        break
    notes.append(note)

if notes:
    add_manual_notes(notes)
    print(f"\n✅ Added {len(notes)} notes")
    print("Generate brief with: python generate_daily_brief.py")
else:
    print("\nNo notes added.")
