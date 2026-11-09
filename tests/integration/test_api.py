import re

from sqlalchemy import select

from batchlight.models import ApiKey, Match
from tests.conftest import post_form, signup


def create_key(client) -> str:
    response = post_form(client, "/app/api-keys", {"name": "POS sync"})
    assert response.status_code == 200
    return re.search(r'value="(bl_[0-9a-f]{8}_[^"]+)"', response.text).group(1)


def test_public_alert_search_and_detail(client, ingest):
    ingest("us-fda-drug")
    data = client.get("/api/v1/alerts", params={"q": "cinacalcet"}).json()
    assert data["total"] == 1
    alert = data["items"][0]
    assert alert["batches"] == [{"batch": "7717825A", "expiry": "11/30/2028"}] and alert["batch_count"] == 1
    assert client.get(f"/api/v1/alerts/{alert['id']}").json()["title"].startswith("Cinacalcet")
    by_batch = client.get("/api/v1/alerts", params={"q": "7717825A"}).json()
    assert by_batch["total"] == 1
    assert client.get("/api/v1/alerts", params={"severity": "bogus"}).status_code == 422


def test_public_batch_check(client, ingest):
    ingest("us-fda-drug")
    hit = client.get("/api/v1/check", params={"batch": "7717825A", "product": "Cinacalcet 30 mg"}).json()
    assert hit["results"][0]["batch_listed"] is True and hit["results"][0]["level"] == "batch"
    assert any(s["id"] == "us-fda-drug" for s in hit["sources_searched"])
    assert "not proof" in hit["note"]
    miss = client.get("/api/v1/check", params={"batch": "NOPE0000", "product": "Cinacalcet 30 mg"}).json()
    assert all(not r["batch_listed"] for r in miss["results"])
    assert client.get("/api/v1/check").status_code == 422


def test_api_key_holdings_and_matches(client, make_client, ingest, db):
    ingest("us-fda-drug")
    signup(client)
    key = create_key(client)
    stored = db.scalar(select(ApiKey))
    assert stored.key_hash != key and key not in stored.key_hash
    api = make_client()
    headers = {"Authorization": f"Bearer {key}"}
    created = api.post(
        "/api/v1/holdings",
        json={"product_name": "Cinacalcet 30 mg tablets", "batch": "7717825A", "expiry_date": "11/2028", "quantity": 30},
        headers=headers,
    )
    assert created.status_code == 201
    assert len(created.json()["new_matches"]) == 1
    bulk = api.post(
        "/api/v1/holdings/bulk",
        headers=headers,
        json={"items": [{"product_name": "Heparin Sodium Injection", "batch": "CU03232A"}, {"product_name": "Ibuprofen 400mg"}]},
    )
    assert bulk.status_code == 201 and bulk.json()["created"] == 2 and bulk.json()["new_matches"] == 1
    matches = api.get("/api/v1/matches", headers=headers).json()
    assert len(matches) == 2
    resolved = api.post(
        f"/api/v1/matches/{matches[0]['id']}/resolve", headers=headers, json={"outcome": "returned", "units_affected": 30}
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert api.get("/api/v1/matches", params={"status": "open"}, headers=headers).json().__len__() == 1
    assert api.get("/api/v1/stats", headers=headers).json()["units_removed"] == 30
    invalid = api.post("/api/v1/holdings", headers=headers, json={"product_name": "X"})
    assert invalid.status_code == 422


def test_api_rejects_bad_and_revoked_keys(client, make_client):
    signup(client)
    key = create_key(client)
    api = make_client()
    assert api.get("/api/v1/holdings").status_code == 401
    assert api.get("/api/v1/holdings", headers={"Authorization": "Bearer bl_deadbeef_notthekeyatallnotthekey"}).status_code == 401
    assert api.get("/api/v1/holdings", headers={"Authorization": f"Bearer {key}x"}).status_code == 401
    assert api.get("/api/v1/holdings", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    key_id = re.search(r"/app/api-keys/([0-9a-f-]{36})/revoke", client.get("/app/api-keys").text).group(1)
    post_form(client, f"/app/api-keys/{key_id}/revoke")
    assert api.get("/api/v1/holdings", headers={"Authorization": f"Bearer {key}"}).status_code == 401


def test_api_keys_are_scoped_to_one_workspace(client, make_client, ingest, db):
    signup(client)
    key_a = create_key(client)
    other = make_client()
    signup(other, email="b@example.org", org="Clinic B")
    key_b = create_key(other)
    api = make_client()
    holding = api.post(
        "/api/v1/holdings", json={"product_name": "Metformin 500mg", "batch": "M1"}, headers={"Authorization": f"Bearer {key_a}"}
    ).json()["holding"]
    as_b = {"Authorization": f"Bearer {key_b}"}
    assert api.get("/api/v1/holdings", headers=as_b).json() == []
    assert api.patch(f"/api/v1/holdings/{holding['id']}", json={"product_name": "Hijack"}, headers=as_b).status_code == 404
    assert api.delete(f"/api/v1/holdings/{holding['id']}", headers=as_b).status_code == 404
    ingest("us-fda-drug")
    for match in db.scalars(select(Match)):
        assert api.post(f"/api/v1/matches/{match.id}/resolve", json={"outcome": "returned"}, headers=as_b).status_code == 404


def test_openapi_document(client):
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/check" in spec["paths"] and "/api/v1/holdings" in spec["paths"]
    assert not any(path.startswith("/app") for path in spec["paths"])


def test_patch_changes_only_the_fields_sent(client, make_client, ingest, db):
    ingest("us-fda-drug")
    signup(client)
    key = create_key(client)
    api = make_client()
    headers = {"Authorization": f"Bearer {key}"}
    created = api.post(
        "/api/v1/holdings",
        headers=headers,
        json={"product_name": "Cinacalcet 30 mg tablets", "batch": "OLD123", "quantity": 30, "location": "Shelf A"},
    ).json()
    patched = api.patch(f"/api/v1/holdings/{created['holding']['id']}", headers=headers, json={"batch": "7717825A"})
    assert patched.status_code == 200
    body = patched.json()
    assert (
        body["holding"]["quantity"] == 30 and body["holding"]["location"] == "Shelf A" and body["holding"]["batch"] == "7717825A"
    )
    assert len(body["new_matches"]) == 1
    cleared = api.patch(f"/api/v1/holdings/{created['holding']['id']}", headers=headers, json={"location": None}).json()
    assert cleared["holding"]["location"] is None and cleared["holding"]["quantity"] == 30
    assert (
        api.patch(f"/api/v1/holdings/{created['holding']['id']}", headers=headers, json={"product_name": None}).status_code == 422
    )
