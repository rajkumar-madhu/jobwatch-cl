import uuid
from sqlalchemy import text
from cronsentinel import pingtokens
from cronsentinel.db import system_session
import os
N = int(os.getenv("JOBS", "2000"))
oid = uuid.uuid4()
toks = []
with system_session() as s:
    s.execute(text("INSERT INTO organizations (id, name, slug, plan) VALUES (:i,'load',:s,'enterprise')"), {"i": oid, "s": f"load-{oid.hex[:6]}"})
    ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o,'w') RETURNING id"), {"o": oid}).scalar()
    rows = []
    for i in range(N):
        raw, h, hint = pingtokens.new_token(); toks.append(raw)
        rows.append({"o": oid, "w": ws, "n": f"load-{i}", "h": h, "hint": hint})
    s.execute(text("INSERT INTO jobs (org_id, workspace_id, name, kind, heartbeat_token_hash, heartbeat_token_hint, status) VALUES (:o,:w,:n,'heartbeat',:h,:hint,'unknown')"), rows)
open("tokens.txt", "w").write("\n".join(toks))
open("org.txt", "w").write(str(oid))
print("seeded", N, "jobs in org", oid)
