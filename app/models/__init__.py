"""
All SQLAlchemy ORM models for BARIYON Receptra
"""

# User models
from app.models.user import Tenant, User

# Shop models
from app.models.shop import Shop, ShopHours, ShopCategory, BusinessType

# Service & Staff models (NEW)
from app.models.service import Service
from app.models.staff import Staff, StaffService

# Phase3E-2: スタッフシフト管理（新規テーブル。alembicは使わず
# Base.metadata.create_all()で自動作成される）
from app.models.staff_shift import StaffWeeklyShift, StaffShiftOverride

# Reservation models
from app.models.reservation import Reservation, ReservationStatus

# Customer models
from app.models.customer import Customer

# Review models
from app.models.review import Review

# Promotion & Coupon models
from app.models.promotion import Promotion, Coupon, PromotionType

# Social models (マイページ: 行きたい店・いいね)
from app.models.social import UserShopRelation

# Analytics models
from app.models.analytics import ShopAnalytics, MonthlyAnalytics

# Existing models
from app.models.receptionist import Receptionist
from app.models.visitor import Visitor

# Realtime Voice AI Phase2: AIスタッフ設定（新規テーブル。alembicは使わず、
# app/main.py の lifespan() 内 Base.metadata.create_all() で自動作成される）
from app.models.ai_staff_settings import AIStaffSettings

# Phase3D: Shop Knowledge & FAQ（新規テーブル。ai_staff_settingsと同様、
# alembicは使わずBase.metadata.create_all()で自動作成される）
from app.models.shop_knowledge import ShopKnowledge, ShopFAQ

# Outbound AI Phase 1: 予約確定通知の架電ジョブ・ログ（新規テーブル。
# 他のPhase3系モデルと同様、alembicは使わずBase.metadata.create_all()で自動作成される）
from app.models.outbound_call import OutboundCallJob, OutboundCallLog, OutboundCallCategory, OutboundCallJobStatus

__all__ = [
    # User/Tenant
    "Tenant",
    "User",
    
    # Shop
    "Shop",
    "ShopHours",
    "ShopCategory",
    "BusinessType",
    
    # Service & Staff (NEW)
    "Service",
    "Staff",
    "StaffService",

    # Phase3E-2: スタッフシフト管理
    "StaffWeeklyShift",
    "StaffShiftOverride",
    
    # Reservation
    "Reservation",
    "ReservationStatus",
    
    # Customer
    "Customer",
    
    # Review
    "Review",
    
    # Promotion
    "Promotion",
    "Coupon",
    "PromotionType",

    # Social
    "UserShopRelation",

    # Analytics
    "ShopAnalytics",
    "MonthlyAnalytics",
    
    # Existing
    "Receptionist",
    "Visitor",

    # Realtime Voice AI Phase2
    "AIStaffSettings",

    # Phase3D: Shop Knowledge & FAQ
    "ShopKnowledge",
    "ShopFAQ",

    # Outbound AI Phase 1
    "OutboundCallJob",
    "OutboundCallLog",
    "OutboundCallCategory",
    "OutboundCallJobStatus",
]
