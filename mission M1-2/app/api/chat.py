"""/api/chat, /api/conversations 라우터."""

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from ..chat import ChatService
from ..models import (CONV_ID_PATTERN, ChatRequest, ChatResponse, ConversationCreate, ConversationDetail,
                      ConversationList, ConversationOut)

router = APIRouter(tags=["chat"])
ConvId = Path(pattern=CONV_ID_PATTERN, description="conv-<32자리 hex>")


def get_chat(request: Request) -> ChatService:
    return request.app.state.chat


@router.post("/chat", response_model=ChatResponse, summary="데이터 근거 GPT 분석 질문 (요청 1회 = GPT 호출 1회)")
def chat(body: ChatRequest, svc: ChatService = Depends(get_chat)):
    return svc.chat(body)


@router.post("/conversations", response_model=ConversationOut, status_code=201, summary="빈 대화 만들기")
def create_conversation(body: ConversationCreate, svc: ChatService = Depends(get_chat)):
    return svc.create_conversation(body)


@router.get("/conversations", response_model=ConversationList, summary="대화 목록 (최근 수정 순)")
def list_conversations(limit: int = Query(50, ge=1, le=200), svc: ChatService = Depends(get_chat)):
    return ConversationList(items=svc.list_conversations(limit))


@router.get("/conversations/{conv_id}", response_model=ConversationDetail, summary="대화와 메시지")
def get_conversation(conv_id: str = ConvId, svc: ChatService = Depends(get_chat)):
    return svc.get_conversation(conv_id)


@router.delete("/conversations/{conv_id}", status_code=204, summary="대화 삭제 (메시지 포함)")
def delete_conversation(conv_id: str = ConvId, svc: ChatService = Depends(get_chat)):
    svc.delete_conversation(conv_id)
    return Response(status_code=204)
