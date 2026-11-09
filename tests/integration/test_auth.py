import re

from sqlalchemy import select

from batchlight.models import Membership, Organization, User
from batchlight.services.email import OUTBOX
from tests.conftest import csrf, post_form, signup


def test_signup_creates_owner_workspace_and_signs_in(client, db):
    response = signup(client)
    assert response.headers["location"].startswith("/app/inventory/new")
    user = db.scalar(select(User).where(User.email == "owner@example.org"))
    assert user and user.password_hash.startswith("$argon2id$")
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    assert membership.role == "owner"
    assert db.get(Organization, membership.org_id).country_code == "NG"
    page = client.get("/app")
    assert page.status_code == 200
    assert "Amina Pharmacy" in page.text


def test_signup_validation_errors_are_shown(client, db):
    response = post_form(
        client,
        "/signup",
        {
            "full_name": "A",
            "email": "not-an-email",
            "password": "short",
            "org_name": "",
            "org_kind": "spaceship",
            "country_code": "ZZ",
        },
    )
    assert response.status_code == 422
    for message in ["Enter your name.", "Enter a valid email address.", "Use at least 10 characters.", "Choose a country."]:
        assert message in response.text
    assert db.scalar(select(User)) is None


def test_signup_rejects_existing_email_without_creating_anything(client, make_client, db):
    signup(client)
    other = make_client()
    response = post_form(
        other,
        "/signup",
        {
            "full_name": "Someone",
            "email": "OWNER@example.org",
            "password": "another long password",
            "org_name": "Second",
            "org_kind": "clinic",
            "country_code": "KE",
        },
    )
    assert response.status_code == 422
    assert db.scalar(select(Organization).where(Organization.name == "Second")) is None


def test_workspace_requires_login(client):
    response = client.get("/app/inventory?q=x", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/app/inventory%3Fq%3Dx"


def test_login_logout_and_wrong_password(client, make_client):
    signup(client)
    fresh = make_client()
    bad = post_form(fresh, "/login", {"email": "owner@example.org", "password": "wrong password!!"})
    assert bad.status_code == 401
    good = post_form(fresh, "/login", {"email": "owner@example.org", "password": "correct horse battery", "next": "/app/team"})
    assert good.status_code == 303 and good.headers["location"] == "/app/team"
    assert fresh.get("/app").status_code == 200
    out = post_form(fresh, "/logout")
    assert out.status_code == 303
    assert fresh.get("/app", follow_redirects=False).status_code == 303


def test_open_redirect_is_blocked(client, make_client):
    signup(client)
    fresh = make_client()
    response = post_form(
        fresh, "/login", {"email": "owner@example.org", "password": "correct horse battery", "next": "//evil.example"}
    )
    assert response.headers["location"] == "/app"


def test_login_is_throttled_after_repeated_failures(client, make_client):
    signup(client)
    fresh = make_client()
    for _ in range(4):
        assert post_form(fresh, "/login", {"email": "owner@example.org", "password": "nope nope nope"}).status_code == 401
    blocked = post_form(fresh, "/login", {"email": "owner@example.org", "password": "correct horse battery"})
    assert blocked.status_code == 429


def test_post_without_csrf_token_is_rejected(client):
    csrf(client)
    response = client.post("/login", data={"email": "a@b.org", "password": "x"}, follow_redirects=False)
    assert response.status_code == 400
    assert "Form expired" in response.text
    forged = client.post("/login", data={"email": "a@b.org", "password": "x", "csrf_token": "forged"}, follow_redirects=False)
    assert forged.status_code == 400


def test_password_reset_flow_revokes_old_sessions(client, make_client):
    signup(client)
    other = make_client()
    response = post_form(other, "/forgot-password", {"email": "owner@example.org"})
    assert "If an account exists" in response.text
    link = re.search(r"http://testserver(/reset-password/\S+)", OUTBOX[-1].get_content()).group(1)
    assert post_form(other, link, {"password": "short"}).status_code == 422
    done = post_form(other, link, {"password": "a brand new passphrase"})
    assert done.status_code == 303
    assert client.get("/app", follow_redirects=False).status_code == 303  # the original session was revoked
    assert post_form(other, link, {"password": "yet another passphrase"}).status_code == 400  # one-time link


def test_forgot_password_does_not_reveal_unknown_emails(client):
    response = post_form(client, "/forgot-password", {"email": "nobody@example.org"})
    assert response.status_code == 200 and "If an account exists" in response.text
    assert OUTBOX == []


def test_invitation_flow_for_new_user(client, make_client, db):
    signup(client)
    response = post_form(client, "/app/team/invite", {"email": "tech@example.org", "role": "member"})
    assert response.status_code == 200
    link = re.search(r"http://testserver(/invite/\S+)", OUTBOX[-1].get_content()).group(1)
    invitee = make_client()
    assert "Join Amina Pharmacy" in invitee.get(link).text
    accepted = post_form(invitee, link, {"full_name": "Tech Person", "password": "pharmacy technician 1"})
    assert accepted.status_code == 303
    user = db.scalar(select(User).where(User.email == "tech@example.org"))
    assert db.scalar(select(Membership.role).where(Membership.user_id == user.id)) == "member"
    assert invitee.get("/app").status_code == 200
    # members cannot invite or manage API keys
    assert post_form(invitee, "/app/team/invite", {"email": "x@example.org", "role": "member"}).status_code == 403
    assert invitee.get("/app/api-keys").status_code == 403


def test_last_owner_cannot_be_demoted(client, db):
    signup(client)
    owner = db.scalar(select(User))
    response = post_form(client, f"/app/team/members/{owner.id}/role", {"role": "member"})
    assert response.status_code == 303
    assert db.scalar(select(Membership.role).where(Membership.user_id == owner.id)) == "owner"
