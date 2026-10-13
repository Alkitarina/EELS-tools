"""Small project-local UI drafts, independent of analysis results and temporary jobs."""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
import uuid

from flask import jsonify, request

VERSION = "eels_ui_preferences_v1"
MAX_BYTES = 256 * 1024


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) - {"controls", "dataset", "legacy", "workflow", "details"}:
        raise ValueError("参数记忆格式无效。")
    controls = snapshot.get("controls")
    for name in ("dataset", "legacy", "workflow", "details"):
        if name in snapshot and not isinstance(snapshot[name], dict):
            raise ValueError("参数分组格式无效。")
    if not isinstance(controls, dict) or len(controls) > 200:
        raise ValueError("参数控件格式无效。")
    for key, value in controls.items():
        if (not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", key) or key.endswith("-file") or key in {"import-path", "wf-structure-confirmed"}
                or not isinstance(value, (str, bool)) or isinstance(value, str) and len(value) > 12000):
            raise ValueError("参数记忆只能保存控件值，不能保存文件或附件。")

    def check(value, depth=0):
        if depth > 12:
            raise ValueError("参数记忆层数过多。")
        if isinstance(value, dict):
            if len(value) > 250 or any(key in {"__proto__", "constructor", "prototype"} for key in value):
                raise ValueError("参数记忆包含无效字段。")
            for item in value.values(): check(item, depth+1)
        elif isinstance(value, list):
            if len(value) > 256:
                raise ValueError("参数记忆不能包含谱图数组。")
            for item in value: check(item, depth+1)
        elif isinstance(value, str):
            if len(value) > 12000: raise ValueError("参数文本过长。")
        elif value is not None and (not isinstance(value, (int, float, bool)) or not math.isfinite(value)):
            raise ValueError("参数记忆包含无效数值。")
    check(snapshot)
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_BYTES:
        raise ValueError("参数记忆超过大小限制。")
    return snapshot


def register_preferences(app, root):
    app.config["UI_PREFERENCES_FILE"] = Path(root) / "web_runs" / "ui_preferences.json"
    lock = threading.RLock()

    def read(path):
        if not path.exists(): return None
        if path.stat().st_size > 2*MAX_BYTES + 4096: raise ValueError("参数记忆文件过大。")
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(record, dict) or record.get("format") != VERSION: raise ValueError("不支持的参数记忆版本。")
        validate_snapshot(record["snapshot"])
        validate_writer(record.get("writer"))
        if not isinstance(record.get("saved_utc"), str): raise ValueError("参数记忆时间格式无效。")
        return record

    def validate_writer(writer):
        if (not isinstance(writer, dict) or not isinstance(writer.get("id"), str) or not 1 <= len(writer["id"]) <= 80
                or type(writer.get("sequence")) is not int or writer["sequence"] < 1):
            raise ValueError("参数记忆请求标识无效。")

    @app.route("/api/ui-preferences", methods=["GET", "POST"])
    def preferences():
        path = Path(app.config["UI_PREFERENCES_FILE"])
        try:
            with lock:
                if request.method == "GET":
                    return jsonify(format=VERSION, record=read(path))
                if request.content_length is None or request.content_length > MAX_BYTES:
                    return jsonify(error="参数记忆超过大小限制。"), 413
                payload = request.get_json(silent=True)
                if not isinstance(payload, dict): raise ValueError("请输入参数记忆对象。")
                snapshot = validate_snapshot(payload.get("snapshot"))
                writer = payload.get("writer", {})
                validate_writer(writer)
                previous = read(path)
                if previous and previous.get("writer", {}).get("id") == writer["id"] and previous["writer"]["sequence"] >= writer["sequence"]:
                    return jsonify(saved_utc=previous["saved_utc"], ignored_stale=True)
                record = {"format": VERSION, "saved_utc": datetime.now(timezone.utc).isoformat(),
                          "writer": writer, "snapshot": snapshot}
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(".tmp_" + path.name + "_" + uuid.uuid4().hex)
                try:
                    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
                        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                return jsonify(saved_utc=record["saved_utc"])
        except (ValueError, TypeError, KeyError, OSError) as error:
            return jsonify(error="参数记忆读写失败，原文件保留：" + str(error)), 400
