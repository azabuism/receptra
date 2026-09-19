"""
店舗のロゴ・サムネイル・カバー画像 エンドポイント
アップロード/削除、画像の配信
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.schemas.user import CurrentUser
from app.models.shop import Shop
from app.routers.shop_media import _read_and_validate_image

router = APIRouter(prefix="/api/v1/shops", tags=["shop-images"])
media_router = APIRouter(prefix="/api/v1/media", tags=["media"])

# スロット名 -> (data列, mime列)
_SLOT_COLUMNS = {
    "logo": ("logo_data", "logo_mime_type"),
    "thumbnail": ("thumbnail_data", "thumbnail_mime_type"),
    "cover": ("cover_data", "cover_mime_type"),
}


async def _get_owned_shop(shop_id: str, current_user: CurrentUser, db: AsyncSession) -> Shop:
    shop = await db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="店舗が見つかりません")
    if shop.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="この店舗を編集する権限がありません")
    return shop


async def _upload_slot(slot: str, shop_id: str, file: UploadFile, current_user: CurrentUser, db: AsyncSession):
    await _get_owned_shop(shop_id, current_user, db)
    data, mime_type = await _read_and_validate_image(file)
    data_col, mime_col = _SLOT_COLUMNS[slot]

    await db.execute(
        update(Shop).where(Shop.id == shop_id).values(**{
            data_col: data,
            mime_col: mime_type,
            "updated_at": datetime.utcnow(),
        })
    )
    await db.commit()
    return {"success": True, "url": f"/api/v1/media/shop-{slot}/{shop_id}"}


async def _delete_slot(slot: str, shop_id: str, current_user: CurrentUser, db: AsyncSession):
    await _get_owned_shop(shop_id, current_user, db)
    data_col, mime_col = _SLOT_COLUMNS[slot]

    await db.execute(
        update(Shop).where(Shop.id == shop_id).values(**{
            data_col: None,
            mime_col: None,
            "updated_at": datetime.utcnow(),
        })
    )
    await db.commit()
    return {"success": True}


async def _serve_slot(slot: str, shop_id: str, db: AsyncSession):
    data_col, mime_col = _SLOT_COLUMNS[slot]
    columns = [getattr(Shop, data_col), getattr(Shop, mime_col)]
    result = await db.execute(select(*columns).filter(Shop.id == shop_id))
    row = result.first()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return Response(content=row[0], media_type=row[1] or "application/octet-stream", headers={
        "Cache-Control": "public, max-age=3600"
    })


@router.post("/{shop_id}/logo", summary="ロゴ画像をアップロード")
async def upload_logo(shop_id: str, file: UploadFile = File(...),
                       current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _upload_slot("logo", shop_id, file, current_user, db)


@router.delete("/{shop_id}/logo", summary="ロゴ画像を削除")
async def delete_logo(shop_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _delete_slot("logo", shop_id, current_user, db)


@router.post("/{shop_id}/thumbnail", summary="サムネイル画像をアップロード")
async def upload_thumbnail(shop_id: str, file: UploadFile = File(...),
                            current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _upload_slot("thumbnail", shop_id, file, current_user, db)


@router.delete("/{shop_id}/thumbnail", summary="サムネイル画像を削除")
async def delete_thumbnail(shop_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _delete_slot("thumbnail", shop_id, current_user, db)


@router.post("/{shop_id}/cover", summary="カバー画像（トップ画像）をアップロード")
async def upload_cover(shop_id: str, file: UploadFile = File(...),
                        current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _upload_slot("cover", shop_id, file, current_user, db)


@router.delete("/{shop_id}/cover", summary="カバー画像を削除")
async def delete_cover(shop_id: str, current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _delete_slot("cover", shop_id, current_user, db)


@media_router.get("/shop-logo/{shop_id}", summary="ロゴ画像を取得")
async def get_logo_image(shop_id: str, db: AsyncSession = Depends(get_db)):
    return await _serve_slot("logo", shop_id, db)


@media_router.get("/shop-thumbnail/{shop_id}", summary="サムネイル画像を取得")
async def get_thumbnail_image(shop_id: str, db: AsyncSession = Depends(get_db)):
    return await _serve_slot("thumbnail", shop_id, db)


@media_router.get("/shop-cover/{shop_id}", summary="カバー画像を取得")
async def get_cover_image(shop_id: str, db: AsyncSession = Depends(get_db)):
    return await _serve_slot("cover", shop_id, db)
