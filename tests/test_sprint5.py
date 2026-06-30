"""
Sprint 5: Test suite
Run from ~/aide: python3 -m pytest tests/test_sprint5.py -v
Or standalone: python3 tests/test_sprint5.py
"""

import sys
import os
import tempfile
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.project_manager import ProjectManager
from tools.project import ProjectTool


def run_tests():
    print("=" * 55)
    print("AIDE Sprint 5 — Project Manager Tests")
    print("=" * 55)
    passed = 0
    failed = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        pm = ProjectManager(db_path=db_path)
        tool = ProjectTool(project_manager=pm)

        # ── Test 1: Create project ──────────────────
        print("\n[1] Create project")
        result = tool.run(
            action="create",
            title="Podcast Launch",
            goal="Launch my weekly tech podcast by June 30",
            deadline="2025-06-30",
        )
        assert "Podcast Launch" in result, f"FAIL: {result}"
        project_id = result.split("`")[1]
        print(f"    ✅ Created project ID: {project_id}")
        passed += 1

        # ── Test 2: List projects ───────────────────
        print("\n[2] List projects")
        result = tool.run(action="list")
        assert "Podcast Launch" in result, f"FAIL: {result}"
        print(f"    ✅ {result.strip()}")
        passed += 1

        # ── Test 3: Add subtasks ────────────────────
        print("\n[3] Add subtasks")
        for title in ["Buy microphone", "Record intro episode", "Design cover art"]:
            r = tool.run(action="add_subtask", project_id=project_id, subtask_title=title)
            assert "added" in r, f"FAIL: {r}"
        print("    ✅ 3 subtasks added")
        passed += 1

        # ── Test 4: Status ──────────────────────────
        print("\n[4] Project status")
        result = tool.run(action="status", project_id=project_id)
        assert "0/3" in result, f"FAIL: expected 0/3 in {result}"
        print(f"    ✅ Status shows 0/3 complete")
        passed += 1

        # ── Test 5: Next step ───────────────────────
        print("\n[5] Next step")
        result = tool.run(action="next_step", project_id=project_id)
        assert "Buy microphone" in result, f"FAIL: {result}"
        print(f"    ✅ Next step: Buy microphone")
        passed += 1

        # ── Test 6: Complete a subtask ──────────────
        print("\n[6] Complete subtask")
        p = pm.get_project(project_id)
        first_subtask_id = p.subtasks[0]["id"]
        result = tool.run(action="complete_subtask", project_id=project_id, subtask_id=first_subtask_id)
        assert "done" in result, f"FAIL: {result}"

        result = tool.run(action="status", project_id=project_id)
        assert "1/3" in result, f"FAIL: expected 1/3 in {result}"
        print("    ✅ 1/3 subtasks complete")
        passed += 1

        # ── Test 7: Add contact ─────────────────────
        print("\n[7] Add contact")
        result = tool.run(
            action="add_contact",
            project_id=project_id,
            contact_name="Sarah",
            contact_email="sarah@example.com",
            contact_role="Producer",
        )
        assert "Sarah" in result, f"FAIL: {result}"
        result = tool.run(action="status", project_id=project_id)
        assert "Sarah" in result, f"FAIL: contact not in status: {result}"
        print("    ✅ Contact Sarah added and visible in status")
        passed += 1

        # ── Test 8: Add note ────────────────────────
        print("\n[8] Add note")
        result = tool.run(action="add_note", project_id=project_id, note="Found a great mic on Amazon for €89.")
        assert "Note added" in result, f"FAIL: {result}"
        print("    ✅ Note added")
        passed += 1

        # ── Test 9: Link email ──────────────────────
        print("\n[9] Link email")
        result = tool.run(action="link_email", project_id=project_id, email_id="email_abc123")
        assert "linked" in result, f"FAIL: {result}"
        p = pm.get_project(project_id)
        assert "email_abc123" in p.linked_email_ids, "FAIL: email not linked"
        print("    ✅ Email thread linked")
        passed += 1

        # ── Test 10: Block subtask ──────────────────
        print("\n[10] Block subtask")
        p = pm.get_project(project_id)
        second_id = p.subtasks[1]["id"]
        result = tool.run(action="block_subtask", project_id=project_id, subtask_id=second_id, block_reason="Waiting on Sarah's feedback")
        assert "blocked" in result.lower(), f"FAIL: {result}"
        print("    ✅ Subtask blocked")
        passed += 1

        # ── Test 11: Stale detection ────────────────
        print("\n[11] Stale project detection")
        stale = pm.get_stale_projects(days_stale=0)  # 0 days = all projects stale
        assert any(p.id == project_id for p in stale), "FAIL: project not in stale list"
        print("    ✅ Stale detection works")
        passed += 1

        # ── Test 12: Log advance ────────────────────
        print("\n[12] Log advance")
        result = tool.run(action="log_advance", project_id=project_id, note="Searched for microphones, found 3 options.")
        assert "logged" in result.lower(), f"FAIL: {result}"
        p = pm.get_project(project_id)
        assert len(p.advance_log) > 0, "FAIL: advance log empty"
        print(f"    ✅ Advance log has {len(p.advance_log)} entries")
        passed += 1

        # ── Test 13: Delete ─────────────────────────
        print("\n[13] Delete project")
        result = tool.run(action="delete", project_id=project_id)
        assert "deleted" in result.lower(), f"FAIL: {result}"
        p = pm.get_project(project_id)
        assert p is None, "FAIL: project still exists after delete"
        print("    ✅ Project deleted")
        passed += 1

    print(f"\n{'='*55}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("🎉 All tests passed! Sprint 5 is ready to integrate.")
    else:
        print("❌ Some tests failed — check output above.")
    print("=" * 55)
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)