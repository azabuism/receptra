"""
Shop Knowledge & FAQ API（Phase3D）

店舗オーナーが自店の店舗情報（駐車場・支払い方法・設備・来店前案内・
キャンセルポリシー）とFAQを設定するためのCRUD、および
Realtime Voice AIのget_shop_info Toolから呼ばれる公開情報取得を提供する。

セキュリティ方針（app/routers/ai_staff_settings.py と同じ既存パターンを踏襲）:
- 変更系（GET含む管理画面用）エンドポイントは全てget_current_user（JWT認証）を使用し、
  shop_idの所有権をサーバー側で必ず検証する（_get_owned_shop）。
- get_shop_info_for_ai() は認証不要のguest-facing Tool呼び出し
  （app/routers/realtime_voice.py から呼ばれる）専用の関数であり、
  ここで返す値は「電話口のお客様に案内してよい情報」のみに限定する。
  内部メモ・売上・顧客情報・他店舗データは絶対に含めない。
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.models.shop_knowledge import ShopKnowledge, ShopFAQ
from app.schemas.shop_knowledge import (
    ShopKnowledgeUpdateRequest,
    ShopKnowledgeResponse,
    ShopFAQCreateRequest,
    ShopFAQUpdateRequest,
    ShopFAQResponse,
)

router = APIRouter(prefix="/api/v1/shops/{shop_id}", tags=["shop_knowledge"])

# get_shop_info Toolが受け付ける有効なtopic一覧。
# realtime_voice_ai.py の get_shop_info Tool schema (enum) と必ず一致させること。
VALID_SHOP_INFO_TOPICS = {
    "parking", "payment", "wifi", "accessibility", "children",
    "smoking", "pets", "facilities", "pre_visit", "cancellation", "faq",
}

# FAQ検索(topic="faq"かつquery指定時)で一度に返す最大件数。
# 「必要最小限の情報だけ返す」方針(section49)により無制限には返さない。
_FAQ_SEARCH_LIMIT = 5


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


async def _get_knowledge_row(shop_id: str, db: AsyncSession) -> Optional[ShopKnowledge]:
    result = await db.execute(select(ShopKnowledge).where(ShopKnowledge.shop_id == shop_id))
    return result.scalars().first()


def _knowledge_to_response(shop_id: str, row: Optional[ShopKnowledge]) -> ShopKnowledgeResponse:
    if row is None:
        # レコードが存在しない場合は全項目Noneのデフォルトレスポンスを返す
        # （フロント側はこれをもって「全項目未設定」として表示する）。
        return ShopKnowledgeResponse(shop_id=shop_id)
    return ShopKnowledgeResponse.model_validate(row)


# ============================================================
# オーナー管理画面用API（認証必須）
# ============================================================

@router.get("/knowledge", response_model=ShopKnowledgeResponse, summary="店舗情報（構造化知識）を取得")
async def get_shop_knowledge(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    row = await _get_knowledge_row(shop_id, db)
    return _knowledge_to_response(shop_id, row)


@router.put("/knowledge", response_model=ShopKnowledgeResponse, summary="店舗情報（構造化知識）を保存（無ければ新規作成）")
async def save_shop_knowledge(
    shop_id: str,
    request: ShopKnowledgeUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)

    row = await _get_knowledge_row(shop_id, db)
    update_data = request.model_dump(exclude_unset=True)
    now = datetime.utcnow()

    if row is None:
        # lazy creation: AIStaffSettingsと同じく、初めて保存されたタイミングでのみ作成する。
        row = ShopKnowledge(
            id=str(uuid.uuid4()),
            shop_id=shop_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)

    for field, value in update_data.items():
        setattr(row, field, value)
    row.updated_at = now

    await db.commit()
    await db.refresh(row)
    return _knowledge_to_response(shop_id, row)


@router.get("/faqs", response_model=list[ShopFAQResponse], summary="FAQ一覧を取得（オーナー用・非公開分も含む）")
async def list_shop_faqs(
    shop_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    result = await db.execute(
        select(ShopFAQ)
        .where(ShopFAQ.shop_id == shop_id)
        .order_by(ShopFAQ.sort_order.asc(), ShopFAQ.created_at.asc())
    )
    return result.scalars().all()


@router.post("/faqs", response_model=ShopFAQResponse, summary="FAQを新規作成")
async def create_shop_faq(
    shop_id: str,
    request: ShopFAQCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    now = datetime.utcnow()
    row = ShopFAQ(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        question=request.question,
        answer=request.answer,
        is_active=request.is_active,
        sort_order=request.sort_order,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/faqs/{faq_id}", response_model=ShopFAQResponse, summary="FAQを更新")
async def update_shop_faq(
    shop_id: str,
    faq_id: str,
    request: ShopFAQUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    row = await db.get(ShopFAQ, faq_id)
    if not row or row.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="FAQが見つかりません")

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/faqs/{faq_id}", summary="FAQを削除")
async def delete_shop_faq(
    shop_id: str,
    faq_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    row = await db.get(ShopFAQ, faq_id)
    if not row or row.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="FAQが見つかりません")

    await db.delete(row)
    await db.commit()
    return {"success": True}


# ============================================================
# Realtime Voice AI (get_shop_info Tool) 用 — 公開情報取得（認証不要）
# app/routers/realtime_voice.py から呼び出される。
# ============================================================

def _parking_info(k: ShopKnowledge) -> tuple[bool, Optional[dict]]:
    known = k.parking_available is not None
    if not known:
        return False, None
    return True, {
        "available": k.parking_available,
        "spaces": k.parking_spaces,
        "type": k.parking_type,
        "fee": k.parking_fee,
        "location": k.parking_location,
        "partner_parking": k.partner_parking,
        "full_guidance": k.parking_full_guidance,
        "notes": k.parking_notes,
    }


def _payment_info(k: ShopKnowledge) -> tuple[bool, Optional[dict]]:
    # 大分類（既存・後方互換。Phase3D以前からの店舗もこの5項目のみで動作する）。
    fields = {
        "cash": k.payment_cash,
        "credit_card": k.payment_credit_card,
        "debit_card": k.payment_debit_card,
        "qr_code": k.payment_qr_code,
        "emoney": k.payment_emoney,
    }
    # ブランド単位の詳細（Phase3H追加）。大分類とは独立して設定され得るため、
    # 「known」の判定にも大分類・ブランド詳細の両方を含める
    # （大分類が空でもブランド詳細だけ登録されているケースを見落とさないため）。
    credit_brands = {
        "visa": k.payment_credit_visa,
        "mastercard": k.payment_credit_mastercard,
        "jcb": k.payment_credit_jcb,
        "amex": k.payment_credit_amex,
        "diners": k.payment_credit_diners,
        "other": k.payment_credit_other,
    }
    qr_brands = {
        "paypay": k.payment_qr_paypay,
        "au_pay": k.payment_qr_au_pay,
        "d_barai": k.payment_qr_d_barai,
        "rakuten_pay": k.payment_qr_rakuten_pay,
        "merpay": k.payment_qr_merpay,
        "other": k.payment_qr_other,
    }
    emoney_brands = {
        "transit_ic": k.payment_emoney_transit_ic,
        "id": k.payment_emoney_id,
        "quicpay": k.payment_emoney_quicpay,
        "rakuten_edy": k.payment_emoney_rakuten_edy,
        "waon": k.payment_emoney_waon,
        "nanaco": k.payment_emoney_nanaco,
        "other": k.payment_emoney_other,
    }
    all_values = (
        list(fields.values()) + list(credit_brands.values())
        + list(qr_brands.values()) + list(emoney_brands.values())
    )
    known = any(v is not None for v in all_values)
    if not known:
        return False, None
    data = dict(fields)
    # ブランド単位の詳細は、そのカテゴリで1件でも登録があれば辞書ごと含める。
    # 辞書内の個々の値がNone（未登録）のブランドも、あえてキーとして残す
    # （「dataに含まれない項目は未設定」という既存ルールを辞書の外側だけで
    # なく内側にも適用できるよう、Noneのまま明示する設計。値がyes/no/
    # conditionalのいずれでもない=未登録という解釈をAI側の指示で徹底する）。
    data["credit_brands"] = credit_brands if any(v is not None for v in credit_brands.values()) else None
    data["qr_brands"] = qr_brands if any(v is not None for v in qr_brands.values()) else None
    data["emoney_brands"] = emoney_brands if any(v is not None for v in emoney_brands.values()) else None
    data["notes"] = k.payment_notes
    return True, data


def _single_facility(value_field: str, k: ShopKnowledge) -> tuple[bool, Optional[dict]]:
    value = getattr(k, value_field)
    known = value is not None
    if not known:
        return False, None
    return True, {"status": value, "notes": k.facilities_notes}


def _facilities_all(k: ShopKnowledge) -> tuple[bool, Optional[dict]]:
    fields = {
        "wifi": k.wifi_available,
        "private_room": k.private_room_available,
        "wheelchair_accessible": k.wheelchair_accessible,
        "children_allowed": k.children_allowed,
        "smoking_policy": k.smoking_policy,
        "pets_allowed": k.pets_allowed,
        "elevator": k.elevator_available,
    }
    known = any(v is not None for v in fields.values())
    if not known:
        return False, None
    data = dict(fields)
    data["notes"] = k.facilities_notes
    return True, data


def _pre_visit_info(k: ShopKnowledge) -> tuple[bool, Optional[dict]]:
    known = bool(k.required_items or k.pre_visit_instructions or k.arrival_guidance)
    if not known:
        return False, None
    return True, {
        "required_items": k.required_items,
        "pre_visit_instructions": k.pre_visit_instructions,
        "arrival_guidance": k.arrival_guidance,
    }


def _cancellation_info(k: ShopKnowledge) -> tuple[bool, Optional[dict]]:
    known = bool(k.cancellation_policy)
    if not known:
        return False, None
    return True, {"policy": k.cancellation_policy}


async def _faq_info(db: AsyncSession, shop_id: str, query: Optional[str]) -> tuple[bool, Optional[dict]]:
    """
    FAQ検索。10件程度を想定した軽量なILIKE検索（Phase3D仕様書 section15）。
    ベクトルDB・追加LLM呼び出しは行わない。完全一致だけに依存しないよう、
    question/answer双方をILIKE部分一致で検索する。
    """
    stmt = select(ShopFAQ).where(ShopFAQ.shop_id == shop_id, ShopFAQ.is_active.is_(True))
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(or_(ShopFAQ.question.ilike(like), ShopFAQ.answer.ilike(like)))
    stmt = stmt.order_by(ShopFAQ.sort_order.asc(), ShopFAQ.created_at.asc()).limit(_FAQ_SEARCH_LIMIT)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    known = len(rows) > 0
    if not known:
        return False, None
    return True, {"items": [{"question": r.question, "answer": r.answer} for r in rows]}


async def get_shop_info_for_ai(db: AsyncSession, shop_id: str, topic: str, query: Optional[str] = None) -> dict:
    """
    Realtime Voice AI の get_shop_info Tool から呼ばれる、認証不要の公開情報取得。

    重要（絶対に守ること）:
    - shop_idは呼び出し元(realtime_voice.py)がURLパスから確定した値をそのまま
      受け取るだけで、この関数自身はLLMの引数からshop_idを受け取らない
      （そもそも引数にshop_idという項目を持たない）。
    - ここで返すのはShopKnowledge/ShopFAQの値のみであり、これらのテーブルは
      設計上「顧客に案内してよい情報」しか保持しない。内部メモ・売上・
      顧客情報・他店舗データを含む値は返さない・返せない。
    - 戻り値は必ず {"success": bool, ...} の形。successがTrueの場合のみ
      "known"(bool)と"data"(dict|None)を含む。DBエラー等の技術的失敗と、
      「情報が未設定なだけ」を区別するため、前者はsuccess:falseで返す。
    """
    if topic not in VALID_SHOP_INFO_TOPICS:
        return {"success": False, "reason_code": "invalid_request"}

    try:
        if topic == "faq":
            known, data = await _faq_info(db, shop_id, query)
            return {"success": True, "known": known, "data": data}

        result = await db.execute(select(ShopKnowledge).where(ShopKnowledge.shop_id == shop_id))
        k = result.scalars().first()
        if k is None:
            # オーナーが一度も店舗情報設定を保存していない(レコード自体が無い)。
            return {"success": True, "known": False, "data": None}

        if topic == "parking":
            known, data = _parking_info(k)
        elif topic == "payment":
            known, data = _payment_info(k)
        elif topic == "wifi":
            known, data = _single_facility("wifi_available", k)
        elif topic == "accessibility":
            known, data = _single_facility("wheelchair_accessible", k)
        elif topic == "children":
            known, data = _single_facility("children_allowed", k)
        elif topic == "smoking":
            known, data = _single_facility("smoking_policy", k)
        elif topic == "pets":
            known, data = _single_facility("pets_allowed", k)
        elif topic == "facilities":
            known, data = _facilities_all(k)
        elif topic == "pre_visit":
            known, data = _pre_visit_info(k)
        elif topic == "cancellation":
            known, data = _cancellation_info(k)
        else:
            # VALID_SHOP_INFO_TOPICSに含まれるが分岐が漏れている場合の安全側フォールバック
            # （実際にはここへ到達しないはずだが、将来topicを追加した際の実装漏れに備える）。
            return {"success": False, "reason_code": "invalid_request"}

        return {"success": True, "known": known, "data": data}
    except Exception:
        return {"success": False, "reason_code": "temporarily_unavailable"}
