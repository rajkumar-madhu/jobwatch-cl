"""R56: SCIM groups (Okta and Entra ID dialects) and group → role mapping."""
import uuid

from sqlalchemy import text

from tests.integration.test_scim import scim  # noqa: F401  (fixture: business org, verified domain, SCIM token, owner key)

GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"


def _user(c, h, dom, name):
    r = c.post("/scim/v2/Users", headers=h, json={"userName": f"{name}@{dom}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _role(org_id, uid):
    from cronsentinel.db import system_session
    with system_session() as s:
        return s.execute(text("SELECT role FROM memberships WHERE org_id=:o AND user_id=:u"), {"o": org_id, "u": uid}).scalar()


def _map(c, owner, gid, role):
    r = c.put(f"/api/v1/sso/scim-groups/{gid}/role", headers=owner, json={"role": role})
    assert r.status_code == 200, r.text


def test_okta_group_push_and_role_mapping(scim, org):  # noqa: F811
    c, h, dom, owner = scim
    ana, raj = _user(c, h, dom, "ana"), _user(c, h, dom, "raj")
    assert _role(org["id"], ana) == "viewer"
    g = c.post("/scim/v2/Groups", headers=h, json={"schemas": [GROUP], "displayName": "JobWatch SRE", "members": [{"value": ana}]})
    assert g.status_code == 201 and g.json()["members"] == [{"value": ana, "display": f"ana@{dom}"}]
    gid = g.json()["id"]
    assert _role(org["id"], ana) == "viewer"                     # unmapped group: no effect
    _map(c, owner, gid, "sre")
    assert _role(org["id"], ana) == "sre" and _role(org["id"], raj) == "viewer"
    # Okta: add with path members; remove with a value filter path; rename with value object
    c.patch(f"/scim/v2/Groups/{gid}", headers=h, json={"Operations": [{"op": "add", "path": "members", "value": [{"value": raj}]}]})
    assert _role(org["id"], raj) == "sre"
    c.patch(f"/scim/v2/Groups/{gid}", headers=h, json={"Operations": [{"op": "remove", "path": f'members[value eq "{ana}"]'}]})
    assert _role(org["id"], ana) == "viewer"                     # left the only mapped group → default role
    r = c.patch(f"/scim/v2/Groups/{gid}", headers=h, json={"Operations": [{"op": "replace", "value": {"id": gid, "displayName": "JobWatch Ops"}}]})
    assert r.json()["displayName"] == "JobWatch Ops"
    assert c.get("/scim/v2/Groups", headers=h, params={"filter": 'displayName eq "jobwatch ops"'}).json()["totalResults"] == 1


def test_entra_dialect_highest_role_wins_and_delete_reverts(scim, org):  # noqa: F811
    c, h, dom, owner = scim
    u = _user(c, h, dom, "lee")
    g1 = c.post("/scim/v2/Groups", headers=h, json={"displayName": "Devs"}).json()["id"]
    g2 = c.post("/scim/v2/Groups", headers=h, json={"displayName": "Admins"}).json()["id"]
    _map(c, owner, g1, "developer"); _map(c, owner, g2, "admin")
    for g in (g1, g2):   # Entra: capitalised ops, members with value lists
        assert c.patch(f"/scim/v2/Groups/{g}", headers=h, json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                       "Operations": [{"op": "Add", "path": "members", "value": [{"value": u}]}]}).status_code == 200
    assert _role(org["id"], u) == "admin"
    c.patch(f"/scim/v2/Groups/{g2}", headers=h, json={"Operations": [{"op": "Remove", "path": "members", "value": [{"value": u}]}]})
    assert _role(org["id"], u) == "developer"
    assert c.delete(f"/scim/v2/Groups/{g1}", headers=h).status_code == 204
    assert _role(org["id"], u) == "viewer"
    # Entra lists groups without members
    assert "members" not in c.get("/scim/v2/Groups", headers=h, params={"excludedAttributes": "members"}).json()["Resources"][0]


def test_owners_and_deactivated_people_are_not_changed(scim, org):  # noqa: F811
    c, h, dom, owner = scim
    from cronsentinel.db import system_session
    boss = _user(c, h, dom, "boss")
    with system_session() as s:
        s.execute(text("UPDATE memberships SET role='owner' WHERE org_id=:o AND user_id=:u"), {"o": org["id"], "u": boss})
    gone = _user(c, h, dom, "gone")
    c.patch(f"/scim/v2/Users/{gone}", headers=h, json={"Operations": [{"op": "replace", "path": "active", "value": False}]})
    g = c.post("/scim/v2/Groups", headers=h, json={"displayName": "Viewers", "members": [{"value": boss}, {"value": gone}]}).json()["id"]
    _map(c, owner, g, "devops")
    assert _role(org["id"], boss) == "owner"                     # never changed by sync
    assert _role(org["id"], gone) is None                        # still no access
    c.patch(f"/scim/v2/Users/{gone}", headers=h, json={"Operations": [{"op": "replace", "path": "active", "value": True}]})
    assert _role(org["id"], gone) == "devops"                    # reactivated with the group's role


def test_hand_set_role_is_left_alone_and_mapping_is_owner_only(scim, org):  # noqa: F811
    c, h, dom, owner = scim
    from cronsentinel.db import system_session
    u = _user(c, h, dom, "kim")
    with system_session() as s:
        s.execute(text("UPDATE memberships SET role='sre' WHERE org_id=:o AND user_id=:u"), {"o": org["id"], "u": u})
    g = c.post("/scim/v2/Groups", headers=h, json={"displayName": "Unmapped", "members": [{"value": u}]}).json()["id"]
    c.patch(f"/scim/v2/Groups/{g}", headers=h, json={"Operations": [{"op": "remove", "path": "members"}]})
    assert _role(org["id"], u) == "sre"                          # no mapped group ever set it: untouched
    # an admin cannot map groups (a mapping can grant admin)
    from cronsentinel.auth import generate_api_key, hash_secret
    prefix, raw = generate_api_key()
    with system_session() as s:
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o,'adm',:p,:k,'admin')"), {"o": org["id"], "p": prefix, "k": hash_secret(raw)})
    assert c.put(f"/api/v1/sso/scim-groups/{g}/role", headers={"X-API-Key": raw}, json={"role": "admin"}).status_code == 403
    assert c.put(f"/api/v1/sso/scim-groups/{g}/role", headers=owner, json={"role": "owner"}).status_code == 422
    listed = c.get("/api/v1/sso/scim-groups", headers=owner).json()
    assert any(x["display_name"] == "Unmapped" and x["members"] == 0 for x in listed)


def test_unknown_members_ignored_and_duplicates_refused(scim, org):  # noqa: F811
    c, h, dom, owner = scim
    g = c.post("/scim/v2/Groups", headers=h, json={"displayName": "Mixed", "members": [{"value": str(uuid.uuid4())}, {"value": "not-a-uuid"}]})
    assert g.status_code == 201 and g.json()["members"] == []
    assert c.post("/scim/v2/Groups", headers=h, json={"displayName": "mixed"}).status_code == 409
    assert c.get("/scim/v2/ResourceTypes").json()["totalResults"] == 2


def test_role_changes_are_audited_with_who_did_it(scim, org):  # noqa: F811
    c, h, dom, owner = scim
    from cronsentinel.db import system_session
    u = _user(c, h, dom, "aud")
    g = c.post("/scim/v2/Groups", headers=h, json={"displayName": "Audited", "members": [{"value": u}]}).json()["id"]
    _map(c, owner, g, "devops")                                                  # owner's mapping → role change
    c.patch(f"/scim/v2/Groups/{g}", headers=h, json={"Operations": [{"op": "remove", "path": "members"}]})   # directory push
    with system_session() as s:
        rows = s.execute(text("""SELECT actor_type, actor_id, payload FROM audit_logs WHERE org_id=:o AND action='scim.role_sync'
                                 AND target_id=:u ORDER BY ts"""), {"o": org["id"], "u": u}).all()
    assert [(r.payload["from"], r.payload["to"]) for r in rows] == [("viewer", "devops"), ("devops", "viewer")]
    assert all(r.actor_id is not None for r in rows)
