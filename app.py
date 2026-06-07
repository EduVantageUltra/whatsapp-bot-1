import os
import sys
import io
import json
import uuid
import queue
import threading
import contextlib
from flask import Flask, request, jsonify, render_template, Response
from werkzeug.utils import secure_filename

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import (
    get_file_type,
    process_image_file, process_pdf_file,
    process_word_file, process_excel_file,
    clean_json, connect_to_sheet, add_to_sheet,
)
from config import SHEET_ID

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
ALLOWED_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff",
    "pdf", "doc", "docx", "xls", "xlsx", "csv",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

# ---------- shared state ----------
files_status = {}     # {file_id: {...}}
all_records   = []    # flat list of extracted rows for the table
data_lock     = threading.Lock()
clients_lock  = threading.Lock()
event_clients = []    # list of per-connection Queue objects
proc_queue    = queue.Queue()


def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------- SSE helpers ----------
def send_sse(data):
    msg = f"data: {json.dumps(data)}\n\n"
    with clients_lock:
        dead = []
        for q in event_clients:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                event_clients.remove(q)
            except ValueError:
                pass


# ---------- file processor ----------
def process_one_file(file_id, file_path):
    filename = os.path.basename(file_path)

    def push(status=None, progress=None, msg=None, **extra):
        with data_lock:
            if status:
                files_status[file_id]["status"] = status
            if progress is not None:
                files_status[file_id]["progress"] = progress
        payload = {"type": "progress", "id": file_id}
        if status:
            payload["status"] = status
        if progress is not None:
            payload["progress"] = progress
        if msg:
            payload["msg"] = msg
        payload.update(extra)
        send_sse(payload)

    try:
        push(status="processing", progress=10, msg="Reading file…")

        file_type = get_file_type(file_path)
        if file_type == "unknown":
            raise ValueError("Unsupported file type")

        push(progress=30, msg="Sending to Claude AI…")

        # Redirect stdout/stderr — extract.py prints emoji (✅ ⚠️) that crash
        # Windows charmap (cp1252) codec when Flask runs without UTF-8 console.
        _sink = io.StringIO()
        with contextlib.redirect_stdout(_sink), contextlib.redirect_stderr(_sink):
            if file_type == "image":
                response = process_image_file(file_path)
            elif file_type == "pdf":
                response = process_pdf_file(file_path)
            elif file_type == "word":
                response = process_word_file(file_path)
            elif file_type == "excel":
                response = process_excel_file(file_path)

        push(progress=70, msg="Saving to Google Sheet…")

        cleaned = clean_json(response)
        parsed  = json.loads(cleaned)
        clients = parsed if isinstance(parsed, list) else [parsed]

        with contextlib.redirect_stdout(_sink), contextlib.redirect_stderr(_sink):
            sheet = connect_to_sheet()
            for c in clients:
                add_to_sheet(sheet, c, filename, file_type)

        records = [
            {
                "name":    str(c.get("name")           or ""),
                "phone":   str(c.get("phone")          or ""),
                "email":   str(c.get("email")          or ""),
                "company": str(c.get("company")        or ""),
                "invoice": str(c.get("invoice_number") or ""),
                "amount":  str(c.get("amount")         or ""),
                "source":  filename,
            }
            for c in clients
        ]

        with data_lock:
            all_records.extend(records)
            files_status[file_id].update(
                {"status": "done", "progress": 100, "count": len(clients)}
            )

        send_sse({
            "type": "done", "id": file_id,
            "status": "done", "progress": 100,
            "count": len(clients), "records": records,
        })

    except Exception as e:
        with data_lock:
            files_status[file_id].update(
                {"status": "failed", "progress": 0, "error": str(e)}
            )
        send_sse({"type": "error", "id": file_id, "status": "failed", "error": str(e)})
    finally:
        try:
            os.remove(file_path)
        except Exception:
            pass


def _worker():
    while True:
        file_id, file_path = proc_queue.get()
        try:
            process_one_file(file_id, file_path)
        finally:
            proc_queue.task_done()


threading.Thread(target=_worker, daemon=True).start()


# ---------- routes ----------
@app.route("/")
def index():
    return render_template("index.html", sheet_id=SHEET_ID)


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files provided"}), 400

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    queued = []

    for f in files:
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename):
            continue

        file_id  = uuid.uuid4().hex[:8]
        filename = secure_filename(f.filename)
        save_path = os.path.join(UPLOAD_FOLDER, f"{file_id}_{filename}")
        f.save(save_path)

        with data_lock:
            files_status[file_id] = {
                "id": file_id, "name": filename,
                "status": "queued", "progress": 0,
            }

        proc_queue.put((file_id, save_path))
        queued.append({"id": file_id, "name": filename})
        send_sse({"type": "queued", "id": file_id, "name": filename})

    return jsonify({"files": queued})


@app.route("/events")
def events():
    def stream():
        q = queue.Queue(maxsize=200)
        with clients_lock:
            event_clients.append(q)
        try:
            with data_lock:
                init = {
                    "type": "init",
                    "files":   list(files_status.values()),
                    "records": list(all_records),
                }
            yield f"data: {json.dumps(init)}\n\n"
            while True:
                try:
                    yield q.get(timeout=25)
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with clients_lock:
                try:
                    event_clients.remove(q)
                except ValueError:
                    pass

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.route("/results")
def results():
    with data_lock:
        return jsonify({"records": list(all_records)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
