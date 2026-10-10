import os
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify, render_template
from supabase import create_client, Client
from dotenv import load_dotenv

# Загружаем переменные окружения из .env (для локальной разработки)
load_dotenv()

app = Flask(__name__)

# --- Инициализация клиента Supabase ---
# Эти переменные нужно будет добавить в настройки Render
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)


# ---------- Утилиты ----------
def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def tasks_for_date(d: date):
    """Возвращает задачи (одиночные + повторяющиеся) на конкретный день из Supabase."""
    weekday = d.weekday()  # 0=Пн ... 6=Вс
    date_iso = d.isoformat()

    # 1. Одиночные задачи на этот день
    single_res = supabase.table("tasks").select("*").eq("type", "single").eq("date", date_iso).execute()

    # 2. Повторяющиеся задачи, у которых в массиве days есть нужный день недели
    # Для этого используем фильтр contains (проверка наличия элемента в массиве)
    recurring_res = supabase.table("tasks").select("*").eq("type", "recurring").contains("days", [weekday]).execute()

    # Объединяем результаты
    return single_res.data + recurring_res.data


def get_log_for_task(task_id: str, d: date):
    """Получает отметку о выполнении для конкретной задачи на конкретную дату."""
    res = supabase.table("logs").select("*").eq("task_id", task_id).eq("date", d.isoformat()).execute()
    return res.data[0] if res.data else None


# ---------- UI ----------
@app.route("/")
def index():
    return render_template("index.html")


# ---------- API: задачи ----------
@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "date is required"}), 400
    d = parse_date(date_str)

    tasks = tasks_for_date(d)

    # Добавляем к каждой задаче её лог (отметку) на этот день
    result = []
    for t in tasks:
        log = get_log_for_task(t["id"], d)
        result.append({**t, "log": log})

    return jsonify(result)


@app.route("/api/tasks", methods=["POST"])
def create_task():
    body = request.get_json() or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    ttype = body.get("type", "single")
    if ttype not in ("single", "recurring"):
        return jsonify({"error": "invalid type"}), 400

    task_data = {
        "title": title,
        "type": ttype,
    }

    if ttype == "single":
        date_str = body.get("date")
        if not date_str:
            return jsonify({"error": "date is required for single task"}), 400
        task_data["date"] = date_str
        task_data["days"] = []  # на всякий случай
    else:  # recurring
        raw_days = body.get("days") or []
        try:
            days = sorted({int(x) for x in raw_days if 0 <= int(x) <= 6})
        except (TypeError, ValueError):
            return jsonify({"error": "invalid days"}), 400
        if not days:
            return jsonify({"error": "days required for recurring task"}), 400
        task_data["days"] = days

    # Вставляем в Supabase и возвращаем созданную запись
    res = supabase.table("tasks").insert(task_data).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    # Удаление задачи автоматически удалит связанные логи
    # благодаря ON DELETE CASCADE в схеме базы данных
    supabase.table("tasks").delete().eq("id", task_id).execute()
    return "", 204


# ---------- API: отметка выполнения ----------
@app.route("/api/tasks/<task_id>/log", methods=["POST"])
def set_log(task_id):
    body = request.get_json() or {}
    date_str = body.get("date")
    status = body.get("status")
    reason = body.get("reason", "") or ""

    if not date_str or status not in ("done", "skipped", "failed", "none"):
        return jsonify({"error": "invalid input"}), 400

    # Сначала удаляем старую отметку на этот день (если она есть)
    supabase.table("logs").delete().eq("task_id", task_id).eq("date", date_str).execute()

    # Если статус не "none", добавляем новую запись
    if status != "none":
        log_data = {
            "task_id": task_id,
            "date": date_str,
            "status": status,
            "reason": reason,
        }
        supabase.table("logs").insert(log_data).execute()

    return jsonify({"ok": True})


# ---------- API: продуктивность ----------
@app.route("/api/productivity", methods=["GET"])
def productivity():
    from_str = request.args.get("from")
    to_str = request.args.get("to")
    if not from_str or not to_str:
        return jsonify({"error": "from and to required"}), 400

    d_from = parse_date(from_str)
    d_to = parse_date(to_str)

    result = []
    d = d_from
    while d <= d_to:
        tasks = tasks_for_date(d)
        done = skipped = failed = pending = 0

        for t in tasks:
            log = get_log_for_task(t["id"], d)
            if not log:
                pending += 1
            elif log["status"] == "done":
                done += 1
            elif log["status"] == "skipped":
                skipped += 1
            elif log["status"] == "failed":
                failed += 1

        # score = выполнено / (выполнено + не выполнено)
        denom = done + failed
        score = (done / denom) if denom > 0 else None

        result.append({
            "date": d.isoformat(),
            "total": len(tasks),
            "done": done,
            "skipped": skipped,
            "failed": failed,
            "pending": pending,
            "score": score,
        })
        d += timedelta(days=1)

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)