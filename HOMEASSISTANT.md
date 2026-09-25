# HOMEASSISTANT.md

日立製ドラム洗濯機 (`custom_components/bigdrum/`) の **Home Assistant 統合の実装**に関するドキュメント。
通信プロトコル (UDP / クラウド API / 暗号化 / パケット形式 / 状態 blob の解釈) そのものは **PROTOCOL.md** を参照すること。

## インストール (HACS)

1. HACS → **Custom repositories** に `https://github.com/nullsnet/bigdrum` を category `Integration` で追加
2. **Hitachi Big Drum** を検索してダウンロード
3. Home Assistant を再起動
4. **設定 → デバイスとサービス → 統合を追加 → Hitachi Big Drum**

## 構成

- **Phase 1 (監視系)**: `sensor` / `binary_sensor`。coordinator が LAN UDP / クラウドで自動切替して状態を取得。
- **Phase 2 (操作系)**: サービス `bigdrum.start` / `bigdrum.stop` (CTRLINS)。
- `protocol.py` / `cloud.py` / `course.py` は HA 非依存のプロトコルモジュールで、**この側が単一ソース (canonical)**。HACS 配布は `custom_components/bigdrum/` だけコピーされるため自己完結にしている。CLI の `bigdrum/` は `bigdrum/_shared.py` (importlib) がこれらの .py を直接読んで `bigdrum.protocol` / `bigdrum.cloud` / `bigdrum.course` として `sys.modules` に登録し、コピーを置かない。HA 側の `__init__.py` (homeassistant / voluptuous を import) は実行しない。編集は必ずこの側で行う。

## 状態取得 (coordinator)

- 公式アプリと同じ方式: **LAN に到達** = UDP `MOVEINF` を 3秒間隔 / **到達不能** = クラウド `MOVEINF` を 60秒間隔。
- 残り時間 (`GETINF 0005`) と洗剤/柔軟剤残量 (`GETINF 0001`) は `MOVEINF` の blob だけでは不足するため補完取得 (PROTOCOL.md §6/§7)。
- クラウド鍵が古いと自動再ログイン (スロットル付き)。
- 取得の健全性は `coordinator.health()` で保持し `diagnostics` に出力 (秘密情報は秘匿)。
- **本体のバージョン** (`FWINFO` → `FWVER`) はファーム更新時のみ変わるため、状態更新 (LAN 直結 / クラウドの**どちらでも**) が成功したときに**スロットル付き** (初回 + 6h毎・スレッド実行) で取得しキャッシュ (`coordinator.device_info()`)。取得値は `DeviceInfo` の `sw_version` として**「デバイス 情報」のファームウェア欄に動的表示**され、`diagnostics` の `coordinator.device` にも `fw_ver` 等として出力。`FWINFO` はクラウド API 専用のため、**UDP 直結のみでクラウド認証情報を持たない設定では取得不可** (その場合は空)。

## エンティティ

### Sensors

| key | 表示名 | 内容 | 備考 |
|---|---|---|---|
| `status` | 運転状態 | `statusCode` → 日本語 (PROTOCOL.md §6.4) | 電源OFF・未報告 (クラウド GRP≠0/3/4・例 2) のときのみ「本体の電源が入っていないか、通信ができない状態です。」表示 (公式アプリと同じ・下記「電源OFF・stale の扱い」) |
| `phase` | 運転フェーズ | 洗い/すすぎ/脱水/乾燥 | フェーズ未定 (待機中など) は「不明」表示。故障の「切断」にはしない (PROTOCOL.md §6.4b) |
| `selCourse` | 選択コース | コース名 (日本語) | 下記「選択コースの日本語表示」 |
| `remainingTime` | 残り時間 | 分 (`device_class=duration`) | 0xFFFF sentinel (None) のみ非表示、真の 0 は表示 |
| `deviceTime` | 状態変化時刻 | 最終運転時刻 (`device_class=timestamp`) | `EntityCategory.DIAGNOSTIC` = ダッシュボード既定では非表示、デバイスの詳細ページ →「Diagnostics」セクションに項目として出る |
| `aiInfo` | AIお洗濯情報 | `aiInfoFlag` の各ビット (PROTOCOL.md §6.6) | `/` 区切り |
| `info` | お知らせ | 通知 | |
| `settings` | コース設定 | GETINF 0001 のコース設定 (PROTOCOL.md §7.1b) | **コース設定の要約**。native=主要4項目 (洗い/脱水/乾燥/すすぎ)。値は **現在選択コース (selCourse) のカタログ `disp`** で値ラベルに解決 (値→名称はコースで変わる: 例 arai=0C は 12分/60分/105分)。属性=コース設定全項目 (名称化可は日本語ラベル+名称、不可は生値)。**洗剤/柔軟剤は含まない** (下記独立センサー)。コース未登録・カタログ未取得時は生値。0001 未取得時は非表示 |
| `detergent` | 洗剤 自動投入量 | `senzaiKijun` の 16進投入量 (mL) | **コース非依存**。`getSenzaiKijun10()` 相当で 16進→10進して「N mL」表示 (0A→10 mL)。属性 `value`=生コード。フィールド欠落時は非表示 |
| `softener` | 柔軟剤 自動投入量 | `nyunanzaiKijun` の 16進投入量 (mL) | **コース非依存**。同様に 16進→10進して「N mL」表示 (0A→10 mL) |
| `transport` | 通信経路 | 直近の取得経路 (LAN / クラウド) | `EntityCategory.DIAGNOSTIC` = Diagnostics セクションに表示。state=最後に実際に取得に成功した経路 (stale 期間も保持)。属性: source (直近更新・stale 含む) / mode / device_addr / last_lan |

> **デバイス情報 (ファームウェア / MAC)**:
> - **ファームウェア**: 本体のバージョン (FWINFO の `FWVER`・例 `04051122`、PROTOCOL.md §9.2) を `DeviceInfo` の `sw_version` として**動的反映** (クラウド経由・スロットル取得 → 取得後に「デバイス 情報」のファームウェア欄へ表示)。
> - **MAC**: 製品の MAC 相当 (製品ID `dst` の `[8:20]`・アプリの「確認」画面 `CO00352Activity.dataUI` と同一、PROTOCOL.md §1) を `DeviceInfo.connections` の `CONNECTION_NETWORK_MAC` として登録 (小文字 `xx:xx:xx:xx:xx:xx`)。「デバイス 情報」に MAC が表示され、**同じ MAC のネットワーク機器 (OPNsense 等のトラッカー) と Linked devices で紐付く**。

### コース設定 / 洗剤・柔軟剤

- **`settings` (コース設定) = 要約 1 個**。コース相対の disp ベース 9 項目
  (arai/susugi/dassui/kanso/suiryo/sensor/jido_osouji/oyutori/onsui_mist) の要約。
  native=主要4項目 (洗い/脱水/乾燥/すすぎ)、属性=全項目 (名称化可は日本語ラベル+名称、不可は生値)。
  値は **現在選択コース (selCourse) のカタログ `disp`** で解決 (値→名称はコースで変わる: 例 arai=0C は 12分/60分/105分)。
- **`detergent` (洗剤) / `softener` (柔軟剤) = 独立センサー** (要約に含めない)。
  `senzaiKijun` / `nyunanzaiKijun` は **16進投入量 (mL)・コース非依存**。アプリは
  `getSenzaiKijun10()` = `BigInteger(hex,16).toString(10)` + " mL" と表示 (BD01070:311 等) で同一
  (0A→10 mL)。非 hex 値は生値にフォールバック。属性 `value`=生コード。
- **かつて**: 名称化できる 11 フィールドを個別エンティティ (`set_arai` 等) に分けていましたが、
  ステータス系と見分けにくく冗長だったため、コース相対な 9 項目は要約に集約し、
  コース非依存な洗剤/柔軟剤の 2 個だけ独立センサーとして残した。
- **フォールバック**: 現在コースがカタログに無い / カタログ未取得時は disp 項目が生コード
  (洗剤/柔軟剤の mL 変換はコース非依存なので継続)。0001 未取得時は非表示。

### Binary sensors

| key | 表示名 | 判定 |
|---|---|---|
| `running` | 運転中 | `statusCode` ∈ {06,07} か `process` ∈ {FA,FB} (PROTOCOL.md §6.4) |
| `remote_possible` | リモート操作可能 | `remoteOperationStatus` != "00" |
| `detergent_low` | 洗剤残量少 | `jidoTonyuZanryoFlag` bit0 (PROTOCOL.md §7.1) |
| `softener_low` | 柔軟剤残量少 | `jidoTonyuZanryoFlag` bit1 |
| `online` | オンライン | 電源ONかつ状態取得に到達中 (常時 available、電源OFF・未報告・無応答=off) |

### 電源OFF・stale の扱い (flapping 防止)

- 通常は LAN UDP (3s) を優先、不可ならクラウド (55s) にフォールバック (coordinator の自動切替)。
- 電源OFF時はクラウドが **GRP=2 (無報告・例外なし)** を返す。これはネットワーク障害ではなく
  本体OFFの**確定信号**。このとき coordinator は `protocol.no_report_parsed()` (公式アプリと同じ
  statusCode=00「本体の電源が入っていないか、通信ができない状態です。」) を**状態として保持**し、
  電源ON時の stale 旧データを**置き換える**。置き換えないと cloud 未達のサイクルで電源ON時の
  旧データが stale として復活し、`online` や状態系エンティティがオフ↔オンを ~55s 周期で
  往復 (flapping) する。
- 電源OFF・未報告 (`noReport`) 中の表示 (公式アプリと同じ・stale サイクルでも維持され flapping なし):
  - `status` (運転状態): **「本体の電源が入っていないか、通信ができない状態です。」**
  - `online`: **off** (常時 available)
  - `phase` / `remainingTime` / `settings` / `detergent` / `softener` / `selCourse` /
    `running` / `remote_possible`: **unavailable** (状態データが存在しないため)
  - `transport`: 最後に取得成功した経路を保持 (この場合は Cloud)
- 復帰 (電源ON) は次回ポーリング (cloud ≤55s / LAN はクールダウン解除後) にて反映。
- 一方、ネットワーク障害 (CloudError / UDP タイムアウト) のときは stale を維持して耐える
  (電源OFFの確定信号ではないため、`noReport` にはしない)。

### LAN 発見 (device_addr 未特定時) と失敗クールダウンの分離

- `device_addr` 未特定 (`None`) ときは UDP `SEARCH` (ブロードキャスト) で IP を探索 (PROTOCOL.md §5)。
  初回は即試行、失敗後は `SEARCH_RETRY` (10s) 間隔で再探索し、発見次第 `MOVEINF` へ継続する。
- **重要**: 「宛先未特定 (SEARCH 待ち)」の失敗は**失敗クールダウン (`LAN_FAIL_COOLDOWN` 60s) を
  セットしない**。以前は未特定時も `SEARCH` をサイクル番号の割込み (`cycle % 20`) に依存させていた
  ため、`SEARCH` 非実行サイクルの「宛先不明」エラーが 60s クールダウンを効かせ、そのクールダウンが
  次の `SEARCH` 周期を常に先送りする悪循環に陥り **`SEARCH` が永遠に実行されず LAN が一度も
  使えなかった**。発見待ち (再試行は `_lan` 内の `SEARCH_RETRY` が担当) と実失敗 (`_device_addr`
  が決まった上での `MOVEINF` 失敗 → 従来どおり 60s クールダウン) を分離することで修正。
- 診断: `transport` センサー属性の `last_lan` が `search-pending: ...` (発見待ち) / `error: ...`
  (実失敗) / `cooldown` / `ok` / `no-data` / `no-key` を示す。UDP 鍵自体が未取得なら `no-key`
  (クラウド設定フローで `ckdn_device_key` から抽出されない・再ログイン要) が原因。

### サービス

| サービス | 動作 |
|---|---|
| `bigdrum.start` | コース選択+開始 (CTRLC=0003)。`course_no` (任意 `delay_minutes` / `options`) を受け取り、COURSEINF から 42桁 CTRLD を組み立て送信 (PROTOCOL.md §8.4) |
| `bigdrum.stop` | 停止 (CTRLC=0001 / CTRLD=03) |

- 送信は **LAN UDP を優先**、不可ならクラウドへフォールバック。LAN で宛先が未特定なら
  送信前に `SEARCH` (ブロードキャスト) で IP を自動発見する。
- UI のフォーム項目は翻訳 (strings.json / translations の `services` 節) で
  「開始 / 停止」「コース」「予約開始 (N 分後・任意)」「対象の洗濯機 (任意)」と表示される。
- 複数台設定時は UI では **device セレクタ** (`device_id`) で洗濯機を選択する。
  YAML からは `device_id` または `entry_id` で指定可能 (未指定は 1 台のみならその台)。
  `device_id` の解決はデバイスレジストリ経由 (デバイスの `config_entries` に
  `hass.data[DOMAIN]` のエントリが唯一含まれるものを使用。0 件・複数件はエラー)。
- `start` の `course_no` は **select ドロップダウン** (コース名+操作種別、例
  「標準コース (洗乾)」= `1201`)。リストは BD-STX110G の COURSEINF カタログ
  (version `0100351720220929`・66コース) から services.yaml へ静的生成したもので、
  洗濯 (11xx/21xx) / 洗乾 (12xx/22xx) / 乾燥 (13xx) / その他 (槽洗浄・アイロン等) の順。
  **カタログ更新時は services.yaml のリストも更新する** (古いリストでも動作するが
  新コースが選べない)。YAML 呼び出しはリスト外の 4桁 hex も渡せる (カタログにあれば開始)。
- `start` の `options`: コース設定 11 項目 (suiryo/arai/susugi/dassui/kanso/onsui_mist/
  sensor/jido_osouji/oyutori/senzai/softener) の 2桁 hex 値で `initial` を上書き。
  未知名のキー・非 hex 値は `ServiceValidationError` で拒否 (壊れた CTRLD を送らない)。
  実 UI フォームには出さないため (services.yaml に未宣言)、YAML/オートメーション用。
- `start` の `delay_minutes` (UI: 「予約開始 (N 分後)」、数値入力 1-1440): **開始までの分数** (10進)。
  1時間後=60 / 3時間後=180。コードが `f"{m:04X}"` に変換して CTRLD 先頭4桁へ置く
  (PROTOCOL.md §8.4)。検証: `1..1440` の範囲外、コースの予約最低リードタイム
  (COURSEINF `yoyaku.initial`) 以下、は `ServiceValidationError`。
- `start` の `marker` (YAML のみ・上級者用): 4桁 hex の低レベル指定
  (`0000`=即時開始)。**`delay_minutes` と併用は不可** (両立時はエラー)。
- **制限**: `start` は CTRLD 組み立てのため COURSEINF (クラウド専用) を要するため
  **クラウド鍵が必須** (LAN 専用設定では使えない)。`stop` は LAN 専用でも使える。
- エラー: 本体が拒否 (UDP `GRP=NAK` / クラウド `SUCCESS≠0`)・経路なしは
  `HomeAssistantError`、入力 (course_no/options/delay_minutes/marker/device_id) が
  不正は `ServiceValidationError`。

## 選択コースの日本語表示

MOVEINF の `selCourse` (4桁) はそのままでは意味が分かりにくいため、COURSEINF カタログ (PROTOCOL.md §10) の `course_set_name` で解決して表示する。

- course_no の**先頭2桁=操作** (11=洗濯 / 12=洗乾 / 13=乾燥 …)、**末尾2桁=コース種別**。同じ `course_set_name` (例「標準コース」) を 1101/1201/1301 が共有し、`course_tab` (洗濯/洗乾/乾燥…) で操作を区別する。
- coordinator が**セットアップ時にカタログを背景で 1 回だけ**取得・キャッシュ (`cloud_courseinf`、クラウド専用)。LAN のみ (クラウド鍵なし) や取得失敗時は**生番号表示にフォールバック** (ログのみ・非ブロッキング)。
- エンティティ属性として `course_no` (生値) / `course_tab` を付与。

## 設定 (config flow)

- **クラウド (推奨)**: 日立メンバーズクラブの ID/パスワード + MEMBERS_KEY/IV でログインし、
  デバイスを指定するとクラウド鍵・トークン・UDP 鍵まで自動取得。自宅外からも利用可 (PROTOCOL.md §9)。
  1 台でもデバイス指定画面が表示され、ここで LAN 用の IP (任意) を設定できる。
  - `members_key` (16進 64桁) / `members_iv` (16進 32桁): 洗濯コンシェルジュアプリの APK から
    抽出する (ConstantCo.MEMBERS_ENCKEY / MEMBERS_IV)。README 参照。
- **LAN**: ペアリング済みの AES 鍵/IV (`udp_key` / `udp_iv`) と洗濯機の IP を手入力。
  インターネット不要だが洗濯機と同じネットワークが必須。
- 任意フィールド (クラウド・LAN 両フロー): `device_addr` (洗濯機の静的 IP。指定すると SEARCH 発見をスキップして直接ユニキャスト) / `broadcast_addr` (サブネットのブロードキャスト、既定 `255.255.255.255`。Docker bridge 等で `255.255.255.255` が届かない場合にサブネット指定) / `nickname`。
- 入力したメール/パスワード/KEY/IV は HA の `config_entries` に保存される。
- エンティティは config flow 完了後に自動生成。診断情報は **システム → 診断** からエクスポート可能。

## 対応機種と注意

- 動作確認済みは 2022 年以前世代の `BD-STX110G` のみ。他機種・新世代 (2022 年以降) では動作しない可能性がある。
- 操作系: `bigdrum.start` (コース選択+開始・`delay_minutes` による N分後の予約開始) / `bigdrum.stop` (停止) (Phase 2)。CTRLC=0005/0006 は未対応。
- `bigdrum.start` のコース選択リスト (services.yaml) は **BD-STX110G のカタログから静的生成** しているため、他機種ではリスト内容が異なる (更新が必要)。
- 自己責任で。家庭内の自分の洗濯機に対してのみ使用すること。
