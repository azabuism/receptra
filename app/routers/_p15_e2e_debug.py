"""
【一時ファイル・テスト後に必ず削除】Outbound AI Phase 1.5 Production E2E cleanup用

Phase 1.5（Owner Notification UX）のProduction E2Eでは、以下の理由により
Phase 1の時のような大きなデバッグルーターは不要と判断した:
- 今回の変更はGET /api/v1/shops/{shop_id}/outbound-calls のレスポンスに
  reservation_guest_name / reservation_date を追加しただけの読み取り専用拡張
  であり、予約作成→ジョブ→worker→ログ生成というパイプライン自体（Phase 1で
  Production E2E済み）には一切手を入れていない。
- そのため、一時テストテナント・一時テスト店舗だけを使い、
  (1) 新しいOpenAIスキーマがProductionに反映されていること
  (2) 履歴が0件のshopに対してエンドポイントが正しく空配列を返すこと
  （Owner UIのEmpty State相当）
  (3) 他tenantからは403で拒否されること
  (4) AIStaffSettings未設定時のデフォルト応答が返ること
  の確認だけで十分と判断した（課金アクティベーションが必要な実予約作成は
  今回のE2Eでは行わない）。

このファイルが唯一提供するのは、テスト後のクリーンアップに必要な
「自テナント削除」エンドポイントのみ（Phase 1のE2E debugルーターと同じ
安全設計を踏襲）。検証完了後は本ファイルごと削除し、再デプロイ後に
OpenAPIから消えていることを確認する。

安全設計:
- 呼び出し元ユーザー自身のtenantにのみ作用する。
- tenant.nameが E2E_MARKER ("【E2E-TEMP】") で始まる場合のみ許可する
  （実テナント・実店舗の名前はこの形式を持たないため、実装ミスがあっても
  最終防衛線として機能する）。
- 店舗が残っている場合は削除を拒否する（先に本番の
  DELETE /api/v1/shops/{shop_id} を使わせることで、Phase 1で修正した
  cascade削除の経路を素通りさせない）。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.shop import Shop
from app.models.user import Tenant
from app.schemas.user import CurrentUser

router = APIRouter(prefix="/api/v1/_e2e_debug", tags=["_e2e_debug_TEMPORARY"])

E2E_MARKER = "【E2E-TEMP】"


@router.delete(
    "/tenants/self",
    status_code=204,
    summary="【一時】呼び出し元自身のE2E-TEMPテナント・ユーザーを完全に削除する（店舗を先に削除しておく必要あり）",
)
async def delete_own_e2e_tenant(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    tenant = await db.get(Tenant, current_user.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="テナントが見つかりません")
    if not tenant.name.startswith(E2E_MARKER):
        raise HTTPException(
            status_code=403,
            detail=f"このエンドポイントはテナント名が{E2E_MARKER}で始まる一時テストテナントにのみ使用できます",
        )

    remaining_shops = await db.execute(
        select(Shop.id).filter(Shop.tenant_id == tenant.id).limit(1)
    )
    if remaining_shops.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="先に本番の DELETE /api/v1/shops/{shop_id} でこのテナントの店舗を削除してください",
        )

    await db.delete(tenant)
    await db.commit()
