#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存量用户迁移：技术工艺本地 JSON -> 配置报价 CPQ（Postgres 唯一权威）。

用法（**默认 dry-run，不写库**）：

    python3 scripts/migrate_users_to_pg.py                 # 只打印计划
    python3 scripts/migrate_users_to_pg.py --dry-run       # 同上（显式写法）
    python3 scripts/migrate_users_to_pg.py --apply         # 真写库 + 落报告
    python3 scripts/migrate_users_to_pg.py --promote USER  # 把已存在账号提为 admin

搬什么：

  · ``DATA_DIR/_auth_users.json``（技术工艺本地账号表）-> ``cpq_wf.cpq_wf_user``；
    口令散列**无损转换**（同算法 sha256-pbkdf2、同迭代数，只是 base64 -> hex 编码），
    所以原口令照旧可用，不需要强制所有人改密码。
  · 同一趟搬 ``DATA_DIR/_user_llm.json``（批次 ## 86 的账号级模型与明文 Key）：
    按 username 换 user_id 后经 ``cpq_user_secrets.seal()`` 加密写
    ``cpq_wf.cpq_wf_user_llm_setting``。不搬这一份，现有账号的个人模型与个人 Key
    会在换存储的那一刻凭空消失。

不搬什么 / 不改什么：

  · 原文件一个字节都不动（既不删也不改写）—— 迁移后仍可复核，回滚时也有据可查；
  · 同名账号已存在 -> 跳过并报告（绝不覆盖线上账号的角色与口令）；
  · ``is_system=true`` 的历史归档账号 -> 导入为 ``status='archived'``；
  · 解析不了的口令散列 -> 不导入、列入 rejected，绝不静默生成空密码。

退出码：dry-run 与默认调用即使连不上 Postgres 也以 0 退出（只打印冲突检查跳过），
因为 dry-run 的职责是出计划、不是连库。``--apply`` 连不上库会明确失败（退出码非 0）。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import pathlib
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:            # 直接 python3 scripts/... 也要能 import cpq_auth
    sys.path.insert(0, str(ROOT))

USERS_FILENAME = "_auth_users.json"
LLM_FILENAME = "_user_llm.json"
REPORT_FILENAME = "_auth_users.migrated.json"
CONFLICT_SKIPPED = "冲突检查跳过"

# 技术工艺角色 -> CPQ 角色。两边角色字典不是一一对应，映射写在这里、显式可查：
#   · admin / viewer 同名；
#   · 工艺侧 process_manager 对应 CPQ 的 process_mgr（其余 *_manager 同理）；
#   · 工艺工程师在 CPQ 没有等价角色 —— 落地只读，并把原角色记进 requested_role，
#     等管理员在用户管理里授予业务角色（与自助注册同一口径：不静默提权、不静默降权）。
ROLE_MAP = {
    "admin": "admin",
    "viewer": "viewer",
    "engineer": "viewer",
    "sales_manager": "sales_mgr",
    "sales_director": "sales_mgr",
    "process_manager": "process_mgr",
    "process_director": "tech_director",
    "reviewer": "tech_director",
    "finance_manager": "finance_mgr",
    "general_manager": "tech_director",
}
# 已经是 CPQ 角色码的原样保留（脚本可反复跑）。
CPQ_ROLE_CODES = ("sales_mgr", "process_mgr", "finance_mgr", "tech_director", "admin", "viewer")

DL = "—"


def data_dir() -> pathlib.Path:
    """本地 JSON 所在目录。与 tech_app 一致：优先 DATA_DIR 环境变量。"""
    raw = str(os.getenv("DATA_DIR") or "").strip()
    if raw:
        return pathlib.Path(raw)
    return ROOT / "tech_app" / "data"


def users_path() -> pathlib.Path:
    return data_dir() / USERS_FILENAME


def llm_path() -> pathlib.Path:
    return data_dir() / LLM_FILENAME


def report_path() -> pathlib.Path:
    return data_dir() / REPORT_FILENAME


# --------------------------------------------------------------------------- #
# 口令散列：无损转换
# --------------------------------------------------------------------------- #
def convert_password_hash(stored: str) -> str:
    """技术工艺 pbkdf2$<iters>$<b64 salt>$<b64 dk> -> CPQ
    pbkdf2_sha256$<iters>$<hex salt>$<hex dk>。

    同算法、同迭代数，只是编码不同 —— 原口令仍能通过 cpq_auth.verify_password。
    已经是 CPQ 格式的原样返回（幂等）；解析不了一律 ValueError（调用方列入
    rejected，绝不生成空密码）。
    """
    text = str(stored or "").strip()
    parts = text.split("$")
    if len(parts) != 4:
        raise ValueError("无法识别的口令散列格式（期望 4 段 $ 分隔）：%r" % text[:48])
    algo, iters, salt_part, dk_part = parts
    try:
        rounds = int(iters)
    except ValueError:
        raise ValueError("口令散列的迭代数不是整数：%r" % iters[:24])
    if rounds <= 0:
        raise ValueError("口令散列的迭代数必须为正整数：%r" % iters[:24])
    if algo == "pbkdf2_sha256":
        try:
            salt_hex = bytes.fromhex(salt_part)
            dk_hex = bytes.fromhex(dk_part)
        except (ValueError, binascii.Error):
            raise ValueError("已经是 CPQ 格式但 salt/散列不是合法 hex：%r" % text[:48])
        if not salt_hex or not dk_hex:
            raise ValueError("已经是 CPQ 格式但 salt/散列为空：%r" % text[:48])
        return "pbkdf2_sha256$%d$%s$%s" % (rounds, salt_hex.hex(), dk_hex.hex())
    if algo != "pbkdf2":
        raise ValueError("无法识别的口令散列算法：%r" % algo[:24])
    try:
        salt = base64.b64decode(salt_part, validate=True)
        dk = base64.b64decode(dk_part, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("口令散列的 salt/散列不是合法 base64：%r" % text[:48])
    if not salt or not dk:
        raise ValueError("口令散列的 salt/散列为空：%r" % text[:48])
    return "pbkdf2_sha256$%d$%s$%s" % (rounds, salt.hex(), dk.hex())


# --------------------------------------------------------------------------- #
# 本地文件读取（只读，不动原文件）
# --------------------------------------------------------------------------- #
def _read_json(path: pathlib.Path):
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def load_legacy_users() -> dict:
    """_auth_users.json -> {username: entry}（不合法/不存在一律空 dict）。"""
    data = _read_json(users_path())
    if not isinstance(data, dict):
        return {}
    clean: dict = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("username") or key or "").strip()
        if name:
            clean[name] = entry
    return clean


def load_account_settings() -> dict:
    """_user_llm.json -> {username: {model, api_keys}}（兼容 {"users": {...}} 一层包装）。"""
    data = _read_json(llm_path())
    if not isinstance(data, dict):
        return {}
    users = data.get("users")
    source = users if isinstance(users, dict) else data
    clean: dict = {}
    for key, entry in source.items():
        if not isinstance(entry, dict):
            continue
        name = str(key or "").strip()
        if not name:
            continue
        keys = entry.get("api_keys")
        clean[name] = {
            "model": str(entry.get("model") or "").strip(),
            "api_keys": {str(p): str(k) for p, k in keys.items() if str(k or "").strip()}
            if isinstance(keys, dict) else {},
        }
    return clean


def map_role(role_code: str) -> tuple:
    """技术工艺角色 -> (CPQ role_code, 说明)。认不出来一律只读并记 requested_role。"""
    raw = str(role_code or "").strip()
    if raw in CPQ_ROLE_CODES:
        return raw, ""
    if raw in ROLE_MAP:
        return ROLE_MAP[raw], ""
    note = "技术工艺角色 %r 在 CPQ 没有等价角色，先落地只读" % (raw or "(空)")
    return "viewer", note


# --------------------------------------------------------------------------- #
# 计划（dry-run 与 apply 共用一份，保证"看到的计划 = 真做的动作"）
# --------------------------------------------------------------------------- #
def build_plan(legacy: dict, settings: dict, existing: set) -> dict:
    plan = {"import": [], "skip": [], "reject": [], "settings": []}
    for username, entry in legacy.items():
        display_name = str(entry.get("display_name") or username)
        item = {"username": username, "display_name": display_name}
        try:
            converted = convert_password_hash(entry.get("password_hash"))
        except ValueError as exc:
            item["reason"] = str(exc)
            plan["reject"].append(item)
            continue
        item["password_hash"] = converted
        role_code, note = map_role(entry.get("role"))
        item["role_code"] = role_code
        requested = str(entry.get("requested_role") or "").strip()
        if not requested and note:
            requested = str(entry.get("role") or "").strip()
        item["requested_role"] = requested
        item["is_system"] = bool(entry.get("is_system"))
        item["status"] = "archived" if item["is_system"] else "active"
        if note:
            item["note"] = note
        if username in existing:
            item["reason"] = "CPQ 中已存在同名账号，不覆盖"
            plan["skip"].append(item)
        else:
            plan["import"].append(item)

    for username, entry in settings.items():
        if username in legacy:
            where = "随建号一并写入"
        elif username in existing:
            where = "按已存在账号写入"
        else:
            where = "skipped（CPQ 里找不到该账号）"
        plan["settings"].append({
            "username": username,
            "model": bool(entry.get("model")),
            "keys": sorted((entry.get("api_keys") or {}).keys()),
            "disposition": where,
        })
    plan["settings"].sort(key=lambda row: row["username"])
    return plan


def print_plan(plan: dict, conflict_note: str) -> None:
    print("=" * 72)
    print("用户迁移计划（技术工艺本地 JSON -> CPQ Postgres）")
    print("  DATA_DIR : %s" % data_dir())
    print("  冲突检查 : %s" % (conflict_note or "已比对 CPQ 现有账号"))
    print("=" * 72)
    if not (plan["import"] or plan["skip"] or plan["reject"]):
        print("（没有本地用户要搬：未找到 %s）" % USERS_FILENAME)
    for item in plan["import"]:
        extra = ""
        if item.get("note"):
            extra += "；%s" % item["note"]
        if item.get("requested_role"):
            extra += "；requested_role=%s" % item["requested_role"]
        print("  [导入] %-16s 显示名=%-10s role=%s status=%s%s"
              % (item["username"], item["display_name"], item["role_code"],
                 item["status"], extra))
    for item in plan["skip"]:
        print("  [跳过] %-16s %s" % (item["username"], item.get("reason") or "已存在"))
    for item in plan["reject"]:
        print("  [拒绝] %-16s %s" % (item["username"], item.get("reason") or "无法解析"))
    if plan["settings"]:
        print("-" * 72)
        print("账号级模型与密钥（%s）：" % LLM_FILENAME)
        for item in plan["settings"]:
            print("  [设置] %-16s model=%s keys=%s -- %s"
                  % (item["username"], "有" if item["model"] else "无",
                     ",".join(item["keys"]) or "无", item["disposition"]))
    print("-" * 72)
    print("合计：导入 %d / 跳过 %d / 拒绝 %d；账号级设置 %d"
          % (len(plan["import"]), len(plan["skip"]), len(plan["reject"]),
             len(plan["settings"])))


# --------------------------------------------------------------------------- #
# 与 CPQ 的交互（全在函数里 import，模块导入阶段绝不连库）
# --------------------------------------------------------------------------- #
def existing_usernames() -> set:
    """CPQ 现有账号名（含停用/归档）。连不上会抛 BackendUnavailable。"""
    import cpq_auth

    names = set()
    for status in ("active", "disabled", "archived"):
        for row in cpq_auth.list_users(status=status) or []:
            name = str((row or {}).get("username") or "").strip()
            if name:
                names.add(name)
    return names


def write_password_hash(user_id, stored_hash: str) -> None:
    """把**已经转换好的**口令散列写进 cpq_wf_user。

    为什么不复用 cpq_auth.set_password()：那个函数会重新加盐哈希，等于把搬过来的
    散列丢掉、强制所有存量账号改密码。迁移要的是"编码转换"，所以这里直接落库。
    """
    import cpq_auth

    conn = cpq_auth._connect()
    try:
        cpq_auth._exec(
            conn,
            "UPDATE cpq_wf_user SET password_hash = %s, updated_at = %s WHERE user_id = %s",
            (str(stored_hash), cpq_auth._ts(cpq_auth._now()), cpq_auth._uid(user_id)))
    finally:
        conn.close()


def run_dry(mode_label: str) -> int:
    legacy = load_legacy_users()
    settings = load_account_settings()
    conflict_note = ""
    existing: set = set()
    try:
        existing = existing_usernames()
    except Exception as exc:                    # noqa: BLE001 - dry-run 不因连不上库而失败
        conflict_note = "%s（%s: %s）-- 计划里不含[已存在账号]的比对结果" % (
            CONFLICT_SKIPPED, type(exc).__name__, str(exc)[:120])
    plan = build_plan(legacy, settings, existing)
    print_plan(plan, conflict_note)
    print("%s：只打印计划，未写库、未落任何文件。" % mode_label)
    print("要真写库：加 --apply（会另落一份 %s 报告）" % REPORT_FILENAME)
    return 0


def run_apply() -> int:
    try:
        import cpq_auth                                     # noqa: F401 - 落库用
        import cpq_user_secrets                             # noqa: F401 - 加密用
    except ImportError as exc:
        print("--apply 需要部署环境的依赖（psycopg[binary] 与 cryptography，见 "
              "requirements.txt），当前解释器缺少：%s" % exc, file=sys.stderr)
        return 2

    legacy = load_legacy_users()
    settings = load_account_settings()
    if not legacy and not settings:
        print("没有本地用户文件 %s，也没有 %s -- 无可搬迁内容，退出。"
              % (users_path(), llm_path()))
        return 0
    try:
        existing = existing_usernames()
    except Exception as exc:                    # noqa: BLE001
        print("--apply 需要连上 CPQ 的 Postgres，但连接失败：%s: %s"
              % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    plan = build_plan(legacy, settings, existing)
    print_plan(plan, "已比对 CPQ 现有账号")

    report = {"generated_at": _now_str(), "data_dir": str(data_dir()),
              "imported": [], "skipped": [], "rejected": [], "account_settings": []}
    for item in plan["reject"]:
        report["rejected"].append({"username": item["username"], "reason": item["reason"]})
    for item in plan["skip"]:
        report["skipped"].append({"username": item["username"], "reason": item["reason"]})

    user_ids: dict = {}                          # username -> str(user_id)

    for item in plan["import"]:
        username = item["username"]
        try:
            created = cpq_auth.create_user(
                username, "__migrated__", item["display_name"], item["role_code"],
                requested_role=item.get("requested_role") or "")
            user_id = str(created.get("user_id") or "")
            write_password_hash(user_id, item["password_hash"])
            if item["status"] != "active":
                cpq_auth.update_user(user_id, {"status": item["status"]})
        except Exception as exc:                # noqa: BLE001
            report["rejected"].append({"username": username,
                                       "reason": "%s: %s" % (type(exc).__name__, exc)})
            print("  [失败] %-16s %s" % (username, exc))
            continue
        user_ids[username] = user_id
        report["imported"].append({
            "username": username, "user_id": user_id, "role_code": item["role_code"],
            "status": item["status"], "display_name": item["display_name"],
            "requested_role": item.get("requested_role") or "",
        })
        print("  [已导入] %-14s user_id=%s role=%s status=%s"
              % (username, user_id, item["role_code"], item["status"]))

    for item in plan["skip"]:
        try:
            row = cpq_auth.find_user(item["username"])
        except Exception:                       # noqa: BLE001
            row = None
        if row:
            user_ids[item["username"]] = str(row.get("user_id") or "")

    for username, entry in settings.items():
        user_id = user_ids.get(username)
        if not user_id:
            report["account_settings"].append(
                {"username": username, "disposition": "skipped",
                 "reason": "CPQ 里找不到该账号，先把账号建出来再重跑"})
            continue
        try:
            model = entry.get("model") or None
            if model is not None or entry.get("api_keys"):
                cpq_auth.set_user_llm(user_id, model=model)
                for provider, key in (entry.get("api_keys") or {}).items():
                    if key:
                        cpq_user_secrets.seal(str(key))     # 先过一道加密，缺密钥当场失败
                        cpq_auth.set_user_llm(user_id, provider=provider, key=str(key))
        except Exception as exc:                # noqa: BLE001
            report["account_settings"].append(
                {"username": username, "disposition": "failed",
                 "reason": "%s: %s" % (type(exc).__name__, exc)})
            continue
        report["account_settings"].append(
            {"username": username, "user_id": user_id, "disposition": "imported",
             "model": bool(entry.get("model")),
             "keys": sorted((entry.get("api_keys") or {}).keys())})

    target = report_path()
    try:
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except OSError as exc:
        print("报告写入失败（%s）：%s" % (target, exc), file=sys.stderr)
        return 1
    print("=" * 72)
    print("迁移报告：%s（imported %d / skipped %d / rejected %d；账号级设置 %d）"
          % (target, len(report["imported"]), len(report["skipped"]),
             len(report["rejected"]), len(report["account_settings"])))
    print("原文件未改动：%s、%s" % (users_path(), llm_path()))
    return 0


def run_promote(username: str) -> int:
    import cpq_auth

    username = str(username or "").strip()
    if not username:
        print("--promote 需要一个用户名", file=sys.stderr)
        return 2
    try:
        row = cpq_auth.find_user(username)
    except Exception as exc:                    # noqa: BLE001
        print("连接 CPQ 失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    if not row:
        print("CPQ 里没有账号 %r -- 先跑 --apply 或让管理员建号。" % username,
              file=sys.stderr)
        return 1
    try:
        updated = cpq_auth.update_user(str(row.get("user_id") or ""), {"role_code": "admin"})
    except Exception as exc:                    # noqa: BLE001
        print("提升失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    print("已把 %s（user_id=%s）提升为 admin；现在是 role_code=%s"
          % (username, updated.get("user_id"), updated.get("role_code")))
    return 0


def _now_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把技术工艺本地用户数据搬到配置报价 CPQ 的 Postgres（默认 dry-run）。")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划，不写库、不落任何文件（默认行为）")
    parser.add_argument("--apply", action="store_true",
                        help="真写库，并把结果报告落 DATA_DIR/%s" % REPORT_FILENAME)
    parser.add_argument("--promote", metavar="USERNAME", default="",
                        help="把一个已存在账号提升为 admin（首个管理员的引导路径）")
    args = parser.parse_args(argv)

    if args.promote:
        if args.apply:
            print("--promote 与 --apply 不能同时用：promote 只动一个账号的角色。",
                  file=sys.stderr)
            return 2
        return run_promote(args.promote)
    if args.apply and args.dry_run:
        print("--apply 与 --dry-run 互斥。", file=sys.stderr)
        return 2
    if args.apply:
        return run_apply()
    return run_dry("dry-run" if args.dry_run else "默认（dry-run）")


if __name__ == "__main__":
    raise SystemExit(main())
