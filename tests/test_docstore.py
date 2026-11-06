"""The local document store that replaces the hosted database."""

import datetime as dt

import pytest

from omniq.docstore import DESCENDING, Database, DuplicateKeyError, ReturnDocument


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def test_crud_filters_and_persistence(tmp_path):
    d = Database(tmp_path / "p.db")
    d.users.create_index("id", unique=True)
    d.users.insert_many([
        {"id": "a", "role": "pro", "tags": ["x", "y"], "n": 3, "meta": {"team": "platform"}},
        {"id": "b", "role": "manager", "tags": ["y"], "n": 7, "meta": {"team": "sales"}},
        {"id": "c", "role": "pro", "n": 1},
    ])
    assert d.users.count_documents({"role": "pro"}) == 2
    assert {u["id"] for u in d.users.find({"tags": "y"})} == {"a", "b"}          # array contains
    assert {u["id"] for u in d.users.find({"n": {"$gte": 3, "$lt": 7}})} == {"a"}
    assert {u["id"] for u in d.users.find({"meta.team": {"$in": ["sales"]}})} == {"b"}
    assert {u["id"] for u in d.users.find({"tags": {"$exists": False}})} == {"c"}
    assert {u["id"] for u in d.users.find({"$or": [{"id": "a"}, {"n": {"$gt": 5}}]})} == {"a", "b"}
    assert {u["id"] for u in d.users.find({"id": {"$regex": "^[ab]$"}})} == {"a", "b"}
    assert [u["id"] for u in d.users.find({}, {"_id": 0, "id": 1}).sort("n", DESCENDING).limit(2)] == ["b", "a"]
    assert "_id" not in d.users.find_one({"id": "a"}, {"_id": 0})
    with pytest.raises(DuplicateKeyError):
        d.users.insert_one({"id": "a"})
    # Survives a reopen.
    again = Database(tmp_path / "p.db")
    assert again.users.count_documents({}) == 3


def test_updates_and_upserts(db):
    db.h.insert_one({"_id": "k", "n": 1, "tags": []})
    db.h.update_one({"_id": "k"}, {"$inc": {"n": 2}, "$push": {"tags": "a"}, "$set": {"x.y": 5}})
    db.h.update_one({"_id": "k"}, {"$addToSet": {"tags": {"$each": ["a", "b"]}}})
    doc = db.h.find_one({"_id": "k"})
    assert doc["n"] == 3 and doc["tags"] == ["a", "b"] and doc["x"] == {"y": 5}
    r = db.h.update_one({"role": "pro", "version": 1}, {"$set": {"ok": True}, "$setOnInsert": {"made": 1}}, upsert=True)
    assert r.upserted_id
    up = db.h.find_one({"role": "pro"})
    assert up["version"] == 1 and up["ok"] and up["made"] == 1
    db.h.update_one({"role": "pro"}, {"$setOnInsert": {"made": 2}}, upsert=True)
    assert db.h.find_one({"role": "pro"})["made"] == 1
    after = db.h.find_one_and_update({"_id": "k"}, {"$inc": {"n": 1}}, return_document=ReturnDocument.AFTER)
    assert after["n"] == 4
    db.h.update_many({}, {"$unset": {"ok": ""}})
    assert "ok" not in db.h.find_one({"role": "pro"})
    assert db.h.delete_many({}).deleted_count == 2


def test_dates_round_trip_and_ttl(db):
    now = dt.datetime.now(dt.timezone.utc)
    db.locks.create_index("expires", expireAfterSeconds=0)
    db.locks.insert_one({"_id": "old", "expires": now - dt.timedelta(seconds=5)})
    db.locks.insert_one({"_id": "new", "expires": now + dt.timedelta(minutes=5)})
    assert [d["_id"] for d in db.locks.find()] == ["new"]
    assert db.locks.find_one({"_id": "new"})["expires"] > now
    assert db.locks.count_documents({"expires": {"$gt": now}}) == 1


def test_vector_search_is_prefiltered(db):
    db.chunks.insert_many([
        {"_id": 1, "category": "board", "embedding": [1.0, 0.0]},
        {"_id": 2, "category": "general", "embedding": [0.9, 0.1]},
        {"_id": 3, "category": "general", "embedding": [0.0, 1.0]},
    ])
    rows = list(db.chunks.aggregate([
        {"$vectorSearch": {"index": "v", "path": "embedding", "queryVector": [1.0, 0.0], "numCandidates": 10,
                           "limit": 2, "filter": {"category": {"$in": ["general"]}}}},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]))
    # The board chunk is the closest match, but the pre-filter excludes it before ranking.
    assert [r["_id"] for r in rows] == [2, 3]
    assert rows[0]["score"] > rows[1]["score"] and "embedding" not in rows[0]


def test_group_unwind_and_graph_lookup(db):
    db.ep.insert_many([{"agent": "a", "ok": True}, {"agent": "a", "ok": False}, {"agent": "b", "ok": True}])
    rows = {r["_id"]: r for r in db.ep.aggregate([
        {"$group": {"_id": "$agent", "n": {"$sum": 1}, "good": {"$sum": {"$cond": ["$ok", 1, 0]}}}},
        {"$sort": {"n": -1}},
    ])}
    assert rows["a"]["n"] == 2 and rows["a"]["good"] == 1 and rows["b"]["n"] == 1
    db.objects.insert_many([{"id": "x"}, {"id": "y"}, {"id": "z"}])
    db.links.insert_many([{"src": "x", "dst": "y", "type": "t"}, {"src": "y", "dst": "z", "type": "t"}])
    one_hop = list(db.objects.aggregate([
        {"$match": {"id": "x"}},
        {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "dst", "connectToField": "src",
                          "as": "out", "maxDepth": 0}},
    ]))
    assert [l["dst"] for l in one_hop[0]["out"]] == ["y"]
    two_hop = list(db.objects.aggregate([
        {"$match": {"id": "x"}},
        {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "dst", "connectToField": "src",
                          "as": "out", "maxDepth": 1}},
    ]))
    assert sorted(l["dst"] for l in two_hop[0]["out"]) == ["y", "z"]
    tagged = list(db.ep.aggregate([{"$project": {"_id": 0, "agent": 1}}, {"$sortByCount": "$agent"}]))
    assert tagged[0] == {"_id": "a", "count": 2}
    assert list(db.ep.aggregate([{"$match": {"ok": True}}, {"$count": "n"}])) == [{"n": 2}]


def test_change_stream_sees_later_writes(db):
    db.runs.insert_one({"_id": "r0"})
    stream = db.runs.watch()
    assert stream.try_next() is None
    db.runs.update_one({"_id": "r0"}, {"$set": {"step": 1}})
    ev = stream.try_next()
    assert ev["fullDocument"]["step"] == 1


def test_slice_projection(db):
    db.conv.insert_one({"_id": "c", "turns": [{"q": 1}, {"q": 2}, {"q": 3}], "updated": 5, "big": "x" * 10})
    row = db.conv.find_one({"_id": "c"}, {"turns": {"$slice": 1}, "updated": 1})
    assert row == {"_id": "c", "turns": [{"q": 1}], "updated": 5}


def test_indexes_persist_across_processes(tmp_path):
    a = Database(tmp_path / "i.db")
    a.users.create_index("id", unique=True)
    a.locks.create_index("expires", expireAfterSeconds=0)
    a.users.insert_one({"id": "x"})
    b = Database(tmp_path / "i.db")  # a second process opening the same file
    with pytest.raises(DuplicateKeyError):
        b.users.insert_one({"id": "x"})
    assert {"name": "expires_1", "key": {"expires": 1}, "expireAfterSeconds": 0} in b.locks.list_indexes()
    with pytest.raises(DuplicateKeyError):
        b.users.insert_one({"id": "y", "_id": b.users.find_one({"id": "x"})["_id"]})
