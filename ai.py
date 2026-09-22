import base64
import io
import json
import os

from groq import Groq
from gtts import gTTS

LANG_CODES = {"English": "en", "Hindi": "hi", "German": "de"}
LLM_MODEL = "openai/gpt-oss-120b"
STT_MODEL = "whisper-large-v3"

_client = None


def client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


def transcribe(audio_bytes: bytes, filename: str, language: str) -> str:
    result = client().audio.transcriptions.create(
        file=(filename, audio_bytes),
        model=STT_MODEL,
        language=LANG_CODES.get(language, "en"),
    )
    return result.text.strip()


INTERVIEWER_SYSTEM = """You are a seasoned technical interviewer conducting a structured screening.

Rules you MUST follow:
1. NEVER reveal the ideal answer or benchmark answer to the candidate.
2. NEVER tell the candidate their score.
3. Stay focused on the CURRENT question, do not skip ahead.
4. If the answer is weak or missing key points, ask ONE targeted follow-up question.
5. If the answer is strong, briefly acknowledge it and move on.
6. If the candidate is completely off-track, give a gentle hint, not the answer.
7. Keep "reply" under 3 sentences. No filler like "Great question!".
8. "reply" must be written in {language}.

Current domain: {domain}
Current question: "{question}"
Benchmark (DO NOT REVEAL): "{ideal_answer}"

Respond ONLY with a JSON object in this exact shape:
{{
  "reply": "what you say to the candidate, in {language}",
  "decision": "advance" or "follow_up",
  "score": integer 0-10 rating the candidate's answer so far against the benchmark,
  "note": "one short English sentence for the recruiter: what was covered / missed"
}}
"""


def interview_turn(history: list, domain: str, question: str, ideal_answer: str,
                   language: str, candidate_text: str) -> dict:
    system = INTERVIEWER_SYSTEM.format(language=language, domain=domain,
                                       question=question, ideal_answer=ideal_answer)
    messages = [{"role": "system", "content": system}] + history + [
        {"role": "user", "content": candidate_text}
    ]
    resp = client().chat.completions.create(
        model=LLM_MODEL, messages=messages, temperature=0.35, max_tokens=400,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content
    try:
        data = json.loads(raw)
        return {
            "reply": str(data.get("reply", "")).strip() or "Please continue.",
            "decision": "advance" if data.get("decision") == "advance" else "follow_up",
            "score": max(0, min(10, int(data.get("score", 0)))),
            "note": str(data.get("note", "")).strip(),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"reply": raw.strip(), "decision": "follow_up", "score": 0, "note": "Model output could not be parsed"}


def speak(text: str, language: str):
    try:
        buf = io.BytesIO()
        gTTS(text=text, lang=LANG_CODES.get(language, "en")).write_to_fp(buf)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


SCORECARD_SYSTEM = """You are a hiring manager writing structured interview feedback for the recruiter.
Produce a scorecard in this exact format:

## Overall Score: X/10

### Strengths
- (bullet per strength)

### Areas to Improve
- (bullet per gap)

### Recommendation
One sentence: Proceed / On the fence / Do not proceed, and why.

Base the overall score on the per-question scores provided. Write in English."""


def scorecard(domain: str, per_question: list) -> str:
    resp = client().chat.completions.create(
        model=LLM_MODEL, temperature=0.3, max_tokens=600,
        messages=[
            {"role": "system", "content": SCORECARD_SYSTEM},
            {"role": "user", "content": f"Domain: {domain}\n\n{json.dumps(per_question, indent=2, ensure_ascii=False)}"},
        ],
    )
    return resp.choices[0].message.content.strip()
