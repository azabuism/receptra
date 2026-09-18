"""
店舗写真・メニュー エンドポイント
写真のアップロード/削除/並び替え、メニューのCRUD、画像の配信
"""

import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop, ShopPhoto, MenuItem
from app.schemas.shop_media import (
    ShopPhotoResponse, MenuItemResponse, ReorderRequest
)

router = APIRouter(prefix="/api/v1/shops", tags=["shop-media"])
media_router = APIRouter(prefix="/api/v1/media", tags=["media"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    """指定された店舗を取得し、ログイン中のテナントが所有しているか確認する"""
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


async def _read_and_validate_image(file: UploadFile) -> tuple[bytes, str]:
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail="対応していない画像形式です（JPEG, PNG, WEBP, GIFのみ対応）"
        )
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="画像サイズが大きすぎます（5MBまで）")
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="画像データが空です")
    return data, file.content_type


def _photo_to_response(photo: ShopPhoto) -> ShopPhotoResponse:
    return ShopPhotoResponse(
        id=photo.id,
        shop_id=photo.shop_id,
        kind=photo.kind,
        url=f"/api/v1/media/shop-photos/{photo.id}",
        display_order=photo.display_order,
        created_at=photo.created_at,
    )


def _menu_item_to_response(item: MenuItem) -> MenuItemResponse:
    return MenuItemResponse(
        id=item.id,
        shop_id=item.shop_id,
        category=item.category,
        name=item.name,
        description=item.description,
        price=item.price,
        photo_url=(f"/api/v1/media/menu-photos/{item.id}" if item.image_data else None),
        is_available=item.is_available,
        display_order=item.display_order,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ---------------------------------------------------------------------------
# 店舗写真
# ---------------------------------------------------------------------------

@router.get("/{shop_id}/photos", response_model=List[ShopPhotoResponse], summary="店舗写真一覧を取得")
async def list_shop_photos(shop_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ShopPhoto).filter(ShopPhoto.shop_id == shop_id)
        .order_by(ShopPhoto.kind, ShopPhoto.display_order)
    )
    photos = result.scalars().all()
    return [_photo_to_response(p) for p in photos]


@router.post("/{shop_id}/photos", response_model=ShopPhotoResponse, summary="店舗写真をアップロード")
async def upload_shop_photo(
    shop_id: str,
    kind: str = Form("other"),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)

    if kind not in ("exterior", "interior", "other"):
        kind = "other"

    data, content_type = await _read_and_validate_image(file)

    result = await db.execute(
        select(ShopPhoto).filter(ShopPhoto.shop_id == shop_id, ShopPhoto.kind == kind)
        .order_by(ShopPhoto.display_order.desc())
    )
    last = result.scalars().first()
    next_order = (last.display_order + 1) if last else 0

    photo = ShopPhoto(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        kind=kind,
        image_data=data,
        mime_type=content_type,
        display_order=next_order,
        created_at=datetime.utcnow(),
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)
    return _photo_to_response(photo)


@router.delete("/{shop_id}/photos/{photo_id}", summary="店舗写真を削除")
async def delete_shop_photo(
    shop_id: str,
    photo_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    photo = await db.get(ShopPhoto, photo_id)
    if not photo or photo.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="写真が見つかりません")
    await db.delete(photo)
    await db.commit()
    return {"success": True}


@router.patch("/{shop_id}/photos/reorder", summary="店舗写真の表示順を変更")
async def reorder_shop_photos(
    shop_id: str,
    request: ReorderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    for item in request.items:
        photo = await db.get(ShopPhoto, item.id)
        if photo and photo.shop_id == shop_id:
            photo.display_order = item.display_order
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# メニュー
# ---------------------------------------------------------------------------

@router.get("/{shop_id}/menu", response_model=List[MenuItemResponse], summary="メニュー一覧を取得")
async def list_menu_items(shop_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MenuItem).filter(MenuItem.shop_id == shop_id)
        .order_by(MenuItem.category, MenuItem.display_order)
    )
    items = result.scalars().all()
    return [_menu_item_to_response(i) for i in items]


@router.post("/{shop_id}/menu", response_model=MenuItemResponse, summary="メニュー項目を追加")
async def create_menu_item(
    shop_id: str,
    category: str = Form("その他"),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    price: Optional[int] = Form(None),
    is_available: bool = Form(True),
    file: Optional[UploadFile] = File(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)

    image_data, mime_type = None, None
    if file is not None and file.filename:
        image_data, mime_type = await _read_and_validate_image(file)

    result = await db.execute(
        select(MenuItem).filter(MenuItem.shop_id == shop_id, MenuItem.category == category)
        .order_by(MenuItem.display_order.desc())
    )
    last = result.scalars().first()
    next_order = (last.display_order + 1) if last else 0

    item = MenuItem(
        id=str(uuid.uuid4()),
        shop_id=shop_id,
        category=category or "その他",
        name=name,
        description=description,
        price=price,
        image_data=image_data,
        mime_type=mime_type,
        is_available=is_available,
        display_order=next_order,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _menu_item_to_response(item)


@router.put("/{shop_id}/menu/{item_id}", response_model=MenuItemResponse, summary="メニュー項目を更新")
async def update_menu_item(
    shop_id: str,
    item_id: str,
    category: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    price: Optional[int] = Form(None),
    is_available: Optional[bool] = Form(None),
    file: Optional[UploadFile] = File(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    item = await db.get(MenuItem, item_id)
    if not item or item.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="メニュー項目が見つかりません")

    if category is not None:
        item.category = category
    if name is not None:
        item.name = name
    if description is not None:
        item.description = description
    if price is not None:
        item.price = price
    if is_available is not None:
        item.is_available = is_available
    if file is not None and file.filename:
        image_data, mime_type = await _read_and_validate_image(file)
        item.image_data = image_data
        item.mime_type = mime_type
    item.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(item)
    return _menu_item_to_response(item)


@router.delete("/{shop_id}/menu/{item_id}", summary="メニュー項目を削除")
async def delete_menu_item(
    shop_id: str,
    item_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    item = await db.get(MenuItem, item_id)
    if not item or item.shop_id != shop_id:
        raise HTTPException(status_code=404, detail="メニュー項目が見つかりません")
    await db.delete(item)
    await db.commit()
    return {"success": True}


@router.patch("/{shop_id}/menu/reorder", summary="メニューの表示順を変更")
async def reorder_menu_items(
    shop_id: str,
    request: ReorderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_shop(shop_id, current_user, db)
    for entry in request.items:
        item = await db.get(MenuItem, entry.id)
        if item and item.shop_id == shop_id:
            item.display_order = entry.display_order
    await db.commit()
    return {"success": True}


# ---------------------------------------------------------------------------
# 画像バイナリ配信
# ---------------------------------------------------------------------------

@media_router.get("/shop-photos/{photo_id}", summary="店舗写真バイナリを配信")
async def get_shop_photo_binary(photo_id: str, db: AsyncSession = Depends(get_db)):
    photo = await db.get(ShopPhoto, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="写真が見つかりません")
    return Response(
        content=photo.image_data,
        media_type=photo.mime_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@media_router.get("/menu-photos/{item_id}", summary="メニュー写真バイナリを配信")
async def get_menu_photo_binary(item_id: str, db: AsyncSession = Depends(get_db)):
    item = await db.get(MenuItem, item_id)
    if not item or not item.image_data:
        raise HTTPException(status_code=404, detail="写真が見つかりません")
    return Response(
        content=item.image_data,
        media_type=item.mime_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
