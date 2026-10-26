#!/usr/bin/env python3
"""
Dong bo spending + so du/ngan sach cua tung Business Center (TikTok API for Business)
vao Lark Base "Cap Nhat BC - LLC 2026". Chay moi ngay 6h sang (gio VN) qua GitHub Actions.

  python tiktok_bc_spend_to_lark.py             # dong bo
  python tiktok_bc_spend_to_lark.py --dry-run   # chi in ket qua, khong ghi vao Base

Bien moi truong (GitHub Secrets):
  TT_ACCESS_TOKEN, LARK_APP_ID, LARK_APP_SECRET, LARK_BASE_TOKEN, LARK_TABLE_ID
  Tuy chon: LARK_DOMAIN (mac dinh https://open.larksuite.com), BC_IDS (bc1,bc2), DAYS_BACK (mac dinh 7)

Script tu doc kieu cot trong Base (Van ban / So / Ngay) va tu doi dinh dang.
Cot nao khong co trong Base se duoc bo qua (vd cot "Khach" do ban tu nhap, script khong dong vao).
"""
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

# Ten cot dung theo bang cua ban
F_BC_NAME = "Tên BC"
F_BC_ID = "ID BC"
F_ADV_ID = "Advertiser ID"   # nen them cot nay (Van ban) de chong trung chinh xac
F_ADV_NAME = "Tên Tk"
F_DATE = "Ngày"
F_SPEND = "Spend"
F_BALANCE = "Số dư"
F_BUDGET = "Budget còn lại"
F_CURRENCY = "Tiền tệ"
F_UPDATED = "Cập nhật lúc"


def env(name, required=True, default=None):
    val = os.getenv(name, default)
    if required and not val:
        sys.exit(f"[LOI] Thieu bien moi truong {name}")
    return val


# ----------------------------- TikTok -----------------------------
def tt_get(path, params, token):
    q = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in params.items()}
    last = {}
    for attempt in range(4):
        r = requests.get(TT_API + path, params=q, headers={"Access-Token": token}, timeout=60)
        try:
            j = r.json()
        except ValueError:
            j = {"code": r.status_code, "message": r.text[:300]}
        if j.get("code") == 0:
            return j.get("data") or {}
        last = j
        if j.get("code") in (40001, 40002, 40100, 40105):
            break  # loi tham so / quyen: khong can thu lai
        time.sleep(2 * (attempt + 1))
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
        })
    return rows


def _basic_report(adv_ids, start, end, token):
    params = {
        "report_type": "BASIC",
        "data_level": "AUCTION_ADVERTISER",
        "dimensions": ["advertiser_id", "stat_time_day"],
        "metrics": ["spend"],
        "start_date": start,
        "end_date": end,
    }
    if len(adv_ids) == 1:
        params["advertiser_id"] = adv_ids[0]
    else:
        params["advertiser_ids"] = adv_ids
    return _parse_report(tt_pages("/report/integrated/get/", params, token, page_size=1000))


def _bc_cost(bc_id, start, end, token):
    """Chi phi tung tai khoan qua API cap BC (khong can duoc chi dinh vao tung tai khoan).
    Thu nhieu dang tham so vi tai lieu TikTok khac nhau giua cac phien ban."""
    tries = [
        {"bc_id": bc_id, "filtering": {"start_date": start, "end_date": end}},
        {"bc_id": bc_id, "start_date": start, "end_date": end},
        {"bc_id": bc_id, "filtering": {"start_time": start + " 00:00:00", "end_time": end + " 23:59:59"}},
    ]
    last = None
    for params in tries:
        try:
            data = tt_get("/bc/account/cost/get/", {**params, "page": 1, "page_size": 50}, token)
        except RuntimeError as e:
            last = e
            continue
        items = data.get("list") or data.get("cost_list") or data.get("advertiser_cost_list") or []
        if items:
            print("   [cost/get] mau du lieu:", json.dumps(items[0], ensure_ascii=False)[:400])
        rows = []
        page, total = 1, (data.get("page_info") or {}).get("total_page", 1) or 1
        while True:
            for it in items:
                adv = str(it.get("advertiser_id") or it.get("account_id") or "")
                amt = None
                for k in ("cost", "amount", "total_cost", "spend", "cash_cost"):
                    if it.get(k) not in (None, ""):
                        amt = float(it.get(k))
                        break
                if adv and amt is not None:
                    d = str(it.get("date") or it.get("stat_time_day") or end)[:10]
                    rows.append({"advertiser_id": adv, "date": d, "spend": amt})
            if page >= total:
                break
            page += 1
            items = (tt_get("/bc/account/cost/get/", {**params, "page": page, "page_size": 50}, token).get("list") or [])
        return rows
    raise RuntimeError(f"cost/get khong dung duoc: {last}")


def get_spend(bc_id, adv_ids, start, end, token):
    """Lay spend tung tai khoan. Thu lan luot:
    1) Bao cao cap BC  2) API chi phi cap BC  3) Bao cao tung tai khoan (can duoc chi dinh)."""
    for dims in (["advertiser_id", "stat_time_day"], ["advertiser_id"]):
        try:
            params = {"report_type": "BC", "bc_id": bc_id, "dimensions": dims,
                      "metrics": ["spend"], "start_date": start, "end_date": end}
            rows = _parse_report(tt_pages("/report/integrated/get/", params, token, page_size=1000))
            for r in rows:
                if not r["date"]:
                    r["date"] = end
            print(f"   -> lay spend bang bao cao cap BC ({','.join(dims)})")
            return rows
        except RuntimeError as e:
            print(f"   (bao cao BC {dims}: {e})")
    try:
        rows = _bc_cost(bc_id, start, end, token)
        if rows:
            print("   -> lay spend bang API chi phi cap BC")
            return rows
        print("   (API chi phi cap BC tra ve rong)")
    except RuntimeError as e:
        print(f"   ({e})")
    rows, failed = [], []
    for i in range(0, len(adv_ids), 5):
        group = adv_ids[i:i + 5]
        try:
            rows += _basic_report(group, start, end, token)
        except RuntimeError:
            for a in group:
                try:
                    rows += _basic_report([a], start, end, token)
                except RuntimeError:
                    failed.append(a)
    if failed:
        print(f"   ({len(failed)}/{len(adv_ids)} tai khoan chua chi dinh cho user -> chi lay duoc so du)")
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


# ----------------------------- Lark Base -----------------------------
def date_to_ms(s):
    y, m, d = (int(x) for x in s[:10].split("-"))
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
        self.base = f"{LARK_API}/bitable/v1/apps/{env('LARK_BASE_TOKEN')}/tables/{env('LARK_TABLE_ID')}"
        self.types = self._field_types()

    def _call(self, method, url, body=None, params=None):
        for attempt in range(4):
            j = requests.request(method, url, headers=self.h, json=body, params=params, timeout=60).json()
            if j.get("code") == 0:
                return j.get("data") or {}
            if attempt == 3 or j.get("code") in (91403, 1254045, 99991672, 99991663):
                raise RuntimeError(f"Lark loi: {j.get('code')} - {j.get('msg')}")
            time.sleep(2 * (attempt + 1))

    def _field_types(self):
        types, token = {}, None
        while True:
            p = {"page_size": 100}
            if token:
                p["page_token"] = token
            data = self._call("GET", self.base + "/fields", params=p)
            for f in data.get("items") or []:
                types[f["field_name"]] = f["type"]  # 1 van ban, 2 so, 5 ngay
            if not data.get("has_more"):
                break
            token = data.get("page_token")
        print("Cot trong Base:", ", ".join(types))
        return types

    def convert(self, fields):
        out = {}
        for name, val in fields.items():
            t = self.types.get(name)
            if t is None or val is None or val == "":
                continue
            if t == 5:  # Ngay
                out[name] = val if isinstance(val, int) else date_to_ms(str(val))
            elif t == 2:  # So
                out[name] = float(val)
            else:  # Van ban va loai khac
                out[name] = str(val)
        return out

    def key_of(self, fields_raw):
        """Key chong trung tu ban ghi Base: ID BC | (Advertiser ID hoac Ten Tk) | Ngay."""
        acc_field = F_ADV_ID if F_ADV_ID in self.types else F_ADV_NAME
        bc = cell_text(fields_raw.get(F_BC_ID))
        acc = cell_text(fields_raw.get(acc_field))
        d = fields_raw.get(F_DATE)
        if isinstance(d, (int, float)):
            d = ms_to_date(d)
        else:
            d = cell_text(d)[:10]
        return f"{bc}|{acc}|{d}"

    def existing(self):
        keys, token = {}, None
        names = [n for n in (F_BC_ID, F_ADV_ID, F_ADV_NAME, F_DATE) if n in self.types]
        while True:
            p = {"page_size": 500}
            if token:
                p["page_token"] = token
            data = self._call("POST", self.base + "/records/search", {"field_names": names}, p)
            for it in data.get("items") or []:
                keys[self.key_of(it.get("fields") or {})] = it["record_id"]
            if not data.get("has_more"):
                break
            token = data.get("page_token")
        return keys

    def batch(self, action, records):
        for i in range(0, len(records), 500):
            self._call("POST", f"{self.base}/records/{action}", {"records": records[i:i + 500]})


# ----------------------------- Main -----------------------------
def main():
    dry = "--dry-run" in sys.argv
    token = env("TT_ACCESS_TOKEN")
    days = max(1, min(30, int(os.getenv("DAYS_BACK", "7"))))
    today = dt.datetime.now(VN_TZ).date()
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    now_ms = int(dt.datetime.now(VN_TZ).timestamp() * 1000)
    print(f"Dong bo tu {start} den {end}")

    rows = []
    for bc in get_bcs(token):
        try:
            bal = get_balances(bc["bc_id"], token)
            spend = get_spend(bc["bc_id"], list(bal), str(start), str(end), token)
        except RuntimeError as e:
            print(f"[BO QUA] BC {bc['bc_id']} ({bc['name']}): {e}")
            continue
        print(f"BC {bc['name']} ({bc['bc_id']}): {len(spend)} dong spend, {len(bal)} tai khoan")
        for r in spend:
            b = bal.get(r["advertiser_id"], {})
            f = {
                F_BC_NAME: bc["name"],
                F_BC_ID: bc["bc_id"],
                F_ADV_ID: r["advertiser_id"],
                F_ADV_NAME: b.get("name") or r["advertiser_id"],
                F_DATE: r["date"],
                F_SPEND: round(r["spend"], 2),
                F_CURRENCY: b.get("currency", ""),
                F_UPDATED: now_ms,
            }
            if r["date"] == str(end):  # so du/budget hien tai -> gan vao dong ngay moi nhat
                f[F_BALANCE] = b.get("balance")
                f[F_BUDGET] = b.get("budget")
            rows.append(f)
        have = {r["advertiser_id"] for r in spend if r["date"] == str(end)}
        for adv, b in bal.items():  # tai khoan chua co spend -> van ghi so du / budget
            if adv in have:
                continue
            rows.append({
                F_BC_NAME: bc["name"], F_BC_ID: bc["bc_id"], F_ADV_ID: adv,
                F_ADV_NAME: b.get("name") or adv, F_DATE: str(end), F_SPEND: 0,
                F_CURRENCY: b.get("currency", ""), F_UPDATED: now_ms,
                F_BALANCE: b.get("balance"), F_BUDGET: b.get("budget"),
            })

    lark_ready = all(os.getenv(k) for k in ("LARK_APP_ID", "LARK_APP_SECRET", "LARK_BASE_TOKEN", "LARK_TABLE_ID"))
    if dry or not lark_ready:
        if not lark_ready:
            print("[CHUA GHI VAO BASE] Thieu secret Lark -> chi hien du lieu xem truoc:")
        for f in rows[:50]:
            print(f"{f[F_DATE]} | {f[F_BC_NAME]} | {f[F_ADV_NAME]} | spend {f[F_SPEND]} {f[F_CURRENCY]}")
        print(json.dumps(rows[:20], ensure_ascii=False, indent=2))
        print(f"(dry-run) Tong {len(rows)} dong, khong ghi vao Base.")
        return

    lark = Lark()
    if F_ADV_ID not in lark.types:
        print(f"[GOI Y] Base chua co cot '{F_ADV_ID}' -> dang chong trung theo '{F_ADV_NAME}'.")
    existing = lark.existing()
    acc_field = F_ADV_ID if F_ADV_ID in lark.types else F_ADV_NAME
    to_create, to_update = [], []
    for f in rows:
        key = f"{f[F_BC_ID]}|{f[acc_field]}|{f[F_DATE]}"
        fields = lark.convert(f)
        if key in existing:
            to_update.append({"record_id": existing[key], "fields": fields})
        else:
            to_create.append({"fields": fields})
    lark.batch("batch_create", to_create)
    lark.batch("batch_update", to_update)
    print(f"Xong: them moi {len(to_create)}, cap nhat {len(to_update)}.")


if __name__ == "__main__":
    main()
