import os
import json
import httpx
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from mangum import Mangum

# --- Database setup (asyncpg + SQLAlchemy core) ---
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Table, Column, Integer, String, Text, DateTime, MetaData, select, insert

DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(DATABASE_URL)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

metadata = MetaData()
letters_table = Table(
    "letters",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("thread_id", Integer),
    Column("sender", String),
    Column("subject", String),
    Column("body", Text),
    Column("created_at", DateTime),
)

# --- Pydantic models ---
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

# --- FastAPI app ---
app = FastAPI(title="Email LLM Service")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- Helper: get proxy settings ---
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
def get_httpx_client():
    return httpx.AsyncClient(proxy=PROXY_URL if PROXY_URL else None, timeout=30.0)

# --- LLM calls through proxy ---
async def call_together(prompt: str, api_key: str) -> str:
    url = "https://api.together.xyz/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",  # Можно заменить на любую бесплатную модель Together
        "messages": [{"role": "user", "content": prompt}],
    }
    async with get_httpx_client() as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

# --- API endpoints ---
@app.post("/api/letters", response_model=LetterOut)
async def create_letter(letter: LetterCreate):
    """Создать новое письмо (начало цепочки)."""
    async with async_session() as session:
        # Если thread_id не указан, создаём новый
        if letter.thread_id is None:
            # Определяем следующий thread_id (максимальный + 1)
            result = await session.execute(select(sqlalchemy.func.max(letters_table.c.thread_id)))
            max_id = result.scalar() or 0
            thread_id = max_id + 1
        else:
            thread_id = letter.thread_id

        ins = letters_table.insert().values(
            thread_id=thread_id,
            sender=letter.sender,
            subject=letter.subject,
            body=letter.body,
            created_at=datetime.utcnow()
        )
        new_id = await session.execute(ins)
        await session.commit()
        # Получить созданную запись
        result = await session.execute(select(letters_table).where(letters_table.c.id == new_id.inserted_primary_key[0]))
        row = result.first()
        return LetterOut(**row._asdict())

@app.get("/api/letters", response_model=List[LetterOut])
async def list_letters():
    """Получить все письма, отсортированные по убыванию даты."""
    async with async_session() as session:
        result = await session.execute(select(letters_table).order_by(letters_table.c.created_at.desc()))
        rows = result.fetchall()
        return [LetterOut(**row._asdict()) for row in rows]

@app.get("/api/threads/{thread_id}", response_model=List[LetterOut])
async def get_thread(thread_id: int):
    """Получить всю цепочку писем по thread_id (сортировка по возрастанию времени)."""
    async with async_session() as session:
        result = await session.execute(
            select(letters_table)
            .where(letters_table.c.thread_id == thread_id)
            .order_by(letters_table.c.created_at.asc())
        )
        rows = result.fetchall()
        if not rows:
            raise HTTPException(404, "Thread not found")
        return [LetterOut(**row._asdict()) for row in rows]

@app.post("/api/letters/{letter_id}/reply")
async def reply_to_letter(letter_id: int, req: ReplyRequest):
    """Ответить на письмо: сгенерировать ответ через LLM и сохранить как новое письмо."""
    async with async_session() as session:
        # Получить исходное письмо
        result = await session.execute(select(letters_table).where(letters_table.c.id == letter_id))
        original = result.first()
        if not original:
            raise HTTPException(404, "Letter not found")

        thread_id = original.thread_id
        # Получить всю историю переписки для контекста
        hist_result = await session.execute(
            select(letters_table).where(letters_table.c.thread_id == thread_id).order_by(letters_table.c.created_at.asc())
        )
        history = hist_result.fetchall()

        # Формируем prompt
        context = ""
        for h in history:
            context += f"From: {h.sender}\nSubject: {h.subject}\n{h.body}\n\n"
        context += f"From: User\nSubject: Re: {original.subject}\n{req.message}\n\n"
        prompt = f"""Ты — помощник в почтовом клиенте. Ответь на письмо в формате обычного email.
Используй тему, начинающуюся с "Re: {original.subject}".
Твой ответ должен содержать только текст письма (без служебных полей, просто тело письма).
Контекст переписки:
{context}
Твой ответ:"""

                # Вызов Together AI через прокси
        api_key = os.getenv("TOGETHER_API_KEY")
        if not api_key:
            raise HTTPException(500, "TOGETHER_API_KEY not set")
        reply_body = await call_together(prompt, api_key)

        # Сохранить ответ как новое письмо
        new_subject = f"Re: {original.subject}"
        ins = letters_table.insert().values(
            thread_id=thread_id,
            sender="AI Assistant",
            subject=new_subject,
            body=reply_body.strip(),
            created_at=datetime.utcnow()
        )
        new_id = await session.execute(ins)
        await session.commit()

        # Вернуть созданное письмо
        result = await session.execute(select(letters_table).where(letters_table.c.id == new_id.inserted_primary_key[0]))
        new_letter = result.first()
        return LetterOut(**new_letter._asdict())

# --- Vercel handler ---
handler = Mangum(app)