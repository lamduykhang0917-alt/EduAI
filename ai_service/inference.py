"""
inference.py
Luồng xử lý chính của AI Chatbot.

Có 2 chế độ (xem config.LLM_PROVIDER):

1. "claude" / "gemini" (MẶC ĐỊNH) — Chatbot trả lời TỰ DO bất kỳ câu hỏi nào
   bằng cách gọi thẳng API của Claude/Gemini, không giới hạn trong dataset.
   Nếu chưa cấu hình API key hoặc gọi API lỗi (mất mạng, hết quota...), hệ
   thống TỰ ĐỘNG rơi về chế độ "dataset" bên dưới cho câu hỏi đó, kèm ghi chú
   rõ trong câu trả lời, để chatbot không bao giờ "im lặng" hoàn toàn.

2. "dataset" — chế độ cũ: preprocessing -> phân loại ý định (intent) ->
   tìm kiếm trong knowledge base/dataset -> sinh câu trả lời. Chỉ trả lời được
   các câu hỏi đã có trong dataset; nếu độ tin cậy thấp sẽ báo "chưa tìm thấy
   thông tin phù hợp" thay vì đoán bừa.

Đây là điểm tích hợp duy nhất mà router /api/chat gọi tới — router không cần
biết chatbot đang chạy chế độ nào.
"""

import json
from typing import Optional

from . import config
from . import llm_client
from .classifier import get_classifier

_classifier = None
_knowledge_base = None


def _load_knowledge_base():
    global _knowledge_base
    if _knowledge_base is None:
        with open(config.KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            _knowledge_base = json.load(f).get("knowledge_base", [])
    return _knowledge_base


def _get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = get_classifier()
    return _classifier


def _search_knowledge(question: str, course: Optional[str] = None) -> Optional[dict]:
    """Tìm bản ghi phù hợp nhất trong knowledge base theo từ khóa."""
    kb = _load_knowledge_base()

    best_item = None
    best_score = 0

    for item in kb:
        if course and item.get("course") != course:
            continue
        keyword_hits = sum(1 for kw in item.get("keywords", []) if kw in question.lower())
        topic_hits = 1 if item.get("topic", "").lower() in question.lower() else 0
        score = keyword_hits * 2 + topic_hits
        if score > best_score:
            best_score = score
            best_item = item

    return best_item if best_score > 0 else None


NO_ANSWER_MESSAGE = (
    "Xin lỗi, tôi chưa tìm thấy thông tin phù hợp với câu hỏi này. "
    "Bạn có thể diễn đạt câu hỏi rõ hơn hoặc chọn môn học liên quan."
)


def _dataset_driven_response(question: str, course: Optional[str], level: str) -> dict:
    """Chế độ cũ: intent classification + tra cứu knowledge base nội bộ."""
    clf = _get_classifier()
    intent_result = clf.predict(question)

    if intent_result.tag == "greeting":
        return {
            "intent": intent_result.tag,
            "confidence": intent_result.confidence,
            "answer": "Xin chào! Tôi có thể giúp gì cho việc học của bạn hôm nay?",
            "source": None,
            "needs_clarification": False,
        }
    if intent_result.tag == "goodbye":
        return {
            "intent": intent_result.tag,
            "confidence": intent_result.confidence,
            "answer": "Tạm biệt! Chúc bạn học tập hiệu quả. Hẹn gặp lại bạn!",
            "source": None,
            "needs_clarification": False,
        }
    if intent_result.tag == "unknown":
        return {
            "intent": "unknown",
            "confidence": intent_result.confidence,
            "answer": NO_ANSWER_MESSAGE,
            "source": None,
            "needs_clarification": True,
        }

    item = _search_knowledge(question, course=course)
    if item is None:
        return {
            "intent": intent_result.tag,
            "confidence": intent_result.confidence,
            "answer": NO_ANSWER_MESSAGE,
            "source": None,
            "needs_clarification": True,
        }

    if intent_result.tag == "summary":
        answer = f"Tóm tắt về \"{item['topic']}\": {item['answer_basic']}"
    elif intent_result.tag in ("explanation",) or level == "advanced":
        answer = item["answer_detailed"] + " Ví dụ: " + item.get("example", "")
    else:
        answer = item["answer_basic"]

    return {
        "intent": intent_result.tag,
        "confidence": intent_result.confidence,
        "answer": answer,
        "source": {
            "id": item["id"],
            "course": item["course"],
            "chapter": item["chapter"],
            "topic": item["topic"],
        },
        "needs_clarification": False,
    }


def _llm_driven_response(question: str, course: Optional[str], course_list=None) -> dict:
    """Chế độ mới: gọi thẳng Claude/Gemini, giới hạn phạm vi trả lời trong các môn học của hệ thống."""
    answer = llm_client.ask_llm(question, course=course, course_list=course_list)
    return {
        "intent": "llm_free_chat",
        "confidence": 1.0,
        "answer": answer,
        "source": None,
        "needs_clarification": False,
    }


def generate_response(question: str, course: Optional[str] = None,
                       level: str = "basic", course_list=None) -> dict:
    """
    Trả về dict:
      {
        "intent": str,
        "confidence": float,
        "answer": str,
        "source": Optional[dict],   # bản ghi knowledge base dùng làm nguồn (chỉ có ở chế độ "dataset")
        "needs_clarification": bool
      }
    """
    if config.LLM_PROVIDER in ("claude", "gemini"):
        try:
            return _llm_driven_response(question, course, course_list)
        except llm_client.LLMError as e:
            # Không để chatbot "im lặng" khi chưa cấu hình API key hoặc lỗi mạng —
            # tự động rơi về dataset nội bộ, đồng thời ghi rõ lý do để Admin biết cần sửa gì.
            fallback = _dataset_driven_response(question, course, level)
            reason = str(e)
            if "503" in reason or "UNAVAILABLE" in reason or "429" in reason:
                reason = "dịch vụ AI đang quá tải, bạn thử gửi lại sau ít phút"
            elif len(reason) > 160:
                reason = reason[:160].replace("\n", " ") + "..."
            fallback["answer"] = (
                f"*(Chưa gọi được AI ngoài: {reason}. Đang dùng dữ liệu nội bộ để trả lời tạm.)*\n\n"
                + fallback["answer"]
            )
            return fallback

    return _dataset_driven_response(question, course, level)

