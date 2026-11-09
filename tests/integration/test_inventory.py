import uuid

from sqlalchemy import select

from batchlight.models import Holding
from tests.conftest import post_form, signup

ITEM = {
    "product_name": "Amoxicillin 500 mg capsules",
    "lot": "AMX-2401",
    "expiry_date": "04/2027",
    "quantity": "120",
    "manufacturer": "Example Pharma Ltd",
}


def test_add_edit_and_archive_holding(client, db):
    signup(client)
    response = post_form(client, "/app/inventory", ITEM)
    assert response.status_code == 303
    holding = db.scalar(select(Holding))
    assert (holding.lot_key, str(holding.expiry_date), holding.quantity) == ("AMX2401", "2027-04-30", 120)
    assert holding.name_tokens == ["amoxicillin"]
    page = client.get("/app/inventory")
    assert "Amoxicillin 500 mg capsules" in page.text and "AMX-2401" in page.text

    edited = post_form(client, f"/app/inventory/{holding.id}/edit", {**ITEM, "quantity": "80"})
    assert edited.status_code == 303
    db.refresh(holding)
    assert holding.quantity == 80

    assert post_form(client, f"/app/inventory/{holding.id}/archive").status_code == 303
    db.refresh(holding)
    assert holding.archived_at is not None
    assert "Nothing here yet." in client.get("/app/inventory").text


def test_forms_carry_a_working_csrf_token(client, db):
    import re

    page = client.get("/signup")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert token == client.cookies["bl_csrf"]
    response = client.post(
        "/signup",
        data={
            "csrf_token": token,
            "full_name": "Real Form",
            "email": "form@example.org",
            "password": "a long enough password",
            "org_name": "Form Pharmacy",
            "org_kind": "pharmacy",
            "country_code": "KE",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    inventory_form = client.get("/app/inventory/new").text
    assert re.search(r'name="csrf_token" value="([^"]+)"', inventory_form).group(1) == token


def test_holding_validation(client, db):
    signup(client)
    response = post_form(
        client, "/app/inventory", {"product_name": "A", "expiry_date": "next year", "quantity": "-3", "product_code": "<script>"}
    )
    assert response.status_code == 422
    assert "Use a date like 2027-04-30" in response.text
    assert db.scalar(select(Holding)) is None


def test_tenant_isolation(client, make_client, db):
    signup(client)
    post_form(client, "/app/inventory", ITEM)
    holding = db.scalar(select(Holding))
    intruder = make_client()
    signup(intruder, email="other@example.org", org="Other Clinic")
    assert intruder.get(f"/app/inventory/{holding.id}/edit").status_code == 404
    assert post_form(intruder, f"/app/inventory/{holding.id}/edit", ITEM).status_code == 404
    assert post_form(intruder, f"/app/inventory/{holding.id}/archive").status_code == 404
    assert "Amoxicillin" not in intruder.get("/app/inventory").text
    assert "Amoxicillin" not in intruder.get("/app/inventory/export.csv").text
    assert intruder.get(f"/app/matches/{uuid.uuid4()}").status_code == 404
    switch = post_form(intruder, "/app/workspaces/switch", {"org_id": str(holding.org_id)})
    assert switch.status_code == 404


def test_csv_import_with_aliases_and_duplicates(client, db):
    signup(client)
    csv_text = (
        "Product,Lot Number,Expiry,Qty,Manufacturer\n"
        "Paracetamol 500mg tablets,PCM001,2027-01-31,500,Acme\n"
        "Metformin 850 mg,MET-77,30/06/2027,90,\n"
        "Paracetamol 500mg tablets,PCM001,2027-01-31,500,Acme\n"
    )
    response = client.post(
        "/app/inventory/import",
        files={"file": ("stock.csv", csv_text, "text/csv")},
        data={"csrf_token": client.cookies["bl_csrf"]},
    )
    assert response.status_code == 200
    assert "Imported 2 items" in response.text and "1 row was already in your list" in response.text
    assert {h.lot_key for h in db.scalars(select(Holding))} == {"PCM001", "MET77"}


def test_csv_import_with_errors_imports_nothing(client, db):
    signup(client)
    csv_text = "product_name,batch,expiry_date,quantity\nGood item,B1,2027-01-01,5\n,B2,2027-01-01,5\nBad date,B3,soon,x\n"
    response = client.post(
        "/app/inventory/import",
        files={"file": ("stock.csv", csv_text, "text/csv")},
        data={"csrf_token": client.cookies["bl_csrf"]},
    )
    assert response.status_code == 422
    assert "Line 3" in response.text and "Line 4" in response.text
    assert db.scalar(select(Holding)) is None


def test_csv_import_requires_product_column(client):
    signup(client)
    response = client.post(
        "/app/inventory/import",
        files={"file": ("x.csv", "foo,bar\n1,2\n", "text/csv")},
        data={"csrf_token": client.cookies["bl_csrf"]},
    )
    assert response.status_code == 422 and "product_name column" in response.text


def test_export_csv_round_trip(client):
    signup(client)
    post_form(client, "/app/inventory", ITEM)
    exported = client.get("/app/inventory/export.csv")
    assert exported.headers["content-type"].startswith("text/csv")
    assert "Amoxicillin 500 mg capsules,,,,Example Pharma Ltd,,AMX-2401,2027-04-30,120" in exported.text


def test_export_neutralises_spreadsheet_formulas(client):
    signup(client)
    post_form(client, "/app/inventory", {"product_name": '=HYPERLINK("http://evil.example","x")', "notes": "+1 call me"})
    exported = client.get("/app/inventory/export.csv").text
    assert "'=HYPERLINK" in exported and "'+1 call me" in exported


def test_member_can_leave_a_workspace(client, make_client, db):
    import re

    from batchlight.models import Membership, User
    from batchlight.services.email import OUTBOX

    signup(client)
    post_form(client, "/app/team/invite", {"email": "member@example.org", "role": "member"})
    link = re.search(r"http://testserver(/invite/\S+)", OUTBOX[-1].get_content()).group(1)
    member = make_client()
    post_form(member, link, {"full_name": "Member Person", "password": "member password 1"})
    user = db.scalar(select(User).where(User.email == "member@example.org"))
    owner = db.scalar(select(User).where(User.email == "owner@example.org"))
    assert post_form(member, f"/app/team/members/{owner.id}/remove").status_code == 403
    assert post_form(member, f"/app/team/members/{user.id}/remove").status_code == 303
    assert db.scalar(select(Membership).where(Membership.user_id == user.id)) is None
