from aiteams.orchestrator.prompt import build_system_prompt, build_wake_prompt, focus_conversation
from aiteams.orchestrator.router import parse_mentions


def test_system_prompt_assembly(repo):
    s = build_system_prompt(str(repo), "demo", "backend")
    assert "You are the Backend" in s and "# Team rules" in s and "Role: Backend" in s
    assert "# Project CLAUDE.md" not in s
    (repo / "CLAUDE.md").write_text("Always use tabs.\n")
    s = build_system_prompt(str(repo), "demo", "backend")
    assert "# Project CLAUDE.md" in s and "Always use tabs." in s


def test_project_role_override(repo):
    (repo / ".aiteams" / "roles").mkdir(parents=True)
    (repo / ".aiteams" / "roles" / "qa.md").write_text("CUSTOM QA PROMPT\n")
    assert "CUSTOM QA PROMPT" in build_system_prompt(str(repo), "demo", "qa")


def test_wake_prompt_digest_and_last_20(db):
    for i in range(25):
        db.add_message("general", "master", "Message", f"old {i}", [], None)
    m1 = db.add_message("dm-backend", "lead", "Message", "@backend check logs", ["backend"], None)
    m2 = db.add_message("general", "master", "Message", "@backend list files", ["backend"], None)
    assert focus_conversation([m1, m2]) == "general"
    p = build_wake_prompt(db, "backend", [m1["id"], m2["id"]])
    assert p.startswith("# New messages for Backend")
    assert "## backend (dm, id `dm-backend`)" in p and "lead (Message) @backend: @backend check logs" in p
    assert "# Conversation: general (channel, id `general`) — last 20 messages" in p
    assert "old 24" in p and "old 4" not in p  # 20 newest of 26 messages in general
    assert "# Instructions" in p and "post_message" in p


def test_focus_falls_back_to_newest_message(db):
    m1 = db.add_message("general", "master", "Message", "@qa a", ["qa"], None)
    m2 = db.add_message("dm-qa", "lead", "Message", "b", parse_mentions("b"), None)
    assert focus_conversation([m1, m2]) == "general"
    assert focus_conversation([m2]) == "dm-qa"
