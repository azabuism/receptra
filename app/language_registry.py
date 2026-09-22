"""
対応言語の分類体系（レジストリ）

Phase 5A（多言語AI受付）で導入する、店舗ごとの「AI受付が対応してよい言語」
「店頭スタッフが対応できる言語」の単一の情報源（single source of truth）。

- 言語の識別子は、DBに保存する際も、APIでやり取りする際も、常に言語コード
  （"ja" など、ISO 639-1 に準拠した小文字の固定キー）を使う。表示名は
  この登録簿から引く（フロントエンドの表示名だけを見て判定してはいけない）。
- 日本語（"ja"）は、AI受付にとって無効化できない必須言語（REQUIRED_AI_LANGUAGE）。
  店舗がAI対応言語から日本語を除外しようとしても、実際に有効になる言語リストには
  常に日本語が含まれる（ensure_ai_language_requirement / effective_ai_languages）。
- 既存店舗（この機能導入前に登録された店舗）は、AI対応言語・スタッフ対応言語の
  両方とも未設定（None）のまま残る。未設定・空リストは「日本語のみ」に安全に
  フォールバックする（effective_languages）。
"""

from typing import List, Optional, TypedDict


class LanguageInfo(TypedDict):
    display_name: str
    flag: str


# MVP対象の4言語。新しい言語を追加する場合はここに1エントリー追加するだけでよい
# （DBスキーマの変更は不要。Shop.ai_supported_languages / staff_supported_languages は
# 単なる文字列配列のJSONカラムのため）。
SUPPORTED_LANGUAGES: dict = {
    "ja": {"display_name": "日本語", "flag": "🇯🇵"},
    "en": {"display_name": "English", "flag": "🇺🇸"},
    "zh": {"display_name": "中文", "flag": "🇨🇳"},
    "ko": {"display_name": "한국어", "flag": "🇰🇷"},
}

# AI受付にとって無効化不可の必須言語。日本語対応の安定化はRECEPTRAの既存の
# 安全対策であり、この機能追加によって弱めてはならない。
REQUIRED_AI_LANGUAGE = "ja"


def is_valid_language_code(code: Optional[str]) -> bool:
    """有効な（登録済みの）言語コードかどうかを判定する。"""
    return isinstance(code, str) and code in SUPPORTED_LANGUAGES


def display_name_for(code: str) -> str:
    """言語コードから表示名を引く。未知のコードの場合はコードそのものを返す
    （フロントエンドの表示が壊れないようにするため）。"""
    info = SUPPORTED_LANGUAGES.get(code)
    return info["display_name"] if info else code


def flag_for(code: str) -> str:
    """言語コードから国旗絵文字を引く。未知のコードの場合は空文字を返す。"""
    info = SUPPORTED_LANGUAGES.get(code)
    return info["flag"] if info else ""


def normalize_language_list(codes: Optional[List[str]]) -> List[str]:
    """入力値（None・空・不正な要素を含む可能性がある）を、有効な言語コードのみの
    重複なしリストに正規化する。順序は入力順を維持する。検証エラーは送出しない
    （表示用など、寛容な変換が必要な場面向け）。"""
    if not codes:
        return []
    result: List[str] = []
    for code in codes:
        if is_valid_language_code(code) and code not in result:
            result.append(code)
    return result


def validate_language_codes(codes: Optional[List[str]], field_name: str = "languages") -> List[str]:
    """リクエストで渡された言語コードリストを検証する（店舗設定の保存時など、
    不正な入力を明示的に拒否したい場面向け）。

    - None は空リストとして扱う（未設定を許可する）。
    - リスト型でない場合、または未登録の言語コードが含まれる場合は ValueError を送出する。
    - 重複は入力順を維持したまま除去する。
    """
    if codes is None:
        return []
    if not isinstance(codes, list):
        raise ValueError(f"{field_name} には言語コードのリストを指定してください")
    invalid = [c for c in codes if not is_valid_language_code(c)]
    if invalid:
        raise ValueError(f"{field_name} に未対応の言語コードが含まれています: {invalid}")
    result: List[str] = []
    for code in codes:
        if code not in result:
            result.append(code)
    return result


def effective_languages(codes: Optional[List[str]]) -> List[str]:
    """DBに保存された値（None も含む）から、実際に有効な言語リストを返す。
    未設定・空・全て不正な場合は日本語のみにフォールバックする
    （既存店舗を安全に「日本語のみ」として扱うためのデフォルト）。"""
    normalized = normalize_language_list(codes)
    return normalized if normalized else [REQUIRED_AI_LANGUAGE]


def effective_ai_languages(codes: Optional[List[str]]) -> List[str]:
    """AI受付が実際に対応してよい言語リストを返す。日本語は無効化不可のため、
    保存値に含まれていなくても必ず先頭に含める。"""
    languages = effective_languages(codes)
    if REQUIRED_AI_LANGUAGE not in languages:
        languages = [REQUIRED_AI_LANGUAGE] + languages
    return languages


def languages_as_dict() -> dict:
    """API/フロントエンド用に SUPPORTED_LANGUAGES をそのまま返す。"""
    return {"languages": SUPPORTED_LANGUAGES}
