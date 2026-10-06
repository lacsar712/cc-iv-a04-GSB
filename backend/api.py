import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, delete, get, patch, post
from litestar.exceptions import HTTPException
from passlib.context import CryptContext
from psycopg.errors import UniqueViolation

from db import SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}

# 载流与短路电流的比较容差，避免 9.0 这种浮点尾巴误判
AMP_EPS = 1e-9


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


def need_writer(request: Request, action: str = "提交IV扫描"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail=f"观察员账号只能翻册，不能{action}")
    return user


def parse_ampacity(raw):
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="载流得填数字，单位安培")
    if val <= 0:
        raise HTTPException(status_code=400, detail="载流得大于 0，重新填一个")
    return val


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
                      created_by, created_at, processed_at,
                      cable_section_code, ampacity_a
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
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
    section_code = (data.get("cable_section_code") or "").strip()
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 先查这串绑的是哪份截面、当时登记的载流是多少
        if not section_code:
            cable = conn.execute(
                "SELECT section_code, ampacity_a, bound_string FROM cables WHERE bound_string = %s",
                (code,),
            ).fetchone()
            if cable is None:
                reject(conn, code, None, None, voc, isc, ff, now, user,
                       f"组串 {code} 还没绑电缆截面，先到「电缆截面」页绑好再交单")
            section_code = cable["section_code"]
            cable_amp = float(cable["ampacity_a"])
        else:
            cable = conn.execute(
                "SELECT section_code, ampacity_a, bound_string FROM cables WHERE section_code = %s",
                (section_code,),
            ).fetchone()
            if cable is None:
                reject(conn, code, section_code, None, voc, isc, ff, now, user,
                       f"截面 {section_code} 还没在台账上登记，先登记载流并绑上这串再交单")
            if cable["bound_string"] != code:
                reject(conn, code, section_code, cable["ampacity_a"], voc, isc, ff, now, user,
                       f"截面 {section_code} 绑的是「{cable['bound_string']}」，"
                       f"不是本组串 {code}，先绑对截面再交单")
            cable_amp = float(cable["ampacity_a"])

        if cable_amp + AMP_EPS < isc:
            reject(conn, code, section_code, cable_amp, voc, isc, ff, now, user,
                   f"截面 {section_code} 的载流只有 {cable_amp:g}A，"
                   f"低于这串的短路电流 {isc:g}A，载流不够，整单退回；先换载流够的截面再交")

        # 载流随单冻住：把当时截面与载流抄进单子，事后改册动不了旧单
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at,
                cable_section_code, ampacity_a)
               VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at, cable_section_code, ampacity_a""",
            (code, voc, isc, ff, user["username"], now, section_code, cable_amp),
        ).fetchone()
        conn.commit()
        return dump(row)


def reject(conn, code, section_code, cable_amp, voc, isc, ff, now, user, reason):
    """整笔退回：扫描单不留行，只在挡回台账上记一笔，然后用人话顶回去。"""
    conn.execute(
        """INSERT INTO scan_rejections
           (string_code, cable_section_code, cable_ampacity_a, voc_v, isc_a, fill_factor,
            reason, rejected_by, rejected_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (code, section_code, cable_amp, voc, isc, ff, reason, user["username"], now),
    )
    conn.commit()
    raise HTTPException(status_code=400, detail=reason)


@get("/api/cables")
async def list_cables(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT section_code, ampacity_a, bound_string, created_by,
                      created_at, updated_at, bound_at
               FROM cables ORDER BY section_code"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/cables", status_code=201)
async def create_cable(request: Request) -> dict:
    user = need_writer(request, action="登记电缆截面")
    data = await request.json()
    section_code = (data.get("section_code") or "").strip()
    if not section_code:
        raise HTTPException(status_code=400, detail="截面编号不能为空")
    ampacity = parse_ampacity(data.get("ampacity_a"))
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            row = conn.execute(
                """INSERT INTO cables
                   (section_code, ampacity_a, created_by, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s)
                   RETURNING section_code, ampacity_a, bound_string, created_by,
                             created_at, updated_at, bound_at""",
                (section_code, ampacity, user["username"], now, now),
            ).fetchone()
            conn.commit()
        except UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"截面 {section_code} 已经登记过了，别重复上账")
        return dump(row)


@patch("/api/cables/{section_code:str}")
async def update_cable_ampacity(request: Request, section_code: str) -> dict:
    user = need_writer(request, action="改电缆载流")
    data = await request.json()
    ampacity = parse_ampacity(data.get("ampacity_a"))
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 只改台账载流；旧扫描单的 ampacity_a 是随单冻结的快照，动不了
        row = conn.execute(
            """UPDATE cables SET ampacity_a=%s, updated_at=%s
               WHERE section_code=%s
               RETURNING section_code, ampacity_a, bound_string, created_by,
                         created_at, updated_at, bound_at""",
            (ampacity, now, section_code),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"截面 {section_code} 还没登记，改不了")
        conn.commit()
        return dump(row)


@post("/api/cables/{section_code:str}/binding")
async def bind_string(request: Request, section_code: str) -> dict:
    user = need_writer(request, action="绑串")
    data = await request.json()
    string_code = (data.get("string_code") or "").strip()
    if not string_code:
        raise HTTPException(status_code=400, detail="要绑的组串编号不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            with conn.transaction():
                # 原子认绑：bound_string 还空着才改得到。两人同时抢，只有一份 UPDATE 命中
                row = conn.execute(
                    """UPDATE cables SET bound_string=%s, bound_at=%s, updated_at=%s
                       WHERE section_code=%s AND bound_string IS NULL
                       RETURNING section_code, ampacity_a, bound_string, created_by,
                                 created_at, updated_at, bound_at""",
                    (string_code, now, now, section_code),
                ).fetchone()
        except UniqueViolation:
            conn.rollback()
            other = conn.execute(
                "SELECT section_code FROM cables WHERE bound_string=%s", (string_code,)
            ).fetchone()
            hint = f"，这串已经绑在截面 {other['section_code']} 上了" if other else ""
            raise HTTPException(
                status_code=409,
                detail=f"组串 {string_code} 刚被别的截面抢走了{hint}，一份组串只准绑一份截面",
            )
        if row is None:
            cable = conn.execute(
                "SELECT bound_string FROM cables WHERE section_code=%s", (section_code,)
            ).fetchone()
            if cable is None:
                raise HTTPException(status_code=404, detail=f"截面 {section_code} 还没登记，先登记载流")
            if cable["bound_string"] == string_code:
                bound = conn.execute(
                    """SELECT section_code, ampacity_a, bound_string, created_by,
                              created_at, updated_at, bound_at
                       FROM cables WHERE section_code=%s""",
                    (section_code,),
                ).fetchone()
                return dump(bound)
            raise HTTPException(
                status_code=409,
                detail=f"截面 {section_code} 已经绑在「{cable['bound_string']}」上了，"
                       f"想换串得先解绑",
            )
        conn.commit()
        return dump(row)


@delete("/api/cables/{section_code:str}/binding", status_code=200)
async def unbind_string(request: Request, section_code: str) -> dict:
    need_writer(request, action="解绑")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """UPDATE cables SET bound_string=NULL, bound_at=NULL, updated_at=%s
               WHERE section_code=%s
               RETURNING section_code, ampacity_a, bound_string, created_by,
                         created_at, updated_at, bound_at""",
            (now, section_code),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"截面 {section_code} 还没登记")
        conn.commit()
        return dump(row)


@get("/api/rejections")
async def list_rejections(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, cable_section_code, cable_ampacity_a,
                      voc_v, isc_a, fill_factor, reason, rejected_by, rejected_at
               FROM scan_rejections ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(route_handlers=[
    health, login, list_logs, create_log,
    list_cables, create_cable, update_cable_ampacity,
    bind_string, unbind_string, list_rejections,
])
