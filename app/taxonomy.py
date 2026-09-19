"""
店舗カテゴリーの分類体系（タクソノミー）

業種（business_type、7分類+その他）ごとに、詳細なサブカテゴリー（Shop.category に保存される
実際の値）を定義する。トップページのカテゴリーカード・サブカテゴリー選択ページ・検索結果ページの
すべてが、ここで定義した内容を単一の情報源（single source of truth）として利用する。

Shop モデル:
- business_type: 業種の大分類キー（"restaurant" など、英語の固定キー。検索・絞り込み用）
- category: 選択された詳細サブカテゴリーの名称（"ラーメン" など、日本語の表示名そのもの）
"""

from typing import Optional, TypedDict, List


class TaxonomyItem(TypedDict, total=False):
    name: str
    icon: str
    image: str  # 画像スラッグ（存在する場合。frontend/public/images/categories/<image>.jpg / .webp）


class TaxonomyGroup(TypedDict):
    key: str
    title: str
    description: str
    icon: str
    items: List[TaxonomyItem]


TAXONOMY: List[TaxonomyGroup] = [
    {
        "key": "restaurant",
        "title": "飲食店",
        "description": "飲食店から最適なお店を探す",
        "icon": "🍽️",
        "items": [
            {"name": "ラーメン", "icon": "🍜", "image": "restaurant-ramen"},
            {"name": "寿司・刺身", "icon": "🍣", "image": "restaurant-sushi"},
            {"name": "イタリアン", "icon": "🍝", "image": "restaurant-italian"},
            {"name": "フレンチ", "icon": "🥖", "image": "restaurant-french"},
            {"name": "中華", "icon": "🥢", "image": "restaurant-chinese"},
            {"name": "焼肉", "icon": "🥩", "image": "restaurant-yakiniku"},
            {"name": "カフェ", "icon": "☕", "image": "restaurant-cafe"},
            {"name": "居酒屋", "icon": "🍺", "image": "restaurant-izakaya"},
            {"name": "バー", "icon": "🍸", "image": "restaurant-bar"},
            {"name": "ハンバーガー", "icon": "🍔", "image": "restaurant-hamburger"},
            {"name": "ステーキ", "icon": "🥩", "image": "restaurant-steak"},
            {"name": "パン屋", "icon": "🥐", "image": "restaurant-bakery"},
            {"name": "デリ・弁当", "icon": "🍱", "image": "restaurant-deli-bento"},
            {"name": "その他（飲食店）", "icon": "🍴"},
        ],
    },
    {
        "key": "beauty",
        "title": "美容",
        "description": "美容サービスから最適なお店を探す",
        "icon": "💄",
        "items": [
            {"name": "ヘアサロン", "icon": "💇", "image": "beauty-hair"},
            {"name": "ネイル・マツエク", "icon": "💅", "image": "beauty-nail"},
            {"name": "エステ・脱毛", "icon": "✨", "image": "beauty-esthe"},
            {"name": "マッサージ・リラクゼーション", "icon": "🙏", "image": "beauty-massage"},
            {"name": "メイク・コスメ", "icon": "💄"},
            {"name": "その他（美容）", "icon": "💫", "image": "beauty-other"},
        ],
    },
    {
        "key": "hotel",
        "title": "ホテル・宿泊",
        "description": "ホテルや宿泊施設から最適な施設を探す",
        "icon": "🏨",
        "items": [
            {"name": "ビジネスホテル", "icon": "🏢", "image": "hotel-business"},
            {"name": "リゾートホテル", "icon": "🏖️", "image": "hotel-resort"},
            {"name": "カプセルホテル", "icon": "🛏️", "image": "hotel-capsule"},
            {"name": "旅館", "icon": "🏮", "image": "hotel-ryokan"},
            {"name": "その他（宿泊）", "icon": "🛎️"},
        ],
    },
    {
        "key": "education",
        "title": "教育",
        "description": "教育サービスから最適なスクールを探す",
        "icon": "📚",
        "items": [
            {"name": "語学スクール", "icon": "🗣️", "image": "education-language"},
            {"name": "プログラミング", "icon": "💻", "image": "education-programming"},
            {"name": "音楽教室", "icon": "🎵", "image": "education-music"},
            {"name": "アート教室", "icon": "🎨", "image": "education-art"},
            {"name": "家庭教師・塾", "icon": "📖", "image": "education-tutoring"},
            {"name": "ヨガ・ダンス", "icon": "💃", "image": "education-yoga"},
            {"name": "その他（教育）", "icon": "🎓"},
        ],
    },
    {
        "key": "medical",
        "title": "医療",
        "description": "医療施設から最適なクリニックを探す",
        "icon": "🏥",
        "items": [
            {"name": "一般クリニック", "icon": "⚕️", "image": "medical-general-clinic"},
            {"name": "総合病院", "icon": "🏥", "image": "medical-hospital"},
            {"name": "歯科医院", "icon": "🦷", "image": "medical-dental"},
            {"name": "眼科", "icon": "👁️", "image": "medical-ophthalmology"},
            {"name": "皮膚科", "icon": "🩺", "image": "medical-dermatology"},
            {"name": "整形外科", "icon": "🦴", "image": "medical-orthopedic"},
            {"name": "心療内科", "icon": "🧠", "image": "medical-psychosomatic"},
            {"name": "リハビリ", "icon": "🏃", "image": "medical-rehabilitation"},
            {"name": "その他（医療）", "icon": "➕"},
        ],
    },
    {
        "key": "fitness",
        "title": "フィットネス",
        "description": "フィットネス施設から最適なジムを探す",
        "icon": "💪",
        "items": [
            {"name": "フィットネスジム", "icon": "🏋️", "image": "fitness-gym"},
            {"name": "ヨガスタジオ", "icon": "🧘", "image": "fitness-yoga"},
            {"name": "格闘技", "icon": "🥊", "image": "fitness-martial-arts"},
            {"name": "スイミングスクール", "icon": "🏊", "image": "fitness-swimming"},
            {"name": "ダンススクール", "icon": "💃", "image": "fitness-dance"},
            {"name": "ピラティス", "icon": "🧘‍♀️", "image": "fitness-pilates"},
            {"name": "球技", "icon": "⚽", "image": "fitness-ball-sports"},
            {"name": "その他（フィットネス）", "icon": "🏅", "image": "fitness-other"},
        ],
    },
    {
        "key": "entertainment",
        "title": "エンタメ・趣味",
        "description": "エンタメ・趣味施設から最適な場所を探す",
        "icon": "🎬",
        "items": [
            {"name": "カラオケ", "icon": "🎤", "image": "entertainment-karaoke"},
            {"name": "ゲームセンター", "icon": "🕹️", "image": "entertainment-gamecenter"},
            {"name": "ボウリング", "icon": "🎳", "image": "entertainment-bowling"},
            {"name": "シネマ", "icon": "🎬", "image": "entertainment-cinema"},
            {"name": "美術館", "icon": "🎨", "image": "entertainment-artmuseum"},
            {"name": "アミューズメントパーク", "icon": "🎡", "image": "entertainment-amusementpark"},
            {"name": "その他（エンタメ）", "icon": "🎪"},
        ],
    },
    {
        "key": "other",
        "title": "その他",
        "description": "上記に当てはまらないサービスを探す",
        "icon": "🧩",
        "items": [
            {"name": "その他", "icon": "🧩"},
        ],
    },
]


def _build_reverse_lookup():
    lookup = {}
    for group in TAXONOMY:
        for item in group["items"]:
            lookup[item["name"]] = group["key"]
    return lookup


_SUBCATEGORY_TO_BUSINESS_TYPE = _build_reverse_lookup()

# 旧カテゴリー値（英語の固定文字列。導入前に登録された店舗が保持している）から
# 新しい (business_type, category) への移行マッピング
LEGACY_CATEGORY_MAP = {
    "RESTAURANT": ("restaurant", "その他（飲食店）"),
    "CAFE": ("restaurant", "カフェ"),
    "RAMEN": ("restaurant", "ラーメン"),
    "SUSHI": ("restaurant", "寿司・刺身"),
    "IZAKAYA": ("restaurant", "居酒屋"),
    "BAR": ("restaurant", "バー"),
    "BEAUTY": ("beauty", "その他（美容）"),
    "SALON": ("beauty", "ヘアサロン"),
    "CLINIC": ("medical", "一般クリニック"),
    "OTHER": ("other", "その他"),
}


def resolve_business_type(category: Optional[str]) -> Optional[str]:
    """サブカテゴリー名（Shop.category の値）から業種キー（business_type）を逆引きする。
    未知の値の場合は None を返す（自由入力の店舗名などを壊さないため）。"""
    if not category:
        return None
    return _SUBCATEGORY_TO_BUSINESS_TYPE.get(category)


def taxonomy_as_dict():
    """API レスポンス用に TAXONOMY をそのまま返す（リスト構造を維持して順序を保つ）"""
    return {"groups": TAXONOMY}
