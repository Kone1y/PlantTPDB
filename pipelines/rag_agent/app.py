import os
from flask import Flask, request, jsonify, send_from_directory, render_template, Response, stream_with_context, redirect
import json
from flask_cors import CORS
from src.service.normal_rag import run_normal_chat, qa_evidence_scope
import tempfile
from src.service.rag import docRetrieval, rag_index, rag_tccode
from src.service.ptdb_utils import ptdb_url_search
import requests
import queue
import threading

os.environ['TMPDIR'] = tempfile.gettempdir()

app = Flask(__name__, static_folder="front", static_url_path="/front", template_folder="front")
CORS(app)

APPLICATION_ROOT = (os.environ.get("APPLICATION_ROOT") or "").strip().rstrip("/")
if APPLICATION_ROOT and not APPLICATION_ROOT.startswith("/"):
    APPLICATION_ROOT = "/" + APPLICATION_ROOT


@app.context_processor
def inject_base_path():
    base = getattr(request, "script_root", None) if request else None
    if not (base and str(base).strip()):
        base = APPLICATION_ROOT
    return {"base_path": base or ""}


RELATIONSHIP_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'relationship.json')

relationshipJson = {}

def load_relationship_data():
    global relationshipJson
    try:
        if not os.path.exists(RELATIONSHIP_JSON_PATH):
            relationshipJson = {}
            return
        with open(RELATIONSHIP_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        relationshipJson = data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to load relationship data: {e}")
        relationshipJson = {}

def save_relationship_data():
    file_path = RELATIONSHIP_JSON_PATH
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(relationshipJson, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving relationship data: {e}")

load_relationship_data()

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "database", "cache")
NORMAL_CACHE_PATH = os.path.join(CACHE_DIR, "normal_cache.json")
INDEX_CACHE_PATH = os.path.join(CACHE_DIR, "index_cache.json")
SYMBOL_CACHE_PATH = os.path.join(CACHE_DIR, "symbol_cache.json")
TCCODE_CACHE_PATH = os.path.join(CACHE_DIR, "tccode_cache.json")


def _load_cache_file(path):
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_cache_file(path, items):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save cache file failed:", path, e)


def _normalize_for_cache(text: str) -> str:
    s = str(text or "").strip().lower()
    s = " ".join(s.split())
    return s


def _norm_question_variants(mode: str, question: str, context: str | None) -> list[str]:
    m = (mode or "").strip().lower()
    q_raw = (question or "").strip()
    ctx = (context or "").strip()
    variants: list[str] = []
    variants.append(_normalize_for_cache(q_raw))
    if ctx:
        if m == "index":
            variants.append(_normalize_for_cache(f"Target index: {ctx}\nQuestion: {q_raw}"))
        elif m == "tccode":
            variants.append(_normalize_for_cache(f"Target TCCODE: {ctx}\nQuestion: {q_raw}"))
        elif m == "symbol":
            variants.append(_normalize_for_cache(f"Target symbol: {ctx}\nQuestion: {q_raw}"))
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _clear_cache_entry(mode: str, question: str, context: str | None = None) -> int:
    mode = (mode or "").strip().lower()
    question = (question or "").strip()
    ctx = (context or "").strip()
    if not question:
        return 0
    q_norms = _norm_question_variants(mode, question, ctx or None)
    ctx_norm = _normalize_for_cache(ctx) if ctx else ""

    if mode == "normal":
        path = NORMAL_CACHE_PATH
        items = _load_cache_file(path)
        before = len(items)
        items = [
            it
            for it in items
            if _normalize_for_cache(str(it.get("question", ""))) not in q_norms
            and (not it.get("question_norm") or str(it.get("question_norm")) not in q_norms)
        ]
        if len(items) != before:
            _save_cache_file(path, items)
        return before - len(items)

    if mode in {"symbol", "index", "tccode"}:
        if mode == "symbol":
            path = SYMBOL_CACHE_PATH
        elif mode == "index":
            path = INDEX_CACHE_PATH
        else:
            path = TCCODE_CACHE_PATH
        items = _load_cache_file(path)
        before = len(items)
        if ctx:
            items = [
                it
                for it in items
                if not (
                    (
                        _normalize_for_cache(str(it.get("question", ""))) in q_norms
                        or str(it.get("question_norm") or "") in q_norms
                    )
                    and (
                        _normalize_for_cache(str(it.get("entity", ""))) == ctx_norm
                        or str(it.get("entity_norm") or "") == ctx_norm
                    )
                )
            ]
        else:
            items = [
                it
                for it in items
                if _normalize_for_cache(str(it.get("question", ""))) not in q_norms
                and (not it.get("question_norm") or str(it.get("question_norm")) not in q_norms)
            ]
        if len(items) != before:
            _save_cache_file(path, items)
        return before - len(items)

    return 0


@app.route('/')
def index():
    return redirect((APPLICATION_ROOT or "") + "/ptdbapi/normal")

@app.route('/ptdbapi/symbol/chat', methods=['POST'])
def symbol_chat():
    data = request.get_json(silent=True) or {}
    question = data.get("question")
    symbol_key = data.get("name") or data.get("gene_name")
    
    if question is None:
        return jsonify({"error": "Missing parameter: question"}), 400
    
    relationship = None
    symbol_name = None
    if symbol_key and symbol_key in relationshipJson:
        rel_obj = relationshipJson[symbol_key]
        relationship = rel_obj.get("relationship")
        symbol_name = rel_obj.get("name")
    else:
        relationship = data.get("relationship")
        symbol_name = symbol_key or data.get("name")

    augmented_question_parts = []
    if symbol_name:
        augmented_question_parts.append(f"Target symbol: {symbol_name}")
    augmented_question_parts.append("Question: " + question)
    augmented_question = "\n".join(augmented_question_parts)
    
    def generate():
        q: queue.Queue = queue.Queue()
        end_token = object()
        state: dict = {"answer": None, "error": None}
        emitted_parts: list[str] = []

        def on_chunk(part: str) -> None:
            if part:
                q.put(part)

        def worker() -> None:
            try:
                state["answer"] = docRetrieval(
                    augmented_question,
                    relationship,
                    symbol_name,
                    stream_handler=on_chunk,
                )
            except Exception as e:
                state["error"] = e
            finally:
                q.put(end_token)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = q.get()
            if item is end_token:
                break
            txt = str(item)
            emitted_parts.append(txt)
            yield txt

        err = state.get("error")
        if err is not None:
            print("symbol_chat unexpected error:", err)
            if not emitted_parts:
                yield "Internal error in symbol_chat backend."
            return

        answer = state.get("answer") or {}
        final_text = str((answer.get("final_answer") or ""))

        return_dois = answer.get("return_dois", []) or []

        already = "".join(emitted_parts)
        if final_text:
            if already and final_text.startswith(already):
                tail = final_text[len(already):]
            else:
                tail = final_text if not already else ""
            if tail:
                yield tail
    
    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/ptdbapi/symbol', methods=['GET'])
def get_symbol_html():
    symbol = request.args.get('symbol') or request.args.get('name') or request.args.get('gene_name')
    return render_template("AIQuestion.html", symbol=symbol or "")

@app.route('/ptdbapi/setRelationJson', methods=['POST'])
def setRelationJson():
    global relationshipJson
    
    data = request.get_json(silent=True) or {}
    print("Received setRelationJson data:", data)

    try:
        symbol_name = data.get("name") or data.get("gene_name")

        graph_data = data.get("data") or {}
        nodes = graph_data.get("nodes", []) if isinstance(graph_data, dict) else []

        if not symbol_name:
            for node in nodes:
                labels = node.get("labels", [])
                props = node.get("properties", {}) or {}
                if "symbol" in labels or "gene" in labels:
                    symbol_name = props.get("name") or props.get("symbol") or props.get("gene_name")
                    if symbol_name:
                        break

        if symbol_name:
            print("Symbol name:", symbol_name)

        relationship_list = None
        if isinstance(data.get("relationship"), list):
            relationship_list = data.get("relationship")
        elif isinstance(graph_data, dict) and isinstance(graph_data.get("relationships"), list):
            relationship_list = graph_data.get("relationships")
        elif isinstance(graph_data, list):
            relationship_list = graph_data
        elif isinstance(data.get("relationships"), list):
            relationship_list = data.get("relationships")
        if relationship_list is None:
            relationship_list = []

        for rel in relationship_list:
            props = rel.get("properties", {}) or {}
            source = props.get("source_paper") or props.get("source")
            if source:
                print("Relationship source_paper:", source)

        data["_parsed_name"] = symbol_name  # debug only, not written into the file body
        data["_parsed_relationship_count"] = len(relationship_list)
    except Exception as e:
        print("Error parsing relationship data for debug output:", e)
    if not data:
        return jsonify({"error": "Missing parameter: data cannot be empty"}), 400

    name = data.get("name") or data.get("gene_name") or data.get("_parsed_name")
    if not name:
        return jsonify({"error": "Missing parameter: name or gene_name"}), 400
    graph_data = data.get("data") or {}
    relationship_list = None
    if isinstance(data.get("relationship"), list):
        relationship_list = data.get("relationship")
    elif isinstance(graph_data, dict) and isinstance(graph_data.get("relationships"), list):
        relationship_list = graph_data.get("relationships")
    elif isinstance(graph_data, list):
        relationship_list = graph_data
    elif isinstance(data.get("relationships"), list):
        relationship_list = data.get("relationships")
    if relationship_list is None:
        relationship_list = []

    existed = name in relationshipJson
    old_entry = relationshipJson.get(name, {})

    new_entry = {
        "name": name or old_entry.get("name"),
        "relationship": relationship_list or old_entry.get("relationship", [])
    }

    relationshipJson[name] = new_entry
    
    save_relationship_data()
    
    return jsonify({
        "message": "Relationship data saved successfully",
        "name": name,
        "jsonPath": RELATIONSHIP_JSON_PATH,
        "total_symbols": len(relationshipJson),
        "existed": existed
    })

@app.route('/ptdbapi/getRelationJson', methods=['GET'])
def getRelationJson():
    name = request.args.get('symbol') or request.args.get('name') or request.args.get('gene_name')

    if not name:
        return jsonify({"error": "Missing parameter: name or gene_name"}), 400
    
    if name in relationshipJson:
        return jsonify({
            "found": True,
            "data": relationshipJson[name]
        })
    else:
        return jsonify({
            "found": False,
            "message": "No data found for this symbol"
        })

@app.route('/ptdbapi/test', methods=['GET'])
def test():
    return render_template("Test.html")


@app.route('/ptdbapi/normal', methods=['GET'])
def get_normal_html():
    return render_template("NormalQuesion.html")

@app.route('/ptdbapi/index', methods=['GET'])
def get_index_html():
    index_value = request.args.get("index") or "AT4G12980"
    suggestions = [
        {
            "display": f"DB summary: {index_value}",
            "input": (
                f"Based on available database information, describe the predicted structure, "
                f"localization and general transporter type of index {index_value}."
            ),
        },
        {
            "display": f"What TC code is associated with {index_value}?",
            "input": (
                f"Based on available database information, what TC family or code is associated "
                f"with the transporter indexed as {index_value}?"
            ),
        },
        {
            "display": f"What substrates does {index_value} transport?",
            "input": (
                f"Based on available database information, what are the predicted substrates "
                f"transported by the protein with index {index_value}?"
            ),
        },
        {
            "display": f"Summarize main findings for {index_value}",
            "input": (
                f"Please summarize current knowledge about the transporter with index {index_value}, "
                f"including structure, localization, substrates and biological roles."
            ),
        },
        {
            "display": f"Open PTDB index page for {index_value}",
            "input": ptdb_url_search(index_value),
        },
    ]
    return render_template(
        "IndexQuestion.html",
        index_value=index_value,
        suggestions=suggestions,
    )


@app.route('/ptdbapi/tccode', methods=['GET'])
def get_tccode_html():
    tccode_value = request.args.get("tccode") or "2.A.7."
    suggestions = [
        {
            "display": f"Explain TC family {tccode_value}",
            "input": (
                f"Explain the TC classification and general transport mechanism of transporters "
                f"in TC family {tccode_value}."
            ),
        },
        {
            "display": f"Substrates of TC family {tccode_value}",
            "input": (
                f"List the typical substrates and transport modes commonly reported for "
                f"transporters in TC family {tccode_value}."
            ),
        },
        {
            "display": f"Plant transporters in {tccode_value}",
            "input": (
                f"In plants, what are representative genes or proteins in TC family {tccode_value}, "
                f"and what are their key functional findings?"
            ),
        },
        {
            "display": f"Structural features of {tccode_value}",
            "input": (
                f"Summarize the structural features such as predicted transmembrane helices and "
                f"conserved motifs that are generally associated with TC family {tccode_value}."
            ),
        },
        {
            "display": f"Functional overview of {tccode_value}",
            "input": (
                f"Provide an integrated overview of function, substrates, localization and "
                f"physiological roles for transporters in TC family {tccode_value}."
            ),
        },
    ]
    return render_template(
        "TccodeQuestion.html",
        tccode_value=tccode_value,
        suggestions=suggestions,
    )


@app.route('/ptdbapi/index/chat', methods=['POST'])
def index_chat():
    data = request.get_json(silent=True) or {}
    question = data.get("question")
    index_value = data.get("index")

    if question is None:
        return jsonify({"error": "Missing parameter: question"}), 400
    if index_value is None:
        return jsonify({"error": "Missing parameter: index"}), 400

    index_value = str(index_value).strip()
    if not index_value:
        return jsonify({"error": "Missing parameter: index"}), 400

    augmented_question_parts = [f"Target index: {index_value}", "Question: " + str(question)]
    augmented_question = "\n".join(augmented_question_parts)

    def generate():
        q: queue.Queue = queue.Queue()
        end_token = object()
        state: dict = {"answer": None, "error": None}
        emitted_parts: list[str] = []

        def on_chunk(part: str) -> None:
            if part:
                q.put(part)

        def worker() -> None:
            try:
                state["answer"] = rag_index(
                    augmented_question,
                    index_value,
                    stream_handler=on_chunk,
                )
            except Exception as e:
                state["error"] = e
            finally:
                q.put(end_token)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = q.get()
            if item is end_token:
                break
            txt = str(item)
            emitted_parts.append(txt)
            yield txt

        err = state.get("error")
        if err is not None:
            print("index_chat unexpected error:", err)
            if not emitted_parts:
                yield "Internal error in index_chat backend."
            return

        answer = state.get("answer") or {}
        final_text = str((answer.get("final_answer") or ""))

        return_dois = answer.get("return_dois", []) or []

        already = "".join(emitted_parts)
        if final_text:
            if already and final_text.startswith(already):
                tail = final_text[len(already):]
            else:
                tail = final_text if not already else ""
            if tail:
                yield tail

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/ptdbapi/tccode/chat', methods=['POST'])
def tccode_chat():
    data = request.get_json(silent=True) or {}
    question = data.get("question")
    tccode_value = data.get("tccode")

    if question is None:
        return jsonify({"error": "Missing parameter: question"}), 400
    if tccode_value is None:
        return jsonify({"error": "Missing parameter: tccode"}), 400

    tccode_value = str(tccode_value).strip()
    if not tccode_value:
        return jsonify({"error": "Missing parameter: tccode"}), 400

    augmented_question_parts = [f"Target TCCODE: {tccode_value}", "Question: " + str(question)]
    augmented_question = "\n".join(augmented_question_parts)

    def generate():
        q: queue.Queue = queue.Queue()
        end_token = object()
        state: dict = {"answer": None, "error": None}
        emitted_parts: list[str] = []

        def on_chunk(part: str) -> None:
            if part:
                q.put(part)

        def worker() -> None:
            try:
                state["answer"] = rag_tccode(
                    augmented_question,
                    tccode_value,
                    stream_handler=on_chunk,
                )
            except Exception as e:
                state["error"] = e
            finally:
                q.put(end_token)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = q.get()
            if item is end_token:
                break
            txt = str(item)
            emitted_parts.append(txt)
            yield txt

        err = state.get("error")
        if err is not None:
            print("tccode_chat unexpected error:", err)
            if not emitted_parts:
                yield "Internal error in tccode_chat backend."
            return

        answer = state.get("answer") or {}
        final_text = str((answer.get("final_answer") or ""))

        return_dois = answer.get("return_dois", []) or []

        already = "".join(emitted_parts)
        if final_text:
            if already and final_text.startswith(already):
                tail = final_text[len(already):]
            else:
                tail = final_text if not already else ""
            if tail:
                yield tail

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/ptdbapi/normal/chat', methods=['POST'])
def normal_chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    return_contexts = data.get("return_contexts", False)
    return_contexts_meta = data.get("return_contexts_meta", False)

    if not question:
        return jsonify({"error": "Missing parameter: question"}), 400

    def generate():
        q: queue.Queue = queue.Queue()
        end_token = object()
        state: dict = {"result": "", "error": None, "contexts": [], "contexts_meta": []}
        emitted_parts: list[str] = []

        def on_chunk(part: str) -> None:
            if part:
                q.put(part)

        def worker() -> None:
            try:
                with qa_evidence_scope() as ev:
                    state["result"] = run_normal_chat(
                        question, stream_handler=on_chunk
                    )
                state["contexts"] = ev["contexts"]
                state["contexts_meta"] = ev["meta"]
            except Exception as e:
                state["error"] = e
            finally:
                q.put(end_token)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = q.get()
            if item is end_token:
                break
            txt = str(item)
            emitted_parts.append(txt)
            yield txt

        err = state.get("error")
        if err is not None:
            print("normal_chat unexpected error:", err)
            if not emitted_parts:
                yield "Internal error in normal_chat backend."
            return

        final_text = str(state.get("result") or "")
        already = "".join(emitted_parts)
        if final_text:
            if already and final_text.startswith(already):
                tail = final_text[len(already):]
            else:
                tail = final_text if not already else ""
            if tail:
                yield tail

        if return_contexts:
            import json
            ctx_json = json.dumps(state.get("contexts") or [], ensure_ascii=False)
            yield "\n__CONTEXTS__\n" + ctx_json

        if return_contexts_meta:
            import json
            ctx_meta_json = json.dumps(state.get("contexts_meta") or [], ensure_ascii=False)
            yield "\n__CONTEXTS_META__\n" + ctx_meta_json

        if data.get("return_db_api_calls"):
            from src.service.normal_rag import drain_ptdb_calls
            import json
            yield "\n__DB_API_CALLS__\n" + json.dumps(drain_ptdb_calls(), ensure_ascii=False)

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route("/ptdbapi/cache/clear", methods=['POST'])
def clear_cache_entry():
    data = request.get_json(silent=True) or {}
    mode = (data.get("mode") or "").strip()
    question = (data.get("question") or "").strip()
    context = (data.get("context") or "").strip() or None

    if not mode or not question:
        return jsonify({"ok": False, "deleted": 0, "message": "Missing mode or question"}), 400

    deleted = _clear_cache_entry(mode, question, context)
    return jsonify({"ok": True, "deleted": deleted})

@app.route("/ptdbapi/testURL", methods=['POST'])
def testURL():
    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol")
    print("Received symbol:", symbol)
    if symbol is None:
        print("Missing parameter: symbol")
        return jsonify({"error": "Missing parameter: symbol"}), 400
    return jsonify({"message": f"Symbol received successfully: {symbol}"}), 200


def _fetch_titles_by_dois(dois, timeout: int = 30):
    cleaned = []
    for d in dois or []:
        s = str(d).strip()
        if s:
            cleaned.append(s)
    if not cleaned:
        return {}

    try:
        indoi = ";".join(cleaned)
        resp = requests.get(
            "https://yanglab.hzau.edu.cn/ptdb/Agent/get_title_by_doi",
            params={"indoi": indoi},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        out = {}
        if isinstance(data, list):
            for rec in data:
                if not isinstance(rec, dict):
                    continue
                doi = str(rec.get("DOI") or "").strip()
                title = str(rec.get("Title") or "").strip()
                if doi and title:
                    out[doi] = title
        return out
    except Exception as e:
        print("fetch title by doi failed:", e)
        return {}


def _append_inline_dois(answer_text, return_dois):
    text = str(answer_text or "").rstrip()
    seen = set()
    dois = []
    for doi in return_dois or []:
        d = str(doi).strip()
        if d and d not in seen:
            seen.add(d)
            dois.append(d)

    if not text or not dois:
        return text

    inline_doi_text = "DOI: " + "; ".join(dois)
    if inline_doi_text in text:
        return text

    return f"{text}\n\n{inline_doi_text}"

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=7679)
