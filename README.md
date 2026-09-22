# Skillbee Voice Interview Agent v2

Admin panel + candidate portal. Whisper (STT) -> LLaMA 3.3 (interviewer) -> gTTS (TTS), stored in SQLite.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in GROQ_API_KEY, ADMIN_PASSWORD, SECRET_KEY
uvicorn main:app --reload
```
- Admin: http://localhost:8000/admin
- Candidate: the unique link the admin copies from the dashboard (`/i/<token>`) plus the 6-digit code.

The microphone works only on `localhost` or HTTPS.

## Flow
1. Admin signs in, creates a question set (questions + ideal answers).
2. Admin creates a link and code for a candidate (name + language).
3. Candidate opens the link, enters the code, and answers by voice. Candidates never see scores.
4. Admin opens the candidate's results: per-question scores, notes, transcript, and a generated scorecard.

## Files
- `main.py`  routes (admin + candidate)
- `ai.py`    Whisper / LLaMA / gTTS pipeline
- `db.py`    SQLite schema and helpers (first run imports `interview_config.json`)
- `templates/`, `static/`  UI
