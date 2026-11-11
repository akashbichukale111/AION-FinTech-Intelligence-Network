# FORGEX Module 03 — wiring (3 steps)
1. Copy `engineering/` to `app/engineering/`; in main app: `from app.engineering.router import router as eng_router; app.include_router(eng_router)`
2. In `engineering/service.py` fix the two adapter imports:
   - `load_plan()` -> your Module 02 get-plan function (must return {id,status:'approved',repository_path,steps:[{id,title,kind?,files?,evidence?,ops?}]})
   - `load_evidence()` -> your Module 01 repo evidence function (optional)
   `ops` = [{op:'replace',file,find,replace} | {op:'create',file,content} | {op:'append',file,content}]
3. Put `frontend/EngineeringWorkspace.jsx` in your React app and route to it.
Env: FORGEX_ENG_DB, FORGEX_WORK_ROOT, FORGEX_LLM_API_KEY (optional; LLM adapter is a stub).
