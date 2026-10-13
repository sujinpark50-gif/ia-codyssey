"""/api/chat, /api/conversations 라우터."""

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth import User
from ..chat import ChatService
from ..models import (CONV_ID_PATTERN, ChatRequest, ChatResponse, ConversationCreate, ConversationDetail,
                      ConversationList, ConversationOut)

router = APIRouter(tags=["chat"])
ConvId = Path(pattern=CONV_ID_PATTERN, description="conv-<32자리 hex>")


def get_chat(request: Request) -> ChatService:
    return request.app.state.chat


# OpenAPI(Swagger)에 "Authorize: Bearer <Firebase ID 토큰>" 을 표시하기 위한 선언일 뿐이다.
# auto_error=False 라 여기서는 아무것도 거부하지 않고, 실제 검증은 Authenticator 가 Authorization 헤더로 한다.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="FirebaseIdToken",
                           description="Firebase Authentication 으로 로그인해 받은 ID 토큰 (허용된 계정만)")


def current_user(request: Request, _: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> User:
    return request.app.state.authenticate(request)


@router.post("/chat", response_model=ChatResponse, summary="데이터 근거 GPT 분석 질문 (요청 1회 = GPT 호출 1회)")
def chat(body: ChatRequest, svc: ChatService = Depends(get_chat), user: User = Depends(current_user)):
    return svc.chat(user, body)


@router.post("/conversations", response_model=ConversationOut, status_code=201, summary="빈 대화 만들기")
def create_conversation(body: ConversationCreate, svc: ChatService = Depends(get_chat),
                        user: User = Depends(current_user)):
    return svc.create_conversation(user, body)


@router.get("/conversations", response_model=ConversationList, summary="대화 목록 (최근 수정 순)")
def list_conversations(limit: int = Query(50, ge=1, le=200), svc: ChatService = Depends(get_chat),
                       user: User = Depends(current_user)):
    return ConversationList(items=svc.list_conversations(user, limit))


@router.get("/conversations/{conv_id}", response_model=ConversationDetail, summary="대화와 메시지")
def get_conversation(conv_id: str = ConvId, svc: ChatService = Depends(get_chat),
                     user: User = Depends(current_user)):
    return svc.get_conversation(user, conv_id)


@router.delete("/conversations/{conv_id}", status_code=204, summary="대화 삭제 (메시지 포함)")
def delete_conversation(conv_id: str = ConvId, svc: ChatService = Depends(get_chat),
                        user: User = Depends(current_user)):
    svc.delete_conversation(user, conv_id)
    return Response(status_code=204)


@router.get("/me", summary="로그인 사용자와 질문 사용량")
def me(svc: ChatService = Depends(get_chat), user: User = Depends(current_user)):
    return {"email": user.email, "chat_limits": svc.limiter.status(user.uid)}
