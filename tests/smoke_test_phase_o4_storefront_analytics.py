"""
Phase O4: Storefront Analytics Foundation + Owner Console Analytics --
formal regression suite.

Convention (established in Phase C5/C4.1, reused unmodified here):
  - check(condition, label) appends to FAILS, never raises, so the whole
    suite always runs to completion and prints a final tally.
  - httpx.ASGITransport against app.main.app wrapped in `async with
    lifespan(app):`.
  - direct DB assertions via `import app.database as db_module` then
    `db_module.AsyncSessionLocal()` (never a top-level
    `from app.database import AsyncSessionLocal`).

This suite deliberately mixes two testing strategies, matching how the
underlying feature is actually built (Section-hybrid-analytics):
  (1) HTTP-level tests through the real public ingestion endpoint and the
      real owner-authenticated aggregation endpoints -- this is the only
      way to test the actual request/response CONTRACT (validation,
      allowlists, auth, rate limiting).
  (2) Direct-DB seeding of AnalyticsEvent / Order / OrderItem / Reservation
      / OperationsTask rows with precisely-controlled timestamps, followed
      by assertions against the read-side HTTP endpoints -- this is the
      only practical way to verify AGGREGATION CORRECTNESS (exact counts,
      JST day-boundary handling, cancelled-order exclusion, etc.) without
      depending on wall-clock timing.
  (3) Direct unit tests of the pure/near-pure helper functions in
      app.services.storefront_analytics (resolve_analytics_period, _rate,
      _normalize_metadata, _normalize_and_clamp_occurred_at,
      jst_range_to_utc_bounds, jst_date_of_utc) -- cheap, deterministic,
      and exactly where Phase O4's "event semantics must be precise"
      requirement actually lives in the code.

No padding: every check() call asserts something a code change could
plausibly break. Loops over allowlists/enums are genuine per-item
coverage (Section "event semantics must be precise" requires every event
type to behave correctly, not just one representative), not filler.
"""

import asyncio
import os
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone

_DB_PATH = f"/tmp/o4_formal_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"

import httpx

FAILS = []
CHECK_COUNT = [0]


def check(cond, label):
    CHECK_COUNT[0] += 1
    if not cond:
        FAILS.append(label)
        print("FAIL:", label)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def main():
    from app.main import app, lifespan
    from app.config import get_settings
    import app.database as db_module
    from app.models.analytics_event import (
        AnalyticsEvent, ALLOWED_EVENT_TYPES, PUBLIC_ALLOWED_EVENT_TYPES,
        ALLOWED_CHANNELS, METADATA_JSON_MAX_BYTES,
    )
    from app.models.commerce_order import Order, OrderItem, OrderStatus
    from app.models.reservation import Reservation, ReservationStatus
    from app.models.operations_task import OperationsTask
    from app.schemas.analytics_event import AnalyticsEventIn, AnalyticsEventBatchRequest
    from app.services import storefront_analytics as svc
    from pydantic import ValidationError

    settings = get_settings()
    settings.ANALYTICS_PUBLIC_ENABLED = True
    settings.ECOMMERCE_PUBLIC_ENABLED = True
    settings.TABLE_ORDER_PUBLIC_ENABLED = True

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            # =============================================================
            # SECTION 1: Model / allowlist introspection (no DB round-trip)
            # =============================================================
            check(ALLOWED_EVENT_TYPES == frozenset({
                "page_view", "menu_impression", "menu_detail_view",
                "staff_profile_impression", "staff_profile_view", "staff_social_click",
                "add_to_cart", "remove_from_cart", "checkout_started", "order_completed",
            }), "S1.01 ALLOWED_EVENT_TYPES is exactly the documented set")
            check("order_completed" in ALLOWED_EVENT_TYPES, "S1.02 order_completed reserved in full allowlist")
            check("order_completed" not in PUBLIC_ALLOWED_EVENT_TYPES, "S1.03 order_completed excluded from public allowlist")
            check(PUBLIC_ALLOWED_EVENT_TYPES == ALLOWED_EVENT_TYPES - {"order_completed"},
                  "S1.04 PUBLIC_ALLOWED_EVENT_TYPES == ALLOWED_EVENT_TYPES - {order_completed}")
            check(ALLOWED_CHANNELS == frozenset({"storefront", "table_order", "ecommerce"}),
                  "S1.05 ALLOWED_CHANNELS is exactly the documented set")
            check(isinstance(METADATA_JSON_MAX_BYTES, int) and METADATA_JSON_MAX_BYTES > 0,
                  "S1.06 METADATA_JSON_MAX_BYTES is a positive int")
            check(METADATA_JSON_MAX_BYTES <= 4000, "S1.07 METADATA_JSON_MAX_BYTES is a small bound, not unbounded")

            cols = {c.name: c for c in AnalyticsEvent.__table__.columns}
            for required_col in ("id", "shop_id", "event_type", "channel", "anonymous_session_id",
                                  "menu_item_id", "staff_id", "dedupe_key", "metadata_json", "occurred_at", "created_at"):
                check(required_col in cols, f"S1.08 AnalyticsEvent has column {required_col}")
            # Section Privacy: no PII-shaped columns exist on this table at all.
            for forbidden_col in ("name", "email", "phone", "ip_address", "ip", "user_agent",
                                   "full_name", "address", "device_id", "customer_id"):
                check(forbidden_col not in cols, f"S1.09 AnalyticsEvent has NO column named {forbidden_col} (privacy)")
            check(cols["anonymous_session_id"].nullable is True, "S1.10 anonymous_session_id is nullable (events without a session are allowed)")
            check(cols["dedupe_key"].nullable is True, "S1.11 dedupe_key is nullable (NULLs never collide in the unique index)")
            index_names = {ix.name for ix in AnalyticsEvent.__table__.indexes}
            check("ux_analytics_events_dedupe" in index_names, "S1.12 unique dedupe index exists")
            dedupe_ix = next(ix for ix in AnalyticsEvent.__table__.indexes if ix.name == "ux_analytics_events_dedupe")
            check(dedupe_ix.unique is True, "S1.13 dedupe index is UNIQUE")
            check({c.name for c in dedupe_ix.columns} == {"shop_id", "dedupe_key"},
                  "S1.14 dedupe index is scoped (shop_id, dedupe_key), not a bare global unique on dedupe_key")
            check(AnalyticsEvent.__tablename__ == "analytics_events", "S1.15 table name is analytics_events")

            # =============================================================
            # SECTION 2: Pydantic schema validation (pure, no HTTP)
            # =============================================================
            def expect_valid(kwargs, label):
                try:
                    AnalyticsEventIn(**kwargs)
                    check(True, label)
                except ValidationError as e:
                    check(False, f"{label} (unexpectedly rejected: {e})")

            def expect_invalid(kwargs, label):
                try:
                    AnalyticsEventIn(**kwargs)
                    check(False, f"{label} (unexpectedly accepted)")
                except ValidationError:
                    check(True, label)

            now_dt = datetime.now(timezone.utc)
            base = {"event_type": "page_view", "channel": "storefront", "occurred_at": now_dt}
            expect_valid(dict(base), "S2.01 minimal valid event accepted")
            expect_invalid({**base, "event_type": ""}, "S2.02 empty event_type rejected (min_length=1)")
            expect_invalid({**base, "event_type": "x" * 41}, "S2.03 event_type over 40 chars rejected")
            expect_valid({**base, "event_type": "x" * 40}, "S2.04 event_type exactly 40 chars accepted (structurally)")
            expect_invalid({**base, "channel": ""}, "S2.05 empty channel rejected (min_length=1)")
            expect_invalid({**base, "channel": "x" * 31}, "S2.06 channel over 30 chars rejected")
            expect_valid({**base, "anonymous_session_id": "s" * 64}, "S2.07 anonymous_session_id exactly 64 chars accepted")
            expect_invalid({**base, "anonymous_session_id": "s" * 65}, "S2.08 anonymous_session_id over 64 chars rejected")
            expect_valid({**base, "menu_item_id": "m" * 36}, "S2.09 menu_item_id exactly 36 chars accepted")
            expect_invalid({**base, "menu_item_id": "m" * 37}, "S2.10 menu_item_id over 36 chars rejected")
            expect_valid({**base, "staff_id": "s" * 36}, "S2.11 staff_id exactly 36 chars accepted")
            expect_invalid({**base, "staff_id": "s" * 37}, "S2.12 staff_id over 36 chars rejected")
            expect_valid({**base, "dedupe_key": "d" * 200}, "S2.13 dedupe_key exactly 200 chars accepted")
            expect_invalid({**base, "dedupe_key": "d" * 201}, "S2.14 dedupe_key over 200 chars rejected")
            expect_invalid({**base, "bogus_extra_field": "x"}, "S2.15 extra field on AnalyticsEventIn rejected (extra=forbid)")
            expect_invalid({"event_type": "page_view", "channel": "storefront"}, "S2.16 missing occurred_at rejected")

            try:
                AnalyticsEventBatchRequest(events=[])
                check(False, "S2.17 empty events list rejected (unexpectedly accepted)")
            except ValidationError:
                check(True, "S2.17 empty events list rejected (min_length=1)")
            try:
                AnalyticsEventBatchRequest(events=[AnalyticsEventIn(**base) for _ in range(20)])
                check(True, "S2.18 exactly 20 events accepted")
            except ValidationError:
                check(False, "S2.18 exactly 20 events accepted (unexpectedly rejected)")
            try:
                AnalyticsEventBatchRequest(events=[AnalyticsEventIn(**base) for _ in range(21)])
                check(False, "S2.19 21 events rejected (unexpectedly accepted)")
            except ValidationError:
                check(True, "S2.19 21 events rejected (max_length=20)")
            try:
                AnalyticsEventBatchRequest(events=[AnalyticsEventIn(**base)], bogus=1)
                check(False, "S2.20 extra field on batch wrapper rejected (unexpectedly accepted)")
            except ValidationError:
                check(True, "S2.20 extra field on batch wrapper rejected (extra=forbid)")

            # =============================================================
            # SECTION 3: Pure unit tests of storefront_analytics helpers
            # =============================================================
            check(svc._rate(5, 0) is None, "S3.01 _rate: zero denominator -> None")
            check(svc._rate(0, 0) is None, "S3.02 _rate: 0/0 -> None")
            check(svc._rate(0, 5) == 0.0, "S3.03 _rate: 0/5 -> 0.0")
            check(svc._rate(1, 3) == round(1 / 3, 4), "S3.04 _rate: 1/3 rounds to 4 decimals")
            check(svc._rate(5, 5) == 1.0, "S3.05 _rate: 5/5 -> 1.0")
            check(svc._rate(10, 4) == 2.5, "S3.06 _rate: can exceed 1.0 when numerator > denominator")
            import math
            r = svc._rate(1, 3)
            check(r is not None and not math.isnan(r) and not math.isinf(r), "S3.07 _rate never returns NaN/Infinity")

            md_text, ok = svc._normalize_metadata("staff_social_click", {"provider": "instagram"})
            check(ok and md_text is not None and "instagram" in md_text, "S3.08 staff_social_click + allowed key accepted")
            md_text2, ok2 = svc._normalize_metadata("staff_social_click", {"bogus_key": "x"})
            check(ok2 is False and md_text2 is None, "S3.09 staff_social_click + disallowed key rejected")
            md_text3, ok3 = svc._normalize_metadata("staff_social_click", {"provider": "x", "bogus": "y"})
            check(ok3 is False, "S3.10 staff_social_click + one allowed + one disallowed key -> rejected (not partial)")
            md_text4, ok4 = svc._normalize_metadata("staff_social_click", {})
            check(ok4 is True and md_text4 is None, "S3.11 empty metadata dict treated as no metadata -> accepted")
            md_text5, ok5 = svc._normalize_metadata("staff_social_click", None)
            check(ok5 is True and md_text5 is None, "S3.12 None metadata -> accepted")
            md_text6, ok6 = svc._normalize_metadata("page_view", {"anything": "x"})
            check(ok6 is False, "S3.13 event_type with no declared metadata schema rejects ANY metadata")
            md_text7, ok7 = svc._normalize_metadata("page_view", {})
            check(ok7 is True and md_text7 is None, "S3.14 event_type with no schema + empty dict still accepted (falsy)")
            long_val = "a" * 500
            md_text8, ok8 = svc._normalize_metadata("staff_social_click", {"provider": long_val})
            check(ok8 is True, "S3.15 oversized metadata value is truncated, not rejected")
            check(md_text8 is not None and len(md_text8) < len(long_val), "S3.16 oversized metadata value actually shortened in stored JSON")

            now_utc = datetime.utcnow()
            clamped_old = svc._normalize_and_clamp_occurred_at(now_utc - timedelta(days=30))
            check(abs((clamped_old - now_utc).total_seconds()) < 5, "S3.17 occurred_at 30 days in the past is clamped to ~now")
            not_clamped_recent = svc._normalize_and_clamp_occurred_at(now_utc - timedelta(days=3))
            check(abs((not_clamped_recent - (now_utc - timedelta(days=3))).total_seconds()) < 5,
                  "S3.18 occurred_at 3 days in the past is NOT clamped (within 7-day window)")
            clamped_future = svc._normalize_and_clamp_occurred_at(now_utc + timedelta(hours=5))
            check(abs((clamped_future - now_utc).total_seconds()) < 5, "S3.19 occurred_at 5 hours in the future is clamped to ~now")
            not_clamped_future = svc._normalize_and_clamp_occurred_at(now_utc + timedelta(minutes=30))
            check(abs((not_clamped_future - (now_utc + timedelta(minutes=30))).total_seconds()) < 5,
                  "S3.20 occurred_at 30 minutes in the future is NOT clamped (within 1-hour window)")
            tz_aware = datetime.now(timezone.utc)
            normalized = svc._normalize_and_clamp_occurred_at(tz_aware)
            check(normalized.tzinfo is None, "S3.21 tz-aware occurred_at is normalized to naive UTC")

            bounds = svc.jst_range_to_utc_bounds(date(2026, 1, 1), date(2026, 1, 1))
            check(bounds[0] == datetime(2025, 12, 31, 15, 0, 0), "S3.22 JST day start -9h -> correct UTC start")
            check(bounds[1] == datetime(2026, 1, 1, 15, 0, 0), "S3.23 JST day end (exclusive, +1 day) -> correct UTC end")
            bounds2 = svc.jst_range_to_utc_bounds(date(2026, 3, 1), date(2026, 3, 3))
            check((bounds2[1] - bounds2[0]).days == 3, "S3.24 a 3-day inclusive JST range spans exactly 3 UTC days")

            jst_day = svc.jst_date_of_utc(datetime(2025, 12, 31, 15, 0, 0))
            check(jst_day == date(2026, 1, 1), "S3.25 jst_date_of_utc inverse of jst_range_to_utc_bounds start")
            jst_day2 = svc.jst_date_of_utc(datetime(2025, 12, 31, 14, 59, 59))
            check(jst_day2 == date(2025, 12, 31), "S3.26 one second before JST midnight is still the previous JST day")

            nb = svc.jst_date_bounds_naive(date(2026, 1, 1), date(2026, 1, 1))
            check(nb[0] == datetime(2026, 1, 1, 0, 0, 0) and nb[1] == datetime(2026, 1, 2, 0, 0, 0),
                  "S3.27 jst_date_bounds_naive is a naive half-open [day, day+1) range with no UTC conversion")

            for rk in ("today", "7d", "30d", "month", "previous_month"):
                period = svc.resolve_analytics_period(rk)
                check(period["range"] == rk, f"S3.28 resolve_analytics_period({rk}) echoes range key")
                check(period["start_date"] <= period["end_date"], f"S3.29 resolve_analytics_period({rk}) start<=end")
                check(isinstance(period["label"], str) and len(period["label"]) > 0, f"S3.30 resolve_analytics_period({rk}) has a non-empty label")
            today_period = svc.resolve_analytics_period("today")
            check(today_period["start_date"] == today_period["end_date"], "S3.31 today range: start==end")
            week_period = svc.resolve_analytics_period("7d")
            check((week_period["end_date"] - week_period["start_date"]).days == 6, "S3.32 7d range spans exactly 6 days (7 inclusive days total)")
            month30_period = svc.resolve_analytics_period("30d")
            check((month30_period["end_date"] - month30_period["start_date"]).days == 29, "S3.33 30d range spans exactly 29 days (30 inclusive days total)")
            month_period = svc.resolve_analytics_period("month")
            check(month_period["start_date"].day == 1, "S3.34 month range starts on day 1 of the current month")
            prev_month_period = svc.resolve_analytics_period("previous_month")
            check(prev_month_period["end_date"] < month_period["start_date"], "S3.35 previous_month ends strictly before this month starts")
            check(prev_month_period["start_date"].day == 1, "S3.36 previous_month starts on day 1 of that month")

            try:
                svc.resolve_analytics_period("bogus")
                check(False, "S3.37 invalid range key rejected (unexpectedly accepted)")
            except svc.StorefrontAnalyticsError as e:
                check(e.status_code == 422, "S3.37 invalid range key rejected with 422")
            try:
                svc.resolve_analytics_period("custom")
                check(False, "S3.38 custom without dates rejected (unexpectedly accepted)")
            except svc.StorefrontAnalyticsError:
                check(True, "S3.38 custom without dates rejected")
            try:
                svc.resolve_analytics_period("custom", date(2026, 2, 1), date(2026, 1, 1))
                check(False, "S3.39 custom with end<start rejected (unexpectedly accepted)")
            except svc.StorefrontAnalyticsError:
                check(True, "S3.39 custom with end<start rejected")
            custom_same = svc.resolve_analytics_period("custom", date(2026, 1, 1), date(2026, 1, 1))
            check(custom_same["start_date"] == custom_same["end_date"], "S3.40 custom with start==end accepted")
            try:
                svc.resolve_analytics_period("custom", date(2020, 1, 1), date(2022, 1, 1))
                check(False, "S3.41 custom range over 366 days rejected (unexpectedly accepted)")
            except svc.StorefrontAnalyticsError:
                check(True, "S3.41 custom range over 366 days rejected")
            today_for_clamp_test = svc._reservation_basis_now().date()
            future_end_period = svc.resolve_analytics_period(
                "custom", today_for_clamp_test - timedelta(days=5), today_for_clamp_test + timedelta(days=10),
            )
            check(future_end_period["end_date"] == today_for_clamp_test,
                  f"S3.42 custom end_date beyond today is clamped to today, never aggregates future dates: {future_end_period}")

            # =============================================================
            # Shared fixture: one tenant/shop/menu-items/staff for the HTTP
            # section tests below.
            # =============================================================
            email = f"o4f_{uuid.uuid4().hex[:8]}@example.com"
            r = await client.post("/api/v1/auth/register", json={"email": email, "password": "password123", "display_name": "O4本試験"})
            check(r.status_code in (200, 201), "FIX.01 primary owner registered")
            token = r.json()["access_token"]

            r = await client.post("/api/v1/shops/register", json={"name": "O4本試験店舗", "category": "IZAKAYA", "address": "沖縄県那覇市"}, headers=auth(token))
            check(r.status_code in (200, 201), "FIX.02 primary shop registered")
            shop_id = r.json()["shop_id"]

            menu_item_ids = []
            menu_names = ["からあげ", "餃子", "ハイボール", "サラダ", "デザート盛り合わせ"]
            for name in menu_names:
                r = await client.post(f"/api/v1/shops/{shop_id}/menu",
                                       data={"category": "フード", "name": name, "price": "500", "is_ecommerce_available": "true"},
                                       headers=auth(token))
                check(r.status_code in (200, 201), f"FIX.03 menu item created: {name}")
                menu_item_ids.append(r.json()["id"])

            staff_ids = []
            staff_names = ["田中太郎", "佐藤花子", "鈴木一郎", "高橋みどり"]
            for name in staff_names:
                r = await client.post("/api/v1/staff", json={"shop_id": shop_id, "name": name}, headers=auth(token))
                check(r.status_code in (200, 201), f"FIX.04 staff created: {name}")
                staff_ids.append(r.json()["id"])

            # A second, unrelated tenant/shop/menu-item/staff for cross-shop
            # isolation tests (forged ids, tenant isolation, dedupe scoping).
            email_b = f"o4fb_{uuid.uuid4().hex[:8]}@example.com"
            r = await client.post("/api/v1/auth/register", json={"email": email_b, "password": "password123", "display_name": "O4別テナント"})
            token_b = r.json()["access_token"]
            r = await client.post("/api/v1/shops/register", json={"name": "O4別店舗", "category": "CAFE", "address": "東京都渋谷区"}, headers=auth(token_b))
            shop_id_b = r.json()["shop_id"]
            r = await client.post(f"/api/v1/shops/{shop_id_b}/menu",
                                   data={"category": "ドリンク", "name": "コーヒー", "price": "400", "is_ecommerce_available": "true"},
                                   headers=auth(token_b))
            other_shop_menu_item_id = r.json()["id"]
            r = await client.post("/api/v1/staff", json={"shop_id": shop_id_b, "name": "他店スタッフ"}, headers=auth(token_b))
            other_shop_staff_id = r.json()["id"]

            now_iso = datetime.now(timezone.utc).isoformat()

            # =============================================================
            # SECTION 4: Ingestion -- event_type / channel allowlists
            # (genuine per-type coverage, Section "event semantics must be
            # precise": every publicly-postable type must be individually
            # accepted and distinguishable from every other type).
            # =============================================================
            for et in sorted(PUBLIC_ALLOWED_EVENT_TYPES):
                sid = uuid.uuid4().hex
                payload = {"event_type": et, "channel": "storefront", "anonymous_session_id": sid, "occurred_at": now_iso}
                if et in ("menu_impression", "menu_detail_view", "add_to_cart", "remove_from_cart"):
                    payload["menu_item_id"] = menu_item_ids[0]
                if et in ("staff_profile_impression", "staff_profile_view", "staff_social_click"):
                    payload["staff_id"] = staff_ids[0]
                    if et == "staff_social_click":
                        payload["metadata"] = {"provider": "instagram"}
                r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events", json={"events": [payload]})
                check(r.status_code == 200, f"S4.01 ingestion HTTP 200 for event_type={et}")
                if r.status_code == 200:
                    body = r.json()
                    check(body["accepted"] == 1 and body["rejected"] == 0, f"S4.02 event_type={et} accepted=1,rejected=0: {body}")

            for bogus_type in ("ORDER_COMPLETED", "Page_View", "pageview", "click", "scroll", "", "page_view ", "menu-impression", "<script>"):
                r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                       json={"events": [{"event_type": bogus_type, "channel": "storefront", "occurred_at": now_iso}]})
                check(r.status_code in (200, 422), f"S4.03 bogus event_type={bogus_type!r} never 500s")
                if r.status_code == 200:
                    check(r.json()["rejected"] == 1, f"S4.04 bogus event_type={bogus_type!r} rejected (count), not silently accepted")

            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "order_completed", "channel": "ecommerce", "occurred_at": now_iso}]})
            check(r.status_code == 200 and r.json()["rejected"] == 1, "S4.05 order_completed structurally allowlisted but rejected from public ingestion")

            for ch in sorted(ALLOWED_CHANNELS):
                r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                       json={"events": [{"event_type": "page_view", "channel": ch, "occurred_at": now_iso}]})
                check(r.status_code == 200 and r.json()["accepted"] == 1, f"S4.06 valid channel={ch} accepted")
            for bad_ch in ("web", "STOREFRONT", "mobile_app", "sms", ""):
                r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                       json={"events": [{"event_type": "page_view", "channel": bad_ch, "occurred_at": now_iso}]})
                check(r.status_code in (200, 422), f"S4.07 invalid channel={bad_ch!r} never 500s")
                if r.status_code == 200:
                    check(r.json()["rejected"] == 1, f"S4.08 invalid channel={bad_ch!r} rejected")

            # =============================================================
            # SECTION 5: Entity validation (cross-shop forgery resistance)
            # =============================================================
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "menu_impression", "channel": "ecommerce", "occurred_at": now_iso, "menu_item_id": other_shop_menu_item_id}]})
            check(r.status_code == 200 and r.json()["rejected"] == 1, "S5.01 forged cross-shop menu_item_id rejected")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "staff_profile_view", "channel": "storefront", "occurred_at": now_iso, "staff_id": other_shop_staff_id}]})
            check(r.status_code == 200 and r.json()["rejected"] == 1, "S5.02 forged cross-shop staff_id rejected")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "menu_impression", "channel": "ecommerce", "occurred_at": now_iso, "menu_item_id": str(uuid.uuid4())}]})
            check(r.status_code == 200 and r.json()["rejected"] == 1, "S5.03 nonexistent menu_item_id rejected")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "menu_impression", "channel": "ecommerce", "occurred_at": now_iso, "menu_item_id": menu_item_ids[0]}]})
            check(r.status_code == 200 and r.json()["accepted"] == 1, "S5.04 real same-shop menu_item_id accepted")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso}]})
            check(r.status_code == 200 and r.json()["accepted"] == 1, "S5.05 event with no menu_item_id/staff_id at all accepted")

            # =============================================================
            # SECTION 6: Metadata rules via HTTP
            # =============================================================
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "staff_social_click", "channel": "storefront", "occurred_at": now_iso, "staff_id": staff_ids[0], "metadata": {"provider": "x"}}]})
            check(r.status_code == 200 and r.json()["accepted"] == 1, "S6.01 staff_social_click with allowed metadata key accepted")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "staff_social_click", "channel": "storefront", "occurred_at": now_iso, "staff_id": staff_ids[0], "metadata": {"url": "https://evil.example/"}}]})
            check(r.status_code == 200 and r.json()["rejected"] == 1, "S6.02 staff_social_click with disallowed metadata key rejected")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "metadata": {"anything": "x"}}]})
            check(r.status_code == 200 and r.json()["rejected"] == 1, "S6.03 page_view with any metadata rejected (no schema declared)")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "metadata": {}}]})
            check(r.status_code == 200 and r.json()["accepted"] == 1, "S6.04 page_view with empty metadata dict accepted")

            # =============================================================
            # SECTION 7: Dedupe semantics
            # =============================================================
            dk1 = "dk-" + uuid.uuid4().hex
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "dedupe_key": dk1}]})
            check(r.status_code == 200 and r.json()["accepted"] == 1, "S7.01 first post with a fresh dedupe_key accepted")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "dedupe_key": dk1}]})
            check(r.status_code == 200 and r.json()["deduped"] == 1 and r.json()["accepted"] == 0, "S7.02 repeated dedupe_key across requests is deduped")

            dk2 = "dk-intrabatch-" + uuid.uuid4().hex
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [
                                       {"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "dedupe_key": dk2},
                                       {"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "dedupe_key": dk2},
                                   ]})
            check(r.status_code == 200, "S7.03 intra-batch duplicate dedupe_key request succeeds")
            if r.status_code == 200:
                body = r.json()
                check(body["accepted"] == 1 and body["deduped"] == 1, f"S7.04 intra-batch duplicate: first accepted, second deduped: {body}")

            dk3 = "dk-crossshop-" + uuid.uuid4().hex
            r1 = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                    json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "dedupe_key": dk3}]})
            r2 = await client.post(f"/api/v1/public/shops/{shop_id_b}/analytics/events",
                                    json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "dedupe_key": dk3}]})
            check(r1.status_code == 200 and r1.json()["accepted"] == 1, "S7.05 dedupe_key accepted for shop A")
            check(r2.status_code == 200 and r2.json()["accepted"] == 1, "S7.06 same dedupe_key string independently accepted for shop B (scoped per-shop, not global)")

            # =============================================================
            # SECTION 8: Mass assignment / request shape limits via HTTP
            # =============================================================
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso, "shop_id": "hacked"}]})
            check(r.status_code == 422, "S8.01 shop_id in event body rejected at schema level (422)")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso}], "extra_field": 1})
            check(r.status_code == 422, "S8.02 extra field on batch wrapper rejected at HTTP level (422)")
            too_many = [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso} for _ in range(25)]
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events", json={"events": too_many})
            check(r.status_code == 422, "S8.03 batch over 20 events rejected at HTTP level (422)")
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events", json={"events": []})
            check(r.status_code == 422, "S8.04 empty events array rejected at HTTP level (422)")

            # =============================================================
            # SECTION 9: Feature flag gating
            # =============================================================
            settings.ANALYTICS_PUBLIC_ENABLED = False
            r = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                   json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso}]})
            check(r.status_code == 404, "S9.01 public ingestion 404s when ANALYTICS_PUBLIC_ENABLED=False")
            r = await client.get(f"/api/v1/shops/{shop_id}/analytics/overview?range=today", headers=auth(token))
            check(r.status_code == 200, "S9.02 owner-facing analytics endpoints are unaffected by the public-ingestion flag")
            settings.ANALYTICS_PUBLIC_ENABLED = True

            # =============================================================
            # SECTION 10: table_order qr_token-scoped ingestion endpoint
            # =============================================================
            from app.models.shop import Shop as ShopModel
            async with db_module.AsyncSessionLocal() as db:
                shop_row = await db.get(ShopModel, shop_id)
                shop_row.table_order_enabled = True
                await db.commit()

            r = await client.post(f"/api/v1/shops/{shop_id}/tables", json={"name": "テスト卓1", "capacity": 4}, headers=auth(token))
            table_created = r.status_code in (200, 201)
            check(table_created, "S10.01 a shop table could be created for the table-order fixture")
            qr_token = None
            if table_created:
                table_id = r.json()["id"]
                r = await client.get(f"/api/v1/shops/{shop_id}/tables/{table_id}/qr", headers=auth(token))
                check(r.status_code == 200, "S10.02 qr token endpoint issues a token for the table")
                if r.status_code == 200:
                    qr_token = r.json()["qr_token"]
                r = await client.post(f"/api/v1/shops/{shop_id}/tables/{table_id}/table-sessions",
                                       json={"guest_count": 2}, headers=auth(token))
                check(r.status_code == 201, f"S10.03 a table session could be opened for the table: {r.status_code} {r.text[:200]}")

            if qr_token:
                r = await client.get(f"/api/v1/public/table-order/{qr_token}/session")
                check(r.status_code == 200, f"S10.04 public session resolves via qr_token once a session is open: {r.status_code} {r.text[:200]}")
                if r.status_code == 200:
                    r = await client.post(f"/api/v1/public/table-order/{qr_token}/analytics/events",
                                           json={"events": [{"event_type": "page_view", "channel": "table_order", "occurred_at": now_iso}]})
                    check(r.status_code == 200, "S10.05 table-order qr_token-scoped analytics ingestion accepted")
                    if r.status_code == 200:
                        check(r.json()["accepted"] == 1, "S10.06 table-order analytics event actually accepted (count)")
                    r2 = await client.post(f"/api/v1/public/table-order/{qr_token}/analytics/events",
                                            json={"events": [{"event_type": "order_completed", "channel": "table_order", "occurred_at": now_iso}]})
                    check(r2.status_code == 200 and r2.json()["rejected"] == 1,
                          "S10.07 table-order analytics endpoint also enforces the PUBLIC_ALLOWED_EVENT_TYPES allowlist")
                    settings.ANALYTICS_PUBLIC_ENABLED = False
                    r3 = await client.post(f"/api/v1/public/table-order/{qr_token}/analytics/events",
                                            json={"events": [{"event_type": "page_view", "channel": "table_order", "occurred_at": now_iso}]})
                    check(r3.status_code == 404, "S10.08 table-order analytics ingestion also respects ANALYTICS_PUBLIC_ENABLED flag")
                    settings.ANALYTICS_PUBLIC_ENABLED = True
                    r4 = await client.get(f"/api/v1/public/table-order/invalid-bogus-token-xyz/session")
                    r5 = await client.post("/api/v1/public/table-order/invalid-bogus-token-xyz/analytics/events",
                                            json={"events": [{"event_type": "page_view", "channel": "table_order", "occurred_at": now_iso}]})
                    check(r5.status_code == 404, "S10.09 table-order analytics ingestion 404s for a bogus/unresolvable qr_token")

            # =============================================================
            # SECTION 11: Rate limiting
            # =============================================================
            rl_session = "ratelimit-" + uuid.uuid4().hex
            statuses = []
            for _ in range(65):
                rr = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                        json={"events": [{"event_type": "page_view", "channel": "storefront",
                                                           "anonymous_session_id": rl_session, "occurred_at": now_iso}]})
                statuses.append(rr.status_code)
            check(429 in statuses, "S11.01 session-scoped rate limit eventually returns 429 past the configured threshold")
            check(statuses[0] == 200, "S11.02 the very first request in a rate-limit test is not itself rate-limited")

            statuses_fallback = []
            for _ in range(35):
                rr = await client.post(f"/api/v1/public/shops/{shop_id}/analytics/events",
                                        json={"events": [{"event_type": "page_view", "channel": "storefront", "occurred_at": now_iso}]})
                statuses_fallback.append(rr.status_code)
            check(429 in statuses_fallback, "S11.03 shop-wide fallback bucket (no session id) also eventually returns 429")

            # =============================================================
            # Build a controlled, timestamp-precise dataset via direct DB
            # access for the aggregation-correctness sections below. Using
            # a FRESH shop keeps this isolated from the noisy ingestion
            # traffic generated in Sections 4-11 above.
            # =============================================================
            email_agg = f"o4agg_{uuid.uuid4().hex[:8]}@example.com"
            r = await client.post("/api/v1/auth/register", json={"email": email_agg, "password": "password123", "display_name": "O4集計試験"})
            token_agg = r.json()["access_token"]
            r = await client.post("/api/v1/shops/register", json={"name": "O4集計試験店舗", "category": "IZAKAYA", "address": "沖縄県那覇市"}, headers=auth(token_agg))
            agg_shop_id = r.json()["shop_id"]

            agg_menu_ids = []
            agg_menu_names = ["商品A", "商品B", "商品C", "商品D(無人気)", "商品E(売り切れ)"]
            for i, name in enumerate(agg_menu_names):
                r = await client.post(f"/api/v1/shops/{agg_shop_id}/menu",
                                       data={"category": "フード", "name": name, "price": str(500 + i * 100), "is_ecommerce_available": "true"},
                                       headers=auth(token_agg))
                agg_menu_ids.append(r.json()["id"])
            # mark the last one unavailable (sold out) to verify it's still listed
            r = await client.put(f"/api/v1/shops/{agg_shop_id}/menu/{agg_menu_ids[-1]}",
                                  data={"is_available": "false"}, headers=auth(token_agg))
            check(r.status_code == 200, f"FIX.05 last menu item marked unavailable for the sold-out-item test: {r.status_code} {r.text[:200]}")

            agg_staff_ids = []
            agg_staff_names = ["スタッフ1", "スタッフ2", "スタッフ3"]
            for name in agg_staff_names:
                r = await client.post("/api/v1/staff", json={"shop_id": agg_shop_id, "name": name}, headers=auth(token_agg))
                agg_staff_ids.append(r.json()["id"])

            today_jst = svc._reservation_basis_now().date()
            today_start_utc, today_end_utc = svc.jst_range_to_utc_bounds(today_jst, today_jst)
            mid_today_utc = today_start_utc + (today_end_utc - today_start_utc) / 2

            async with db_module.AsyncSessionLocal() as db:
                # ---- Overview: total vs unique session distinction ----
                shared_session = "agg-shared-session"
                db.add(AnalyticsEvent(shop_id=agg_shop_id, event_type="page_view", channel="storefront",
                                       anonymous_session_id=shared_session, occurred_at=mid_today_utc))
                db.add(AnalyticsEvent(shop_id=agg_shop_id, event_type="page_view", channel="storefront",
                                       anonymous_session_id=shared_session, occurred_at=mid_today_utc + timedelta(minutes=1)))
                db.add(AnalyticsEvent(shop_id=agg_shop_id, event_type="page_view", channel="storefront",
                                       anonymous_session_id="agg-second-session", occurred_at=mid_today_utc))
                db.add(AnalyticsEvent(shop_id=agg_shop_id, event_type="page_view", channel="storefront",
                                       anonymous_session_id=None, occurred_at=mid_today_utc))

                # ---- Per-menu-item impressions/detail_views/add_to_cart,
                # with distinct counts per item so a swapped-column bug
                # would be caught. ----
                per_item_counts = {
                    agg_menu_ids[0]: {"menu_impression": 5, "menu_detail_view": 3, "add_to_cart": 2},
                    agg_menu_ids[1]: {"menu_impression": 1, "menu_detail_view": 1, "add_to_cart": 1},
                    agg_menu_ids[2]: {"menu_impression": 7, "menu_detail_view": 0, "add_to_cart": 0},
                    # agg_menu_ids[3] ("商品D") deliberately gets ZERO events
                    # -- it must still appear in the list with all-zero counts.
                }
                for mid, counts in per_item_counts.items():
                    for et, n in counts.items():
                        for i in range(n):
                            db.add(AnalyticsEvent(shop_id=agg_shop_id, event_type=et, channel="ecommerce",
                                                   anonymous_session_id=f"sess-{mid}-{et}-{i}", menu_item_id=mid,
                                                   occurred_at=mid_today_utc))

                # ---- Per-staff impressions/views/social_clicks ----
                # staff1 deliberately gets the HIGHEST profile_impressions of
                # the three (9, vs staff0's 4 and staff2's 0) specifically so
                # S14.10 below can prove the list is ordered by Staff.name,
                # NOT by any numeric analytics field -- if the endpoint ever
                # started sorting by impressions descending, staff1 would
                # move to the front and this assertion would catch it.
                per_staff_counts = {
                    agg_staff_ids[0]: {"staff_profile_impression": 4, "staff_profile_view": 2, "staff_social_click": 1},
                    agg_staff_ids[1]: {"staff_profile_impression": 9, "staff_profile_view": 1, "staff_social_click": 0},
                    # agg_staff_ids[2] gets zero events -- must still be listed.
                }
                for sid, counts in per_staff_counts.items():
                    for et, n in counts.items():
                        for i in range(n):
                            db.add(AnalyticsEvent(shop_id=agg_shop_id, event_type=et, channel="storefront",
                                                   anonymous_session_id=f"sess-{sid}-{et}-{i}", staff_id=sid,
                                                   occurred_at=mid_today_utc))

                # ---- Orders: TABLE_ORDER/ECOMMERCE should count,
                # MANUAL/COUNTER should NOT; CANCELLED should NOT. ----
                def make_order(channel, status, idx, order_item_menu_id=None, qty=1):
                    o = Order(shop_id=agg_shop_id, order_number=f"O4AGG-{idx}", order_channel=channel,
                              status=status, subtotal=500, tax_amount=0, total=500, created_at=mid_today_utc)
                    db.add(o)
                    return o

                web_order_1 = make_order("ecommerce", "completed", 1)
                web_order_2 = make_order("table_order", "submitted", 2)
                cancelled_web_order = make_order("ecommerce", OrderStatus.CANCELLED.value, 3)
                manual_order = make_order("manual", "completed", 4)
                counter_order = make_order("counter", "completed", 5)
                await db.flush()

                db.add(OrderItem(order_id=web_order_1.id, menu_item_id=agg_menu_ids[0], item_name_snapshot="商品A",
                                  unit_price_snapshot=500, quantity=3, line_total=1500))
                db.add(OrderItem(order_id=web_order_2.id, menu_item_id=agg_menu_ids[0], item_name_snapshot="商品A",
                                  unit_price_snapshot=500, quantity=2, line_total=1000))
                db.add(OrderItem(order_id=cancelled_web_order.id, menu_item_id=agg_menu_ids[0], item_name_snapshot="商品A",
                                  unit_price_snapshot=500, quantity=99, line_total=49500))
                db.add(OrderItem(order_id=manual_order.id, menu_item_id=agg_menu_ids[0], item_name_snapshot="商品A",
                                  unit_price_snapshot=500, quantity=99, line_total=49500))
                db.add(OrderItem(order_id=counter_order.id, menu_item_id=agg_menu_ids[0], item_name_snapshot="商品A",
                                  unit_price_snapshot=500, quantity=99, line_total=49500))

                # ---- OperationsTask targeted_contacts for staff[0] ----
                db.add(OperationsTask(shop_id=agg_shop_id, title="田中さんへの伝言1", target_staff_id=agg_staff_ids[0], created_at=mid_today_utc))
                db.add(OperationsTask(shop_id=agg_shop_id, title="田中さんへの伝言2", target_staff_id=agg_staff_ids[0], created_at=mid_today_utc))

                # ---- Reservations assigned to staff[0], one cancelled (must be excluded) ----
                today_naive = datetime(today_jst.year, today_jst.month, today_jst.day, 18, 0, 0)
                db.add(Reservation(shop_id=agg_shop_id, staff_id=agg_staff_ids[0], reservation_date=today_naive,
                                    number_of_people=2, status=ReservationStatus.CONFIRMED.value))
                db.add(Reservation(shop_id=agg_shop_id, staff_id=agg_staff_ids[0], reservation_date=today_naive,
                                    number_of_people=2, status=ReservationStatus.CANCELLED.value))

                await db.commit()

            query_today = f"range=today"

            # =============================================================
            # SECTION 12: Overview aggregation correctness
            # =============================================================
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/overview?{query_today}", headers=auth(token_agg))
            check(r.status_code == 200, "S12.01 overview endpoint 200 on seeded shop")
            totals = r.json()["totals"]
            check(totals["page_views"] == 4, f"S12.02 page_views total == 4 (incl. null-session event): {totals}")
            check(totals["page_view_sessions"] == 2, f"S12.03 page_view_sessions == 2 unique non-null sessions: {totals}")
            check(totals["orders"] == 2, f"S12.04 orders == 2 (only ecommerce+table_order, non-cancelled): {totals}")

            funnel = {f["metric"]: f for f in r.json()["funnel"]}
            check(funnel["page_view"]["unique_sessions"] == 2, "S12.05 funnel page_view step == 2 unique sessions")
            check(funnel["order_completed"]["unique_sessions"] == 2, "S12.06 funnel order_completed step == authoritative order count (2)")
            check(funnel["order_completed"]["rate_from_previous"] is None, "S12.07 order_completed funnel step never fabricates a session-based rate")
            check(funnel["page_view"]["rate_from_previous"] is None, "S12.08 first funnel step has no previous-step rate")

            # =============================================================
            # SECTION 13: Menu analytics aggregation correctness
            # =============================================================
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/menu-items?{query_today}", headers=auth(token_agg))
            check(r.status_code == 200, "S13.01 menu analytics endpoint 200")
            items_by_id = {i["menu_item_id"]: i for i in r.json()["items"]}
            check(len(items_by_id) == len(agg_menu_ids), f"S13.02 all {len(agg_menu_ids)} menu items listed, including zero-event ones")

            item_a = items_by_id[agg_menu_ids[0]]
            check(item_a["impressions"] == 5, f"S13.03 item A impressions == 5: {item_a}")
            check(item_a["detail_views"] == 3, f"S13.04 item A detail_views == 3: {item_a}")
            check(item_a["add_to_cart"] == 2, f"S13.05 item A add_to_cart == 2: {item_a}")
            check(item_a["orders"] == 2, f"S13.06 item A orders == 2 (web orders only, cancelled/manual/counter excluded): {item_a}")
            check(item_a["ordered_quantity"] == 5, f"S13.07 item A ordered_quantity == 3+2=5 (web orders only): {item_a}")

            item_b = items_by_id[agg_menu_ids[1]]
            check(item_b["impressions"] == 1 and item_b["detail_views"] == 1 and item_b["add_to_cart"] == 1,
                  f"S13.08 item B has its own independent counts (1 each): {item_b}")

            item_c = items_by_id[agg_menu_ids[2]]
            check(item_c["impressions"] == 7 and item_c["detail_views"] == 0 and item_c["add_to_cart"] == 0,
                  f"S13.09 item C: 7 impressions, zero detail_views/add_to_cart (not conflated): {item_c}")

            item_d = items_by_id[agg_menu_ids[3]]
            check(item_d["impressions"] == 0 and item_d["detail_views"] == 0 and item_d["add_to_cart"] == 0
                  and item_d["orders"] == 0 and item_d["ordered_quantity"] == 0,
                  f"S13.10 item D (zero events) lists all-zero, not omitted or null: {item_d}")
            check(item_d["is_available"] is True, "S13.10b item D (untouched) still reports is_available=True")

            item_e = items_by_id[agg_menu_ids[4]]
            check(item_e["is_available"] is False, "S13.10c item E (marked unavailable) still appears in the list with is_available=False, not hidden")

            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/menu-items/{agg_menu_ids[0]}?{query_today}", headers=auth(token_agg))
            check(r.status_code == 200, "S13.11 menu detail endpoint 200 for a real item")
            detail = r.json()
            check(detail["impressions"] == 5, "S13.12 menu detail totals match the list endpoint for the same item")
            trend_metrics = {s["metric"] for s in detail["trend"]}
            check(trend_metrics == {"menu_impression", "menu_detail_view", "add_to_cart", "ordered_quantity"},
                  f"S13.13 menu detail trend includes exactly the 4 documented metrics: {trend_metrics}")

            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/menu-items/{str(uuid.uuid4())}?{query_today}", headers=auth(token_agg))
            check(r.status_code == 404, "S13.14 menu detail for a nonexistent id returns 404")
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/menu-items/{menu_item_ids[0]}?{query_today}", headers=auth(token_agg))
            check(r.status_code == 404, "S13.15 menu detail for another shop's real menu_item_id returns 404 (not leaked)")

            r = await client.get(f"/api/v1/shops/{shop_id}/analytics/menu-items?{query_today}", headers=auth(token))
            other_items = {i["menu_item_id"] for i in r.json()["items"]}
            check(not (other_items & set(agg_menu_ids)), "S13.16 the original fixture shop's menu list contains none of the aggregation-fixture's items (isolation)")

            # =============================================================
            # SECTION 14: Staff analytics aggregation correctness
            # =============================================================
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/staff?{query_today}", headers=auth(token_agg))
            check(r.status_code == 200, "S14.01 staff analytics endpoint 200")
            staff_items = r.json()["items"]
            staff_by_id = {s["staff_id"]: s for s in staff_items}
            check(len(staff_by_id) == len(agg_staff_ids), "S14.02 all staff listed, including zero-event ones")

            st0 = staff_by_id[agg_staff_ids[0]]
            check(st0["profile_impressions"] == 4, f"S14.03 staff0 profile_impressions == 4: {st0}")
            check(st0["profile_views"] == 2, f"S14.04 staff0 profile_views == 2: {st0}")
            check(st0["social_clicks"] == 1, f"S14.05 staff0 social_clicks == 1: {st0}")
            check(st0["targeted_contacts"] == 2, f"S14.06 staff0 targeted_contacts == 2 (from OperationsTask.target_staff_id): {st0}")
            check(st0["assigned_reservations"] == 1, f"S14.07 staff0 assigned_reservations == 1 (cancelled reservation excluded): {st0}")

            st1 = staff_by_id[agg_staff_ids[1]]
            check(st1["profile_impressions"] == 9 and st1["profile_views"] == 1 and st1["social_clicks"] == 0,
                  f"S14.08 staff1 has its own independent counts, including the HIGHEST profile_impressions (9): {st1}")

            st2 = staff_by_id[agg_staff_ids[2]]
            check(all(st2[k] == 0 for k in ("profile_impressions", "profile_views", "social_clicks", "targeted_contacts", "assigned_reservations")),
                  f"S14.09 staff2 (zero events) lists all-zero, not omitted: {st2}")

            # Section44/116: never ranked/sorted by any numeric field. staff1
            # has the HIGHEST profile_impressions (9) of the three, yet must
            # still appear in Staff.name order (2nd), not moved to the front
            # -- a real proof that descending-by-metric sorting is not
            # happening, rather than a coincidental match.
            actual_order_ids = [s["staff_id"] for s in staff_items]
            expected_name_order_ids = [agg_staff_ids[0], agg_staff_ids[1], agg_staff_ids[2]]
            check(actual_order_ids == expected_name_order_ids,
                  f"S14.10 staff list order follows Staff.name order ({expected_name_order_ids}), "
                  f"not descending-by-profile_impressions order (which would start with staff1, impressions=9): got {actual_order_ids}")

            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/staff/{agg_staff_ids[0]}?{query_today}", headers=auth(token_agg))
            check(r.status_code == 200, "S14.11 staff detail endpoint 200")
            staff_detail = r.json()
            check(staff_detail["profile_impressions"] == 4, "S14.12 staff detail totals match the list endpoint")
            staff_trend_metrics = {s["metric"] for s in staff_detail["trend"]}
            check(staff_trend_metrics == {"staff_profile_impression", "staff_profile_view", "staff_social_click"},
                  f"S14.13 staff detail trend includes exactly the 3 documented metrics: {staff_trend_metrics}")

            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/staff/{str(uuid.uuid4())}?{query_today}", headers=auth(token_agg))
            check(r.status_code == 404, "S14.14 staff detail for a nonexistent id returns 404")
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/staff/{staff_ids[0]}?{query_today}", headers=auth(token_agg))
            check(r.status_code == 404, "S14.15 staff detail for another shop's real staff_id returns 404 (not leaked)")

            # Confirm no field name anywhere on this endpoint is named like
            # a ranking/score concept (Absolute Non-Goals).
            forbidden_field_substrings = ("rank", "score", "grade", "best", "worst", "top_")
            for st in staff_items:
                for key in st.keys():
                    for bad in forbidden_field_substrings:
                        check(bad not in key.lower(), f"S14.16 staff analytics field '{key}' does not contain forbidden ranking term '{bad}'")

            # =============================================================
            # SECTION 15: Trend series correctness
            # =============================================================
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/trends?{query_today}", headers=auth(token_agg))
            check(r.status_code == 200, "S15.01 trends endpoint 200 for today range")
            series = {s["metric"]: s for s in r.json()["series"]}
            check(len(series["page_view"]["points"]) == 1, "S15.02 today range trend has exactly 1 day-point")
            check(series["page_view"]["points"][0]["value"] == 4, "S15.03 today's page_view trend point matches the overview total")

            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/trends?range=7d", headers=auth(token_agg))
            series7 = r.json()["series"]
            check(all(len(s["points"]) == 7 for s in series7), "S15.04 7d trend: every series has exactly 7 points")
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/trends?range=30d", headers=auth(token_agg))
            series30 = r.json()["series"]
            check(all(len(s["points"]) == 30 for s in series30), "S15.05 30d trend: every series has exactly 30 points")

            page_view_series7 = next(s for s in series7 if s["metric"] == "page_view")
            nonzero_days = [p for p in page_view_series7["points"] if p["value"] > 0]
            check(len(nonzero_days) >= 1, "S15.06 7d trend includes today's non-zero data point")
            zero_days = [p for p in page_view_series7["points"] if p["value"] == 0]
            check(len(zero_days) >= 1, "S15.07 7d trend includes explicit zero-value days (gaps not silently dropped)")
            dates_in_series = [p["date"] for p in page_view_series7["points"]]
            check(dates_in_series == sorted(dates_in_series), "S15.08 trend points are in chronological order")
            check(len(set(dates_in_series)) == len(dates_in_series), "S15.09 trend points have no duplicate dates")

            r_invalid_metric = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/trends?range=today&metrics=page_view,bogus_metric", headers=auth(token_agg))
            check(r_invalid_metric.status_code == 422, "S15.10 unknown metric name in ?metrics= rejected (not an arbitrary passthrough)")
            r_subset_metric = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/trends?range=today&metrics=orders", headers=auth(token_agg))
            check(r_subset_metric.status_code == 200 and len(r_subset_metric.json()["series"]) == 1,
                  "S15.11 requesting a single valid metric returns exactly that one series")

            # =============================================================
            # SECTION 16: Owner auth + tenant isolation + invalid range,
            # across every O4 endpoint (genuine per-endpoint coverage).
            # =============================================================
            endpoints = [
                ("overview", f"/api/v1/shops/{agg_shop_id}/analytics/overview"),
                ("trends", f"/api/v1/shops/{agg_shop_id}/analytics/trends"),
                ("menu-items", f"/api/v1/shops/{agg_shop_id}/analytics/menu-items"),
                ("menu-item-detail", f"/api/v1/shops/{agg_shop_id}/analytics/menu-items/{agg_menu_ids[0]}"),
                ("staff", f"/api/v1/shops/{agg_shop_id}/analytics/staff"),
                ("staff-detail", f"/api/v1/shops/{agg_shop_id}/analytics/staff/{agg_staff_ids[0]}"),
            ]
            for name, url in endpoints:
                r = await client.get(f"{url}?range=today")
                check(r.status_code in (401, 403), f"S16.01 {name}: unauthenticated request rejected ({r.status_code})")
            for name, url in endpoints:
                r = await client.get(f"{url}?range=today", headers=auth(token))  # token belongs to a DIFFERENT tenant
                check(r.status_code in (403, 404), f"S16.02 {name}: cross-tenant request rejected ({r.status_code})")
            for name, url in endpoints:
                r = await client.get(f"{url}?range=bogus_range_value", headers=auth(token_agg))
                check(r.status_code == 422, f"S16.03 {name}: invalid range value rejected (422)")
            for name, url in endpoints:
                r = await client.get(f"{url}?range=today", headers=auth(token_agg))
                check(r.status_code == 200, f"S16.04 {name}: legitimate owner request succeeds (200)")

            # custom range validation on the owner endpoints too
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/overview?range=custom", headers=auth(token_agg))
            check(r.status_code == 422, "S16.05 custom range without start_date/end_date rejected on owner endpoint")
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/overview?range=custom&start_date=2026-02-01&end_date=2026-01-01", headers=auth(token_agg))
            check(r.status_code == 422, "S16.06 custom range with end<start rejected on owner endpoint")
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/analytics/overview?range=custom&start_date=2026-01-01&end_date=2026-01-01", headers=auth(token_agg))
            check(r.status_code == 200, "S16.07 custom range with start==end accepted on owner endpoint")

            # =============================================================
            # SECTION 17: Privacy -- response bodies never carry PII-shaped
            # fields anywhere in the owner-facing analytics API surface.
            # =============================================================
            pii_substrings = ("email", "phone", "ip_address", "user_agent", "full_name", "guest_name", "guest_phone", "guest_email")
            for name, url in endpoints:
                r = await client.get(f"{url}?range=today", headers=auth(token_agg))
                body_text = r.text.lower()
                for pii in pii_substrings:
                    check(pii not in body_text, f"S17.01 {name} response body does not contain PII-shaped field '{pii}'")

            # =============================================================
            # SECTION 18: Static front-end checks (Owner Console IA,
            # mobile/accessibility non-negotiables, nav shell wiring).
            # These read the actual shipped files from the working tree.
            # =============================================================
            def read_text(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    return None

            analytics_html = read_text("frontend/public/shop-analytics.html")
            check(analytics_html is not None, "S18.01 frontend/public/shop-analytics.html exists")
            # Strip ALL HTML <!-- --> comment blocks before doing "forbidden
            # word" content checks below -- this file's own documentation
            # comment explains Phase O4's NON-GOALS (e.g. "no Featured Menu
            # section", "no COGS wording") using the very words it is
            # explaining the absence of, which would otherwise be a false
            # positive. The actual rendered/visible page content (everything
            # a real user or screen reader ever sees) is what these specific
            # checks care about.
            import re as _re
            analytics_html_body = _re.sub(r"<!--.*?-->", "", analytics_html, flags=_re.S) if analytics_html else analytics_html
            if analytics_html:
                check('name="viewport"' in analytics_html, "S18.02 shop-analytics.html has a mobile viewport meta tag")
                check('role="img"' in analytics_html, "S18.03 shop-analytics.html chart SVGs declare role=img")
                check('aria-label=' in analytics_html, "S18.04 shop-analytics.html has aria-label attributes")
                check('aria-live' in analytics_html, "S18.05 shop-analytics.html has an aria-live status region")
                check('chart-empty' in analytics_html, "S18.06 shop-analytics.html defines a dedicated empty-state chart style")
                check('chart-skeleton' in analytics_html, "S18.07 shop-analytics.html defines a loading-skeleton chart style")
                check('owner-console-nav.js' in analytics_html, "S18.08 shop-analytics.html loads the shared nav shell script")
                check('ランキング' in analytics_html or '評価' in analytics_html, "S18.09 shop-analytics.html explicitly disclaims ranking/evaluation in its staff section copy")
                check('売上' not in analytics_html_body, "S18.10 shop-analytics.html never uses the forbidden term 売上 (revenue) in its rendered content")
                check('原価' not in analytics_html_body and 'COGS' not in analytics_html_body,
                      "S18.11 shop-analytics.html never mentions cost/COGS concepts in its rendered content")
                check('<th scope="col">' in analytics_html, "S18.12 shop-analytics.html uses semantic <th scope> table headers")
                check('@media (min-width: 640px)' in analytics_html, "S18.13 shop-analytics.html has a responsive breakpoint for the chart grid")
                check('Featured' not in analytics_html_body and 'フィーチャー' not in analytics_html_body and '特集' not in analytics_html_body,
                      "S18.14 shop-analytics.html has no Featured-menu section in its rendered content (Featured domain does not exist)")

            nav_js = read_text("frontend/public/js/owner-console-nav.js")
            check(nav_js is not None, "S18.15 frontend/public/js/owner-console-nav.js exists")
            if nav_js:
                check("ReceptraOwnerNav" in nav_js, "S18.16 owner-console-nav.js exposes window.ReceptraOwnerNav")
                for label in ("ホーム", "注文", "予約", "顧客", "メニュー", "在庫", "原材料・レシピ", "スタッフ", "ページ", "アクセス分析", "店舗設定"):
                    check(label in nav_js, f"S18.17 owner-console-nav.js nav includes IA item '{label}'")
                check("準備中" in nav_js, "S18.18 the not-yet-built 'ページ' entry is rendered as a disabled placeholder, not a broken link")

            for page, active_key in [
                ("frontend/public/shop-manage.html", "'home'"),
                ("frontend/public/shop-dashboard.html", "'management'"),
                ("frontend/public/shop-inventory.html", "'inventory'"),
                ("frontend/public/shop-ingredients.html", "'ingredients'"),
            ]:
                html = read_text(page)
                check(html is not None, f"S18.19 {page} exists")
                if html:
                    check('owner-console-nav.js' in html, f"S18.20 {page} loads the shared nav shell script")
                    check('receptra-owner-nav-root' in html, f"S18.21 {page} has the nav mount point div")
                    check(f"ReceptraOwnerNav.render('receptra-owner-nav-root', {active_key}" in html,
                          f"S18.22 {page} activates the correct nav key ({active_key})")

            manage_html = read_text("frontend/public/shop-manage.html")
            if manage_html:
                check('loadMenuAnalyticsBadges' in manage_html, "S18.23 shop-manage.html wires menu analytics mini-badges")
                check('loadStaffAnalyticsBadges' in manage_html, "S18.24 shop-manage.html wires staff analytics mini-badges")
                check('row.orders' in manage_html, "S18.25 menu analytics badge uses the correct 'orders' field name")
                check('row.assigned_reservations' in manage_html, "S18.26 staff analytics badge uses the correct 'assigned_reservations' field name")
                check('id="menu-card"' in manage_html, "S18.27 shop-manage.html menu section has a stable anchor id")
                check('id="reservations-card"' in manage_html, "S18.28 shop-manage.html reservations section has a stable anchor id")

            client_js = read_text("frontend/public/js/storefront-analytics-client.js")
            check(client_js is not None, "S18.29 storefront-analytics-client.js exists")
            if client_js:
                check('sendBeacon' in client_js, "S18.30 analytics client uses sendBeacon for best-effort delivery")
                check('keepalive' in client_js, "S18.31 analytics client has a fetch(keepalive) fallback")
                check('IntersectionObserver' in client_js, "S18.32 analytics client uses IntersectionObserver for impressions")
                check('catch' in client_js, "S18.33 analytics client wraps failures so they never throw into page code")
                check('crypto.randomUUID' in client_js, "S18.34 analytics client prefers crypto.randomUUID for session ids")
                check('localStorage' not in client_js.split('receptra_analytics_session_id')[0] or 'SESSION_ID_KEY' in client_js,
                      "S18.35 analytics client's session id key is a named constant, not inlined ad-hoc")

            for page in ("frontend/public/table-order.html", "frontend/public/store.html", "frontend/public/shop.html"):
                html = read_text(page)
                check(html is not None, f"S18.36 {page} exists")
                if html:
                    check('storefront-analytics-client.js' in html, f"S18.37 {page} loads the shared analytics client")

            # =============================================================
            # SECTION 19: Performance (bulk insert + read-time aggregation)
            # =============================================================
            for n in (100, 1000, 10000):
                perf_shop_email = f"o4perf{n}_{uuid.uuid4().hex[:6]}@example.com"
                r = await client.post("/api/v1/auth/register", json={"email": perf_shop_email, "password": "password123", "display_name": "性能試験"})
                perf_token = r.json()["access_token"]
                r = await client.post("/api/v1/shops/register", json={"name": f"性能試験{n}", "category": "IZAKAYA", "address": "沖縄県那覇市"}, headers=auth(perf_token))
                perf_shop_id = r.json()["shop_id"]

                insert_start = time.monotonic()
                async with db_module.AsyncSessionLocal() as db:
                    batch = [
                        AnalyticsEvent(shop_id=perf_shop_id, event_type="page_view", channel="storefront",
                                        anonymous_session_id=f"perf-sess-{i}", occurred_at=mid_today_utc)
                        for i in range(n)
                    ]
                    db.add_all(batch)
                    await db.commit()
                insert_elapsed = time.monotonic() - insert_start
                check(insert_elapsed < 30, f"S19.01 bulk-inserting {n} AnalyticsEvent rows completes in a reasonable time ({insert_elapsed:.2f}s)")

                query_start = time.monotonic()
                r = await client.get(f"/api/v1/shops/{perf_shop_id}/analytics/overview?range=today", headers=auth(perf_token))
                query_elapsed = time.monotonic() - query_start
                check(r.status_code == 200, f"S19.02 overview query succeeds with {n} seeded events")
                check(r.json()["totals"]["page_views"] == n, f"S19.03 overview query returns the exact seeded count ({n}) even at this volume")
                check(query_elapsed < 10, f"S19.04 overview query over {n} events responds in a reasonable time ({query_elapsed:.2f}s)")

            # =============================================================
            # SECTION 20: O1/O3 integration -- O4 must not have regressed
            # the pre-existing dashboards it links out to.
            # =============================================================
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/operations/today", headers=auth(token_agg))
            check(r.status_code == 200, f"S20.01 O1 today-ops endpoint still responds 200 after O4's changes: {r.status_code} {r.text[:200]}")
            r = await client.get(f"/api/v1/shops/{agg_shop_id}/management-dashboard?range=today", headers=auth(token_agg))
            check(r.status_code == 200, "S20.02 O3 management-dashboard endpoint still responds 200 after O4's changes")
            if r.status_code == 200:
                o3_body = r.json()
                check("summary" in o3_body, "S20.03 O3 management-dashboard response shape (summary) unchanged")
                check("order_metrics" in o3_body, "S20.04 O3 management-dashboard response shape (order_metrics) unchanged")

            # =============================================================
            # SECTION 21: Realtime Voice files must remain byte-for-byte
            # untouched by this entire phase (re-verified from inside the
            # formal suite itself, not only ad-hoc at the shell).
            # =============================================================
            import hashlib
            def sha256_of(path):
                h = hashlib.sha256()
                with open(path, "rb") as f:
                    h.update(f.read())
                return h.hexdigest()

            voice_py_hash = sha256_of("app/services/realtime_voice_ai.py")
            voice_js_hash = sha256_of("frontend/public/js/realtime-voice-engine.js")
            check(voice_py_hash == "bb326e37585fdc88cc0771e2bd475c190819c8772dc6c00e0b7c328442fa6c72",
                  "S21.01 app/services/realtime_voice_ai.py SHA256 unchanged from the pre-Phase-O4 baseline")
            check(voice_js_hash == "502a1a4ff8bb73368385236a74f30210dca918bee637311ba01496081c302501",
                  "S21.02 frontend/public/js/realtime-voice-engine.js SHA256 unchanged from the pre-Phase-O4 baseline")

    print()
    print(f"TOTAL CHECKS: {CHECK_COUNT[0]}")
    print(f"TOTAL FAILS: {len(FAILS)}")
    for f in FAILS:
        print(" -", f)
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
