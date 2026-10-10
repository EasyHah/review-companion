"""One private, atomically replaced store for independent review records."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from datetime import datetime, timezone
import uuid

if __package__:
    from .file_policy import is_hidden_resource, is_hidden_document, is_documentation_path
else:
    from file_policy import is_hidden_resource, is_hidden_document, is_documentation_path


class StoreError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, field, limit, allow_empty=True):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise StoreError("invalid_argument", f"{field} 必须是至多 {limit} 字的文字")
    if not allow_empty and not value.strip():
        raise StoreError("invalid_argument", f"{field} 不能为空")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise StoreError("invalid_argument", f"{field} 必须是有效的 Unicode 文字") from exc
    return value


class ReviewStore:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.path = self.directory / "reviews.json"

    def _locked(self, operation, write=False):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.directory / ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self._load()
            result = operation(state)
            if write:
                self._save(state)
            return copy.deepcopy(result)

    def _load(self):
        if not self.path.exists():
            return {"schema_version": 1, "latest_review_id": None, "reviews": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("state_unreadable", "回顾记录无法读取；原文件已保留，请检查数据目录") from exc
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise StoreError("unsupported_schema", "回顾记录格式不受此开发版支持；原文件已保留")
        if not isinstance(state.get("reviews"), dict):
            raise StoreError("state_unreadable", "回顾记录结构损坏；原文件已保留")
        latest = state.get("latest_review_id")
        if latest is not None and latest not in state["reviews"]:
            raise StoreError("state_unreadable", "最近回顾的定位记录损坏；原文件已保留")
        return state

    def _save(self, state):
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        fd, name = tempfile.mkstemp(prefix=".reviews-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _get(self, state, review_id=None):
        identifier = review_id or state["latest_review_id"]
        if identifier is None:
            raise StoreError("no_review", "还没有回顾记录，请先让 skill 建立本次分支回顾")
        if not isinstance(identifier, str) or identifier not in state["reviews"]:
            raise StoreError("review_not_found", "没有找到指定回顾")
        return state["reviews"][identifier]

    @staticmethod
    def _check_version(review, expected):
        if type(expected) is not int:
            raise StoreError("invalid_argument", "expected_version 必须是读取记录得到的整数版本")
        if expected != review["version"]:
            raise StoreError("version_conflict", "回顾已在其他操作中更新，请重新读取后再确认", current_version=review["version"])

    @staticmethod
    def _touch(review):
        review["version"] += 1
        review["updated_at"] = now()

    def create(self, snapshot):
        def apply(state):
            identity = (snapshot["repository"], snapshot["base_sha"], snapshot["target_sha"])
            for existing in state["reviews"].values():
                comparison = existing["comparison"]
                if identity == (comparison["repository"], comparison["base_sha"], comparison["target_sha"]):
                    state["latest_review_id"] = existing["review_id"]
                    existing["last_opened_at"] = now()
                    return existing
            identifier, timestamp = uuid.uuid4().hex, now()
            files = {}
            groups = {}
            for source in snapshot["files"]:
                item = {**source, "responsibility": "", "reviewed": False, "conclusion": ""}
                files[item["id"]] = item
                parts = PurePosixPath(item["path"]).parts
                key = parts[0] if len(parts) > 1 else "根目录"
                if key not in groups and len(groups) >= 49:
                    key = "其他目录"
                groups.setdefault(key, []).append(item["id"])
            features = [{"id": f"group-{i}", "title": f"{key}改动", "behavior": "待 skill 概括行为变化", "implementation": f"涉及 {len(ids)} 份改动文件", "file_ids": ids} for i, (key, ids) in enumerate(groups.items())]
            title = f"{snapshot.get('target_branch') or snapshot['target_ref']} 相对 {snapshot['main_ref']}"
            review = {"review_id": identifier, "version": 1, "title": title[:200], "comparison": {k: v for k, v in snapshot.items() if k != "files"}, "files": files, "features": features, "overview_ready": not files, "created_at": timestamp, "updated_at": timestamp, "last_opened_at": timestamp}
            state["reviews"][identifier] = review
            state["latest_review_id"] = identifier
            return review
        return self._locked(apply, write=True)

    def raw_review(self, review_id):
        return self._locked(lambda state: self._get(state, review_id))

    @staticmethod
    def _visible_files(review):
        return {identifier: item for identifier, item in review["files"].items() if not is_hidden_resource(item)}

    @staticmethod
    def _risk_notes(review, item):
        if 'risk_notes' in item:
            return item['risk_notes']
        # Read-only compatibility with the first trial. No record migration.
        notes = []
        for risk in review.get('risk_overview', {}).get('risks', []):
            refs = risk.get('evidence', [])
            if any(is_documentation_path(ref.get('path')) for ref in refs):
                continue
            feature = next((f for f in review.get('features', []) if f['id'] == risk.get('feature_id')), None)
            if not feature:
                continue
            candidates = set()
            for fid in feature['file_ids']:
                file = review.get('files', {}).get(fid)
                if not file or is_hidden_resource(file):
                    continue
                for ref in refs:
                    side = ref.get('side')
                    if side not in ('base', 'target'):
                        continue
                    if (side == 'base' and file['status'] == 'A') or (side == 'target' and file['status'] == 'D'):
                        continue
                    path = (file.get('old_path') or file['path']) if side == 'base' else file['path']
                    if ref.get('path') == path:
                        candidates.add(fid)
            # Old records identify a feature, not the role of each evidence
            # file. Only project an unambiguous owner; never guess from prose.
            if len(candidates) == 1 and item['id'] in candidates:
                note = risk.get('title', '')[:260]
                if note and note not in notes: notes.append(note)
        return notes[:5]

    @staticmethod
    def _public_file(item, review=None):
        notes = item.get("conclusions", {})
        if not isinstance(notes, dict):
            raise StoreError("state_unreadable", "文件短评结构损坏；原文件已保留")
        return {**item, "risk_notes": ReviewStore._risk_notes(review or {}, item), "conclusions": {kind: notes.get(kind, "") for kind in ("explanation", "impact")}}

    def overview(self, review_id=None, activate=False, repository=None):
        if type(activate) is not bool:
            raise StoreError("invalid_argument", "activate 必须是布尔值")
        def apply(state):
            review = self._get(state, review_id)
            if activate:
                state["latest_review_id"] = review["review_id"]
                review["last_opened_at"] = now()
            files = self._visible_files(review)
            features, next_file, seen = [], None, set()
            for feature in review["features"]:
                ids = [identifier for identifier in feature["file_ids"] if identifier in files]
                if not ids:
                    continue
                features.append({k: v for k, v in feature.items() if k != "file_ids"} | {"risk_count": sum(bool(self._risk_notes(review, files[i])) for i in ids), "total": len(ids), "reviewed": sum(bool(files[i]["reviewed"]) for i in ids)})
                for identifier in ids:
                    if identifier not in seen and next_file is None and not files[identifier]["reviewed"]:
                        next_file = {**self._public_file(files[identifier], review), "feature_id": feature["id"]}
                    seen.add(identifier)
            recent = sorted((item for item in state["reviews"].values() if repository is None or str(Path(item['comparison']['repository']).resolve()) == repository), key=lambda item: item["last_opened_at"], reverse=True)[:20]
            recent_summaries = []
            for item in recent:
                visible = self._visible_files(item)
                recent_summaries.append({"review_id": item["review_id"], "title": item["title"], "comparison": {key: item["comparison"].get(key) for key in ("repository", "base_sha", "target_sha")}, "total": len(visible), "reviewed": sum(bool(f["reviewed"]) for f in visible.values()), "updated_at": item["updated_at"]})
            comparison = dict(review["comparison"])
            comparison["status_counts"] = {status: sum(f["status"] == status for f in files.values()) for status in ("A", "M", "D", "R", "C", "T")}
            excluded = comparison.get("excluded_resource_count", 0) + len(review["files"]) - len(files)
            comparison["excluded_resource_count"] = excluded
            return {"review_id": review["review_id"], "version": review["version"], "title": review["title"], "comparison": comparison, "features": features, "overview_ready": not files or review["overview_ready"], "progress": {"total": len(files), "reviewed": sum(bool(item["reviewed"]) for item in files.values())}, "excluded_resource_count": excluded, "next_file": next_file, "recent_reviews": recent_summaries}
        return self._locked(apply, write=activate)

    def list_files(self, review_id, feature_id=None, offset=0, limit=50):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_argument", "文件分页要求 offset ≥ 0，limit 为 1 到 100")
        review = self.raw_review(review_id)
        files = self._visible_files(review)
        if feature_id is None:
            ids = list(files)
        else:
            feature = next((f for f in review["features"] if f["id"] == feature_id), None)
            if feature is None:
                raise StoreError("feature_not_found", "没有找到指定功能")
            ids = [identifier for identifier in feature["file_ids"] if identifier in files]
        items, size = [], 0
        for identifier in ids[offset:offset + limit]:
            item = self._public_file(files[identifier], review)
            item_size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if size + item_size > 90_000:
                if not items:
                    raise StoreError("item_too_large", "该文件的文字信息过大，无法分页返回")
                break
            items.append(item)
            size += item_size
        end = offset + len(items)
        return {"review_id": review_id, "version": review["version"], "feature_id": feature_id, "items": items, "total": len(ids), "offset": offset, "next_offset": end if end < len(ids) else None}

    def set_file(self, review_id, file_id, expected_version, reviewed=True, conclusion=None, impact_conclusion=None):
        if type(reviewed) is not bool:
            raise StoreError("invalid_argument", "reviewed 必须是布尔值")
        return self._update_file(review_id, file_id, expected_version, reviewed, "explanation", conclusion, impact_conclusion)

    def set_conclusion(self, review_id, file_id, expected_version, conclusion_kind, conclusion):
        text(conclusion, "conclusion", 500)
        return self._update_file(review_id, file_id, expected_version, None, conclusion_kind, conclusion)

    def _update_file(self, review_id, file_id, expected_version, reviewed, conclusion_kind, conclusion, impact_conclusion=None):
        if conclusion_kind not in ("explanation", "impact"):
            raise StoreError("invalid_argument", "conclusion_kind 应为 explanation 或 impact")
        if conclusion is not None:
            text(conclusion, "conclusion", 500)
        if impact_conclusion is not None:
            text(impact_conclusion, "impact_conclusion", 500)
        def apply(state):
            review = self._get(state, review_id)
            self._check_version(review, expected_version)
            if file_id not in review["files"]:
                raise StoreError("file_not_found", "文件不属于这次固定范围的回顾")
            item = review["files"][file_id]
            if reviewed is not None:
                item["reviewed"] = reviewed
            if conclusion is not None or impact_conclusion is not None:
                notes = item.setdefault("conclusions", {})
                if not isinstance(notes, dict):
                    raise StoreError("state_unreadable", "文件短评结构损坏；原文件已保留")
                if conclusion is not None: notes[conclusion_kind] = conclusion
                if impact_conclusion is not None: notes['impact'] = impact_conclusion
            self._touch(review)
        self._locked(apply, write=True)
        return self.overview(review_id)

    def set_file_risks(self, review_id, expected_version, updates):
        if not isinstance(updates, list) or not 1 <= len(updates) <= 50:
            raise StoreError('invalid_argument', 'updates 须包含1–50份文件')
        def apply(state):
            review = self._get(state, review_id)
            self._check_version(review, expected_version)
            visible = self._visible_files(review)
            seen = set()
            for update in updates:
                if not isinstance(update, dict) or set(update) != {'file_id', 'notes'}:
                    raise StoreError('invalid_argument', '每项只包含 file_id 和 notes')
                fid, notes = update['file_id'], update['notes']
                if not isinstance(fid, str) or fid not in visible or fid in seen:
                    raise StoreError('file_not_found', '风险标记须对应本次可回顾文件，且不能重复')
                if not isinstance(notes, list) or len(notes) > 5:
                    raise StoreError('invalid_argument', '每份文件最多5条风险线索')
                seen.add(fid)
                for note in notes: text(note, 'risk_note', 300, False)
            for update in updates:
                review['files'][update['file_id']]['risk_notes'] = list(update['notes'])
            self._touch(review)
        self._locked(apply, write=True)
        return self.overview(review_id)

    def set_overview(self, review_id, expected_version, features):
        if not isinstance(features, list) or not 1 <= len(features) <= 49:
            raise StoreError("invalid_argument", "features 应包含 1 到 49 个功能分组")
        def apply(state):
            review = self._get(state, review_id)
            self._check_version(review, expected_version)
            result, covered = [], set()
            for index, feature in enumerate(features):
                if not isinstance(feature, dict) or set(feature) - {"title", "behavior", "implementation", "file_ids", "responsibilities"}:
                    raise StoreError("invalid_argument", "功能分组字段不正确")
                title = text(feature.get("title"), "title", 100, False)
                behavior = text(feature.get("behavior"), "behavior", 240, False)
                implementation = text(feature.get("implementation"), "implementation", 240, False)
                ids = feature.get("file_ids")
                field = f"features[{index}].file_ids"
                if not isinstance(ids, list) or not ids:
                    raise StoreError("invalid_argument", f"{field} 必须是非空文件标识列表", field=field)
                seen = set()
                for position, identifier in enumerate(ids):
                    location = f"{field}[{position}]"
                    if not isinstance(identifier, str):
                        raise StoreError("invalid_argument", f"{location} 必须是文件标识文字", field=location)
                    if identifier not in review["files"]:
                        raise StoreError("invalid_argument", f"{location} 不属于本次回顾：{identifier}。从已返回的文件清单原样复制 ID；不需要重读源码或补齐全部分组。", field=location, invalid_file_id=identifier)
                    if identifier in seen:
                        raise StoreError("invalid_argument", f"{location} 在本分组内重复：{identifier}。删除重复项后重试。", field=location, duplicate_file_id=identifier)
                    seen.add(identifier)
                roles = feature.get("responsibilities", {})
                if not isinstance(roles, dict) or any(identifier not in ids for identifier in roles):
                    raise StoreError("invalid_argument", "职责说明必须对应本功能的文件")
                for identifier, role in roles.items():
                    review["files"][identifier]["responsibility"] = text(role, "responsibility", 300)
                feature_id = hashlib.sha256(f"{index}:{title}".encode()).hexdigest()[:16]
                result.append({"id": feature_id, "title": title, "behavior": behavior, "implementation": implementation, "file_ids": ids})
                covered.update(ids)
            remaining = [identifier for identifier in self._visible_files(review) if identifier not in covered]
            if remaining:
                result.append({"id": "ungrouped", "title": "其他改动", "behavior": "待补充功能概括", "implementation": f"还有 {len(remaining)} 份文件尚未归类", "file_ids": remaining})
            review["features"] = result
            review["overview_ready"] = not remaining
            self._touch(review)
        self._locked(apply, write=True)
        return self.overview(review_id)
