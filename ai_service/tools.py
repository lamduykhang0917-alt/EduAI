import json
import os
import sqlite3
from contextlib import contextmanager

from . import config
from . import rag

DB_PATH = os.path.join(config.BASE_DIR, "backend", "eduai.db")

CONFIRMATION_REQUIRED = {"submit_quiz"}

TOOL_DEFINITIONS = [
    {
        "name": "search_documents",
        "description": (
            "Tìm kiếm nội dung kiến thức liên quan trong tài liệu/knowledge base của hệ thống "
            "theo một câu hỏi hoặc chủ đề. Dùng khi sinh viên hỏi kiến thức, cần tra cứu, "
            "giải thích khái niệm, hoặc cần nội dung để tóm tắt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Câu hỏi hoặc chủ đề cần tìm"},
                "course": {"type": "string", "description": "Tên môn học để lọc kết quả (tùy chọn)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_quiz",
        "description": "Tạo một bài kiểm tra/câu hỏi ôn tập mới từ ngân hàng câu hỏi theo môn, chương, độ khó.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {"type": "string"},
                "chapter": {"type": "string"},
                "num_questions": {"type": "integer", "default": 5},
                "difficulty": {"type": "string", "description": "basic | medium | advanced"},
            },
            "required": ["course"],
        },
    },
    {
        "name": "submit_quiz",
        "description": (
            "Nộp bài kiểm tra để tính điểm. Đây là hành động làm thay đổi dữ liệu kết quả học tập "
            "của sinh viên nên LUÔN cần sinh viên xác nhận trước khi thực thi thật."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "quiz_id": {"type": "integer"},
                "answers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_id": {"type": "string"},
                            "selected_option": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["quiz_id", "answers"],
        },
    },
    {
        "name": "get_progress",
        "description": "Lấy tiến độ học tập hiện tại của sinh viên (theo từng môn, điểm trung bình, số bài đã làm).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recommendation",
        "description": "Lấy đề xuất nội dung nên học tiếp theo dựa trên kết quả và tiến độ học tập của sinh viên.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _load_question_bank():
    with open(config.QUIZ_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["questions"]


def _tool_search_documents(args: dict, user_id: int) -> dict:
    results = rag.search(args["query"], course=args.get("course"), top_k=3)
    if not results:
        return {"found": False, "results": [], "message": "Không tìm thấy nội dung phù hợp trong tài liệu/knowledge base."}
    return {"found": True, "results": results}


def _tool_generate_quiz(args: dict, user_id: int) -> dict:
    bank = _load_question_bank()
    filtered = [q for q in bank if q["course"] == args["course"]]
    if args.get("chapter"):
        filtered = [q for q in filtered if q["chapter"] == args["chapter"]]
    if args.get("difficulty"):
        filtered = [q for q in filtered if q["difficulty"] == args["difficulty"]]
    if not filtered:
        return {"found": False, "message": "Không tìm thấy câu hỏi phù hợp trong ngân hàng câu hỏi hiện có."}

    num_questions = args.get("num_questions", 5)
    selected = filtered[:num_questions]

    with _connect() as conn:
        course_row = conn.execute("SELECT id FROM courses WHERE name = ?", (args["course"],)).fetchone()
        course_id = course_row["id"] if course_row else None
        cur = conn.execute(
            "INSERT INTO quizzes (user_id, course_id, num_questions, difficulty) VALUES (?, ?, ?, ?)",
            (user_id, course_id, len(selected), args.get("difficulty") or "mixed"),
        )
        quiz_id = cur.lastrowid

    return {
        "found": True,
        "quiz_id": quiz_id,
        "questions": [{"id": q["id"], "content": q["content"], "options": q["options"]} for q in selected],
    }


def _tool_submit_quiz(args: dict, user_id: int) -> dict:
    bank = {q["id"]: q for q in _load_question_bank()}
    correct_count = 0
    detailed = []
    for ans in args["answers"]:
        q = bank.get(ans.get("question_id"))
        if not q:
            continue
        is_correct = ans.get("selected_option") == q["correct_answer"]
        if is_correct:
            correct_count += 1
        detailed.append({
            "question_id": q["id"],
            "is_correct": is_correct,
            "correct_option": q["correct_answer"],
            "explanation": q["explanation"],
        })

    total = len(args["answers"])
    score = round((correct_count / total) * 10, 2) if total else 0

    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO quiz_results (quiz_id, user_id, score, correct_count, total_count, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (args["quiz_id"], user_id, score, correct_count, total),
        )
        result_id = cur.lastrowid
        conn.execute(
            "INSERT INTO activity_logs (user_id, action, detail) VALUES (?, 'submit_quiz', ?)",
            (user_id, f"quiz_id={args['quiz_id']} score={score}"),
        )

    return {
        "result_id": result_id,
        "score": score,
        "correct_count": correct_count,
        "total_count": total,
        "details": detailed,
    }


def _tool_get_progress(args: dict, user_id: int) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT p.percent_complete, c.name as course_name FROM learning_progress p "
            "JOIN courses c ON p.course_id = c.id WHERE p.user_id = ?",
            (user_id,),
        ).fetchall()
        total_quizzes = conn.execute(
            "SELECT COUNT(*) c FROM quiz_results WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        avg_score = conn.execute(
            "SELECT AVG(score) a FROM quiz_results WHERE user_id = ?", (user_id,)
        ).fetchone()["a"]

    return {
        "by_course": [dict(r) for r in rows],
        "total_quizzes_completed": total_quizzes,
        "average_score": round(avg_score, 2) if avg_score else 0,
    }


def _tool_get_recommendation(args: dict, user_id: int) -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT content, reason FROM recommendations WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        if rows:
            return {"recommendations": [dict(r) for r in rows]}

        wrong_topic = conn.execute(
            "SELECT q.chapter_id, COUNT(*) c FROM quiz_answers qa "
            "JOIN questions q ON qa.question_id = q.id "
            "JOIN quiz_results r ON qa.quiz_result_id = r.id "
            "WHERE r.user_id = ? AND qa.is_correct = 0 "
            "GROUP BY q.chapter_id ORDER BY c DESC LIMIT 1",
            (user_id,),
        ).fetchone()

    if wrong_topic:
        return {"recommendations": [{
            "content": "Bạn nên ôn lại các câu hỏi đã làm sai gần đây trước khi tiếp tục chương mới.",
            "reason": "Dựa trên kết quả bài kiểm tra gần nhất",
        }]}

    return {"recommendations": [{
        "content": "Hãy bắt đầu với môn Trí tuệ nhân tạo - chương Tìm kiếm trong không gian trạng thái.",
        "reason": "Đề xuất khởi đầu cho sinh viên mới",
    }]}


_DISPATCH = {
    "search_documents": _tool_search_documents,
    "generate_quiz": _tool_generate_quiz,
    "submit_quiz": _tool_submit_quiz,
    "get_progress": _tool_get_progress,
    "get_recommendation": _tool_get_recommendation,
}


class ToolError(Exception):
    pass


def execute(name: str, arguments: dict, user_id: int) -> dict:
    handler = _DISPATCH.get(name)
    if handler is None:
        raise ToolError(f"Tool không tồn tại: {name}")
    return handler(arguments, user_id)
