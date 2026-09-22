import hmac
import os
import secrets
from contextlib import asynccontextmanager
from typing import List

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import ai
import db

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-env")
MAX_FOLLOWUPS = 2
LANGUAGES = list(ai.LANG_CODES.keys())


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Skillbee Interview Agent", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def is_admin(request: Request) -> bool:
    return request.session.get("admin") is True


def require_admin_api(request: Request):
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Admin login required")


@app.get("/")
def root():
    return RedirectResponse("/admin")


@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@app.post("/admin/login")
def login(request: Request, password: str = Form(...)):
    if hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        request.session["admin"] = True
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {"error": "Wrong password."}, status_code=401)


@app.get("/admin/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request):
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    base = str(request.base_url).rstrip("/")
    with db.get_db() as conn:
        interviews = conn.execute(
            "SELECT i.*, (SELECT COUNT(*) FROM questions q WHERE q.interview_id = i.id) AS n_questions "
            "FROM interviews i ORDER BY i.id DESC").fetchall()
        rows = conn.execute(
            "SELECT v.*, i.title AS interview_title FROM invites v "
            "JOIN interviews i ON i.id = v.interview_id ORDER BY v.id DESC").fetchall()
        invites = []
        for r in rows:
            total = conn.execute("SELECT COUNT(*) FROM questions WHERE interview_id = ?",
                                 (r["interview_id"],)).fetchone()[0]
            invites.append({**dict(r), "total": total, "avg": db.average_score(conn, r["id"]),
                            "link": f"{base}/i/{r['token']}"})
    return templates.TemplateResponse(request, "admin.html", {
        "interviews": interviews, "invites": invites, "languages": LANGUAGES})


class QuestionIn(BaseModel):
    question: str
    ideal_answer: str


class InterviewIn(BaseModel):
    title: str
    domain: str
    questions: List[QuestionIn]


@app.post("/admin/interviews")
def create_interview(request: Request, body: InterviewIn):
    require_admin_api(request)
    qs = [q.model_dump() for q in body.questions if q.question.strip() and q.ideal_answer.strip()]
    if not body.title.strip() or not qs:
        raise HTTPException(400, "Add a title and at least one question with an ideal answer.")
    with db.get_db() as conn:
        iid = db.create_interview(conn, body.title.strip(), body.domain.strip() or body.title.strip(), qs)
    return {"id": iid}


@app.post("/admin/interviews/{interview_id}/delete")
def delete_interview(request: Request, interview_id: int):
    require_admin_api(request)
    with db.get_db() as conn:
        conn.execute("DELETE FROM interviews WHERE id = ?", (interview_id,))
    return {"ok": True}


@app.post("/admin/invites")
def create_invite(request: Request, interview_id: int = Form(...),
                  candidate_name: str = Form(...), language: str = Form("English")):
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    if language not in ai.LANG_CODES or not candidate_name.strip():
        raise HTTPException(400, "Invalid candidate name or language.")
    token = secrets.token_urlsafe(9)
    code = f"{secrets.randbelow(10**6):06d}"
    with db.get_db() as conn:
        if not conn.execute("SELECT 1 FROM interviews WHERE id = ?", (interview_id,)).fetchone():
            raise HTTPException(404, "Interview not found.")
        conn.execute(
            "INSERT INTO invites (interview_id, token, access_code, candidate_name, language) VALUES (?,?,?,?,?)",
            (interview_id, token, code, candidate_name.strip(), language))
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/invite/{invite_id}", response_class=HTMLResponse)
def invite_detail(request: Request, invite_id: int):
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    with db.get_db() as conn:
        inv = conn.execute(
            "SELECT v.*, i.title, i.domain FROM invites v JOIN interviews i ON i.id = v.interview_id "
            "WHERE v.id = ?", (invite_id,)).fetchone()
        if not inv:
            raise HTTPException(404, "Invite not found.")
        per_q = db.question_scores(conn, invite_id)
        follow_ups = {}
        for t in conn.execute("SELECT q_index FROM turns WHERE invite_id = ?", (invite_id,)):
            follow_ups[t["q_index"]] = follow_ups.get(t["q_index"], 0) + 1
        rows = [{**dict(t), "follow_ups": follow_ups[i] - 1} for i, t in sorted(per_q.items())]
        turns = conn.execute("SELECT * FROM turns WHERE invite_id = ? ORDER BY id", (invite_id,)).fetchall()
        total = len(db.get_questions(conn, inv["interview_id"]))
        avg = db.average_score(conn, invite_id)
    return templates.TemplateResponse(request, "admin_invite.html", {
        "inv": inv, "rows": rows, "turns": turns, "total": total, "avg": avg})


@app.post("/admin/invite/{invite_id}/scorecard")
def make_scorecard(request: Request, invite_id: int):
    require_admin_api(request)
    with db.get_db() as conn:
        inv = conn.execute(
            "SELECT v.*, i.domain FROM invites v JOIN interviews i ON i.id = v.interview_id WHERE v.id = ?",
            (invite_id,)).fetchone()
        if not inv:
            raise HTTPException(404, "Invite not found.")
        per_q = [{"question": t["question"], "answer": t["answer"], "score": t["score"], "note": t["note"]}
                 for _, t in sorted(db.question_scores(conn, invite_id).items())]
    if not per_q:
        raise HTTPException(400, "This candidate has not answered anything yet.")
    try:
        return {"scorecard": ai.scorecard(inv["domain"], per_q)}
    except Exception as e:
        raise HTTPException(502, f"Scorecard generation failed: {e}")


def get_invite(conn, token: str):
    inv = conn.execute(
        "SELECT v.*, i.title, i.domain FROM invites v JOIN interviews i ON i.id = v.interview_id "
        "WHERE v.token = ?", (token,)).fetchone()
    if not inv:
        raise HTTPException(404, "This interview link is not valid.")
    return inv


def require_candidate(request: Request, token: str):
    if request.session.get(f"cand_{token}") is not True:
        raise HTTPException(401, "Enter your access code first.")


@app.get("/i/{token}", response_class=HTMLResponse)
def candidate_page(request: Request, token: str):
    with db.get_db() as conn:
        inv = get_invite(conn, token)
    return templates.TemplateResponse(request, "candidate.html", {
        "token": token, "name": inv["candidate_name"], "title": inv["title"], "language": inv["language"]})


def state_payload(conn, inv) -> dict:
    questions = db.get_questions(conn, inv["interview_id"])
    finished = inv["status"] == "finished"
    return {
        "finished": finished,
        "total": len(questions),
        "number": min(inv["current_index"] + 1, len(questions)),
        "question": None if finished else questions[inv["current_index"]]["question"],
    }


@app.get("/api/i/{token}/state")
def candidate_state(request: Request, token: str):
    with db.get_db() as conn:
        inv = get_invite(conn, token)
        if request.session.get(f"cand_{token}") is not True:
            return {"authed": False, "finished": inv["status"] == "finished"}
        return {"authed": True, **state_payload(conn, inv)}


@app.post("/api/i/{token}/start")
def candidate_start(request: Request, token: str, code: str = Form(...)):
    with db.get_db() as conn:
        inv = get_invite(conn, token)
        if not hmac.compare_digest(code.strip().encode(), inv["access_code"].encode()):
            raise HTTPException(403, "Wrong access code.")
        request.session[f"cand_{token}"] = True
        if inv["status"] == "pending":
            conn.execute("UPDATE invites SET status='in_progress', started_at=datetime('now') WHERE id=?",
                         (inv["id"],))
            inv = get_invite(conn, token)
        return {"authed": True, **state_payload(conn, inv)}


@app.post("/api/i/{token}/answer")
def candidate_answer(request: Request, token: str, audio: UploadFile = File(...)):
    require_candidate(request, token)
    data = audio.file.read()
    if len(data) < 1500:
        raise HTTPException(400, "Recording is too short. Please try again.")
    ext = "m4a" if "mp4" in (audio.content_type or "") else "webm"

    with db.get_db() as conn:
        inv = get_invite(conn, token)
        if inv["status"] == "finished":
            return {"finished": True, "reply": "This interview is already complete.", "audio": None}
        questions = db.get_questions(conn, inv["interview_id"])
        idx = inv["current_index"]
        ref = questions[idx]
        history = []
        for t in conn.execute("SELECT answer, reply FROM turns WHERE invite_id=? ORDER BY id", (inv["id"],)):
            history += [{"role": "user", "content": t["answer"]},
                        {"role": "assistant", "content": t["reply"]}]

    try:
        text = ai.transcribe(data, f"answer.{ext}", inv["language"])
    except Exception:
        raise HTTPException(502, "Could not transcribe the recording. Please try again.")
    if not text:
        raise HTTPException(400, "We could not hear anything. Please speak closer to the microphone.")
    try:
        result = ai.interview_turn(history, inv["domain"], ref["question"], ref["ideal_answer"],
                                   inv["language"], text)
    except Exception:
        raise HTTPException(502, "The interviewer is unavailable right now. Please try again.")

    advance = result["decision"] == "advance" or inv["follow_up_count"] >= MAX_FOLLOWUPS
    new_idx = idx + 1 if advance else idx
    finished = new_idx >= len(questions)

    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO turns (invite_id, q_index, question, answer, reply, score, decision, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (inv["id"], idx, ref["question"], text, result["reply"], result["score"],
             result["decision"], result["note"]))
        conn.execute(
            "UPDATE invites SET current_index=?, follow_up_count=?, status=?, "
            "finished_at=CASE WHEN ? THEN datetime('now') ELSE finished_at END WHERE id=?",
            (new_idx, 0 if advance else inv["follow_up_count"] + 1,
             "finished" if finished else "in_progress", finished, inv["id"]))
        inv = get_invite(conn, token)
        payload = state_payload(conn, inv)

    return {"transcript": text, "reply": result["reply"],
            "audio": ai.speak(result["reply"], inv["language"]), **payload}
