"""Format DailyBrief for Telegram output."""
from daily_brief.schema import DailyBrief, ItemType, Priority

PRIORITY_ICON = {Priority.HIGH: "🔴", Priority.MEDIUM: "🟡", Priority.LOW: "🟢"}
TYPE_ICON = {
    ItemType.CALENDAR_AGENDA: "🗓",
    ItemType.EMAIL_DIGEST: "📬",
    ItemType.DRAFT_MESSAGE: "✉️",
    ItemType.PREPARED_TASK: "📋",
    ItemType.APPROVAL_REQUEST: "🛡",
}

def format_for_telegram(brief: DailyBrief) -> str:
    headline = (
        brief.summary.headline.strip()
        if brief.summary and brief.summary.headline
        else "Good morning. I've prepared your day."
    )
    lines = [f"🌅 *{headline}*\n"]

    if not brief.items:
        lines.append("Your day is clear. Nothing urgent needs preparation.")
        return "\n".join(lines)

    for item in brief.items:
        icon = PRIORITY_ICON.get(item.priority, "🟡")
        type_icon = TYPE_ICON.get(item.type, "📋")
        lines.append(f"{icon} {type_icon} *{item.title}*")
        if item.reason:
            lines.append(f"   _{item.reason}_")
        lines.append("")

    return "\n".join(lines)
