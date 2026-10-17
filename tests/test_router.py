from aiteams.orchestrator.router import parse_mentions, roles_to_wake, route


def msg(db, conv, author, text, mentions=None):
    m = db.add_message(conv, author, "Message", text, parse_mentions(text, mentions), None)
    return m, db.conversation(conv)


def test_parse_mentions():
    assert parse_mentions("hi @Backend and @qa, not @nobody, email a@b.com") == ["backend", "qa"]
    assert parse_mentions("x", ["@Lead", "lead", "bogus"]) == ["lead"]


def test_mention_and_dm_rules(db):
    m, c = msg(db, "general", "master", "@manager ask @backend")
    assert roles_to_wake(m, c) == ["manager", "backend"]
    m, c = msg(db, "dm-qa", "master", "Reply OK")
    assert roles_to_wake(m, c) == ["qa"]
    m, c = msg(db, "dm-qa", "master", "@lead look")
    assert roles_to_wake(m, c) == ["lead", "qa"]


def test_untagged_master_wakes_manager_only_in_channel_or_feature(db):
    m, c = msg(db, "general", "master", "hello team")
    assert roles_to_wake(m, c) == ["manager"]
    t = db.create_thread("Login feature")
    m, c = msg(db, t["id"], "master", "start")
    assert roles_to_wake(m, c) == ["manager"]
    m, c = msg(db, "general", "backend", "done")
    assert roles_to_wake(m, c) == []


def test_role_never_wakes_itself_and_system_wakes_nobody(db):
    m, c = msg(db, "dm-backend", "backend", "note to self @backend")
    assert roles_to_wake(m, c) == []
    m, c = msg(db, "dm-backend", "system", "resumed")
    assert roles_to_wake(m, c) == []


def test_merge_into_queued_wake_and_origin_upgrade(db):
    m1, c = msg(db, "general", "lead", "@backend do x")
    w1 = route(db, m1, c)
    assert len(w1) == 1 and w1[0]["origin"] == "role" and w1[0]["message_ids"] == [m1["id"]]
    m2, c2 = msg(db, "dm-backend", "master", "also y")
    w2 = route(db, m2, c2)
    assert w2[0]["id"] == w1[0]["id"]
    assert w2[0]["message_ids"] == [m1["id"], m2["id"]]
    assert w2[0]["origin"] == "master"
    assert len(db.queued_wakes()) == 1


def test_master_first_order(db):
    m1, c1 = msg(db, "general", "lead", "@qa test it")
    route(db, m1, c1)
    m2, c2 = msg(db, "dm-backend", "master", "hi")
    route(db, m2, c2)
    assert [w["role"] for w in db.queued_wakes()] == ["backend", "qa"]


def test_master_mention_is_stored_but_never_woken(db):
    assert parse_mentions("@master the DB is missing, @lead fyi") == ["master", "lead"]
    m, c = msg(db, "general", "qa", "@master the DB is missing")
    assert m["mentions"] == ["master"] and roles_to_wake(m, c) == []
    f = db.create_feature("Export")
    m, c = msg(db, f["conversation_id"], "master", "go")
    assert roles_to_wake(m, c) == ["manager"]
