from fastapi import APIRouter

from app.ai.chat_engine import chat_reply
from app.deps import CurrentUser, DbSession
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.portfolio_service import portfolio_summary_text

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    db: DbSession,
    user: CurrentUser,
) -> ChatResponse:
    summary = portfolio_summary_text(db, user)
    msgs = [m.model_dump() for m in body.messages]
    reply = await chat_reply(user, summary, msgs)
    return ChatResponse(reply=reply)
