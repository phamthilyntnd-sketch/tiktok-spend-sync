#!/usr/bin/env python3
"""
Dong bo chi tieu (spend) cua tat ca Business Center TikTok vao Lark Base, truoc 6h sang moi ngay.

Base gom 2 bang chinh (+ bang "Giải thích cột" va "Kết nối email TikTok"):
  "Báo cáo BC"  : 1 dong = 1 BC x 1 ky (Tháng / Quý). Che do xem: Tháng này, Quý này, Theo tháng, Theo quý.
  "Chi tiết acc": 1 dong = 1 tai khoan x 1 ngay / 1 thang / 1 quy (cot "Loại kỳ").
                  Dong Ngày giu KEEP_DETAIL_DAYS (90) ngay -> luon duoi gioi han 20.000 dong.
                  Chi tai khoan co chi tieu moi co dong -> TK lau khong dung khong hien.
  "Kết nối email TikTok": dan link/auth_code -> tu ket noi email moi (chay --connect moi 15 phut).

  python tiktok_bc_spend_to_lark.py             # dong bo
  python tiktok_bc_spend_to_lark.py --connect   # chi ket noi email moi
  python tiktok_bc_spend_to_lark.py --dry-run   # chi in ket qua

Bien moi truong: LARK_APP_ID, LARK_APP_SECRET, LARK_BASE_TOKEN, TT_APP_ID, TT_APP_SECRET,
  TT_ACCESS_TOKEN (+ _2.._10, tuy chon). Tuy chon: DAYS_BACK (2), MONTHS_BACK (1), QUARTERS_BACK (1),
  KEEP_DETAIL_DAYS (90), PARALLEL (3), FX_RATES, ALERT_CHANGE_PCT (30), ALERT_MIN_USD (20), LOW_BALANCE_DAYS (3)
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
    "Tăng trưởng (tuần)": ["Tăng trưởng (tuần) %", "Tăng trưởng (tuần)"],
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
# ======================= CAU TRUC BASE (v6: 2 bang chinh) =======================
# Bang 1 "Báo cáo BC": 1 dong = 1 BC x 1 ky (Tháng / Quý). Loc theo cot Kỳ de chon thang/quy.
# Bang 2 "Chi tiết acc": 1 dong = 1 tai khoan x (1 ngay | 1 thang | 1 quy), cot "Loại kỳ" de loc.
#   Dong "Ngày" chi giu KEEP_DETAIL_DAYS ngay (gioi han ~20.000 dong/bang), dong Tháng/Quý giu mai.
#   Chi tai khoan CO chi tieu moi co dong -> tai khoan lau khong dung tu dong khong hien.
T_REP = "Báo cáo BC"
T_ACC = "Chi tiết acc"
KEEP_DETAIL_DAYS = int(os.getenv("KEEP_DETAIL_DAYS", "400"))  # Base nang len 50.000 dong
K_KIND, K_PERIOD, K_CUR, K_KEY = "Loại kỳ", "Kỳ", "Kỳ hiện tại", "Mã dòng"
REP_FIELDS = [  # 1 van ban, 2 so, 3 chon 1, 5 ngay, 7 checkbox
    ("Tên BC", 1), ("ID BC", 1), (K_KIND, 3), (K_PERIOD, 3), (K_CUR, 7),
    ("Tổng spend (USD)", 2), ("Ngân sách (USD)", 2), ("% ngân sách đã dùng", 2),
    ("Dự kiến cuối kỳ (USD)", 2), ("Spend kỳ trước (USD)", 2), ("% dự kiến so với kỳ trước", 2),
    ("Số TK chi tiêu", 2), ("Tổng TK trong BC", 2),
    ("Mức cảnh báo", 3), ("Cảnh báo", 1), ("Xu hướng", 3),
    ("Spend hôm qua (USD)", 2), ("% so với hôm kia", 2), ("Spend 7 ngày (USD)", 2),
    ("TB/ngày 7 ngày (USD)", 2), ("Số dư TK (USD)", 2),
    ("Số liệu đến ngày", 5), ("Cập nhật lúc", 5), (K_KEY, 1),
]
ACC_FIELDS = [  # chi dong theo NGAY: 1 dong = 1 tai khoan x 1 ngay
    ("Tên Tk", 1), ("Ngày", 5), ("Spend (USD)", 2), ("Spend", 2), ("Tiền tệ", 1), ("Tỷ giá (1 USD)", 2),
    ("Tên BC", 1), ("ID BC", 1), ("Advertiser ID", 1), ("Tuần", 3), ("Tháng", 3), ("Quý", 3),
    ("Số dư hiện tại", 2), ("Quốc gia", 1), ("Múi giờ", 1), ("Loại hình QC", 1),
    ("Cập nhật lúc", 5), (K_KEY, 1),
]
# ---- Ket noi email TikTok (quan ly tu dien auth_code) ----
T_CONNECT = "Kết nối email TikTok"
C_TOKEN = "Token (hệ thống tự điền)"
C_FIELDS = [("Email", 1), ("Mã auth_code", 1), ("Trạng thái", 1), ("Số BC", 2), ("Danh sách BC", 1),
            (C_TOKEN, 1), ("Cập nhật lúc", 5)]
T_HELP = "Giải thích cột"
H_FIELDS = [("Mã", 1), ("Bảng", 1), ("Cột", 1), ("Ý nghĩa", 1), ("Cách tính / nguồn", 1), ("Ai nhập", 1),
            ("Thứ tự", 2)]
# Bang cu -> doi ten de quan ly biet ma xoa (khong xoa du lieu)
OLD_TABLES = {"Tổng hợp BC": "🗑 Có thể xoá - Tổng hợp BC", "Tổng theo ngày": "🗑 Có thể xoá - Tổng theo ngày",
              "Tổng BC theo tháng": "🗑 Có thể xoá - Tổng BC theo tháng",
              "Tổng acc theo tháng": "🗑 Có thể xoá - Tổng acc theo tháng",
              "Chi tiết acc (60 ngày)": "🗑 Có thể xoá - Chi tiết acc (60 ngày)"}

_S, _M = "Hệ thống tự điền", "Quản lý tự nhập"
HELP = [
    ("Báo cáo BC", "Cách dùng", "Mỗi dòng = 1 BC trong 1 kỳ (1 tháng hoặc 1 quý)", "Chọn chế độ xem 'Tháng này' / 'Quý này', hoặc Lọc cột Kỳ = tháng/quý muốn xem", "—"),
    ("Báo cáo BC", "Loại kỳ", "Tháng hoặc Quý", "", _S),
    ("Báo cáo BC", "Kỳ", "Tháng (vd 2026-T11) hoặc quý (vd 2026-Q4)", "Lọc cột này để đổi kỳ xem", _S),
    ("Báo cáo BC", "Kỳ hiện tại", "Đánh dấu ✓ nếu là tháng/quý đang chạy", "Tự bỏ dấu khi sang tháng/quý mới", _S),
    ("Báo cáo BC", "Tổng spend (USD)", "Tổng chi tiêu của BC trong kỳ (kỳ hiện tại: tính đến hôm qua)", "Báo cáo TikTok cấp BC, đổi USD", _S),
    ("Báo cáo BC", "Ngân sách (USD)", "Ngân sách của BC trong kỳ đó", "Nhập tay trên đúng dòng tháng/quý", _M),
    ("Báo cáo BC", "% ngân sách đã dùng", "Đã tiêu bao nhiêu % ngân sách", "Tổng spend / Ngân sách × 100", _S),
    ("Báo cáo BC", "Dự kiến cuối kỳ (USD)", "Ước tính tổng chi tiêu khi hết tháng/quý", "Kỳ hiện tại: đã tiêu + TB/ngày 7 ngày × số ngày còn lại. Kỳ đã qua: = Tổng spend", _S),
    ("Báo cáo BC", "Spend kỳ trước (USD)", "Tổng của tháng/quý liền trước", "Báo cáo TikTok", _S),
    ("Báo cáo BC", "% dự kiến so với kỳ trước", "Kỳ này dự kiến tăng/giảm bao nhiêu so với kỳ trước", "(Dự kiến - Kỳ trước) / Kỳ trước × 100", _S),
    ("Báo cáo BC", "Số TK chi tiêu", "Số tài khoản có tiêu tiền trong kỳ", "Đếm tài khoản (không đếm dòng)", _S),
    ("Báo cáo BC", "Tổng TK trong BC", "Tổng số tài khoản quảng cáo đang nằm trong BC", "TikTok", _S),
    ("Báo cáo BC", "Mức cảnh báo", "🔴 Khẩn cấp / 🟠 Cần chú ý / 🟢 Bình thường (chỉ dòng kỳ hiện tại)", "🔴: ngừng chi tiêu đột ngột, vượt ngân sách, số dư < 1 ngày. 🟠: ±30% so với hôm kia, 7 ngày giảm ≥30%, dùng ≥90% ngân sách, dự kiến vượt ngân sách, số dư < 3 ngày", _S),
    ("Báo cáo BC", "Cảnh báo", "Lý do cụ thể", "", _S),
    ("Báo cáo BC", "Xu hướng", "Tăng / Giảm / Ổn định", "7 ngày gần nhất so với 7 ngày trước: ≥ +10% Tăng, ≤ -10% Giảm", _S),
    ("Báo cáo BC", "Spend hôm qua · % so với hôm kia", "Chi tiêu ngày hôm qua và mức thay đổi", "", _S),
    ("Báo cáo BC", "Spend 7 ngày · TB/ngày 7 ngày", "Tổng 7 ngày gần nhất và trung bình mỗi ngày", "", _S),
    ("Báo cáo BC", "Số dư TK (USD)", "Tổng số dư còn lại của các tài khoản trong BC", "API số dư TikTok", _S),
    ("Báo cáo BC", "Số liệu đến ngày · Cập nhật lúc", "Số liệu tính đến ngày nào, ghi lúc mấy giờ", "Cập nhật trước 6h sáng mỗi ngày", _S),
    ("Chi tiết acc", "Cách dùng", "Mỗi dòng = 1 tài khoản tiêu bao nhiêu trong 1 ngày", "Chế độ xem 'Tất cả' = từng ngày; 'Tổng theo tháng' / 'Tổng theo quý' = nhóm theo tháng/quý rồi theo tài khoản, xem tổng ở dòng nhóm. Lọc thêm Tên BC / Tên Tk / Ngày tuỳ ý", "—"),
    ("Chi tiết acc", "Ngày · Tuần · Tháng · Quý", "Ngày phát sinh chi tiêu và tuần/tháng/quý của ngày đó (để lọc, nhóm)", "Tuần theo ISO, thứ 2 → CN. Giữ 400 ngày gần nhất (~31.000 dòng, dưới giới hạn 50.000)", _S),
    ("Chi tiết acc", "Spend (USD)", "Số tiền tài khoản tiêu trong ngày, đổi ra USD", "Spend / Tỷ giá", _S),
    ("Chi tiết acc", "Spend · Tiền tệ · Tỷ giá (1 USD)", "Số tiền gốc theo tiền tệ của tài khoản và tỷ giá dùng để đổi", "Báo cáo TikTok; tỷ giá open.er-api.com", _S),
    ("Chi tiết acc", "Số dư hiện tại", "Số dư tài khoản (chỉ ghi ở dòng ngày gần nhất)", "API số dư TikTok", _S),
    ("Chi tiết acc", "Quốc gia · Múi giờ · Loại hình QC", "Thị trường, múi giờ tài khoản, mục tiêu chiến dịch", "TikTok", _S),
    ("Chi tiết acc", "Tài khoản lâu không dùng", "Không hiển thị", "Chỉ ngày có chi tiêu mới có dòng", "—"),
    ("Kết nối email TikTok", "Email", "Email đang quản trị BC (để ghi nhớ)", "", _M),
    ("Kết nối email TikTok", "Mã auth_code", "Dán cả đường link sau khi bấm Uỷ quyền (hoặc riêng auth_code)", "Hệ thống tự kết nối trong ≤15 phút rồi ghi '✔ đã dùng'", _M),
    ("Kết nối email TikTok", "Trạng thái · Số BC · Danh sách BC", "Kết nối được chưa, thấy những BC nào", "", _S),
    ("Kết nối email TikTok", C_TOKEN, "Chìa khoá truy cập của email — không sửa, không chia sẻ", "", _S),
]

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


def prior_months(end_d):
    """Cac thang truoc can lay lai: MONTHS_BACK (mac dinh 0; ngay 1-3 hang thang tu lay lai thang truoc)."""
    n = int(os.getenv("MONTHS_BACK", "0") or 0)
    if end_d.day <= 3:
        n = max(n, 1)
    out, first = [], end_d.replace(day=1)
    for _ in range(min(n, 12)):
        last = first - dt.timedelta(days=1)
        first = last.replace(day=1)
        out.append((first, last))
    return out


def pct(a, b):
    return round((a - b) / b * 100, 1) if b else None


NOTES = []


def note(*args):
    """In ra log + gom lai thanh 1 ghi chu tong ket tren trang GitHub Actions."""
    msg = " ".join(str(a) for a in args)
    print(msg)
    NOTES.append(msg)


def flush_notes():
    if os.getenv("GITHUB_ACTIONS") and NOTES:
        msg = "\n".join(NOTES)
        print("::notice::" + msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


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
            elif t == 7:
                out[name] = bool(val)
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


# ----------------------------- Ket noi email (tu bang Lark) -----------------------------
def parse_auth_code(text):
    """Nhan ca ma auth_code lan ca duong link sau khi uy quyen (co ?auth_code=...)."""
    from urllib.parse import urlparse, parse_qs
    text = (text or "").strip()
    if "auth_code=" in text or "code=" in text:
        q = parse_qs(urlparse(text if "://" in text else "https://x/?" + text.split("?", 1)[-1]).query)
        for k in ("auth_code", "code"):
            if q.get(k):
                return q[k][0].strip()
    return text


def exchange_auth_code(code):
    app_id, secret = os.getenv("TT_APP_ID"), os.getenv("TT_APP_SECRET")
    if not app_id or not secret:
        raise RuntimeError("chua cai TT_APP_ID / TT_APP_SECRET tren GitHub")
    j = requests.post(TT_API + "/oauth2/access_token/", timeout=60,
                      json={"app_id": app_id, "secret": secret, "auth_code": code}).json()
    tok = (j.get("data") or {}).get("access_token")
    if j.get("code") != 0 or not tok:
        raise RuntimeError(f"{j.get('code')} - {j.get('message')}")
    return tok


def connect_emails(lark, only_new=False):
    """Doc bang 'Kết nối email TikTok': doi auth_code -> token, kiem tra token, tra ve list token."""
    tbl = Table(lark, lark.ensure_table(T_CONNECT, C_FIELDS), C_FIELDS)
    now_ms = int(dt.datetime.now(VN_TZ).timestamp() * 1000)
    tokens, updates = [], []
    for rid, fr in tbl.scan():
        raw = cell_text(fr.get("Mã auth_code")).strip()
        tok = cell_text(fr.get(C_TOKEN)).strip()
        email = cell_text(fr.get("Email")).strip() or rid
        new_code = raw and not raw.startswith("✔")
        if only_new and not new_code:
            if tok:
                tokens.append(tok)
            continue
        upd = {"Cập nhật lúc": now_ms}
        if new_code:
            try:
                tok = exchange_auth_code(parse_auth_code(raw))
                upd[C_TOKEN] = tok
                upd["Mã auth_code"] = "✔ đã dùng " + dt.datetime.now(VN_TZ).strftime("%d/%m %H:%M")
                note(f"Ket noi email moi: {email} -> thanh cong")
            except RuntimeError as e:
                upd["Trạng thái"] = f"❌ Mã không dùng được ({e}). Lấy mã mới và dán lại (mã chỉ sống ~1 giờ)."
                updates.append({"record_id": rid, "fields": tbl.convert(upd)})
                note(f"Ket noi email {email} loi: {e}")
                if tok:
                    tokens.append(tok)
                continue
        if tok:
            try:
                bcs = get_bcs(tok)
                upd["Trạng thái"] = "✅ Đã kết nối"
                upd["Số BC"] = len(bcs)
                upd["Danh sách BC"] = "; ".join(b["name"] for b in bcs)[:3000]
                tokens.append(tok)
            except RuntimeError as e:
                upd["Trạng thái"] = f"❌ Token lỗi ({e}). Uỷ quyền lại và dán auth_code mới."
        elif not raw:
            upd["Trạng thái"] = "⏳ Chưa có auth_code"
        updates.append({"record_id": rid, "fields": tbl.convert(upd)})
    tbl.batch("batch_update", updates)
    return tokens


def write_help(lark):
    tbl = Table(lark, lark.ensure_table(T_HELP, H_FIELDS), H_FIELDS)
    ex = {cell_text(fr.get("Mã")): rid for rid, fr in tbl.scan({"field_names": ["Mã"]})}
    rows = [{"Mã": f"{b}|{c}", "Bảng": b, "Cột": c, "Ý nghĩa": y, "Cách tính / nguồn": k, "Ai nhập": a,
             "Thứ tự": i + 1} for i, (b, c, y, k, a) in enumerate(HELP)]
    tbl.upsert(rows, lambda f: (f["Mã"],), ex)


def trim_old(tbl, date_field, keep_days, label):
    """Xoa dong cu hon keep_days de bang khong cham gioi han 20.000 dong cua Lark."""
    if keep_days <= 0 or date_field not in tbl.types:
        return
    cutoff = dt.datetime.now(VN_TZ).date() - dt.timedelta(days=keep_days)
    try:
        recs = tbl.scan({"field_names": [date_field], "filter": {"conjunction": "and", "conditions": [
            {"field_name": date_field, "operator": "isLess", "value": ["ExactDate", str(date_to_ms(cutoff))]}]}})
    except RuntimeError:
        recs = tbl.scan({"field_names": [date_field]})
    old = []
    for rid, fr in recs:
        d = fr.get(date_field)
        d = ms_to_date(d) if isinstance(d, (int, float)) else cell_text(d)[:10]
        if d and d < str(cutoff):
            old.append(rid)
    for i in range(0, len(old), 500):
        tbl.l.call("POST", f"{tbl.base}/records/batch_delete", {"records": old[i:i + 500]})
    if old:
        print(f"{label}: xoa {len(old)} dong truoc {cutoff} (da co trong bang theo thang).")


# ----------------------------- Ky (thang / quy) -----------------------------
def month_lbl(d):
    return f"{d.year}-T{d.month:02d}"


def quarter_lbl(d):
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def q_start(d):
    return dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def periods_for(end_d):
    """Cac ky can lay: thang/quy hien tai + MONTHS_BACK thang truoc + QUARTERS_BACK quy truoc (mac dinh 1)."""
    out = []
    first = end_d.replace(day=1)
    out.append({"kind": "Tháng", "label": month_lbl(end_d), "start": first, "end": end_d, "cur": True,
                "last": dt.date(end_d.year, end_d.month, calendar.monthrange(end_d.year, end_d.month)[1])})
    for _ in range(max(1, min(24, int(os.getenv("MONTHS_BACK", "1") or 1)))):
        last = first - dt.timedelta(days=1)
        first = last.replace(day=1)
        out.append({"kind": "Tháng", "label": month_lbl(first), "start": first, "end": last, "cur": False, "last": last})
    qs = q_start(end_d)
    qe_m = qs.month + 2
    out.append({"kind": "Quý", "label": quarter_lbl(end_d), "start": qs, "end": end_d, "cur": True,
                "last": dt.date(qs.year, qe_m, calendar.monthrange(qs.year, qe_m)[1])})
    for _ in range(max(1, min(8, int(os.getenv("QUARTERS_BACK", "1") or 1)))):
        last = qs - dt.timedelta(days=1)
        qs = q_start(last)
        out.append({"kind": "Quý", "label": quarter_lbl(qs), "start": qs, "end": last, "cur": False, "last": last})
    for i, p in enumerate(out):  # ky lien truoc cua moi ky hien tai
        p["prev"] = next((x["label"] for x in out[i + 1:] if x["kind"] == p["kind"]), None) if p["cur"] else None
    return out


# ----------------------------- Xu ly 1 BC -----------------------------
def process_bc(bc, start, end, tokens, with_balance, periods):
    """1 BC co the duoc cap cho nhieu email: thu lan luot tung token."""
    res = {"bal": {}, "daily": [], "w7": [], "p7": [], "per": {}, "attr": {}, "obj": {}}
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
        try:
            res["w7"] = rep.range(end_d - dt.timedelta(days=6), end_d)
            res["p7"] = rep.range(end_d - dt.timedelta(days=13), end_d - dt.timedelta(days=7))
            for p in periods:
                res["per"][p["label"]] = rep.range(p["start"], p["end"])
        except RuntimeError as e:
            print(f"   [{bc['name']}] khong lay duoc so tong hop: {e}")
        break
    cur_m = periods[0]["label"]
    active = sorted({r["advertiser_id"] for r in res["daily"] + res["per"].get(cur_m, []) if r["spend"] > 0})
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


# ----------------------------- Lark: doi ten bang cu, tao che do xem -----------------------------
def list_tables(lark):
    out, token = {}, None
    while True:
        p = {"page_size": 100}
        if token:
            p["page_token"] = token
        data = lark.call("GET", lark.app + "/tables", params=p)
        for t in data.get("items") or []:
            out[t["name"]] = t["table_id"]
        if not data.get("has_more"):
            return out
        token = data.get("page_token")


def rename_table(lark, tid, new):
    lark.call("PATCH", f"{lark.app}/tables/{tid}", {"name": new})
    note(f"Doi ten bang cu -> '{new}' (du lieu giu nguyen)")


def prepare_tables(lark):
    """Bang 'Báo cáo BC' / 'Chi tiết acc' cu (cau truc khac) -> doi ten '(cũ)'. Tra ve ngan sach cu."""
    tabs = list_tables(lark)
    old_budget = {}
    for name in (T_REP, T_ACC):
        tid = tabs.get(name)
        if not tid:
            continue
        t = Table(lark, tid)
        if name == T_REP and K_KIND in t.types:
            continue  # da la cau truc moi
        if name == T_ACC and K_KEY in t.types and K_KIND not in t.types:
            continue
        if name == T_REP and "Ngân sách tháng (USD)" in t.types:
            for _, fr in t.scan({"field_names": ["ID BC", "Ngân sách tháng (USD)"]}):
                v = fr.get("Ngân sách tháng (USD)")
                if v not in (None, ""):
                    old_budget[cell_text(fr.get("ID BC")).strip()] = float(v)
        new = name + " (cũ)" if K_KIND not in t.types else "🗑 Có thể xoá - Chi tiết acc (bản trộn ngày/tháng/quý)"
        while new in tabs:
            new += " "
        rename_table(lark, tid, new)
    for old, new in OLD_TABLES.items():
        if old in tabs and new not in tabs:
            rename_table(lark, tabs[old], new)
    return old_budget


def ensure_views(lark, tid, specs):
    """specs: [(ten view, [(cot, gia tri)])] -> tao view loc san (best effort)."""
    base = f"{lark.app}/tables/{tid}"
    try:
        views = {v["view_name"]: v["view_id"] for v in (lark.call("GET", base + "/views", params={"page_size": 100}).get("items") or [])}
        fields = {f["field_name"]: f for f in (lark.call("GET", base + "/fields", params={"page_size": 100}).get("items") or [])}
    except RuntimeError as e:
        print(f"   (khong doc duoc view: {e})")
        return
    for name, conds in specs:
        try:
            if name in views:
                vid = views[name]
                prop = (lark.call("GET", f"{base}/views/{vid}").get("view") or {}).get("property") or {}
                if ((prop.get("filter_info") or {}).get("conditions")):
                    continue  # da co bo loc
            else:
                vid = lark.call("POST", base + "/views", {"view_name": name, "view_type": "grid"}).get("view", {}).get("view_id")
                if not conds:
                    note(f"Tao che do xem '{name}'")
            if not conds:
                continue
            cl = []
            for col, val in conds:
                f = fields[col]
                if f["type"] == 7:
                    cl.append({"field_id": f["field_id"], "operator": "is", "value": "[\"true\"]" if val else "[\"false\"]"})
                else:
                    opt = next((o["id"] for o in ((f.get("property") or {}).get("options") or []) if o["name"] == val), None)
                    if not opt:
                        raise RuntimeError(f"chua co lua chon '{val}' o cot {col}")
                    cl.append({"field_id": f["field_id"], "operator": "is", "value": json.dumps([opt])})
            lark.call("PATCH", f"{base}/views/{vid}", {"property": {"filter_info": {"conjunction": "and", "conditions": cl}}})
            note(f"Tao che do xem '{name}'")
        except (RuntimeError, KeyError) as e:
            note(f"(che do xem '{name}' chua dat duoc bo loc: {e})")


def set_current_flags(tbl, keys_true, now_ms):
    """Bo dau 'Kỳ hiện tại' o cac dong khong con la ky hien tai."""
    try:
        recs = tbl.scan({"field_names": [K_KEY, K_CUR], "filter": {"conjunction": "and", "conditions": [
            {"field_name": K_CUR, "operator": "is", "value": ["true"]}]}})
    except RuntimeError:
        recs = tbl.scan({"field_names": [K_KEY, K_CUR]})
    upd = [{"record_id": rid, "fields": {K_CUR: False}} for rid, fr in recs
           if fr.get(K_CUR) is True and cell_text(fr.get(K_KEY)) not in keys_true]
    tbl.batch("batch_update", upd)


# ----------------------------- Main -----------------------------
def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    dry = "--dry-run" in sys.argv
    tokens = [os.getenv("TT_ACCESS_TOKEN")] + [os.getenv(f"TT_ACCESS_TOKEN_{i}") for i in range(2, 11)]
    tokens = [t.strip() for t in tokens if t and t.strip()]
    lark_ready = all(os.getenv(k) for k in ("LARK_APP_ID", "LARK_APP_SECRET", "LARK_BASE_TOKEN"))
    lark = Lark() if lark_ready else None
    if "--connect" in sys.argv:  # chay nhanh moi 15 phut: chi doi auth_code moi
        if lark:
            connect_emails(lark, only_new=True)
        return
    if lark and not dry:
        try:
            more = connect_emails(lark)
            tokens += [t for t in more if t not in tokens]
            print(f"Bang '{T_CONNECT}': {len(more)} email da ket noi.")
        except RuntimeError as e:
            print(f"[CANH BAO] Khong doc duoc bang ket noi email: {e}")
    if not tokens:
        sys.exit("[LOI] Chua co token TikTok nao (GitHub secret hoac bang Kết nối email TikTok).")
    days = max(1, min(KEEP_DETAIL_DAYS, int(os.getenv("DAYS_BACK", "2"))))
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
    periods = periods_for(end)
    pmap = {p["label"]: p for p in periods}
    t0 = time.time()

    bcs, bc_seen = [], set()
    for i, tk in enumerate(tokens, 1):
        try:
            found = get_bcs(tk)
        except RuntimeError as e:
            print(f"[TOKEN {i}] loi: {e} | dai {len(tk)} ky tu, '{tk[:4]}...{tk[-4:]}'")
            continue
        for b in found:
            if b["bc_id"] in bc_seen:
                next(x for x in bcs if x["bc_id"] == b["bc_id"])["tokens"].append(tk)
            else:
                b["tokens"] = [tk]
                bc_seen.add(b["bc_id"])
                bcs.append(b)
        print(f"[TOKEN {i}] thay {len(found)} BC")
    print(f"Dong bo {start} -> {end} | {len(bcs)} BC | ky: {', '.join(pmap)}")

    fx = get_fx()
    fetch_start = min(str(start), prev)

    def usd(amount, cur):
        cur = (cur or "").upper()
        return amount / fx[cur] if cur in fx else None

    acc_rows, rep_rows, summary, no_cur = [], [], [], set()
    tk_mtd = {}
    def handle(bc, res):
        bal, attr, objs = res["bal"], res["attr"], res["obj"]
        curmap = {}
        for src in [res["daily"], res["w7"], res["p7"]] + list(res["per"].values()):
            for r in src:
                if r.get("currency"):
                    curmap[r["advertiser_id"]] = r["currency"].upper()
        for adv, a in attr.items():
            if a.get("currency"):
                curmap.setdefault(adv, a["currency"].upper())
        for adv, b in bal.items():
            if b.get("currency"):
                curmap[adv] = b["currency"].upper()

        def tot(rows):
            s = 0.0
            for r in rows:
                v = usd(r["spend"], curmap.get(r["advertiser_id"]))
                if v is None and r["spend"] > 0:
                    no_cur.add(r["advertiser_id"])
                s += v or 0
            return s

        def acc_base(adv, r):
            b, a = bal.get(adv, {}), attr.get(adv, {})
            cur = curmap.get(adv, "")
            v = usd(r["spend"], cur)
            f = {"Tên Tk": b.get("name") or r.get("name") or adv, "Advertiser ID": adv,
                 "Tên BC": bc["name"], "ID BC": bc["bc_id"], "Spend": round(r["spend"], 2), "Tiền tệ": cur,
                 "Quốc gia": a.get("country", ""), "Múi giờ": a.get("tz", ""), "Cập nhật lúc": now_ms}
            if v is not None:
                f["Tỷ giá (1 USD)"] = fx[cur]
                f["Spend (USD)"] = round(v, 2)
            return f, v

        # --- dong Ngày ---
        day_usd = {str(end): 0.0, prev: 0.0}
        n_y, seen = 0, set()
        for r in res["daily"]:
            if r["spend"] <= 0 or (r["advertiser_id"], r["date"]) in seen:
                continue
            seen.add((r["advertiser_id"], r["date"]))
            adv = r["advertiser_id"]
            f, v = acc_base(adv, r)
            if r["date"] in day_usd:
                day_usd[r["date"]] += v or 0
            if r["date"] == str(end):
                n_y += 1
            if r["date"] not in dates:
                continue
            d = dt.date.fromisoformat(r["date"])
            f.update({"Ngày": r["date"], "Tuần": week_label(r["date"]), "Tháng": month_lbl(d),
                      "Quý": quarter_lbl(d), K_KEY: f"{bc['bc_id']}|{adv}|{r['date']}"})
            if r["date"] == str(end):
                f["Số dư hiện tại"] = bal.get(adv, {}).get("balance")
                if adv in objs:
                    f["Loại hình QC"] = fmt_objectives(objs[adv])
            acc_rows.append(f)

        # --- dong Tháng / Quý cho tung tai khoan ---
        per_usd, per_n = {}, {}
        for p in periods:
            rows = res["per"].get(p["label"], [])
            per_usd[p["label"]] = tot(rows)
            per_n[p["label"]] = sum(1 for r in rows if r["spend"] > 0)
        tk_mtd[bc["bc_id"]] = per_usd[periods[0]["label"]]

        w7, p7 = tot(res["w7"]), tot(res["p7"])
        bal_usd = sum(usd(float(b["balance"]), b.get("currency")) or 0
                      for b in bal.values() if b.get("balance") not in (None, ""))
        summary.append({"bc": bc, "y": day_usd[str(end)], "p": day_usd[prev], "w7": w7, "p7": p7,
                        "avg7": w7 / 7, "n_y": n_y, "bal": bal_usd if bal else None, "n_all": len(bal) or None,
                        "per": per_usd, "per_n": per_n})
        print(f"BC {bc['name']}: hom qua {day_usd[str(end)]:,.2f} USD | thang {per_usd[periods[0]['label']]:,.2f} USD")

    failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_bc, bc, fetch_start, str(end), bc["tokens"], with_balance, periods): bc for bc in bcs}
        for fu in as_completed(futs):
            bc = futs[fu]
            try:
                res = fu.result()
            except Exception as e:
                failed.append(bc)
                print(f"[THU LAI SAU] BC {bc['bc_id']} ({bc['name']}): {e}")
                continue
            handle(bc, res)
    for bc in failed:  # thu lai tuan tu cac BC loi (thuong do vuot gioi han goi API)
        time.sleep(10)
        try:
            handle(bc, process_bc(bc, fetch_start, str(end), bc["tokens"], with_balance, periods))
            note(f"BC {bc['name']}: lan dau loi, thu lai thanh cong")
        except Exception as e:
            note(f"[BO QUA] BC {bc['bc_id']} ({bc['name']}): {e}")

    if no_cur:
        print(f"[CANH BAO] {len(no_cur)} TK chua biet tien te: {', '.join(sorted(no_cur)[:20])}")
    if dry or not lark_ready:
        for s in sorted(summary, key=lambda x: -x["y"])[:30]:
            print(f"{s['bc']['name']}: hom qua {s['y']:,.2f} | 7 ngay {s['w7']:,.2f} | " +
                  " | ".join(f"{k} {v:,.0f}" for k, v in s["per"].items()))
        print("(dry-run) khong ghi vao Base.")
        return

    # ---------- Chuan bi Base ----------
    old_budget = prepare_tables(lark)
    write_help(lark)
    rep = Table(lark, lark.ensure_table(T_REP, REP_FIELDS), REP_FIELDS)
    acc = Table(lark, lark.ensure_table(T_ACC, ACC_FIELDS), ACC_FIELDS)

    # ---------- Bao cao BC ----------
    ex_rep, budgets = {}, {}
    for rid, fr in rep.scan({"field_names": [K_KEY, "Ngân sách (USD)"]}):
        k = cell_text(fr.get(K_KEY))
        ex_rep[k] = rid
        if fr.get("Ngân sách (USD)") not in (None, ""):
            budgets[k] = float(fr["Ngân sách (USD)"])
    cur_keys = set()
    n_red = n_or = 0
    for s in summary:
        bcid = s["bc"]["bc_id"]
        dchg, wchg = pct(s["y"], s["p"]), pct(s["w7"], s["p7"])
        trend = "Ổn định"
        if wchg is not None:
            trend = "📈 Tăng" if wchg >= 10 else ("📉 Giảm" if wchg <= -10 else "Ổn định")
        elif s["w7"] > 0:
            trend = "🆕 Mới chi tiêu"
        elif s["w7"] == 0:
            trend = "Không chi tiêu"
        for p in periods:
            total = s["per"][p["label"]]
            key = f"{bcid}|{p['label']}"
            if total <= 0 and key not in ex_rep:
                continue  # BC khong chi tieu trong ky -> khong tao dong
            budget = budgets.get(key)
            if budget is None and p["cur"] and p["kind"] == "Tháng" and bcid in old_budget:
                budget = old_budget[bcid]
            fc = total + s["avg7"] * (p["last"] - end).days if p["cur"] else total
            prev_tot = s["per"].get(p["prev"]) if p["prev"] else None
            f = {"Tên BC": s["bc"]["name"], "ID BC": bcid, K_KIND: p["kind"], K_PERIOD: p["label"],
                 K_CUR: p["cur"], "Tổng spend (USD)": round(total, 2), "Dự kiến cuối kỳ (USD)": round(fc, 2),
                 "Số TK chi tiêu": s["per_n"][p["label"]], "Số liệu đến ngày": str(min(end, p["end"])),
                 "Cập nhật lúc": now_ms, K_KEY: key}
            if budget:
                f["Ngân sách (USD)"] = budget
                f["% ngân sách đã dùng"] = round(total / budget * 100, 1)
            if prev_tot is not None:
                f["Spend kỳ trước (USD)"] = round(prev_tot, 2)
                f["% dự kiến so với kỳ trước"] = pct(fc, prev_tot)
            if p["cur"]:
                cur_keys.add(key)
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
                    if total >= budget:
                        red.append(f"🚨 Vượt ngân sách {total - budget:,.0f} USD")
                    elif total >= 0.9 * budget:
                        orange.append("⚠️ Đã dùng ≥90% ngân sách")
                    if fc > budget > total:
                        orange.append(f"⚠️ Dự kiến vượt ngân sách {fc - budget:,.0f} USD")
                if s["bal"] is not None and s["avg7"] > 0 and s["bal"] < s["avg7"] * low_days:
                    (red if s["bal"] < s["avg7"] else orange).append(f"💰 Số dư chỉ đủ ~{s['bal'] / s['avg7']:.1f} ngày")
                level = "🔴 Khẩn cấp" if red else ("🟠 Cần chú ý" if orange else
                                                   ("⚪ Không chi tiêu" if s["w7"] == 0 else "🟢 Bình thường"))
                if p["kind"] == "Tháng":
                    n_red += level.startswith("🔴")
                    n_or += level.startswith("🟠")
                f.update({"Mức cảnh báo": level, "Cảnh báo": " | ".join(red + orange) or "✅ Bình thường",
                          "Xu hướng": trend, "Spend hôm qua (USD)": round(s["y"], 2), "% so với hôm kia": dchg,
                          "Spend 7 ngày (USD)": round(s["w7"], 2), "TB/ngày 7 ngày (USD)": round(s["avg7"], 2),
                          "Số dư TK (USD)": round(s["bal"], 2) if s["bal"] is not None else None,
                          "Tổng TK trong BC": s["n_all"]})
            rep_rows.append(f)
    c1, u1 = rep.upsert(rep_rows, lambda f: (f[K_KEY],), ex_rep)
    set_current_flags(rep, cur_keys, now_ms)
    note(f"Bang {T_REP}: them moi {c1}, cap nhat {u1} (ky: {', '.join(pmap)}).")

    # ---------- Chi tiet acc ----------
    want = {f[K_KEY] for f in acc_rows}
    ex_acc = {}
    for rid, fr in acc.scan({"field_names": [K_KEY]}):
        k = cell_text(fr.get(K_KEY))
        if k in want:
            ex_acc[k] = rid
    c2, u2 = acc.upsert(acc_rows, lambda f: (f[K_KEY],), ex_acc)
    trim_old(acc, "Ngày", KEEP_DETAIL_DAYS, T_ACC)
    note(f"Bang {T_ACC}: them moi {c2}, cap nhat {u2}.")

    ensure_views(lark, lark.find_table(T_REP), [
        ("Tháng này", [(K_KIND, "Tháng"), (K_CUR, True)]), ("Quý này", [(K_KIND, "Quý"), (K_CUR, True)]),
        ("Theo tháng", [(K_KIND, "Tháng")]), ("Theo quý", [(K_KIND, "Quý")])])
    ensure_views(lark, lark.find_table(T_ACC), [("Tổng theo tháng", []), ("Tổng theo quý", [])])

    audit(acc, tk_mtd, summary, periods[0]["label"])
    note(f"Xong sau {time.time() - t0:.0f}s. Hom qua {end}: {sum(s['y'] for s in summary):,.2f} USD | "
         + " | ".join(f"{p['label']}: {sum(s['per'][p['label']] for s in summary):,.2f} USD" for p in periods)
         + f" | canh bao: {n_red} khan cap, {n_or} can chu y")


def audit(acc, tk_mtd, summary, mlabel):
    """Doi soat: tong dong Ngày trong thang (Base) vs tong thang TikTok; dong trung."""
    recs = acc.scan({"field_names": [K_KEY, "ID BC", "Tháng", "Spend (USD)"]})
    seen, dup, base = set(), 0, {}
    total = len(recs)
    for _, fr in recs:
        k = cell_text(fr.get(K_KEY))
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        if cell_text(fr.get("Tháng")) == mlabel:
            b = cell_text(fr.get("ID BC"))
            base[b] = base.get(b, 0) + float(fr.get("Spend (USD)") or 0)
    bad = []
    for bcid, tk in tk_mtd.items():
        bv = base.get(bcid, 0)
        if abs(bv - tk) > max(1, 0.01 * tk):
            name = next((s["bc"]["name"] for s in summary if s["bc"]["bc_id"] == bcid), bcid)
            bad.append(f"{name}: Base {bv:,.2f} vs TikTok {tk:,.2f}")
    note(f"KIEM TRA: bang {T_ACC} co {total} dong (gioi han 50.000), {dup} dong trung. Tong ngay thang {mlabel} vs TikTok: "
         + ("KHOP tat ca BC." if not bad else f"{len(bad)} BC lech -> " + " | ".join(bad[:10])))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        note(f"LOI: {e}")
        raise
    finally:
        flush_notes()
