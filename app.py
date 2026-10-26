import secrets
import sqlite3
import threading
from pathlib import Path
from flask import Flask, jsonify, render_template, request, session
from rag import load_chunks, retrieve
from answers import short_answer

BASE = Path(__file__).resolve().parent
app = Flask(__name__)
app.config.update(SECRET_KEY=secrets.token_hex(32), MAX_CONTENT_LENGTH=20_000,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
(BASE / 'instance').mkdir(exist_ok=True)
DB = BASE / 'instance' / 'chat.sqlite3'
with sqlite3.connect(DB) as con:
    con.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, sid TEXT, role TEXT, content TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)')
lock = threading.Lock()
chunks, warnings = load_chunks(BASE / 'documents')

def sid():
    if 'sid' not in session:
        session['sid'] = secrets.token_urlsafe(24)
    return session['sid']

def rows():
    with sqlite3.connect(DB) as con:
        return con.execute('SELECT role, content FROM messages WHERE sid=? ORDER BY id DESC LIMIT 12', (sid(),)).fetchall()[::-1]

@app.before_request
def same_origin():
    if request.method == 'POST':
        from urllib.parse import urlparse
        origin = request.headers.get('Origin')
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error='Cross-origin request rejected.'), 403

@app.get('/')
def index():
    sid()
    return render_template('index.html')

@app.get('/api/status')
def status():
    return jsonify(mode='offline', chunks=len(chunks), warnings=warnings)

@app.get('/api/history')
def history():
    return jsonify(messages=[{'role': r, 'content': c} for r, c in rows()])

@app.post('/api/clear')
def clear():
    with sqlite3.connect(DB) as con:
        con.execute('DELETE FROM messages WHERE sid=?', (sid(),))
    return jsonify(ok=True)

@app.post('/api/reload')
def reload_docs():
    global chunks, warnings
    with lock:
        chunks, warnings = load_chunks(BASE / 'documents')
    return jsonify(chunks=len(chunks), warnings=warnings)

@app.post('/api/chat')
def chat():
    data = request.get_json(silent=True)
    question = data.get('message') if isinstance(data, dict) else None
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        return jsonify(error='Enter a question between 1 and 2000 characters.'), 400
    question = question.strip()
    found = retrieve(question, chunks)
    sources = [{'id': i, 'file': c['source'], 'page': c['page']} for i, c in enumerate(found, 1)]
    answer = short_answer(question, chunks)
    with sqlite3.connect(DB) as con:
        con.executemany('INSERT INTO messages(sid,role,content) VALUES(?,?,?)', [(sid(), 'user', question), (sid(), 'assistant', answer)])
    return jsonify(answer=answer, sources=sources)

@app.errorhandler(413)
def too_large(error):
    return jsonify(error='Request is too large.'), 413

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
