# 画像ファイルのインストール手順

## 概要
updated されたHTMLファイルは既にインストール済みです。
次に、最適化された画像ファイル（WebP + JPEG）をこのディレクトリにコピーする必要があります。

## 必要な画像ファイル（合計14ファイル）

### WebP形式（最新ブラウザ対応）
- category-restaurant.webp
- category-beauty.webp
- category-hotel.webp
- category-education.webp
- category-medical.webp
- category-fitness.webp
- category-entertainment.webp

### JPEG形式（フォールバック）
- category-restaurant.jpg
- category-beauty.jpg
- category-hotel.jpg
- category-education.jpg
- category-medical.jpg
- category-fitness.jpg
- category-entertainment.jpg

## ファイルサイズ
- WebPファイル: 9-13KB（合計83KB）
- JPEGファイル: 15-21KB（合計129KB）
- 合計: 約212KB

## インストール方法

### 方法1: tar.gzアーカイブから抽出
クラウドで準備されたアーカイブファイル `category-images.tar.gz` を使用します。

```bash
# アーカイブを展開
tar xzf category-images.tar.gz -C ~/www/receptra/frontend/

# ファイルを確認
ls -lh ~/www/receptra/frontend/category-*.{webp,jpg}
```

### 方法2: 個別ファイルとしてコピー
各ファイルをクラウドストレージから個別にダウンロードしてこのディレクトリに配置します。

## 確認
すべてのファイルが揃ったら、以下を実行して確認します：

```bash
# ファイル数確認（14ファイルであることを確認）
ls -1 category-*.{webp,jpg} 2>/dev/null | wc -l

# ファイルサイズ確認
ls -lh category-*.{webp,jpg} | awk '{sum+=$5} END {print "Total:", sum}'
```

## テスト

ホームページをブラウザで開いて、画像が表示されることを確認してください。

```bash
# ローカルサーバーでテスト
python3 -m http.server 8000 &
# その後ブラウザで http://localhost:8000/receptra-homepage-improved.html にアクセス
```

## 注記
- HTMLファイルは既に更新済みです（オレンジ色スキーム＆<picture>タグ）
- WebP形式をサポートするブラウザでは自動的にWebPが読み込まれます
- その他のブラウザではJPEGがフォールバックとして使用されます
- すべてのファイルは遅延ロード対応です

