import os, json; os.environ["NIGHTSHIFT_DB"]="/tmp/t.db"
if os.path.exists("/tmp/t.db"): os.remove("/tmp/t.db")
import nightshift as n
from datetime import timedelta

class M1(n.Module01):
    def snapshot(s, r, t): return [
        {"category":"dependency","title":"outdated lib","evidence":["requirements.txt:3"],"impact":"low","confidence":.9},
        {"category":"security","title":"maybe weak hash","evidence":["a.py:1"],"impact":"med","confidence":.5},
        {"category":"debt","title":"no evidence","evidence":[],"confidence":.99}]
class M2(n.Module02):
    def create_task(s, r, f): return "T1"
    def generate_plan(s, t): return {"plan_id":"P1","files":["requirements.txt"],"tags":[],"risk":"low"}
    def is_approved(s, p): return True
class M3(n.Module03):
    def prepare(s, p, t): return {"patch_ref":"patch-1","checks":{"tests":True}}
    def execute(s, p, a, t): return {"verified":True,"patch_ref":"patch-1","rollback_ref":"rb-1"}

# 1 unwired => blocked
s = n.create_schedule({"repo_id":"r","task_type":"deps","kind":"daily","spec":{"time":"02:00"},"tz":"Asia/Kolkata","policy_level":3})
rid = n.manual_run(s["id"],"me"); n.claim_next(); n.run_pipeline(rid)
assert n.q("select status from runs where id=?",(rid,))[0]["status"]=="blocked"
# 2 duplicate manual run guard + slot idempotency
rid2 = n.manual_run(s["id"],"me")
try: n.manual_run(s["id"],"me"); assert 0
except ValueError: pass
n.x("delete from runs where id=?",(rid2,))
n.x("update schedules set next_run=? where id=?",(n.iso(n.now()-timedelta(minutes=1)),s["id"]))
n.enqueue_due(); n.x("update schedules set next_run=? where id=?",(n.iso(n.now()-timedelta(minutes=1)),s["id"]))
n.enqueue_due(); n.enqueue_due()
print("scheduled runs:", n.q("select count(*) c from runs where trigger='schedule'")[0]["c"])
# 3 wired, level 3 low-risk approved => executed; suspected not planned; no-evidence dropped
n.set_adapters(M1(),M2(),M3()); n.x("delete from runs")
rid = n.manual_run(s["id"],"me"); n.claim_next(); n.run_pipeline(rid)
r=n.q("select status from runs where id=?",(rid,))[0]["status"]; print("status",r)
print({f["title"]:f["status"] for f in n.q("select * from findings where run_id=?",(rid,))})
# 4 level 1 never executes
s1 = n.create_schedule({"repo_id":"r2","task_type":"deps","kind":"custom","spec":{"every_minutes":60},"policy_level":1})
rid = n.manual_run(s1["id"],"me"); n.claim_next(); n.run_pipeline(rid)
assert not [e for e in n.q("select * from events where run_id=?",(rid,)) if e["stage"] in("prepare","execute")]
# 5 high risk => approval queue
class M2H(M2):
    def generate_plan(s,t): return {"plan_id":"P2","files":["db"],"tags":["migration"],"risk":"high"}
n.set_adapters(M1(),M2H(),M3())
rid = n.manual_run(s["id"],"me"); n.claim_next(); n.run_pipeline(rid)
print("pending approvals", len(n.q("select * from approvals where status='pending'")))
# 6 interrupted never success
rid = n.manual_run(s["id"],"me"); n.claim_next()
n.x("update runs set lease_until=? where id=?",(n.iso(n.now()-timedelta(seconds=5)),rid)); n.recover_expired()
assert n.q("select status from runs where id=?",(rid,))[0]["status"]=="interrupted"
print("ALL OK")
