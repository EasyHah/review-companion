#!/usr/bin/env python3
"""Local review service with explicit user-triggered fixed-file editor actions."""
import argparse
import json
import os
from pathlib import Path
import sys

from companion_store import ReviewStore, StoreError
from git_snapshot import SnapshotError, collect_snapshot, read_commit_subject, export_source, export_diff
from editor_launcher import launch_editor
from file_policy import is_hidden_document

VERSION = "0.4.0"
ROOT = Path(__file__).resolve().parents[1]
RESOURCE = f"ui://review-companion/overview-{VERSION}.html"


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
ID = {"type": "string", "minLength": 1}
INTEGER = {"type": "integer"}
BOOL = {"type": "boolean"}
VERSION_SCHEMA = {"type": "integer", "minimum": 1}
FEATURE = obj({"title": {"type": "string", "minLength": 1, "maxLength": 100}, "behavior": {"type": "string", "minLength": 1, "maxLength": 240}, "implementation": {"type": "string", "minLength": 1, "maxLength": 240}, "file_ids": {"type": "array", "minItems": 1, "items": ID}, "responsibilities": {"type": "object", "additionalProperties": {"type": "string", "maxLength": 300}}}, ("title", "behavior", "implementation", "file_ids"))

SPECS = {
    "set_file_risks": ("将对话已提出的风险线索映射到对应文件。只更新传入文件的短说明，不改变功能分组、结论或回顾进度。notes=[]仅清除该文件标记；未传文件保持原样。详细依据与验证留在对话。", obj({"review_id": ID, "expected_version": VERSION_SCHEMA, "updates": {"type":"array","minItems":1,"maxItems":50,"items": obj({"file_id":ID,"notes":{"type":"array","maxItems":5,"items":{"type":"string","minLength":1,"maxLength":300}}}, ("file_id","notes"))}}, ("review_id","expected_version","updates")), False),
    "read_fixed_evidence": ("分页读取回顾固定提交源码或两端 Diff 的文字证据，不接受任意路径。offset/limit 按字符计算，按 next_offset 继续读取。", obj({"review_id": ID, "file_id": ID, "mode": {"type": "string", "enum": ["source", "diff"]}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 24000}}, ("review_id", "file_id")), True),
    "start_review": ("建立整个开发分支相对主分支的固定提交回顾。只读取已提交改动，不 fetch 或切换分支；同一固定范围再次打开保留进度。随后由 skill 轻量梳理功能总览。", obj({"repository_path": ID, "main_ref": ID, "target_ref": ID}, ("repository_path",)), False),
    "read_review": ("只读回顾数据，不打开面板。用于继续讲解、查询进度和后台刷新。省略 review_id 读取最近回顾。打开面板请仅在用户要求时使用 open_review。", obj({"review_id": ID}), True),
    "open_review": ("首次或用户明确要求时打开回顾面板；指定 review_id 会选择该回顾。不在每份文件讲解、查询或更新结论时重复调用，已打开面板自行刷新。", obj({"review_id": ID}), False),
    "activate_review": ("由用户明确选择另一份回顾时，将它设为最近回顾并返回总览；后台刷新应使用只读 read_review。", obj({"review_id": ID}, ("review_id",)), False),
    "list_review_files": ("按页读取此次固定范围的文件职责、回顾状态和短结论。用 next_offset 继续；不要把单页当成完整文件清单。", obj({"review_id": ID, "feature_id": ID, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("review_id",)), True),
    "set_review_overview": ("保存 skill 轻量梳理的功能总览：行为变化在前、实现概括在后。只引用本次回顾的文件标识；保留既有进度和短结论。必须使用刚读取的 expected_version。title≤100字，behavior/implementation各≤240字，职责≤300字；file_ids原样复制。未归类文件自动保留。", obj({"review_id": ID, "expected_version": VERSION_SCHEMA, "features": {"type": "array", "minItems": 1, "maxItems": 49, "items": FEATURE}}, ("review_id", "expected_version", "features")), False),
    "set_file_review": ("仅在对话中实际交付当前文件讲解后保存；总览或读取关联文件不算已讲解。默认保存当前一份后结束，除非用户明确批量请求。在同一次更新中保存 reviewed、conclusion（改动）和 impact_conclusion（影响）。省略任一结论时保留原值。仅单独追问影响时用 set_file_conclusion，不改进度。版本冲突先重读。", obj({"review_id": ID, "file_id": ID, "expected_version": VERSION_SCHEMA, "reviewed": BOOL, "conclusion": {"type": "string", "maxLength": 500}, "impact_conclusion": {"type": "string", "maxLength": 500}}, ("review_id", "file_id", "expected_version")), False),
    "set_file_conclusion": ("分别保存 explanation 解释结论或 impact 影响结论；只改指定结论，不覆盖另一类结论或改变回顾状态。已有面板自行刷新，无需重新打开。", obj({"review_id": ID, "file_id": ID, "expected_version": VERSION_SCHEMA, "conclusion_kind": {"type": "string", "enum": ["explanation", "impact"]}, "conclusion": {"type": "string", "maxLength": 500}}, ("review_id", "file_id", "expected_version", "conclusion_kind", "conclusion")), False),
    "prepare_editor": ("将固定提交中的源文件或比较两端导出为只读文件，返回编辑器链接和可复制命令。删除文件使用基准版本。不启动编辑器，不返回源码内容。", obj({"review_id": ID, "file_id": ID, "line": {"type": "integer", "minimum": 1}, "mode": {"type": "string", "enum": ["source", "diff"]}}, ("review_id", "file_id")), False),
    "open_project": ("仅由用户点击面板按钮，打开本次回顾对应的本机项目目录；项目树使用当前工作区。", obj({"review_id": ID}, ("review_id",)), False),
    "open_editor": ("仅在用户点击源码/Diff按钮或明确要求打开编辑器时，导出固定版本并向本机已安装的 VS Code 发起打开请求。不得在讲解或进度更新时自动调用。失败会返回可手工执行的命令。", obj({"review_id": ID, "file_id": ID, "line": {"type": "integer", "minimum": 1}, "mode": {"type": "string", "enum": ["source", "diff"]}}, ("review_id", "file_id")), False),
}




def validate(value, schema, label="arguments"):
    kind = schema.get("type")
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str), "integer": type(value) is int, "boolean": type(value) is bool}
    if kind and not matches.get(kind, False):
        raise StoreError("invalid_argument", f"{label} 类型不正确")
    if "enum" in schema and value not in schema["enum"]:
        raise StoreError("invalid_argument", f"{label} 值不受支持")
    if kind == "object":
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            raise StoreError("invalid_argument", f"{label} 缺少必需字段")
        for key, item in value.items():
            nested = properties.get(key, schema.get("additionalProperties", True))
            if nested is False:
                raise StoreError("invalid_argument", f"不支持字段 {label}.{key}")
            if isinstance(nested, dict):
                validate(item, nested, f"{label}.{key}")
    if kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 100_000):
            raise StoreError("invalid_argument", f"{label} 数量不正确")
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{label}[{index}]")
    if kind == "string":
        minimum, maximum = schema.get("minLength", 0), schema.get("maxLength", 8192)
        if "\x00" in value:
            raise StoreError("invalid_argument", f"{label} 不能包含空字符", field=label)
        if len(value) < minimum or len(value) > maximum:
            raise StoreError("invalid_argument", f"{label} 当前 {len(value)} 字，要求 {minimum}–{maximum} 字；只修正此字段后重试。", field=label, actual_length=len(value), min_length=minimum, max_length=maximum)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise StoreError("invalid_argument", f"{label} 必须是有效的 Unicode 文字") from exc
    if kind == "integer" and (value < schema.get("minimum", -sys.maxsize) or value > schema.get("maximum", sys.maxsize)):
        raise StoreError("invalid_argument", f"{label} 超出范围")


class Service:
    def __init__(self, data_directory, repository_scope=None):
        self.store = ReviewStore(data_directory)
        self.repository_scope = str(Path(repository_scope).resolve()) if repository_scope else None

    def _present(self, view):
        if self.repository_scope:
            view['recent_reviews']=[r for r in view.get('recent_reviews',[]) if str(Path(r['comparison']['repository']).resolve()) == self.repository_scope]
        # Git metadata is read after the state lock is released. The saved
        # identities, versions and progress never change for presentation.
        memo = {}

        def subject(comparison, side):
            key = (comparison.get("repository"), comparison.get(f"{side}_sha"))
            if key not in memo:
                try:
                    memo[key] = read_commit_subject(*key)
                except SnapshotError:
                    memo[key] = None
            return memo[key]

        def display_title(base, target):
            base = "基准提交（说明暂不可用）" if base is None else (base or "空提交说明")
            target = "目标提交（说明暂不可用）" if target is None else (target or "空提交说明")
            return f"{target[:80]} 相对 {base[:80]}"

        comparison = view["comparison"]
        base, target = subject(comparison, "base"), subject(comparison, "target")
        comparison["base_subject"], comparison["target_subject"] = base, target
        view["title"] = display_title(base, target)
        for recent in view.get("recent_reviews", []):
            scope = recent.get("comparison", {})
            recent["title"] = display_title(subject(scope, "base"), subject(scope, "target"))
        return view

    def _overview(self, review_id=None, activate=False):
        if self.repository_scope and review_id is None:
            def select(state):
                latest=state['reviews'].get(state['latest_review_id'])
                if latest and str(Path(latest['comparison']['repository']).resolve()) == self.repository_scope:return latest['review_id']
                records=[r for r in state['reviews'].values() if str(Path(r['comparison']['repository']).resolve()) == self.repository_scope]
                if not records:raise StoreError('no_review','本项目还没有回顾记录')
                return max(records,key=lambda r:r['last_opened_at'])['review_id']
            review_id=self.store._locked(select)
        return self._present(self.store.overview(review_id, activate, self.repository_scope))

    def dispatch(self, name, arguments):
        if not isinstance(name, str) or name not in SPECS:
            raise StoreError("unknown_tool", "没有找到指定工具")
        validate(arguments, SPECS[name][1])
        if name == "set_file_risks": return self._present(self.store.set_file_risks(**arguments))
        if name == 'read_fixed_evidence':
            prepared=self.dispatch('prepare_editor',{k:v for k,v in arguments.items() if k in ('review_id','file_id','mode')})
            mode=arguments.get('mode','source')
            files=[('source',prepared['path'])] if mode=='source' else [('base',prepared['left_path']),('target',prepared['right_path'])]
            chunks=[]
            for label,path in files:
                raw=Path(path).read_bytes()
                if b'\x00' in raw:raise StoreError('binary_evidence','固定文件含二进制内容，请显式打开外部编辑器')
                chunks.append('--- '+label+' ---\n'+raw.decode('utf-8','replace'))
            content='\n'.join(chunks);offset=arguments.get('offset',0);limit=arguments.get('limit',24000)
            record=self.store.raw_review(arguments['review_id'])
            return {'review_id':arguments['review_id'],'file_id':arguments['file_id'],'comparison':record['comparison'],'mode':mode,'text':content[offset:offset+limit],'offset':offset,'next_offset':offset+limit if offset+limit<len(content) else None}
        if name == "start_review":
            snapshot = collect_snapshot(arguments["repository_path"], arguments.get("main_ref"), arguments.get("target_ref", "HEAD"))
            record = self.store.create(snapshot)
            result = self._overview(record["review_id"])
            if not record["overview_ready"]:
                result["first_files"] = self.store.list_files(record["review_id"], limit=20)
            return result
        if name == "read_review":
            return self._overview(**arguments)
        if name == "open_review":
            try:
                identifier = arguments.get("review_id")
                return self._overview(identifier, activate=bool(identifier))
            except StoreError as error:
                if error.code != "no_review":
                    raise
                return {"review_id": None, "empty": True, "message": str(error), "features": [], "recent_reviews": []}
        if name == "activate_review":
            return self._overview(arguments["review_id"], activate=True)
        if name == "list_review_files":
            return self.store.list_files(**arguments)
        if name == "set_review_overview":
            return self._present(self.store.set_overview(**arguments))
        if name == "set_file_review":
            return self._present(self.store.set_file(**arguments))
        if name == "set_file_conclusion":
            return self._present(self.store.set_conclusion(**arguments))
        record = self.store.raw_review(arguments["review_id"])
        if name == "open_project":
            return launch_editor({}, "project", repository=record["comparison"]["repository"])
        file = record["files"].get(arguments["file_id"])
        if file is None:
            raise StoreError("file_not_found", "文件不属于这次固定范围的回顾")
        if is_hidden_document(file):
            raise StoreError('excluded_document', '文档文件不属于本次代码回顾与证据检索范围')
        export_root = self.store.directory / "exports"
        mode = arguments.get("mode", "source")
        if mode == "diff":
            prepared = export_diff(record["comparison"], file, export_root)
        else:
            prepared = export_source(record["comparison"], file, export_root, arguments.get("line", 1))
        return launch_editor(prepared, mode, repository=record["comparison"]["repository"]) if name == "open_editor" else prepared

    def tools(self):
        result = []
        for name, (description, schema, readonly) in SPECS.items():
            meta = {"ui": {"visibility": ["model", "app"]}, "openai/widgetAccessible": True}
            if name == "open_review":
                meta["ui"]["resourceUri"] = RESOURCE
                meta["openai/outputTemplate"] = RESOURCE
                meta["openai/ui"] = {"entrypoints": [{"type": "thread"}]}
            if name in ("open_editor", "open_project"):
                meta["ui"]["visibility"] = ["app"]
            result.append({"name": name, "description": description, "inputSchema": schema, "annotations": {"readOnlyHint": readonly, "destructiveHint": False, "openWorldHint": False}, "_meta": meta})
        return result

    def rpc(self, method, params):
        if method == "initialize":
            return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}}, "serverInfo": {"name": "review-companion-dev", "version": VERSION}, "instructions": "Let reviewing-development-changes lead explanations. read_review is data-only; open_review renders the panel and is only for initial/user-requested opening, never each file or update. Use fixed comparison SHAs. Explain behavior, implementation and impact together, then set_file_review with conclusion and impact_conclusion in one update. Save impact answers via set_file_conclusion(conclusion_kind=impact) without changing review status or explanation. Never send messages automatically. Resource additions/modifications are excluded from recap progress. Native UI/editor acceptance remains user-operated."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools()}
        if method == "tools/call":
            try:
                result = self.dispatch(params.get("name"), params.get("arguments", {}))
                encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                if len(encoded.encode("utf-8")) > 180_000:
                    raise StoreError("result_too_large", "返回文字过大，请减少每页文件数或总览文字")
                return {"content": [{"type": "text", "text": encoded}], "structuredContent": result, "isError": False}
            except (StoreError, SnapshotError) as exc:
                error = {"code": exc.code, "message": str(exc), **getattr(exc, "details", {})}
                return {"content": [{"type": "text", "text": json.dumps({"error": error}, ensure_ascii=False)}], "structuredContent": {"error": error}, "isError": True}
        if method == "resources/list":
            return {"resources": [{"uri": RESOURCE, "name": "Review companion overview", "mimeType": "text/html;profile=mcp-app"}]}
        if method == "resources/read":
            if params.get("uri") != RESOURCE:
                raise StoreError("resource_not_found", "没有找到指定面板")
            return {"contents": [{"uri": RESOURCE, "mimeType": "text/html;profile=mcp-app", "text": (ROOT / "assets" / "app.html").read_text(encoding="utf-8"), "_meta": {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}, "openai/widgetCSP": {"connect_domains": [], "resource_domains": []}}}]}
        raise StoreError("method_not_found", "不支持此 MCP 方法")


def serve(service):
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON"}}
        else:
            if not isinstance(request, dict):
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            elif "id" not in request:
                continue
            else:
                identifier = request["id"]
                try:
                    params = request.get("params", {})
                    if not isinstance(params, dict):
                        raise StoreError("invalid_argument", "params must be an object")
                    response = {"jsonrpc": "2.0", "id": identifier, "result": service.rpc(request.get("method"), params)}
                except StoreError as exc:
                    code = -32601 if exc.code == "method_not_found" else -32602
                    response = {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": str(exc)}}
                except Exception as exc:
                    print(f"review-companion: {type(exc).__name__}", file=sys.stderr)
                    response = {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32603, "message": "Internal error; existing records retained"}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["mcp", "doctor"])
    parser.add_argument("--data-dir", default=os.environ.get("REVIEW_COMPANION_DATA_DIR") or os.environ.get("PLUGIN_DATA") or str(ROOT / "data"))
    arguments = parser.parse_args()
    if arguments.mode == "doctor":
        print(json.dumps({"version": VERSION, "python": sys.version.split()[0], "data_directory": str(Path(arguments.data_dir).expanduser().resolve()), "ui_resource_exists": (ROOT / "assets" / "app.html").is_file(), "registration_status": "not_checked"}, ensure_ascii=False))
    else:
        serve(Service(arguments.data_dir))


if __name__ == "__main__":
    main()
