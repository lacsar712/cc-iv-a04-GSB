import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, patch
from litestar.exceptions import HTTPException
from psycopg import errors as pg_errors

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")

from passlib.context import CryptContext
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def need_writer(request: Request, action: str = "提交"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail=f"观察员只能翻看，不能{action}")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      cable_spec, ampacity_a, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request, "交扫描单")
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 交单前先查这串绑了哪根电缆，载流取册子的当前值
        bound = conn.execute(
            """SELECT c.spec_code, c.ampacity_a
               FROM cable_bindings b JOIN cables c ON c.id = b.cable_id
               WHERE b.string_code = %s""",
            (code,),
        ).fetchone()

        def reject(reason: str, spec, ampacity):
            conn.execute(
                """INSERT INTO scan_rejections
                   (string_code, voc_v, isc_a, fill_factor, cable_spec, ampacity_a,
                    reason, rejected_by, rejected_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (code, voc, isc, ff, spec, ampacity, reason, user["username"], now),
            )
            conn.commit()
            raise HTTPException(status_code=400, detail=reason)

        if bound is None:
            reject(
                f"组串「{code}」还没绑电缆截面，先到电缆截面页绑好再交单",
                None, None,
            )
        spec = bound["spec_code"]
        ampacity = float(bound["ampacity_a"])
        if ampacity < isc:
            reject(
                f"截面{spec}允许载流只有{ampacity:g}A，扛不住这串{isc:g}A的短路电流，"
                f"换更粗的电缆绑好再来",
                spec, ampacity,
            )
        # 校验通过：截面编号和载流随单冻住，事后改册动不了这张单
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, cable_spec, ampacity_a,
                created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         cable_spec, ampacity_a, created_by, created_at, processed_at""",
            (code, voc, isc, ff, spec, ampacity, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/cables")
async def list_cables(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.id, c.spec_code, c.ampacity_a, c.created_by, c.created_at,
                      c.updated_at, b.string_code AS bound_string, b.bound_by, b.bound_at
               FROM cables c
               LEFT JOIN cable_bindings b ON b.cable_id = c.id
               ORDER BY c.id"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/cables", status_code=201)
async def create_cable(request: Request) -> dict:
    user = need_writer(request, "登记电缆")
    data = await request.json()
    spec = (data.get("spec_code") or "").strip()
    if not spec:
        raise HTTPException(status_code=400, detail="截面编号不能为空")
    try:
        ampacity = float(data.get("ampacity_a"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="允许载流得填数字，单位安培")
    if ampacity <= 0:
        raise HTTPException(status_code=400, detail="允许载流得大于 0")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            row = conn.execute(
                """INSERT INTO cables (spec_code, ampacity_a, created_by, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s)
                   RETURNING id, spec_code, ampacity_a, created_by, created_at, updated_at""",
                (spec, ampacity, user["username"], now, now),
            ).fetchone()
            conn.commit()
        except pg_errors.UniqueViolation:
            raise HTTPException(
                status_code=409,
                detail=f"截面{spec}已经登记过了，载流变了直接在册子上改，不用再登一遍",
            )
        return dump(row)


@patch("/api/cables/{cable_id:int}")
async def update_cable(cable_id: int, request: Request) -> dict:
    user = need_writer(request, "改电缆册子")
    data = await request.json()
    try:
        ampacity = float(data.get("ampacity_a"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="允许载流得填数字，单位安培")
    if ampacity <= 0:
        raise HTTPException(status_code=400, detail="允许载流得大于 0")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """UPDATE cables SET ampacity_a=%s, updated_at=%s
               WHERE id=%s
               RETURNING id, spec_code, ampacity_a, created_by, created_at, updated_at""",
            (ampacity, now, cable_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="册子里没有这根电缆")
        conn.commit()
        return dump(row)


@post("/api/bindings", status_code=201)
async def create_binding(request: Request) -> dict:
    user = need_writer(request, "绑串")
    data = await request.json()
    spec = (data.get("spec_code") or "").strip()
    string_code = (data.get("string_code") or "").strip()
    if not spec:
        raise HTTPException(status_code=400, detail="要绑哪个截面？截面编号不能为空")
    if not string_code:
        raise HTTPException(status_code=400, detail="要绑到哪串？组串编号不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 锁住这根电缆的册子行：两个人同时绑同一截面时，后来的排队等锁，
        # 拿到锁后就能看见先到的绑定
        cable = conn.execute(
            "SELECT id, spec_code FROM cables WHERE spec_code = %s FOR UPDATE",
            (spec,),
        ).fetchone()
        if cable is None:
            raise HTTPException(
                status_code=404,
                detail=f"册子里没有截面{spec}，先登记电缆再来绑串",
            )
        clash = conn.execute(
            """SELECT c.spec_code, b.string_code, b.bound_by
               FROM cable_bindings b JOIN cables c ON c.id = b.cable_id
               WHERE b.cable_id = %s OR b.string_code = %s""",
            (cable["id"], string_code),
        ).fetchone()
        if clash is not None:
            if clash["string_code"] == string_code:
                detail = (
                    f"组串「{string_code}」已经绑了截面{clash['spec_code']}，"
                    f"一串只能绑一根电缆"
                )
            else:
                detail = (
                    f"截面{spec}已经被{clash['bound_by']}绑给组串"
                    f"「{clash['string_code']}」了，同一根电缆不能绑两串"
                )
            raise HTTPException(status_code=409, detail=detail)
        try:
            conn.execute(
                """INSERT INTO cable_bindings
                   (cable_id, string_code, bound_by, bound_at)
                   VALUES (%s,%s,%s,%s)""",
                (cable["id"], string_code, user["username"], now),
            )
            conn.commit()
        except pg_errors.UniqueViolation:
            # 不同电缆并发绑同一组串时，靠唯一约束在这里拦下第二份
            raise HTTPException(
                status_code=409,
                detail=f"组串「{string_code}」刚被别人绑走了，刷新看看最新绑定",
            )
        return {
            "cable_id": cable["id"],
            "spec_code": cable["spec_code"],
            "string_code": string_code,
            "bound_by": user["username"],
            "bound_at": now.isoformat(),
        }


@get("/api/rejections")
async def list_rejections(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, cable_spec, ampacity_a,
                      reason, rejected_by, rejected_at
               FROM scan_rejections ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(route_handlers=[
    health, login, list_logs, create_log,
    list_cables, create_cable, update_cable,
    create_binding, list_rejections,
])
