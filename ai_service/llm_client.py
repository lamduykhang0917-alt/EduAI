"""
llm_client.py
Gọi API của nhà cung cấp LLM bên ngoài (Claude hoặc Gemini) để chatbot trả lời
TỰ DO bất kỳ câu hỏi nào của sinh viên — không giới hạn trong dataset/knowledge base.

Đây là điểm tích hợp DUY NHẤT cần sửa nếu muốn đổi nhà cung cấp AI khác (OpenAI,
DeepSeek...) — chỉ cần viết thêm 1 hàm call_xxx() tương tự rồi thêm nhánh trong
ask_llm(), không cần sửa inference.py hay bất kỳ router nào khác.
"""

import requests
from . import config


class LLMError(Exception):
    """Raised khi gọi LLM thất bại (thiếu API key, lỗi mạng, lỗi từ nhà cung cấp...)."""
    pass


def _build_system_prompt(course: str = None, course_list=None) -> str:
    system = config.LLM_SYSTEM_PROMPT
    if course_list:
        joined = ", ".join(course_list)
        system += (
            f" Bạn CHỈ được trả lời các câu hỏi thuộc phạm vi các môn học sau: {joined}. "
            "Nếu câu hỏi của sinh viên không liên quan đến bất kỳ môn học nào ở trên, "
            "hãy từ chối một cách lịch sự, giải thích rằng bạn chỉ hỗ trợ trong phạm vi "
            "các môn học của hệ thống, và gợi ý sinh viên đặt câu hỏi liên quan đến một "
            "trong các môn học đó."
        )
    if course:
        system += f" Sinh viên đang học môn: {course}."
    return system


def call_claude(question: str, course: str = None, course_list=None) -> str:
    if not config.ANTHROPIC_API_KEY:
        raise LLMError(
            "Chưa cấu hình ANTHROPIC_API_KEY. Xem hướng dẫn trong ai_service/config.py "
            "hoặc file backend/.env.example."
        )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "system": _build_system_prompt(course, course_list),
                "messages": [{"role": "user", "content": question}],
            },
            timeout=30,
        )
    except requests.RequestException as e:
        raise LLMError(f"Không thể kết nối tới Claude API: {e}")

    if resp.status_code != 200:
        raise LLMError(f"Claude API trả về lỗi ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    answer = "\n".join(parts).strip()
    if not answer:
        raise LLMError("Claude API trả về nội dung rỗng")
    return answer


def call_gemini(question: str, course: str = None, course_list=None) -> str:
    if not config.GEMINI_API_KEY:
        raise LLMError(
            "Chưa cấu hình GEMINI_API_KEY. Xem hướng dẫn trong ai_service/config.py "
            "hoặc file backend/.env.example."
        )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    )
    try:
        resp = requests.post(
            url,
            json={
                "system_instruction": {"parts": [{"text": _build_system_prompt(course, course_list)}]},
                "contents": [{"role": "user", "parts": [{"text": question}]}],
            },
            timeout=30,
        )
    except requests.RequestException as e:
        raise LLMError(f"Không thể kết nối tới Gemini API: {e}")

    if resp.status_code != 200:
        raise LLMError(f"Gemini API trả về lỗi ({resp.status_code}): {resp.text[:300]}")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError("Gemini API trả về dữ liệu không đúng định dạng mong đợi")


def ask_llm(question: str, course: str = None, course_list=None) -> str:
    """Điểm gọi chung — tự động chọn nhà cung cấp theo config.LLM_PROVIDER."""
    if config.LLM_PROVIDER == "claude":
        return call_claude(question, course, course_list)
    if config.LLM_PROVIDER == "gemini":
        return call_gemini(question, course, course_list)
    raise LLMError(f"LLM_PROVIDER không hợp lệ: '{config.LLM_PROVIDER}' (chỉ nhận 'claude' hoặc 'gemini')")
