import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from mangum import Mangum
from openai import AsyncOpenAI

app = FastAPI(title="Email LLM Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set")

async def supabase_request(method: str, path: str, json_data: dict = None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    async with httpx.AsyncClient(proxy=os.getenv("HTTP_PROXY"), timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=json_data)
        else:
            raise ValueError("Unsupported method")
        resp.raise_for_status()
        if not resp.content:
            return []
        return resp.json()

class LetterCreate(BaseModel):
    thread_id: Optional[int] = None
    sender: str
    subject: str
    body: str

class LetterOut(BaseModel):
    id: int
    thread_id: int
    sender: str
    subject: str
    body: str
    created_at: datetime

class ReplyRequest(BaseModel):
    message: str
    provider: str

async def call_deepseek(prompt: str, api_key: str) -> str:
    """
    Отправляет запрос к DeepSeek API и возвращает ответ.
    """
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
    )

    try:
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "You are a helpful email assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка при вызове DeepSeek API: {e}")
        raise HTTPException(status_code=500, detail=f"DeepSeek API error: {str(e)}")

@app.post("/api/letters", response_model=LetterOut)
async def create_letter(letter: LetterCreate):
    if letter.thread_id is None:
        result = await supabase_request("GET", "letters?select=thread_id&order=thread_id.desc&limit=1")
        max_id = result[0]["thread_id"] if result else 0
        thread_id = max_id + 1
    else:
        thread_id = letter.thread_id
    new_letter = {
        "thread_id": thread_id,
        "sender": letter.sender,
        "subject": letter.subject,
        "body": letter.body,
        "created_at": datetime.utcnow().isoformat()
    }
    result = await supabase_request("POST", "letters", json_data=new_letter)
    created = result[0] if isinstance(result, list) else result
    return LetterOut(**created)

@app.get("/api/letters", response_model=List[LetterOut])
async def list_letters():
    result = await supabase_request("GET", "letters?order=created_at.desc")
    return [LetterOut(**item) for item in result]

@app.get("/api/threads/{thread_id}", response_model=List[LetterOut])
async def get_thread(thread_id: int):
    result = await supabase_request("GET", f"letters?thread_id=eq.{thread_id}&order=created_at.asc")
    if not result:
        raise HTTPException(404, "Thread not found")
    return [LetterOut(**item) for item in result]

@app.post("/api/letters/{letter_id}/reply")
async def reply_to_letter(letter_id: int, req: ReplyRequest):
    letters = await supabase_request("GET", f"letters?id=eq.{letter_id}")
    if not letters:
        raise HTTPException(404, "Letter not found")
    original = letters[0]
    thread_id = original["thread_id"]
    thread = await supabase_request("GET", f"letters?thread_id=eq.{thread_id}&order=created_at.asc")
    context = ""
    for h in thread:
        context += f"From: {h['sender']}\nSubject: {h['subject']}\n{h['body']}\n\n"
    context += f"From: User\nSubject: Re: {original['subject']}\n{req.message}\n\n"
    prompt = f"""Ты — помощник в почтовом клиенте. Ответь на письмо в формате обычного email.
Используй тему, начинающуюся с "Re: {original['subject']}".
Твой ответ должен содержать только текст письма (без служебных полей, просто тело письма).
Контекст переписки:
{context}
Твой ответ:"""
    # --- Используем DeepSeek вместо Together AI ---
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise HTTPException(500, "DEEPSEEK_API_KEY environment variable not set")
    reply_body = await call_deepseek(prompt, api_key)
    new_letter = {
        "thread_id": thread_id,
        "sender": "AI Assistant",
        "subject": f"Re: {original['subject']}",
        "body": reply_body.strip(),
        "created_at": datetime.utcnow().isoformat()
    }
    result = await supabase_request("POST", "letters", json_data=new_letter)
    if isinstance(result, list) and len(result) > 0:
        created = result[0]
    elif isinstance(result, dict):
        created = result
    else:
        raise HTTPException(500, "Failed to create letter: empty response")
    return LetterOut(**created)

handler = Mangum(app)