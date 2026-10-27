#!/usr/bin/env python3
"""
Dong bo spending cua tung Business Center (TikTok API for Business) vao Lark Base.
Chay moi ngay 6h sang (gio VN) qua GitHub Actions.

Bang trong Base:
  1) Bang chi tiet (LARK_TABLE_ID): 1 dong = 1 tai khoan quang cao x 1 ngay
     + Quoc gia, Mui gio, Loai hinh quang cao (theo muc tieu chien dich)
  2) "Tổng hợp BC" (tu tao): 1 dong = 1 BC x 1 ngay -> tang/giam, 7 ngay, thang nay,
     du kien cuoi thang, % ngan sach, canh bao
  3) "Ngân sách BC" (tu tao): quan ly nhap ngan sach thang (USD) cho tung BC

  python tiktok_bc_spend_to_lark.py             # dong bo
  python tiktok_bc_spend_to_lark.py --dry-run   # chi in ket qua, khong ghi vao Base

Bien moi truong (GitHub Secrets):
  TT_ACCESS_TOKEN (+ TT_ACCESS_TOKEN_2..10), LARK_APP_ID, LARK_APP_SECRET, LARK_BASE_TOKEN, LARK_TABLE_ID
  Tuy chon: DAYS_BACK (2), PARALLEL (3), FETCH_BALANCE (1), FX_RATES (vd VND=26000),
            ALERT_CHANGE_PCT (30), ALERT_MIN_USD (20), LOW_BALANCE_DAYS (3)
"""
import calendar
import datetime as dt
import json
import os
import sys
import time

import requests

sys.stdout.reconfigure(line_buffering=True)  # in log ngay lap tuc tren GitHub

TT_API = "https://business-api.tiktok.com/open_api/v1.3"
LARK_API = os.getenv("LARK_DOMAIN", "https://open.larksuite.com").rstrip("/") + "/open-apis"
VN_TZ = dt.timezone(dt.timedelta(hours=7))

# ---- Bang chi tiet ----
F_BC_NAME = "Tên BC"
F_BC_ID = "ID BC"
F_ADV_ID = "Advertiser ID"
F_ADV_NAME = "Tên Tk"
F_DATE = "Ngày"
F_SPEND = "Spend"
F_BALANCE = "Số dư"
F_BUDGET = "Budget còn lại"
F_CURRENCY = "Tiền tệ"
F_UPDATED = "Cập nhật lúc"
F_SPEND_USD = "Spend (USD)"
F_RATE = "Tỷ giá (1 USD)"
F_MONTH = "Tháng"
F_WEEK = "Tuần"
F_COUNTRY = "Quốc gia"
F_TZ = "Múi giờ"
F_OBJ = "Loại hình QC"
# Ten khac cua cot (khi ban doi ten cot trong Base, them ten moi vao day)
ALIASES = {
    F_SPEND: ["Spend (USD & VNĐ)", "Spend"],
    F_SPEND_USD: ["Spend trong ngày (USD)", "Spend (USD)"],
}

# ---- Bang Tong hop BC ----
T_SUMMARY = "Tổng hợp BC"
S_KEY = "Mã dòng"
S_FIELDS = [  # (ten cot, kieu: 1 van ban, 2 so, 5 ngay)
    (S_KEY, 1), ("Ngày", 5), ("Tên BC", 1), ("ID BC", 1), ("Tháng", 1),
    ("Số TK có chi tiêu", 2), ("Spend hôm qua (USD)", 2), ("Spend hôm kia (USD)", 2),
    ("% so với hôm kia", 2), ("Spend 7 ngày (USD)", 2), ("Spend 7 ngày trước (USD)", 2),
    ("% 7 ngày", 2), ("TB/ngày 7 ngày (USD)", 2), ("Spend tháng này (USD)", 2),
    ("Dự kiến cả tháng (USD)", 2), ("Ngân sách tháng (USD)", 2), ("% ngân sách đã dùng", 2),
    ("% dự kiến/ngân sách", 2), ("Ngân sách còn lại (USD)", 2), ("Số dư TK (USD)", 2),
    ("Xu hướng", 1), ("Cảnh báo", 1), ("Loại hình chính", 1), ("Quốc gia chính", 1),
    ("Cập nhật lúc", 5),
]
# ---- Bang Bao cao BC (quan ly tao, script dien them cot so lieu) ----
T_REPORT = "Báo cáo BC"
R_FIELDS = [
    ("Mức cảnh báo", 1), ("Cảnh báo", 1), ("Xu hướng", 1),
    ("Spend hôm qua (USD)", 2), ("% so với hôm kia", 2), ("Spend 7 ngày (USD)", 2), ("Tăng trưởng (tuần)", 2),
    ("TB/ngày 7 ngày (USD)", 2), ("Spend tháng này (USD)", 2), ("Dự kiến cả tháng (USD)", 2),
    ("Ngân sách tháng (USD)", 2), ("% ngân sách đã dùng", 2), ("Spend quý này (USD)", 2), ("Dự kiến hết Quý", 2),
    ("TK chi tiêu hôm qua", 2), ("TK chi tiêu tháng này", 2), ("Tổng TK trong BC", 2), ("Số dư TK (USD)", 2),
    ("Cập nhật lúc", 5),
]
# ---- Bang Tong theo ngay ----
T_DAILY = "Tổng theo ngày"
D_FIELDS = [("Mã dòng", 1), ("Ngày", 5), ("Tuần", 1), ("Tháng", 1), ("Tên BC", 1), ("ID BC", 1),
            ("Spend (USD)", 2), ("Số TK chi tiêu", 2), ("Cập nhật lúc", 5)]
# ---- Bang Ngan sach BC ----
T_BUDGET = "Ngân sách BC"
B_FIELDS = [("Mã dòng", 1), ("Tháng", 1), ("Tên BC", 1), ("ID BC", 1), ("Ngân sách tháng (USD)", 2), ("Ghi chú", 1)]

OBJECTIVES = {
    "PRODUCT_SALES": "Bán hàng (Product Sales)", "SHOP_PURCHASES": "Mua hàng TikTok Shop",
    "WEB_CONVERSIONS": "Chuyển đổi web", "CONVERSIONS": "Chuyển đổi", "TRAFFIC": "Lưu lượng truy cập",
    "VIDEO_VIEWS": "Lượt xem video", "REACH": "Phạm vi tiếp cận", "ENGAGEMENT": "Tương tác",
    "LEAD_GENERATION": "Thu thập khách hàng tiềm năng", "APP_PROMOTION": "Quảng bá ứng dụng",
    "APP_INSTALL": "Cài đặt ứng dụng", "RF_REACH": "Reach & Frequency", "CATALOG_SALES": "Bán hàng theo danh mục",
    "LIVE_SHOPPING": "Bán hàng LIVE", "GMV_MAX": "GMV Max", "FOLLOWERS": "Tăng người theo dõi",
}


DBG = {}
WRITABLE = {1, 2, 3, 4, 5, 7, 11, 13, 15, 17, 22, 23}  # bo qua cot cong thuc / lookup


def env(name, required=True, default=None):
    val = os.getenv(name, default)
    if required and not val:
        sys.exit(f"[LOI] Thieu bien moi truong {name}")
    return val


# ----------------------------- TikTok -----------------------------
def tt_get(path, params, token):
    q = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in params.items()}
    last = {}
    for attempt in range(6):
        try:
            r = requests.get(TT_API + path, params=q, headers={"Access-Token": token}, timeout=60)
            j = r.json()
        except Exception as e:
            j = {"code": -1, "message": str(e)[:300]}
        if j.get("code") == 0:
            return j.get("data") or {}
        last = j
        if j.get("code") in (40001, 40002, 40105):
            break  # loi tham so / quyen: khong can thu lai
        time.sleep(3 * (attempt + 1))  # 40100 = vuot gioi han 10 lan goi/giay -> cho roi thu lai
    raise RuntimeError(f"TikTok {path} loi: {last.get('code')} - {last.get('message')}")


def tt_pages(path, params, token, list_key="list", page_size=50):
    page = 1
    while True:
        data = tt_get(path, {**params, "page": page, "page_size": page_size}, token)
        for item in data.get(list_key) or []:
            yield item
        total = (data.get("page_info") or {}).get("total_page", 1) or 1
        if page >= total:
            break
        page += 1


def get_bcs(token):
    only = [x.strip() for x in (os.getenv("BC_IDS") or "").split(",") if x.strip()]
    bcs = []
    for item in tt_pages("/bc/get/", {}, token):
        info = item.get("bc_info") or item
        bc_id = str(info.get("bc_id", ""))
        if bc_id and (not only or bc_id in only):
            bcs.append({"bc_id": bc_id, "name": info.get("name", "")})
    return bcs


def _parse_report(items):
    rows = []
    for item in items:
        d = item.get("dimensions") or {}
        m = item.get("metrics") or {}
        rows.append({
            "advertiser_id": str(d.get("advertiser_id", "")),
            "date": str(d.get("stat_time_day", ""))[:10],
            "spend": float(m.get("spend") or 0),
            "currency": str(m.get("currency") or ""),
            "name": str(m.get("advertiser_name") or ""),
        })
    return rows


class BCReport:
    """Bao cao cap BC: khong can duoc chi dinh vao tung tai khoan. Nho bo metrics dung duoc."""

    def __init__(self, bc_id, token):
        self.bc_id, self.token = bc_id, token
        self.metric_sets = [["spend", "currency", "advertiser_name"], ["spend", "currency"], ["spend"]]

    def range(self, start, end):
        """Tong spend tung tai khoan trong khoang [start, end]."""
        for mi, metrics in enumerate(self.metric_sets):
            params = {"report_type": "BC", "bc_id": self.bc_id, "dimensions": ["advertiser_id"],
                      "metrics": metrics, "start_date": str(start), "end_date": str(end)}
            try:
                rows = _parse_report(tt_pages("/report/integrated/get/", params, self.token, page_size=1000))
                self.metric_sets = self.metric_sets[mi:]
                return rows
            except RuntimeError:
                if mi == len(self.metric_sets) - 1:
                    raise
        return []


def _basic_report(adv_ids, start, end, token):
    params = {"report_type": "BASIC", "data_level": "AUCTION_ADVERTISER",
              "dimensions": ["advertiser_id", "stat_time_day"], "metrics": ["spend"],
              "start_date": start, "end_date": end}
    if len(adv_ids) == 1:
        params["advertiser_id"] = adv_ids[0]
    else:
        params["advertiser_ids"] = adv_ids
    return _parse_report(tt_pages("/report/integrated/get/", params, token, page_size=1000))


def get_daily_spend(rep, adv_ids, start, end, token):
    """Spend tung tai khoan theo tung ngay."""
    try:
        rows = []
        day = dt.date.fromisoformat(start)
        while day <= dt.date.fromisoformat(end):
            for r in rep.range(day, day):
                r["date"] = str(day)
                rows.append(r)
            day += dt.timedelta(days=1)
        return rows
    except RuntimeError as e:
        print(f"   (bao cao BC: {e}) -> thu bao cao tung tai khoan")
    rows = []
    for i in range(0, len(adv_ids), 5):
        group = adv_ids[i:i + 5]
        try:
            rows += _basic_report(group, start, end, token)
        except RuntimeError:
            for a in group:
                try:
                    rows += _basic_report([a], start, end, token)
                except RuntimeError:
                    pass
    return rows


def get_balances(bc_id, token):
    out = {}
    params = {"bc_id": bc_id, "fields": ["budget_remaining"]}
    for a in tt_pages("/advertiser/balance/get/", params, token, list_key="advertiser_account_list"):
        out[str(a.get("advertiser_id", ""))] = {
            "name": a.get("advertiser_name", ""),
            "balance": a.get("account_balance", a.get("balance")),
            "budget": a.get("budget_remaining"),
            "currency": a.get("currency", ""),
        }
    return out


def get_attributes(bc_id, adv_ids, token):
    """Quoc gia / tien te cua tung tai khoan (API cap BC tra ve dang gop -> hoi tung TK)."""
    out = {}
    for adv in adv_ids:
        try:
            data = tt_get("/bc/advertiser/attribute/", {"bc_id": bc_id, "advertiser_ids": [adv]}, token)
        except RuntimeError as e:
            if not DBG.get("attr_err"):
                DBG["attr_err"] = 1
                print(f"   [CHAN DOAN] thuoc tinh TK loi: {e}")
            continue
        regions = data.get("region_codes") or []
        curs = data.get("currencies") or []
        out[adv] = {"country": ",".join(regions), "tz": "", "currency": curs[0] if len(curs) == 1 else ""}
    return out


def get_objectives_bc(bc_id, day, token):
    """Thu lay spend theo muc tieu chien dich o cap BC (khong can chi dinh vao TK)."""
    for dims in (["advertiser_id", "objective_type"], ["objective_type", "advertiser_id"]):
        params = {"report_type": "BC", "bc_id": bc_id, "dimensions": dims, "metrics": ["spend"],
                  "start_date": day, "end_date": day}
        try:
            out = {}
            for it in tt_pages("/report/integrated/get/", params, token, page_size=1000):
                d, m = it.get("dimensions") or {}, it.get("metrics") or {}
                sp = float(m.get("spend") or 0)
                if sp > 0:
                    adv = str(d.get("advertiser_id", ""))
                    k = str(d.get("objective_type") or "KHAC")
                    out.setdefault(adv, {})[k] = out.get(adv, {}).get(k, 0) + sp
            return out
        except RuntimeError as e:
            last = e
    if not DBG.get("obj_bc"):
        DBG["obj_bc"] = 1
        print(f"   [CHAN DOAN] loai hinh cap BC khong ho tro: {last}")
    return None


def get_objectives(adv_id, day, token):
    """Spend theo muc tieu chien dich cua 1 tai khoan trong 1 ngay (can token co quyen vao TK)."""
    params = {"report_type": "BASIC", "data_level": "AUCTION_CAMPAIGN", "advertiser_id": adv_id,
              "dimensions": ["campaign_id"], "metrics": ["spend", "objective_type"],
              "start_date": day, "end_date": day}
    agg = {}
    for it in tt_pages("/report/integrated/get/", params, token, page_size=1000):
        if not DBG.get("obj_ok"):
            DBG["obj_ok"] = 1
            print("   [CHAN DOAN] loai hinh QC mau:", json.dumps(it, ensure_ascii=False)[:400])
        m = it.get("metrics") or {}
        sp = float(m.get("spend") or 0)
        if sp > 0:
            k = str(m.get("objective_type") or "KHAC")
            agg[k] = agg.get(k, 0) + sp
    return agg


def fmt_objectives(agg):
    tot = sum(agg.values())
    if not tot:
        return ""
    parts = sorted(agg.items(), key=lambda x: -x[1])
    return " | ".join(f"{OBJECTIVES.get(k, k)} {v / tot:.0%}" for k, v in parts)


# ----------------------------- Ty gia & nhan -----------------------------
def get_fx():
    """So don vi tien te / 1 USD. FX_RATES (vd: VND=26000) uu tien hon ty gia online."""
    rates = {"USD": 1.0}
    try:
        j = requests.get("https://open.er-api.com/v6/latest/USD", timeout=30).json()
        if j.get("result") == "success":
            rates.update({k: float(v) for k, v in j["rates"].items()})
            print(f"Ty gia ngay {j.get('time_last_update_utc', '')[:16]}: 1 USD = {rates.get('VND', 0):,.0f} VND")
    except Exception as e:
        print(f"[CANH BAO] Khong lay duoc ty gia online: {e}")
    for part in (os.getenv("FX_RATES") or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            rates[k.strip().upper()] = float(v)
    return rates


def month_label(ds):
    return str(ds)[:7]


def week_label(ds):
    d = dt.date.fromisoformat(str(ds)[:10])
    y, w, _ = d.isocalendar()
    mon = d - dt.timedelta(days=d.weekday())
    sun = mon + dt.timedelta(days=6)
    return f"{y}-W{w:02d} ({mon:%d/%m}-{sun:%d/%m})"


def pct(a, b):
    return round((a - b) / b * 100, 1) if b else None


# ----------------------------- Lark Base -----------------------------
def date_to_ms(s):
    y, m, d = (int(x) for x in str(s)[:10].split("-"))
    return int(dt.datetime(y, m, d, tzinfo=VN_TZ).timestamp() * 1000)


def ms_to_date(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, VN_TZ).strftime("%Y-%m-%d")


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in v)
    if isinstance(v, dict):
        return str(v.get("text") or v.get("value") or "")
    return str(v)


class Lark:
    def __init__(self):
        r = requests.post(LARK_API + "/auth/v3/tenant_access_token/internal", json={
            "app_id": env("LARK_APP_ID"), "app_secret": env("LARK_APP_SECRET")}, timeout=30)
        j = r.json()
        if j.get("code") != 0:
            sys.exit(f"[LOI] Lark token: {j.get('code')} - {j.get('msg')}")
        self.h = {"Authorization": "Bearer " + j["tenant_access_token"],
                  "Content-Type": "application/json; charset=utf-8"}
        self.app = f"{LARK_API}/bitable/v1/apps/{env('LARK_BASE_TOKEN')}"

    def call(self, method, url, body=None, params=None):
        for attempt in range(5):
            try:
                j = requests.request(method, url, headers=self.h, json=body, params=params, timeout=60).json()
            except Exception as e:
                j = {"code": -1, "msg": str(e)[:200]}
            if j.get("code") == 0:
                return j.get("data") or {}
            if attempt == 4 or j.get("code") in (91402, 91403, 1254045, 99991672, 99991663):
                raise RuntimeError(f"Lark loi: {j.get('code')} - {j.get('msg')}")
            time.sleep(2 * (attempt + 1))

    def find_table(self, name):
        token = None
        while True:
            p = {"page_size": 100}
            if token:
                p["page_token"] = token
            data = self.call("GET", self.app + "/tables", params=p)
            for t in data.get("items") or []:
                if t.get("name") == name:
                    return t["table_id"]
            if not data.get("has_more"):
                return None
            token = data.get("page_token")

    def ensure_table(self, name, fields):
        """Tim bang theo ten, chua co thi tao moi. Tra ve table_id."""
        token = None
        while True:
            p = {"page_size": 100}
            if token:
                p["page_token"] = token
            data = self.call("GET", self.app + "/tables", params=p)
            for t in data.get("items") or []:
                if t.get("name") == name:
                    return t["table_id"]
            if not data.get("has_more"):
                break
            token = data.get("page_token")
        body = {"table": {"name": name, "default_view_name": "Tất cả",
                          "fields": [{"field_name": n, "type": t} for n, t in fields]}}
        data = self.call("POST", self.app + "/tables", body)
        print(f"Da tu tao bang '{name}' trong Base.")
        return data.get("table_id")


class Table:
    def __init__(self, lark, table_id, want_fields=()):
        self.l = lark
        self.base = f"{lark.app}/tables/{table_id}"
        self.types = self._field_types()
        self.alias = {}
        for logical, names in ALIASES.items():
            for n in names:
                if n in self.types:
                    self.alias[logical] = n
                    break
        for name, typ in want_fields:  # tu tao cot con thieu
            if name in self.types or name in self.alias:
                continue
            try:
                self.l.call("POST", self.base + "/fields", {"field_name": name, "type": typ})
                self.types[name] = typ
                print(f"Da tu tao cot '{name}'.")
            except RuntimeError as e:
                print(f"[GOI Y] Khong tao duoc cot '{name}': {e}")

    def _field_types(self):
        types, token = {}, None
        while True:
            p = {"page_size": 100}
            if token:
                p["page_token"] = token
            data = self.l.call("GET", self.base + "/fields", params=p)
            for f in data.get("items") or []:
                types[f["field_name"]] = f["type"]
            if not data.get("has_more"):
                break
            token = data.get("page_token")
        return types

    def convert(self, fields):
        out = {}
        for name, val in fields.items():
            name = self.alias.get(name, name)
            t = self.types.get(name)
            if t is None or val is None or val == "" or t not in WRITABLE:
                continue
            if t == 5:
                out[name] = val if isinstance(val, int) else date_to_ms(str(val))
            elif t == 2:
                out[name] = float(val)
            else:
                out[name] = str(val)
        return out

    def scan(self, body=None):
        """Doc ban ghi (co the loc). Tra ve list (record_id, fields)."""
        out, token = [], None
        while True:
            p = {"page_size": 500}
            if token:
                p["page_token"] = token
            data = self.l.call("POST", self.base + "/records/search", body or {}, p)
            out += [(it["record_id"], it.get("fields") or {}) for it in data.get("items") or []]
            if not data.get("has_more"):
                break
            token = data.get("page_token")
        return out

    def batch(self, action, records):
        for i in range(0, len(records), 500):
            self.l.call("POST", f"{self.base}/records/{action}", {"records": records[i:i + 500]})

    def upsert(self, rows, keyfn, existing):
        """existing: {key: record_id}. Them moi hoac cap nhat."""
        create, update = [], []
        for f in rows:
            rid = None
            for k in keyfn(f):
                rid = rid or existing.get(k)
            fields = self.convert(f)
            (update.append({"record_id": rid, "fields": fields}) if rid else create.append({"fields": fields}))
        self.batch("batch_create", create)
        self.batch("batch_update", update)
        return len(create), len(update)


def date_filter(field, dates, is_date):
    if is_date:
        conds = [{"field_name": field, "operator": "is", "value": ["ExactDate", str(date_to_ms(d))]} for d in dates]
    else:
        conds = [{"field_name": field, "operator": "is", "value": [str(d)]} for d in dates]
    return {"conjunction": "or", "conditions": conds}


# ----------------------------- Xu ly 1 BC -----------------------------
def process_bc(bc, start, end, tokens, with_balance):
    """1 BC co the duoc cap cho nhieu email: thu lan luot tung token."""
    res = {"bal": {}, "daily": [], "w7": [], "p7": [], "mtd": [], "qtd": [], "attr": {}, "obj": {}}
    if with_balance:
        last = None
        for tk in tokens:
            try:
                res["bal"] = get_balances(bc["bc_id"], tk)
                break
            except RuntimeError as e:
                last = e
        else:
            print(f"   [{bc['name']}] khong lay duoc so du ({last}) -> van lay spend")
    end_d = dt.date.fromisoformat(end)
    for i, tk in enumerate(tokens):
        rep = BCReport(bc["bc_id"], tk)
        daily = get_daily_spend(rep, list(res["bal"]), start, end, tk)
        if not daily and i < len(tokens) - 1:
            continue
        res["daily"], res["token"] = daily, tk
        try:  # so lieu tong hop cho quan ly
            res["w7"] = rep.range(end_d - dt.timedelta(days=6), end_d)
            res["p7"] = rep.range(end_d - dt.timedelta(days=13), end_d - dt.timedelta(days=7))
            res["mtd"] = rep.range(end_d.replace(day=1), end_d)
            q_start = dt.date(end_d.year, 3 * ((end_d.month - 1) // 3) + 1, 1)
            res["qtd"] = res["mtd"] if q_start.month == end_d.month else rep.range(q_start, end_d)
        except RuntimeError as e:
            print(f"   [{bc['name']}] khong lay duoc so tong hop: {e}")
        break
    active = sorted({r["advertiser_id"] for r in res["daily"] + res["mtd"] if r["spend"] > 0})
    tk = res.get("token") or tokens[0]
    if active:
        res["attr"] = get_attributes(bc["bc_id"], active, tk)
    if os.getenv("FETCH_OBJECTIVE", "1") == "1":
        by_bc = get_objectives_bc(bc["bc_id"], end, tk)
        if by_bc is not None:
            res["obj"] = by_bc
        for adv in sorted({r["advertiser_id"] for r in res["daily"] if r["spend"] > 0 and r["date"] == end}):
            if adv in res["obj"]:
                continue
            for t in tokens:
                try:
                    res["obj"][adv] = get_objectives(adv, end, t)
                    break
                except RuntimeError as e:
                    if not DBG.get("obj"):
                        DBG["obj"] = 1
                        print(f"   [CHAN DOAN] loai hinh QC loi: {e}")
                    continue
    return res


# ----------------------------- Main -----------------------------
def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    dry = "--dry-run" in sys.argv
    env("TT_ACCESS_TOKEN")
    tokens = [os.getenv("TT_ACCESS_TOKEN")] + [os.getenv(f"TT_ACCESS_TOKEN_{i}") for i in range(2, 11)]
    tokens = [t.strip() for t in tokens if t and t.strip()]
    days = max(1, min(30, int(os.getenv("DAYS_BACK", "2"))))
    workers = max(1, int(os.getenv("PARALLEL", "3")))
    with_balance = os.getenv("FETCH_BALANCE", "1") == "1"
    alert_pct = float(os.getenv("ALERT_CHANGE_PCT", "30"))
    alert_min = float(os.getenv("ALERT_MIN_USD", "20"))
    low_days = float(os.getenv("LOW_BALANCE_DAYS", "3"))
    today = dt.datetime.now(VN_TZ).date()
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    dates = [str(start + dt.timedelta(days=i)) for i in range(days)]
    prev = str(end - dt.timedelta(days=1))
    now_ms = int(dt.datetime.now(VN_TZ).timestamp() * 1000)
    t0 = time.time()

    bcs, bc_seen = [], set()
    for i, tk in enumerate(tokens, 1):
        try:
            found = get_bcs(tk)
        except RuntimeError as e:
            print(f"[TOKEN {i}] loi: {e} | kiem tra: dai {len(tk)} ky tu, bat dau '{tk[:4]}', ket thuc '{tk[-4:]}'")
            continue
        new = [b for b in found if b["bc_id"] not in bc_seen]
        for b in found:
            if b["bc_id"] in bc_seen:
                next(x for x in bcs if x["bc_id"] == b["bc_id"])["tokens"].append(tk)
        for b in new:
            b["tokens"] = [tk]
            bc_seen.add(b["bc_id"])
        bcs += new
        print(f"[TOKEN {i}] thay {len(found)} BC ({len(new)} BC moi)")
    print(f"Dong bo {start} -> {end} | {len(bcs)} BC | {len(tokens)} token | song song {workers} BC")

    fx = get_fx()
    # lay spend hom kia de so sanh (neu DAYS_BACK = 1)
    fetch_start = min(str(start), prev)

    def usd(amount, cur):
        cur = (cur or "").upper()
        return amount / fx[cur] if cur in fx else None

    detail, summary, no_cur = [], [], set()
    acc_bcs, mtd_by_bc = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_bc, bc, fetch_start, str(end), bc["tokens"], with_balance): bc for bc in bcs}
        for fu in as_completed(futs):
            bc = futs[fu]
            try:
                res = fu.result()
            except Exception as e:
                print(f"[BO QUA] BC {bc['bc_id']} ({bc['name']}): {e}")
                continue
            bal, attr, objs = res["bal"], res["attr"], res["obj"]
            curmap = {}
            for src in (res["daily"], res["w7"], res["p7"], res["mtd"]):
                for r in src:
                    if r.get("currency"):
                        curmap[r["advertiser_id"]] = r["currency"].upper()
            for adv, a in attr.items():
                if a.get("currency"):
                    curmap.setdefault(adv, a["currency"].upper())
            for adv, b in bal.items():
                if b.get("currency"):
                    curmap[adv] = b["currency"].upper()

            def tot_usd(rows):
                s = 0.0
                for r in rows:
                    v = usd(r["spend"], curmap.get(r["advertiser_id"]))
                    if v is None and r["spend"] > 0:
                        no_cur.add(r["advertiser_id"])
                    s += v or 0
                return s

            # --- bang chi tiet ---
            day_usd = {d: 0.0 for d in (str(end), prev)}
            n_active = 0
            seen = set()
            for r in res["daily"]:
                if r["spend"] <= 0:
                    continue
                key = (r["advertiser_id"], r["date"])
                if key in seen:
                    continue
                seen.add(key)
                adv = r["advertiser_id"]
                b, a = bal.get(adv, {}), attr.get(adv, {})
                cur = curmap.get(adv, "")
                v = usd(r["spend"], cur)
                if r["date"] in day_usd:
                    day_usd[r["date"]] += v or 0
                if r["date"] == str(end):
                    n_active += 1
                if r["date"] not in dates:
                    continue
                f = {
                    F_BC_NAME: bc["name"], F_BC_ID: bc["bc_id"], F_ADV_ID: adv,
                    F_ADV_NAME: b.get("name") or r.get("name") or adv, F_DATE: r["date"],
                    F_SPEND: round(r["spend"], 2), F_CURRENCY: cur, F_UPDATED: now_ms,
                    F_MONTH: month_label(r["date"]), F_WEEK: week_label(r["date"]),
                    F_COUNTRY: a.get("country", ""), F_TZ: a.get("tz", ""),
                }
                if v is not None:
                    f[F_RATE] = fx[cur]
                    f[F_SPEND_USD] = round(v, 2)
                else:
                    no_cur.add(adv)
                if r["date"] == str(end):
                    f[F_BALANCE] = b.get("balance")
                    f[F_BUDGET] = b.get("budget")
                    if adv in objs:
                        f[F_OBJ] = fmt_objectives(objs[adv])
                detail.append(f)

            # --- tong hop BC ---
            obj_tot = {}
            for agg in objs.values():
                for k, v in agg.items():
                    obj_tot[k] = obj_tot.get(k, 0) + v
            cty_tot = {}
            for r in res["mtd"]:
                codes = [x for x in (attr.get(r["advertiser_id"], {}).get("country") or "").split(",") if x]
                for c in codes:  # TK nhieu quoc gia -> chia deu spend
                    cty_tot[c] = cty_tot.get(c, 0) + (usd(r["spend"], curmap.get(r["advertiser_id"])) or 0) / len(codes)
            bal_usd = sum(usd(float(b["balance"]), b.get("currency")) or 0
                          for b in bal.values() if b.get("balance") not in (None, ""))
            w7, p7, mtd, qtd = tot_usd(res["w7"]), tot_usd(res["p7"]), tot_usd(res["mtd"]), tot_usd(res["qtd"])
            dim = calendar.monthrange(end.year, end.month)[1]
            avg7 = w7 / 7
            forecast = mtd + avg7 * (dim - end.day)
            q_end_m = 3 * ((end.month - 1) // 3) + 3
            q_end = dt.date(end.year, q_end_m, calendar.monthrange(end.year, q_end_m)[1])
            forecast_q = qtd + avg7 * (q_end - end).days
            for r in res["mtd"]:
                if r["spend"] > 0:
                    acc_bcs.setdefault(r["advertiser_id"], {})[bc["bc_id"]] = usd(r["spend"], curmap.get(r["advertiser_id"])) or 0
            mtd_by_bc[bc["bc_id"]] = mtd
            summary.append({
                "bc": bc, "y": day_usd[str(end)], "p": day_usd[prev], "w7": w7, "p7": p7, "avg7": avg7,
                "mtd": mtd, "fc": forecast, "n": n_active, "bal": bal_usd if bal else None,
                "qtd": qtd, "fq": forecast_q, "n_mtd": sum(1 for r in res["mtd"] if r["spend"] > 0),
                "n_all": len(bal) or None,
                "obj": fmt_objectives(obj_tot), "cty": ", ".join(k for k, _ in sorted(cty_tot.items(), key=lambda x: -x[1])[:3]),
            })
            print(f"BC {bc['name']}: {n_active} TK chi tieu | hom qua {day_usd[str(end)]:,.2f} USD | thang nay {mtd:,.2f} USD")

    if no_cur:
        print(f"[CANH BAO] {len(no_cur)} TK chua biet tien te -> chua quy doi USD: {', '.join(sorted(no_cur)[:20])}")
    print(f"Lay du lieu TikTok xong sau {time.time() - t0:.0f}s: {len(detail)} dong chi tiet, {len(summary)} BC.")

    lark_ready = all(os.getenv(k) for k in ("LARK_APP_ID", "LARK_APP_SECRET", "LARK_BASE_TOKEN", "LARK_TABLE_ID"))
    if dry or not lark_ready:
        for s in sorted(summary, key=lambda x: -x["y"])[:30]:
            print(f"{s['bc']['name']}: hom qua {s['y']:,.2f} | hom kia {s['p']:,.2f} | 7 ngay {s['w7']:,.2f} | thang {s['mtd']:,.2f} | du kien {s['fc']:,.2f}")
        print("(dry-run) khong ghi vao Base.")
        return

    lark = Lark()
    # 1) Bang chi tiet
    det = Table(lark, env("LARK_TABLE_ID"), [
        (F_SPEND_USD, 2), (F_RATE, 2), (F_MONTH, 1), (F_WEEK, 1), (F_ADV_ID, 1),
        (F_COUNTRY, 1), (F_TZ, 1), (F_OBJ, 1)])
    names = [n for n in (F_BC_ID, F_ADV_ID, F_ADV_NAME, F_DATE) if n in det.types]
    try:
        recs = det.scan({"field_names": names, "filter": date_filter(F_DATE, dates, det.types.get(F_DATE) == 5)})
    except RuntimeError as e:
        print(f"   (loc theo ngay loi: {e} -> doc ca bang)")
        recs = det.scan({"field_names": names})
    existing = {}
    for rid, fr in recs:
        d = fr.get(F_DATE)
        d = ms_to_date(d) if isinstance(d, (int, float)) else cell_text(d)[:10]
        bcid = cell_text(fr.get(F_BC_ID))
        existing[f"{bcid}|{cell_text(fr.get(F_ADV_ID))}|{d}"] = rid
        existing.setdefault(f"{bcid}|{cell_text(fr.get(F_ADV_NAME))}|{d}", rid)
    c, u = det.upsert(detail, lambda f: (f"{f[F_BC_ID]}|{f[F_ADV_ID]}|{f[F_DATE]}",
                                         f"{f[F_BC_ID]}|{f[F_ADV_NAME]}|{f[F_DATE]}"), existing)
    print(f"Bang chi tiet: them moi {c}, cap nhat {u}.")

    # ---------- 2) Ngan sach: doc tu Bao cao BC (uu tien) hoac Tong hop BC ----------
    month = month_label(end)
    summ = Table(lark, lark.ensure_table(T_SUMMARY, S_FIELDS), S_FIELDS)
    ex_sum, budgets = {}, {}
    for rid, fr in summ.scan({"field_names": ["ID BC", "Ngân sách tháng (USD)"]}):
        bcid = cell_text(fr.get("ID BC"))
        if not bcid:
            continue
        ex_sum.setdefault(bcid, rid)
        v = fr.get("Ngân sách tháng (USD)")
        if v not in (None, "") and bcid not in budgets:
            budgets[bcid] = float(v)

    rep_tbl, rep_rows = None, {}
    rep_id = lark.find_table(T_REPORT)
    if rep_id:
        rep_tbl = Table(lark, rep_id, R_FIELDS)
        print("Cot trong Bao cao BC:", ", ".join(f"{k}({v})" for k, v in rep_tbl.types.items()))
        for rid, fr in rep_tbl.scan():
            bcid = cell_text(fr.get("ID BC")).strip()
            if not bcid:
                continue
            rep_rows.setdefault(bcid, rid)
            v = fr.get("Ngân sách tháng (USD)")
            if v not in (None, ""):
                budgets[bcid] = float(v)

    # ---------- 3) Tinh canh bao + ghi Tong hop BC ----------
    rows, rep_updates = [], []
    for s in summary:
        bcid = s["bc"]["bc_id"]
        budget = budgets.get(bcid)
        dchg, wchg = pct(s["y"], s["p"]), pct(s["w7"], s["p7"])
        red, orange = [], []
        if s["p"] >= alert_min and s["y"] == 0:
            red.append("⛔ Ngừng chi tiêu (hôm kia còn chạy)")
        elif dchg is not None and max(s["y"], s["p"]) >= alert_min:
            if dchg >= alert_pct:
                orange.append(f"🔺 Tăng {dchg:+.0f}% so với hôm kia")
            elif dchg <= -alert_pct:
                orange.append(f"🔻 Giảm {dchg:+.0f}% so với hôm kia")
        if wchg is not None and max(s["w7"], s["p7"]) >= alert_min * 7 and wchg <= -alert_pct:
            orange.append(f"📉 7 ngày giảm {wchg:+.0f}%")
        if budget:
            if s["mtd"] >= budget:
                red.append(f"🚨 Vượt ngân sách tháng {s['mtd'] - budget:,.0f} USD")
            elif s["mtd"] >= 0.9 * budget:
                orange.append("⚠️ Đã dùng ≥90% ngân sách")
            if s["fc"] > budget and s["mtd"] < budget:
                orange.append(f"⚠️ Dự kiến vượt ngân sách {s['fc'] - budget:,.0f} USD")
        if s["bal"] is not None and s["avg7"] > 0 and s["bal"] < s["avg7"] * low_days:
            (red if s["bal"] < s["avg7"] else orange).append(f"💰 Số dư chỉ đủ ~{s['bal'] / s['avg7']:.1f} ngày")
        if red:
            level = "🔴 Khẩn cấp"
        elif orange:
            level = "🟠 Cần chú ý"
        elif s["w7"] == 0 and s["mtd"] == 0:
            level = "⚪ Không chi tiêu"
        else:
            level = "🟢 Bình thường"
        alert_txt = " | ".join(red + orange) or "✅ Bình thường"
        trend = "Ổn định"
        if wchg is not None:
            trend = "📈 Tăng" if wchg >= 10 else ("📉 Giảm" if wchg <= -10 else "Ổn định")
        elif s["w7"] > 0:
            trend = "🆕 Mới chi tiêu"
        elif s["w7"] == 0:
            trend = "Không chi tiêu"
        s.update(level=level, alert=alert_txt, trend=trend, dchg=dchg, wchg=wchg, budget=budget)
        rows.append({
            S_KEY: bcid, "Ngày": str(end), "Tên BC": s["bc"]["name"], "ID BC": bcid, "Tháng": month,
            "Số TK có chi tiêu": s["n"], "Spend hôm qua (USD)": round(s["y"], 2),
            "Spend hôm kia (USD)": round(s["p"], 2), "% so với hôm kia": dchg,
            "Spend 7 ngày (USD)": round(s["w7"], 2), "Spend 7 ngày trước (USD)": round(s["p7"], 2),
            "% 7 ngày": wchg, "TB/ngày 7 ngày (USD)": round(s["avg7"], 2),
            "Spend tháng này (USD)": round(s["mtd"], 2), "Dự kiến cả tháng (USD)": round(s["fc"], 2),
            "% ngân sách đã dùng": round(s["mtd"] / budget * 100, 1) if budget else None,
            "% dự kiến/ngân sách": round(s["fc"] / budget * 100, 1) if budget else None,
            "Ngân sách còn lại (USD)": round(budget - s["mtd"], 2) if budget else None,
            "Số dư TK (USD)": round(s["bal"], 2) if s["bal"] is not None else None,
            "Xu hướng": trend, "Cảnh báo": alert_txt,
            "Loại hình chính": s["obj"], "Quốc gia chính": s["cty"], "Cập nhật lúc": now_ms,
        })
        if bcid in rep_rows:
            rep_updates.append({"record_id": rep_rows[bcid], "fields": rep_tbl.convert({
                "Mức cảnh báo": level, "Cảnh báo": alert_txt, "Xu hướng": trend,
                "Spend hôm qua (USD)": round(s["y"], 2), "% so với hôm kia": dchg,
                "Spend 7 ngày (USD)": round(s["w7"], 2), "Tăng trưởng (tuần)": wchg,
                "Spend tháng này (USD)": round(s["mtd"], 2), "Dự kiến cả tháng (USD)": round(s["fc"], 2),
                "Spend quý này (USD)": round(s["qtd"], 2), "Dự kiến hết Quý": round(s["fq"], 2),
                "TB/ngày 7 ngày (USD)": round(s["avg7"], 2),
                "TK chi tiêu hôm qua": s["n"], "TK chi tiêu tháng này": s["n_mtd"], "Tổng TK trong BC": s["n_all"],
                "% ngân sách đã dùng": round(s["mtd"] / budget * 100, 1) if budget else None,
                "Số dư TK (USD)": round(s["bal"], 2) if s["bal"] is not None else None,
                "Cập nhật lúc": now_ms})})
    c2, u2 = summ.upsert(rows, lambda f: (f["ID BC"],), ex_sum)
    print(f"Bang Tong hop BC: them moi {c2}, cap nhat {u2}.")
    if rep_tbl:
        rep_tbl.batch("batch_update", rep_updates)
        missing = [s for s in summary if s["bc"]["bc_id"] not in rep_rows and s["mtd"] > 0]
        print(f"Bang Bao cao BC: cap nhat {len(rep_updates)}/{len(rep_rows)} BC.")
        if missing:
            print("   BC co chi tieu thang nay nhung CHUA co trong Bao cao BC: " +
                  "; ".join(f"{s['bc']['name']} ({s['bc']['bc_id']}) {s['mtd']:,.0f} USD" for s in missing))

    # ---------- 4) Bang Tong theo ngay (moi BC x moi ngay) ----------
    daily_tbl = Table(lark, lark.ensure_table(T_DAILY, D_FIELDS), D_FIELDS)
    agg = {}
    for f in detail:
        k = (f[F_BC_ID], f[F_DATE])
        a = agg.setdefault(k, {"Mã dòng": f"{f[F_BC_ID]}|{f[F_DATE]}", "Ngày": f[F_DATE], "Tuần": f[F_WEEK],
                               "Tháng": f[F_MONTH], "Tên BC": f[F_BC_NAME], "ID BC": f[F_BC_ID],
                               "Spend (USD)": 0.0, "Số TK chi tiêu": 0, "Cập nhật lúc": now_ms})
        a["Spend (USD)"] = round(a["Spend (USD)"] + (f.get(F_SPEND_USD) or 0), 2)
        a["Số TK chi tiêu"] += 1
    try:
        drecs = daily_tbl.scan({"field_names": ["Mã dòng"], "filter": date_filter("Ngày", dates, True)})
    except RuntimeError:
        drecs = daily_tbl.scan({"field_names": ["Mã dòng"]})
    dex = {cell_text(fr.get("Mã dòng")): rid for rid, fr in drecs}
    c3, u3 = daily_tbl.upsert(list(agg.values()), lambda f: (f["Mã dòng"],), dex)
    print(f"Bang Tong theo ngay: them moi {c3}, cap nhat {u3}.")

    # ---------- 5) Kiem tra sai sot ----------
    audit(det, summary, acc_bcs, mtd_by_bc, month, end)

    total_y = sum(s["y"] for s in summary)
    n_red = sum(1 for s in summary if s["level"].startswith("🔴"))
    n_or = sum(1 for s in summary if s["level"].startswith("🟠"))
    print(f"Xong sau {time.time() - t0:.0f}s. Tong spend {end}: {total_y:,.2f} USD | thang {month}: "
          f"{sum(s['mtd'] for s in summary):,.2f} USD | du kien ca thang: {sum(s['fc'] for s in summary):,.2f} USD"
          f" | canh bao: {n_red} khan cap, {n_or} can chu y")


def audit(det, summary, acc_bcs, mtd_by_bc, month, end):
    """Doi soat: dong rac, dong trung, TK nam o nhieu BC, tong thang trong Base vs TikTok."""
    print("===== KIEM TRA SAI SOT =====")
    usd_col = det.alias.get(F_SPEND_USD, F_SPEND_USD)
    names = [n for n in (F_BC_ID, F_ADV_ID, F_DATE, usd_col) if n in det.types]
    recs = det.scan({"field_names": names})
    empty_adv = zero = 0
    seen, dup, dup_usd = {}, 0, 0.0
    base_mtd = {}
    junk_ids = []
    for rid, fr in recs:
        adv = cell_text(fr.get(F_ADV_ID))
        v = fr.get(usd_col)
        v = float(v) if v not in (None, "") else 0.0
        d = fr.get(F_DATE)
        d = ms_to_date(d) if isinstance(d, (int, float)) else cell_text(d)[:10]
        bcid = cell_text(fr.get(F_BC_ID))
        if not adv:
            empty_adv += 1
            junk_ids.append(rid)
            continue
        if v <= 0:
            zero += 1
            junk_ids.append(rid)
            continue
        k = f"{bcid}|{adv}|{d}"
        if k in seen:
            dup += 1
            dup_usd += v
            junk_ids.append(rid)
            continue
        seen[k] = rid
        if d[:7] == month:
            base_mtd[bcid] = base_mtd.get(bcid, 0) + v
    print(f"Bang chi tiet: {len(recs)} dong | dong thieu Advertiser ID: {empty_adv} | dong spend = 0/trong: {zero} "
          f"| dong trung: {dup} ({dup_usd:,.2f} USD bi cong 2 lan)")
    multi = {a: b for a, b in acc_bcs.items() if len(b) > 1}
    if multi:
        extra = sum(sum(v.values()) - max(v.values()) for v in multi.values())
        print(f"TK nam o nhieu BC (bi tinh o ca 2 BC): {len(multi)} TK, ~{extra:,.2f} USD thang nay bi cong lap. VD: "
              + "; ".join(f"{a} -> {len(b)} BC" for a, b in list(multi.items())[:5]))
    else:
        print("TK nam o nhieu BC: khong co.")
    bad = []
    for bcid, tk in mtd_by_bc.items():
        bv = base_mtd.get(bcid, 0)
        if abs(bv - tk) > max(1, 0.01 * tk):
            name = next((s["bc"]["name"] for s in summary if s["bc"]["bc_id"] == bcid), bcid)
            bad.append(f"{name}: Base {bv:,.2f} vs TikTok {tk:,.2f}")
    print(f"Doi soat tong thang {month} (Base vs TikTok, den {end}): " +
          ("KHOP tat ca BC." if not bad else f"{len(bad)} BC lech -> " + " | ".join(bad[:15])))
    if os.getenv("CLEAN_JUNK") == "1" and junk_ids:
        for i in range(0, len(junk_ids), 500):
            det.l.call("POST", f"{det.base}/records/batch_delete", {"records": junk_ids[i:i + 500]})
        print(f"Da xoa {len(junk_ids)} dong rac/trung trong bang chi tiet.")
    elif junk_ids:
        print(f"=> Co {len(junk_ids)} dong rac/trung. Chay voi CLEAN_JUNK=1 de xoa.")

if __name__ == "__main__":
    main()
