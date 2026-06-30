#!/usr/bin/env python3
"""
Generate daily brief for morning screen
Run this daily at 6:30 AM (or manually for testing)
"""
import asyncio
from datetime import date

from core.llm import LLMClient
from core.settings import Settings

from daily_brief.generator import DailyBriefGenerator
from daily_brief.storage import save_daily_brief
from daily_brief.ui.cli import display_morning_screen


async def main():
    print("\n🌅 AIDE Daily Brief Generator")
    print("="*60)
    
    # Initialize LLM (using your existing AIDE LLM client)
    settings = Settings()
    llm = LLMClient()
    
    # Create generator
    generator = DailyBriefGenerator(llm)
    
    # Generate brief
    brief = await generator.generate(date.today())
    
    # Save to disk
    save_daily_brief(brief)
    
    # Display preview
    print("\n" + "="*60)
    print("Preview of Morning Screen:")
    print("="*60)
    display_morning_screen(brief.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
