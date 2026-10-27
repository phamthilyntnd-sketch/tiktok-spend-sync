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
F_SPEND_USD = "Spend (USD)"
F_RATE = "Tỷ giá (1 USD)"
F_MONTH = "Tháng"
F_WEEK = "Tuần"
# Ten khac cua cot (khi ban doi ten cot trong Base, them ten moi vao day)
ALIASES = {
    F_SPEND: ["Spend (USD & VNĐ)", "Spend"],
    F_SPEND_USD: ["Spend trong ngày (USD)", "Spend (USD)"],
}


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
        r = requests.get(TT_API + path, params=q, headers={"Access-Token": token}, timeout=60)
        try:
            j = r.json()
        except ValueError:
            j = {"code": r.status_code, "message": r.text[:300]}
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
    # Bao cao cap BC: khong can chi dinh tung tai khoan. TikTok chi cho dimensions=[advertiser_id],
    # nen lay tung ngay mot de co spend theo ngay.
    try:
        d0 = dt.date.fromisoformat(start)
        d1 = dt.date.fromisoformat(end)
        rows = []
        metric_sets = [["spend", "currency", "advertiser_name"], ["spend", "currency"], ["spend"]]
        day = d0
        while day <= d1:
            ds = str(day)
            for mi, metrics in enumerate(metric_sets):
                params = {"report_type": "BC", "bc_id": bc_id, "dimensions": ["advertiser_id"],
                          "metrics": metrics, "start_date": ds, "end_date": ds}
                try:
                    got = _parse_report(tt_pages("/report/integrated/get/", params, token, page_size=1000))
                    metric_sets = metric_sets[mi:]  # nho bo metrics dung duoc cho cac ngay sau
                    break
                except RuntimeError:
                    if mi == len(metric_sets) - 1:
                        raise
            for r in got:
                r["date"] = ds
                rows.append(r)
            day += dt.timedelta(days=1)
        print("   -> lay spend bang bao cao cap BC (theo tung ngay)")
        return rows
    except RuntimeError as e:
        print(f"   (bao cao BC: {e})")
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


def month_label(ds):
    return ds[:7]  # vd 2026-10


def week_label(ds):
    d = dt.date.fromisoformat(ds[:10])
    y, w, _ = d.isocalendar()
    mon = d - dt.timedelta(days=d.weekday())
    sun = mon + dt.timedelta(days=6)
    return f"{y}-W{w:02d} ({mon:%d/%m}-{sun:%d/%m})"  # vd 2026-W44 (26/10-01/11)


# ----------------------------- Ty gia -----------------------------
def get_fx():
    """So don vi tien te / 1 USD. Uu tien bien FX_RATES (vd: VND=26300,THB=36), sau do lay tu open.er-api.com."""
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
        self.alias = {}
        for logical, names in ALIASES.items():  # nhan ca ten cot da doi
            for n in names:
                if n in self.types:
                    self.alias[logical] = n
                    break
        for name in (F_SPEND_USD, F_RATE):  # tu tao cot quy doi USD (kieu So)
            if name in self.alias or name not in ALIASES and name in self.types:
                continue
            if name not in self.types:
                try:
                    self._call("POST", self.base + "/fields", {"field_name": name, "type": 2})
                    self.types[name] = 2
                    print(f"Da tu tao cot '{name}' trong Base.")
                except RuntimeError as e:
                    print(f"[GOI Y] Khong tao duoc cot '{name}': {e}")
        for name in (F_MONTH, F_WEEK):  # cot loc theo thang / tuan (Van ban)
            if name not in self.types:
                try:
                    self._call("POST", self.base + "/fields", {"field_name": name, "type": 1})
                    self.types[name] = 1
                    print(f"Da tu tao cot '{name}' trong Base.")
                except RuntimeError as e:
                    print(f"[GOI Y] Khong tao duoc cot '{name}': {e}")
        if F_ADV_ID not in self.types:  # tu tao cot Advertiser ID de chong trung chinh xac
            try:
                self._call("POST", self.base + "/fields", {"field_name": F_ADV_ID, "type": 1})
                self.types[F_ADV_ID] = 1
                print(f"Da tu tao cot '{F_ADV_ID}' trong Base.")
            except RuntimeError as e:
                print(f"[GOI Y] Khong tao duoc cot '{F_ADV_ID}': {e}")

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
            name = self.alias.get(name, name)
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

    def _scan(self, body):
        keys, token = {}, None
        while True:
            p = {"page_size": 500}
            if token:
                p["page_token"] = token
            data = self._call("POST", self.base + "/records/search", body, p)
            for it in data.get("items") or []:
                fr = it.get("fields") or {}
                keys[self.key_of(fr)] = it["record_id"]
                # them khoa theo Ten Tk cho cac dong cu chua co Advertiser ID
                d = fr.get(F_DATE)
                d = ms_to_date(d) if isinstance(d, (int, float)) else cell_text(d)[:10]
                keys.setdefault(f"{cell_text(fr.get(F_BC_ID))}|{cell_text(fr.get(F_ADV_NAME))}|{d}", it["record_id"])
            if not data.get("has_more"):
                break
            token = data.get("page_token")
        return keys

    def existing(self, dates):
        """Chi doc cac dong thuoc nhung ngay dang dong bo (nhanh, khong doc ca bang)."""
        names = [n for n in (F_BC_ID, F_ADV_ID, F_ADV_NAME, F_DATE) if n in self.types]
        if self.types.get(F_DATE) == 5:
            conds = [{"field_name": F_DATE, "operator": "is", "value": ["ExactDate", str(date_to_ms(d))]} for d in dates]
        else:
            conds = [{"field_name": F_DATE, "operator": "is", "value": [d]} for d in dates]
        try:
            return self._scan({"field_names": names, "filter": {"conjunction": "or", "conditions": conds}})
        except RuntimeError as e:
            print(f"   (loc theo ngay loi: {e} -> doc ca bang)")
            return self._scan({"field_names": names})

    def batch(self, action, records):
        for i in range(0, len(records), 500):
            self._call("POST", f"{self.base}/records/{action}", {"records": records[i:i + 500]})


# ----------------------------- Main -----------------------------
def process_bc(bc, start, end, tokens, with_balance):
    """1 BC co the duoc cap cho nhieu email: thu lan luot tung token, token nao co quyen thi dung."""
    bal = {}
    if with_balance:
        for tk in tokens:
            try:
                bal = get_balances(bc["bc_id"], tk)
                break
            except RuntimeError as e:
                last = e
        else:
            print(f"   [{bc['name']}] khong token nao lay duoc so du ({last}) -> van lay spend")
    for i, tk in enumerate(tokens):
        spend = get_spend(bc["bc_id"], list(bal), start, end, tk)
        if spend or i == len(tokens) - 1:
            return bal, spend
    return bal, []


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    dry = "--dry-run" in sys.argv
    env("TT_ACCESS_TOKEN")
    # Nhieu tai khoan TikTok (nhieu email): TT_ACCESS_TOKEN, TT_ACCESS_TOKEN_2 ... TT_ACCESS_TOKEN_10
    tokens = [os.getenv("TT_ACCESS_TOKEN")] + [os.getenv(f"TT_ACCESS_TOKEN_{i}") for i in range(2, 11)]
    tokens = [t.strip() for t in tokens if t and t.strip()]
    days = max(1, min(30, int(os.getenv("DAYS_BACK", "2"))))
    workers = max(1, int(os.getenv("PARALLEL", "5")))
    with_balance = os.getenv("FETCH_BALANCE", "1") == "1"
    today = dt.datetime.now(VN_TZ).date()
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    dates = [str(start + dt.timedelta(days=i)) for i in range(days)]
    now_ms = int(dt.datetime.now(VN_TZ).timestamp() * 1000)
    t0 = time.time()
    bcs, bc_seen = [], set()
    for i, tk in enumerate(tokens, 1):
        try:
            found = get_bcs(tk)
        except RuntimeError as e:
            print(f"[TOKEN {i}] loi: {e} | kiem tra: dai {len(tk)} ky tu (dung la 40), bat dau '{tk[:4]}', ket thuc '{tk[-4:]}'")
            continue
        new = [b for b in found if b["bc_id"] not in bc_seen]
        for b in found:  # BC trung giua nhieu email -> gom token, chi lay 1 lan
            if b["bc_id"] in bc_seen:
                next(x for x in bcs if x["bc_id"] == b["bc_id"])["tokens"].append(tk)
        for b in new:
            b["tokens"] = [tk]
            bc_seen.add(b["bc_id"])
        bcs += new
        print(f"[TOKEN {i}] thay {len(found)} BC ({len(new)} BC moi)")
    print(f"Dong bo {start} -> {end} | {len(bcs)} BC | {len(tokens)} token | chay song song {workers} BC")

    fx = get_fx()
    no_cur = set()
    rows, seen, summary = [], set(), []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_bc, bc, str(start), str(end), bc["tokens"], with_balance): bc for bc in bcs}
        for fu in as_completed(futs):
            bc = futs[fu]
            try:
                bal, spend = fu.result()
            except Exception as e:
                print(f"[BO QUA] BC {bc['bc_id']} ({bc['name']}): {e}")
                continue
            total_end = 0.0
            n = 0
            for r in spend:
                if r["spend"] <= 0:  # chi ghi tai khoan co tieu tien
                    continue
                key = (bc["bc_id"], r["advertiser_id"], r["date"])
                if key in seen:
                    continue
                seen.add(key)
                b = bal.get(r["advertiser_id"], {})
                cur = (b.get("currency") or r.get("currency") or "").upper()
                f = {
                    F_BC_NAME: bc["name"], F_BC_ID: bc["bc_id"], F_ADV_ID: r["advertiser_id"],
                    F_ADV_NAME: b.get("name") or r.get("name") or r["advertiser_id"], F_DATE: r["date"],
                    F_SPEND: round(r["spend"], 2), F_CURRENCY: cur, F_UPDATED: now_ms,
                    F_MONTH: month_label(r["date"]), F_WEEK: week_label(r["date"]),
                }
                if cur and cur in fx:
                    f[F_RATE] = fx[cur]
                    f[F_SPEND_USD] = round(r["spend"] / fx[cur], 2)
                else:
                    no_cur.add(r["advertiser_id"])
                if r["date"] == str(end):
                    f[F_BALANCE] = b.get("balance")
                    f[F_BUDGET] = b.get("budget")
                    total_end += f.get(F_SPEND_USD) or 0
                rows.append(f)
                n += 1
            summary.append((bc["name"], n, total_end))
            print(f"BC {bc['name']}: {n} dong co spend | tong spend {end}: {total_end:,.2f} USD")

    if no_cur:
        print(f"[CANH BAO] {len(no_cur)} tai khoan chua biet tien te -> chua quy doi USD: {', '.join(sorted(no_cur)[:20])}")
    print(f"Lay du lieu TikTok xong sau {time.time() - t0:.0f}s, tong {len(rows)} dong.")
    lark_ready = all(os.getenv(k) for k in ("LARK_APP_ID", "LARK_APP_SECRET", "LARK_BASE_TOKEN", "LARK_TABLE_ID"))
    if dry or not lark_ready:
        if not lark_ready:
            print("[CHUA GHI VAO BASE] Thieu secret Lark -> chi hien du lieu xem truoc:")
        for f in rows[:50]:
            print(f"{f[F_DATE]} | {f[F_BC_NAME]} | {f[F_ADV_NAME]} | spend {f[F_SPEND]} {f[F_CURRENCY]}")
        print(f"(dry-run) Tong {len(rows)} dong, khong ghi vao Base.")
        return

    lark = Lark()
    existing = lark.existing(dates)
    acc_field = F_ADV_ID if F_ADV_ID in lark.types else F_ADV_NAME
    to_create, to_update = [], []
    for f in rows:
        key = f"{f[F_BC_ID]}|{f[acc_field]}|{f[F_DATE]}"
        # dong cu (truoc khi co cot Advertiser ID) duoc nhan dien theo Ten Tk
        old_key = f"{f[F_BC_ID]}|{f[F_ADV_NAME]}|{f[F_DATE]}"
        fields = lark.convert(f)
        rid = existing.get(key) or existing.get(old_key)
        if rid:
            to_update.append({"record_id": rid, "fields": fields})
        else:
            to_create.append({"fields": fields})
    lark.batch("batch_create", to_create)
    lark.batch("batch_update", to_update)
    print(f"Xong sau {time.time() - t0:.0f}s: them moi {len(to_create)}, cap nhat {len(to_update)}. Tong spend ngay {end}: {sum(f.get(F_SPEND_USD) or 0 for f in rows if f[F_DATE] == str(end)):,.2f} USD")


if __name__ == "__main__":
    main()
