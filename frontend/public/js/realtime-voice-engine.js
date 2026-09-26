        const params = new URLSearchParams(window.location.search);
        const shopId = params.get('id');
        // ?debug=1 を付けて開いた場合のみ、技術者向けの通話状態・レイテンシ・
        // イベントログを表示する。一般のお客様向けの画面はシンプルに保つ。
        const debugMode = params.get('debug') === '1';
        if (debugMode) {
            document.getElementById('debugPanels').style.display = 'block';
            document.getElementById('debugBadge').style.display = 'inline-block';
            // CRITICAL INCIDENT調査（音声ブツブツ/ノイズ→応答停止）: このボタンは
            // debugMode時のみ表示されるため、リスナーの登録もdebugMode時のみ行う。
            const copyBtnEl = document.getElementById('copyDebugLogBtn');
            if (copyBtnEl) copyBtnEl.addEventListener('click', copyDebugLog);
        }
        // ?noagc=1 は debug/test専用のマイク制約比較モード（Production利用者の
        // defaultは変更しない）。autoGainControlをfalseで要求した場合に、
        // 無音時/BGM時/雑談時/実発話時のマイクレベルやspeech_started頻度が
        // どう変わるかを比較調査する目的のみに使う。debugMode=falseの場合は
        // このパラメータを無視し、通常どおりautoGainControl: trueで動作する
        // （?noagc=1単体をお客様が誤って付けても、?debug=1を伴わない限り
        // Production挙動は一切変わらない）。
        const noAgcMode = debugMode && params.get('noagc') === '1';
        if (noAgcMode) {
            logEvent('[debug] ?noagc=1: autoGainControl=falseで比較計測します（Production defaultはtrueのまま変更なし）');
        }

        // ===== NAME Forced Commit Observation PoC（今回追加・観測専用） =====
        // 目的: NAME Answer Window（~5秒）到達時、正常なsemantic_vadのターン
        // 終了（speech_stopped/committed/item_created）が間に合わなかった
        // 場合に、input_audio_buffer.commitを最大1回だけ手動送信し、OpenAI
        // Realtime側がその後どう振る舞うか（committed/item.created/
        // response.created/AI音声まで自動的に進むのか、empty buffer等の
        // errorになるのか）を実際のサーバー挙動として観測する。
        // response.createは絶対に追加送信しない（詳細はmaybeSendNameCommitPoc
        // 定義部のコメント参照）。
        //
        // 安全条件（最重要・必ず両方満たすこと）: このPoCは、今回の検証のために
        // 新規作成した、BARIYON自身のtenant配下のテスト専用店舗
        // 「RECEPTRA Realtime PoC Test」のshop_idと、debug=1の両方が揃った
        // 場合にのみ有効化する。どちらか一方だけでは絶対に有効化しない。
        // 既存の本番利用店舗・他テナントの店舗（デモクリニック・ダニエル等）には
        // 一切影響しない。
        const NAME_COMMIT_POC_SHOP_ID = '65932cb5-97db-460e-b9d6-0471fce23d88'; // 「RECEPTRA Realtime PoC Test」（BARIYON自テナント作成・実店舗ではない）
        const nameCommitPocEnabled = debugMode && shopId === NAME_COMMIT_POC_SHOP_ID;

        // ===== Conversation Takeover Observation PoC（今回追加・観測専用） =====
        // 目的: NAME Forced Commit Observation PoCとは別目的の、独立したPoC。
        // 「長い自由発話（症状説明・来店理由の長い説明等）が終わらない場合に、
        // 一定時間で強制的にターンを成立させたらOpenAI Realtime側が実際に
        // どう振る舞うか」を安全に観測する。NAME側と同じ理由
        // （response.createの二重送信リスク・turn_detection有効時の手動commitが
        // 公式にサポートされた組み合わせではないこと）により、こちらも
        // input_audio_buffer.commitのみを最大1回送信する観測専用の仕組みとし、
        // response.createは絶対に追加送信しない（詳細はmaybeSendTakeoverCommitPoc
        // 定義部のコメント参照）。
        //
        // 安全条件はNAME PoCと全く同じ（両方満たす場合のみ有効化）:
        // PoC専用テスト店舗「RECEPTRA Realtime PoC Test」のshop_id + debug=1。
        // 既存の本番利用店舗・他テナントの店舗には一切影響しない。
        //
        // NAME PoCとの関係（重要・意図的な設計）: 同じshop_id定数
        // (NAME_COMMIT_POC_SHOP_ID)を参照するが、有効化フラグ・状態変数・
        // 関数はNAME側と完全に独立させている（1つの巨大なForced Turn
        // Completion機構へ統合しない、という明示的な方針に基づく）。
        const takeoverPocEnabled = debugMode && shopId === NAME_COMMIT_POC_SHOP_ID;

        // ===== LOCAL SILENCE ASSIST — OBSERVATION PoC（FAST TURN 4・観測専用） =====
        // 目的: 「お客様の発話がブラウザ側から見て実質的に終わったタイミング」
        // と、OpenAI semantic_vadが実際にinput_audio_buffer.speech_stoppedを
        // 送ってくるタイミングの差（local_to_server_gap）を実測するための、
        // 純粋な観測機能。
        //
        // 最重要（絶対に守ること）: このPoCはinput_audio_buffer.commit /
        // response.create / response.cancel / input_audio_buffer.clear /
        // conversation.item.truncate を含む、いかなるRealtime会話制御イベントも
        // 一切送信しない。既存のNAME/Takeover/Short Choice/PHONE/SHORT_ANSWER
        // Forced Commit群とも接続しない（完全に独立）。既存のsemantic_vad・
        // eagerness・Forced Commit・Conversation Takeoverの動作には一切介入
        // せず、通話結果（予約成立の可否・AIの応答内容）は現在の本番と
        // 完全に同一のまま変わらない。
        //
        // 安全条件（NAME/Takeover PoCと全く同じ・両方満たす場合のみ有効化）:
        // PoC専用テスト店舗「RECEPTRA Realtime PoC Test」のshop_id + debug=1。
        // 一般店舗・Public AI Call（call.html。CSSで#debugPanels自体を
        // !importantで常時非表示にしているため、そもそもdebugMode関連の
        // 見た目は一切出ない）には一切影響しない。新しいdebug UI（パネル・
        // ボタン等）も追加しない。既存のlogEvent/pushTimelineEventにログ行が
        // 増えるだけで、これらは既存の#debugPanels（debug=1時のみ表示）配下の
        // 既存表示にそのまま乗るため、一般のお客様向け画面には何も表示されない。
        //
        // 使用する音声パイプライン: 新しいAudioContext/AnalyserNodeは作らず、
        // 既存のsetupMicLevelMeter()が既に毎フレーム計算しているマイク音量
        // （pct値・後述のtick()内）をそのまま再利用する。MediaStreamの複製も
        // 行わない。
        //
        // fail-open設計: 以下のロジックはすべてtry/catchで包み、万一例外が
        // 発生しても通話・音声・予約処理には一切影響しない（観測専用機能の
        // 失敗で通話が止まることは絶対に無いようにする）。
        const LOCAL_SILENCE_POC_SHOP_ID = NAME_COMMIT_POC_SHOP_ID; // 既存PoC専用テスト店舗をそのまま流用
        const localSilenceObservationEnabled = debugMode && shopId === LOCAL_SILENCE_POC_SHOP_ID;
        // 観測上の「無音候補」を作るためだけの暫定閾値・下限。今回はcommit判断に
        // 一切使用しない（FAST TURN 4.1で実測データを見てから閾値を検討する）。
        const LOCAL_SILENCE_CANDIDATE_THRESHOLD_PCT = 8;
        const LOCAL_SILENCE_MIN_PAUSE_MS = 150; // これより短い無音候補はログのノイズとして記録しない
        // 「発話開始後まだ一度も音を検知していない」世代をまたいだ誤相関を防ぐための
        // 世代カウンタ（既存Answer Window/Forced Commit群と同じ設計）。
        let localSilenceGeneration = 0;
        let localSilenceActive = false; // 現在のターンを観測中かどうか
        let localSilenceFirstSpeechDetectedAt = null; // このターンで最初に閾値を超えた時刻
        let localSilenceCandidateStartedAt = null; // 現在継続中の無音候補の開始時刻（無ければnull）
        let localSilencePauseCandidates = []; // このターン中、声が戻ってきて終わった無音候補(ms)の一覧
        let localSilencePeakPct = null;
        let localSilenceSumPct = 0;
        let localSilenceSampleCount = 0;
        // 発話ターン外（非アクティブ時）のマイク音量から緩やかに更新する、
        // 観測用の参考値（簡易的な背景ノイズレベル。会話制御には使用しない）。
        let localSilenceBackgroundFloorPct = null;

        document.getElementById('backLink').href = shopId ? ('/shop.html?id=' + encodeURIComponent(shopId)) : '/';

        const loadingEl = document.getElementById('loading');
        const notFoundEl = document.getElementById('not-found');
        const contentEl = document.getElementById('content');
        const pageShopName = document.getElementById('pageShopName');
        const bigMic = document.getElementById('bigMic');
        const statusText = document.getElementById('statusText');
        const subStatusText = document.getElementById('subStatusText');
        const startBtn = document.getElementById('startBtn');
        const endBtn = document.getElementById('endBtn');
        const eventLogEl = document.getElementById('eventLog');
        const latestLatencyEl = document.getElementById('latestLatency');
        const avgLatencyEl = document.getElementById('avgLatency');
        const latencyCountEl = document.getElementById('latencyCount');
        const errorBannerEl = document.getElementById('errorBanner');
        const micLevelFillEl = document.getElementById('micLevelFill');
        const stMicPermEl = document.getElementById('stMicPerm');
        const stMicTrackEl = document.getElementById('stMicTrack');
        const stWebrtcEl = document.getElementById('stWebrtc');
        const stSessionEl = document.getElementById('stSession');
        const stUserSpeakEl = document.getElementById('stUserSpeak');
        const stAiSpeakEl = document.getElementById('stAiSpeak');
        const usageElapsedEl = document.getElementById('usageElapsed');
        const usageResponseCountEl = document.getElementById('usageResponseCount');
        const usageInputAudioEl = document.getElementById('usageInputAudio');
        const usageInputTextEl = document.getElementById('usageInputText');
        const usageCachedEl = document.getElementById('usageCached');
        const usageOutputAudioEl = document.getElementById('usageOutputAudio');
        const usageOutputTextEl = document.getElementById('usageOutputText');
        const usageTotalTokensEl = document.getElementById('usageTotalTokens');
        const usageLogEl = document.getElementById('usageLog');
        const playbackRecoverBtn = document.getElementById('playbackRecoverBtn');

        if (!shopId) {
            loadingEl.style.display = 'none';
            notFoundEl.style.display = 'block';
            throw new Error('shop id missing');
        }
        loadingEl.style.display = 'none';
        contentEl.style.display = 'flex';

        // NAME Forced Commit Observation PoC: debug=1のときのみ、今回の通話で
        // このPoCが有効かどうかをインジケーターと簡易ログに反映する
        // （一般のお客様向け画面には一切表示しない。#debugPanels自体が
        // debug=1のときのみdisplay:blockになるため、このインジケーターも
        // それに連動する）。
        if (debugMode) {
            const pocIndicatorEl = document.getElementById('nameCommitPocIndicator');
            if (pocIndicatorEl) {
                pocIndicatorEl.textContent = 'NAME Commit PoC: ' + (nameCommitPocEnabled ? 'ENABLED' : 'DISABLED');
            }
            // Conversation Takeover Observation PoC: NAME側と同じ表示方針
            // （PoC専用テスト店舗+debug=1のときのみENABLED表示）。
            const takeoverIndicatorEl = document.getElementById('takeoverPocIndicator');
            if (takeoverIndicatorEl) {
                takeoverIndicatorEl.textContent = 'Takeover PoC: ' + (takeoverPocEnabled ? 'ENABLED' : 'DISABLED');
            }
        }

        // ===== イベントログ =====
        function logEvent(text) {
            const t = new Date().toLocaleTimeString('ja-JP', { hour12: false }) + '.' + String(new Date().getMilliseconds()).padStart(3, '0');
            eventLogEl.textContent += `[${t}] ${text}\n`;
            eventLogEl.scrollTop = eventLogEl.scrollHeight;
        }

        // ===== 通話状態グリッド =====
        function setStatus(el, text, cls) {
            el.textContent = text;
            el.classList.remove('ok', 'warn', 'bad');
            if (cls) el.classList.add(cls);
        }
        function showErrorBanner(text) {
            errorBannerEl.textContent = text;
            errorBannerEl.style.display = 'block';
        }
        function hideErrorBanner() {
            errorBannerEl.style.display = 'none';
        }

        // ===== secure context / API可用性チェック =====
        // getUserMediaはHTTPS(またはlocalhost)のsecure contextでしか使えない。
        // 本番はRailway上でHTTPS配信されているためここは通常問題ないが、
        // 「マイク許可ダイアログが出ない」という報告があった際に、まずこの
        // 前提条件そのものが崩れていないかを実機の画面上で確認できるようにする。
        (function checkSecureContext() {
            const isSecure = window.isSecureContext;
            const hasMediaDevices = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
            logEvent('isSecureContext=' + isSecure + ' / navigator.mediaDevices=' + !!navigator.mediaDevices
                + ' / getUserMedia=' + hasMediaDevices + ' / URL=' + location.href);
            if (!isSecure || !hasMediaDevices) {
                showErrorBanner(
                    '⚠️ このページはマイクを使用できる環境（HTTPS）で開かれていないか、'
                    + 'このブラウザがマイク機能に対応していません。URLがhttps://で始まっているか確認してください。'
                );
                setStatus(stMicPermEl, '利用不可（非secure context）', 'bad');
                startBtn.disabled = true;
            }
        })();

        // ===== レイテンシ計測 =====
        // OpenAI Realtime APIのイベント名・仕様は変更が速い領域のため、特定の
        // JSON イベント名だけに依存せず、「サーバーが発話終了を検知した時刻
        // (input_audio_buffer.speech_stopped)」から「実際にAIの声が聞こえ始めた
        // 時刻（WebRTCで受信した音声トラックの音量が実際に立ち上がった瞬間、
        // Web Audio APIで検出）」までを直接計測する。これにより、WebRTC接続では
        // 音声チャンクがJSONイベントとしては流れてこない（音声トラックで直接
        // 流れる）という現在の仕様変更の影響を受けずに、体感レイテンシを
        // そのまま測定できる。
        let lastSpeechStoppedAt = null;
        let aiSpeakingNow = false;
        const latencySamples = [];
        // 無言化調査用（PHASE1/2/6）: 直近のinput_audio_buffer.speech_startedの
        // 発生時刻と、その瞬間AI音声出力中だったかどうかのスナップショット。
        // speech_stopped側で「発話継続時間」を計算するためだけに使う一時変数
        // （音声内容は一切保持しない、数値のタイムスタンプとbooleanのみ）。
        let lastSpeechStartedAt = null;
        let speechStartedDuringAiOutput = false;

        // FAST TURN 2（Section3/20）: ターンレイテンシーの内訳計測用。
        // 既存のlastSpeechStoppedAtベースの計測（発話終了→AI発話検知、上記）は
        // 既に(E)の値そのものを取得できているため、新しいログ機構は追加せず、
        // 既存の3イベントハンドラ（committed / response.created / AI音声検知）に
        // 数値の差分計算を数行追加するだけで内訳(B)(C)(D)を得る。
        // 値はミリ秒の数値のみ・音声内容やPIIは一切含まない。debugMode=falseの
        // 画面には影響しない（logEvent自体は#debugPanels配下にのみ表示される
        // 既存の仕組みにそのまま乗せる）。callGeneration単位の厳密なスコープ管理は
        // 行わない（既存のlastSpeechStoppedAt等も同様の単純なnull管理のため、
        // 既存パターンとの一貫性を優先）。
        let lastCommittedAtForLatency = null;
        let lastResponseCreatedAtForLatency = null;
        let turnLatencySpeechDurationMs = null;
        let turnLatencyVadTailMs = null;
        let turnLatencyCommitToResponseMs = null;
        let turnLatencyFastTurnLabel = null;
        // FAST TURN 3.1（実機レイテンシ診断）: Tool Call（check_availability等）
        // 自体の所要時間を計測するための一時変数。既存のTOOL_FETCH_STARTED/
        // TOOL_FETCH_SUCCESS(ERROR)イベントはタイムスタンプ付きでタイムライン
        // には既に記録されているが、TURN LATENCY 1行ログには含まれていなかった。
        // また、下のresponse.created側の修正と対で、Tool呼び出しの往復時間が
        // commit_to_responseへ誤って合算されるのを防ぐ（詳細は該当箇所）。
        // 値はミリ秒の数値・Tool名（PIIなし。check_availability等の固定文字列
        // のみ）のみで、動作（VAD/Tool呼び出し自体）は一切変更しない。
        let toolCallStartedAtForLatency = null;
        let turnLatencyToolOccurred = false;
        let turnLatencyToolName = null;
        let turnLatencyToolDurationMs = null;

        // FAST TURN 3.6B（Tool Continuation Proof）: function_call_output送信
        // からAI音声再生開始までの一連のイベント（T0〜T10）を、debugMode限定で
        // 相関追跡するための一時状態。call_idそのものはPIIではないが表示は
        // 短縮形に留める（下のtoolContinuationTraceShortId参照）。この状態は
        // 観測のみに使われ、Tool呼び出し・Realtime制御イベントの送信有無や
        // 内容を一切変更しない（既存のsendResponseCreate等の判断ロジックは
        // 無変更）。1ターンにつき1つのTool継続トレースのみを保持する単純な
        // null管理（既存のlastCommittedAtForLatency等と同じパターン）。
        let toolContinuationTraceCallId = null;
        let toolContinuationTraceShortId = null;
        let toolContinuationTraceT0 = null;
        let toolContinuationTraceActive = false;

        // ===== FAST TURN HOTFIX 4（今回追加）: TOOL CONTINUATION RESPONSE
        // WATCHDOG（診断専用・観測のみ） =====
        // 背景: 実機で「明日、二人で12時に予約したいです」に対しAIが
        // 「承知しました。12時のお時間ですね」まで発話した直後に完全に
        // 無応答で停止した事例が報告された。check_availabilityはdate/time/
        // party_sizeの3つだけで呼び出し可能（既存のTool定義で確認済み）で、
        // かつそのTool説明文自体が「確認を待たず必ずこの関数を呼び出して
        // ください」と明記しているため、この発話だけで（お名前・電話番号を
        // 待たずに）check_availabilityが呼ばれた可能性がある。もしそうなら、
        // 「承知しました。12時のお時間ですね」はsystem prompt既存の
        // 「Tool呼び出しの直前にできるだけ一言添える」に沿った一言であり、
        // 続く空き状況案内＋次の質問は本来Tool結果を受けた2回目の
        // response.created（T7）で発話されるはずだったが、それが一度も
        // 届かなかった可能性がある。
        // 監査の結果、T6（response.create送信成功）からT7（response.created
        // 受信）までの間には、現在いかなるタイムアウト・フォールバックも
        // 存在しないことを確認した（sendResponseCreateはdc.send()の成功可否
        // しか見ておらず、OpenAI側が実際にresponse.createdを返すかどうかは
        // 一切追跡していない）。これは前フェーズで発見した「commit後に
        // response.createdが一切届かない」構造的リスクの、Tool継続チェーン版
        // にあたる。
        // 今回はこの空白を埋める挙動変更（新しいresponse.create再送信や
        // タイムアウト後の代替発話等）は一切行わない（証拠不十分なため）。
        // 代わりに、この空白が実際に発生しているかどうかを次の実機テストで
        // 確実に可視化するため、T6送信後、一定時間T7が届かなければ
        // 「観測専用」の1行をタイムラインへ記録するだけのwatchdogを追加する。
        // 何もsend/再試行はしない。既存のtoolContinuationTraceActive/
        // toolContinuationTraceCallIdの値をreadするだけで、書き換えない。
        const TOOL_CONTINUATION_RESPONSE_WATCHDOG_MS = 8000; // 8秒: 通常のTool往復+モデル生成時間に対し十分な余裕を見た診断用の目安値（挙動には無関係）
        let toolContinuationWatchdogTimerId = null;
        let toolContinuationWatchdogForCallId = null;

        function armToolContinuationResponseWatchdog(forCallId) {
            cancelToolContinuationResponseWatchdog('rearm');
            toolContinuationWatchdogForCallId = forCallId;
            toolContinuationWatchdogTimerId = setTimeout(() => {
                toolContinuationWatchdogTimerId = null;
                // 診断専用: ここでは絶対に何も送信しない（response.create再送信
                // 等は一切行わない）。同じcall_idのトレースがまだアクティブな
                // ままT7に到達していない場合にのみ、観測用の1行を記録する。
                if (toolContinuationTraceActive && toolContinuationTraceCallId === forCallId) {
                    pushToolContinuationTrace('TOOL_CONTINUATION_WATCHDOG_NO_RESPONSE_CREATED_AFTER_T6 (waitedMs='
                        + TOOL_CONTINUATION_RESPONSE_WATCHDOG_MS + ', diagnostic_only_no_action_taken=true)');
                    // FAST TURN HOTFIX 5（今回追加）: pushToolContinuationTrace/
                    // pushTimelineEventはDOM（#diagTimeline・Copy Debug Log用の
                    // recentEvents配列）にしか書き込まず、ブラウザの開発者
                    // Consoleには一切出力していなかったことが監査で判明した
                    // （実機でこのWATCHDOGを"Console"で検索しても見つからない
                    // のは、発火していないからではなく、そもそもConsoleに
                    // 出していなかったことが原因の可能性がある）。この事実を
                    // 次回の実機テストで正しく確認できるよう、console.logへも
                    // 明示的に出す（送信・再試行等の動作は一切追加しない）。
                    console.log('[TOOL_CONTINUATION_WATCHDOG_NO_RESPONSE_CREATED_AFTER_T6] callIdTail='
                        + String(forCallId || '').slice(-8) + ' waitedMs=' + TOOL_CONTINUATION_RESPONSE_WATCHDOG_MS);
                }
            }, TOOL_CONTINUATION_RESPONSE_WATCHDOG_MS);
        }

        function cancelToolContinuationResponseWatchdog(reason) {
            if (toolContinuationWatchdogTimerId === null) return;
            clearTimeout(toolContinuationWatchdogTimerId);
            toolContinuationWatchdogTimerId = null;
            toolContinuationWatchdogForCallId = null;
        }

        function recordLatencySample(ms) {
            latencySamples.push(ms);
            latestLatencyEl.textContent = Math.round(ms) + ' ms';
            const avg = latencySamples.reduce((a, b) => a + b, 0) / latencySamples.length;
            avgLatencyEl.textContent = Math.round(avg) + ' ms';
            latencyCountEl.textContent = String(latencySamples.length);
            logEvent(`レイテンシ計測: ${Math.round(ms)}ms（発話終了 → AI発話検知）`);
            if (zeroWaitAwaitingFirstPostGreetingLatency) {
                zeroWaitAwaitingFirstPostGreetingLatency = false;
                logEvent('[ZeroWait] KPI-5 Greeting後の最初のお客様発話→AI応答開始 = ' + Math.round(ms) + 'ms');
            }
        }

        // ===== 音声診断用の状態（PHASE1〜11: マイクINPUT〜AI音声OUTPUT経路の
        // 各段階を観測可能にするためだけの変数群。PIIは一切保持しない）=====
        // AudioContextの参照そのものを保持していなかったため、通話中に
        // running/suspendedのどちらかを後から確認する手段が無かった
        // （PHASE6の調査対象）。ここに保持するだけで、resume()等の挙動変更は
        // 一切行わない。
        let micAudioCtx = null;
        let remoteAudioCtx = null;
        // リモート（AI）音声トラック・再生の状態（PHASE4/5/11用）。
        let remoteAudioEl = null;
        let remoteTrackReceived = false;
        let remoteTrackMuted = null;
        let remoteTrackReadyState = null;
        // 'unknown' | 'playing' | 'blocked' | 'paused'
        let remotePlayState = 'unknown';
        // 送信側（あなたの声）のWebRTC統計トレンド（PHASE3/11用）。
        // 'unknown' | 'increasing' | 'stopped'
        let outboundPacketsTrend = 'unknown';
        let lastOutboundAudioStats = null;
        let statsPollIntervalId = null;
        // Mobile Audio Reliability Phase PHASE6/7: packetsSentとは別に、実際の
        // 音声エネルギー(totalAudioEnergy)の増減も独立して追跡する（「packetは
        // 送っているが実質的な音声が無い」ケースを区別するため）。ブラウザが
        // totalAudioEnergyを提供しない場合は'unsupported'のまま。
        // 'unknown' | 'increasing' | 'stopped' | 'unsupported'
        let audioEnergyTrend = 'unknown';
        let lastOutboundAudioEnergy = null;
        // 再調査（第2ラウンド・Inbound Audio診断）: 受信側（AIの声）のWebRTC
        // inbound-rtp統計。OpenAI→ブラウザへの音声パケット自体が途切れて
        // いるのか（packetsReceived停止・packetsLost増加・jitter急増）、
        // パケットは正常に届いているのにHTMLAudioElement側の再生だけが
        // 途切れているのか（audioEl側のwaiting/stalledイベントだけが発生）を
        // 切り分けるために追加。ブラウザが対応しない項目はN/Aのまま。
        // 'unknown' | 'increasing' | 'stopped'
        let inboundPacketsTrend = 'unknown';
        let lastInboundAudioStats = null;
        let lastPacketsLost = null;
        let lastJitter = null;
        // CRITICAL INCIDENT調査（音声ブツブツ/ノイズ）: concealedSamples系は
        // 「WebRTCが受信側でパケットロス/ジッタを取り繕うために代替音声で
        // 穴埋めした量」を示す、ブツブツ/プチプチ音の最も直接的な技術的
        // シグナル。jitterBufferDelay/jitterBufferEmittedCountは受信側の
        // バッファリング遅延の指標。既存のpacketsLost/jitterと同じ
        // typeof安全ガード方式で追加するだけで、収集の仕組み自体は変更しない。
        // ブラウザが対応しない場合はN/Aのまま（存在しないfieldを仮定しない）。
        let lastConcealedSamples = null;
        let lastSilentConcealedSamples = null;
        let lastJitterBufferDelay = null;
        let lastJitterBufferEmittedCount = null;
        // PHASE9: ユーザー発話検知(VAD)の現在状態。'idle' | 'speech'
        let userVadState = 'idle';
        // PHASE10: AI応答生成の現在状態。'idle' | 'active' | 'done' | 'error'
        let responseState = 'idle';

        // ===== Expected Answer Window（雑音による名前/電話番号ターン長時間化調査） =====
        //
        // 背景（実機症状）: 周囲に人の声・雑音があると、AIが「谷村です」等の
        // 短い回答の後も雑音を発話の続きとして拾ってしまい、
        // input_audio_buffer.speech_stopped がなかなか来ず、名前の回答ターンが
        // 終了しないことがある。
        //
        // 設計方針（重要・必ず守ること。今回の監査結果に基づく）:
        // - semantic_vad自体のグローバル設定（eagerness等）は一切変更しない。
        // - 「全会話を5秒で切る」ものではなく、直前にAIがNAME/PHONE/YES_NOの
        //   ような短い定型回答を尋ねた直後にだけ適用する軽量な状態
        //   （expectedAnswerType）と、その経過時間の観測用タイマーに限定する。
        // - 現時点ではこの区間は【観測のみ】である。タイムボックス到達時に
        //   input_audio_buffer.commit を手動送信してターンを強制終了させる
        //   PHASE3の仕組みは、今回の監査でOpenAI Realtime API公式ドキュメント
        //   および実機の外部事例（OpenAI Developer Community等）を確認した結果、
        //   turn_detection（semantic_vad/server_vad）が有効な状態での手動commitは
        //   公式にサポートされた組み合わせではなく、"buffer has 0ms of audio"等の
        //   エラーを誘発する実例が確認されたため、本番の実通話へ影響するリスクが
        //   排除できないと判断し、今回は実装を見送った（完了報告で詳細を説明する）。
        //   そのため、ANSWER_WINDOW_EXPIRED後もOpenAIへは何も送信せず、既存の
        //   semantic_vadによる通常のターン終了検知をそのまま待つ（挙動は今回
        //   変更していない。観測用ログが増えるだけ）。
        // - expectedAnswerTypeの判定は、AI自身の発話内容
        //   （response.output_audio_transcript.done）に対する簡易なキーワード
        //   一致のみで行う軽量なヒューリスティックであり、大きな状態機械は
        //   作らない。判定できない場合はNONEのまま（誤ってNAME/PHONE扱いに
        //   しない安全側フォールバック）。
        // - タイマーの起点はAIの発話開始ではなく、ユーザーが実際に話し始めた
        //   input_audio_buffer.speech_started を起点にする（PHASE6の明示的な
        //   指示）。
        // - PII（氏名・電話番号等の内容）はタイムラインに一切記録しない。
        //   記録するのはtype文字列（NAME/PHONE/YES_NO/VISIT_REASON）・
        //   経過時間(ms)のみ。
        // 追加要件（Conversation Time Control）: 来店理由（VISIT_REASON）も
        // 同じ仕組みに追加する。ただしこれも従来通り【観測のみ】であり、
        // 30秒到達だけを理由に発話を打ち切ることは一切しない（このAnswer
        // Windowの仕組み自体が最初からOpenAIへ何も送信しない設計のため、
        // 自動的に「話している最中は切らない」という要件を満たす）。
        let expectedAnswerType = 'NONE'; // 'NONE' | 'NAME' | 'PHONE' | 'YES_NO' | 'SHORT_CHOICE' | 'VISIT_REASON'
        let answerWindowTimerId = null;
        let answerWindowType = null; // 現在アクティブなタイマーが対象とするtype
        // Short Choice 3-Second Turn（今回追加）: 「2択質問」（YES_NO・SHORT_CHOICE）は、
        // NAME Forced Commit Observation PoC・Conversation Takeover Observation PoCの
        // 実機検証で安全性を確認できたmanual input_audio_buffer.commitパターンを、
        // 初めて全店舗（本番）へ適用する機能。約3秒はあくまで上限目安であり、
        // 通常のsemantic_vadがそれより早くspeech_stopped→committedまで進んだ場合は
        // 何もせずそのまま任せる（下のmaybeSendShortAnswerCommit()のコメント参照）。
        // SHORT_ANSWER（PHASE O5.8で本Answer Window機構からは除外・置換）:
        // 当初はTIME/DATE/PARTY_SIZEのような短答質問にも既存YES_NO/SHORT_CHOICEと
        // 同じ「speech_startedを起点に3秒」という設計を適用していたが、O5.7 Audit
        // の結果、これは「話し終わってからどれくらい経ったか」ではなく
        // 「話し始めてから何秒か」を基準にしてしまうため、(a)長い一続きの発話を
        // 話している最中に強制commitしてしまうリスク、(b)環境雑音による
        // speech_started/speech_stopped の繰り返しでAnswer Window自体が一度も
        // 完走できずForced Commitが永久に発火しないリスク、の両方を抱えることが
        // 判明した（O5.7 Audit Report参照）。そのためSHORT_ANSWERのみ、この
        // 共有Answer Window機構（本テーブル・startAnswerWindowIfNeeded/
        // cancelAnswerWindow）からは意図的に外し、下のSHORT_ANSWER_FINALIZE_GRACE_MS
        // による「speech_stopped基準」の新方式（armQuickAnswerFinalizeTimer/
        // cancelQuickAnswerFinalizeTimer）へ完全に置換した。NAME/PHONE/YES_NO/
        // SHORT_CHOICE/VISIT_REASONは全て本テーブル・本機構をそのまま使い続けて
        // おり、今回一切変更していない（O5.8スコープ外）。
        const ANSWER_WINDOW_LIMITS_MS = { NAME: 5000, PHONE: 10000, YES_NO: 3000, SHORT_CHOICE: 3000, VISIT_REASON: 30000 };

        // ===== NAME Forced Commit Observation PoC: 追加の最小状態（今回追加） =====
        // 有効化条件・目的はファイル冒頭のnameCommitPocEnabled定義を参照。
        // 大きな状態機械は作らず、「NAME質問1回（世代）につき手動commitは
        // 最大1回まで」を管理する最小限の状態のみを持つ（PHASE6）。
        //
        // 「NAME質問1回」を識別する世代カウンタ。classifyExpectedAnswerType()
        // でAIが新たにNAMEを尋ねたと判定するたびに+1する。
        let nameAnswerGeneration = 0;
        // このNAME世代について、既に手動commitを送信済みならその世代番号
        // （未送信ならnull）。同一世代につき手動commitは最大1回のみ。
        let pocCommitSentGeneration = null;
        // このNAME世代について、手動commitより前に正常なターン終了
        // （speech_stopped/committed/item_created）が先に届いたかどうか。
        // trueの場合、送信直前の最終確認（PHASE7）で手動commitを見送る。
        let nameTurnNormalCompletionSeen = false;
        // 直近の手動commit送信時刻（PHASE9のelapsed_ms計測の起点。performance.now()）。
        // null＝現在手動commit送信待ち/観測対象ではない。
        let pocCommitSentAt = null;
        // その送信がどのcallGeneration（通話）で行われたかを覚えておき、
        // 通話をまたいだ誤相関を防ぐ。
        let pocCommitCallGeneration = null;
        // PHASE9: 手動commit送信後に観測したい4種の反応それぞれについて、
        // 一度だけelapsed_msを記録済みかどうか（二重ログ防止）。
        let pocCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };

        // ===== Short Choice 3-Second Turn: 追加の最小状態（今回追加。全店舗適用） =====
        // NAME Forced Commit Observation PoC（PoC店舗+debug=1限定）・Conversation
        // Takeover Observation PoC（同）とは異なり、これは全店舗（本番）に適用する
        // 機能である。ただし安全パターン自体（世代管理・正常完了レースガード・
        // commitは1世代につき最大1回・response.createは絶対に追加送信しない）は
        // それらのPoCで実機検証済みのものをそのまま踏襲する。既存NAME/Takeover PoCの
        // 状態・関数とは完全に独立させる（引き続き1つの汎用機構にまとめない）。
        //
        // 対象はexpectedAnswerType==='YES_NO'（既存の確認質問）または
        // 'SHORT_CHOICE'（今回追加。AM/PM等、明確な2択質問）の場合のみ。
        //
        // 「2択質問1回」を識別する世代カウンタ。classifyExpectedAnswerType()で
        // AIが新たにYES_NO/SHORT_CHOICEを尋ねたと判定するたびに+1する。
        let shortChoiceAnswerGeneration = 0;
        // この世代について、既に手動commitを送信済みならその世代番号
        // （未送信ならnull）。同一世代につき手動commitは最大1回のみ。
        let shortChoiceCommitSentGeneration = null;
        // この世代について、手動commitより前に正常なターン終了
        // （speech_stopped/committed/item_created）が先に届いたかどうか。
        // trueの場合、送信直前の最終確認で手動commitを見送る
        // （NAME PoCと同じレース対策）。
        let shortChoiceTurnNormalCompletionSeen = false;
        // 直近の手動commit送信時刻（elapsed_ms計測の起点。performance.now()）。
        // null＝現在手動commit送信待ち/観測対象ではない。
        let shortAnswerCommitSentAt = null;
        // その送信がどのcallGeneration（通話）で行われたかを覚えておき、
        // 通話をまたいだ誤相関を防ぐ。
        let shortAnswerCommitCallGeneration = null;
        // 手動commit送信後に観測したい4種の反応それぞれについて、一度だけ
        // elapsed_msを記録済みかどうか（二重ログ防止。NAME PoCと同じ構造）。
        let shortAnswerCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };

        // ===== PHONE Forced Commit（電話番号ターン専用の安全上限。今回追加。全店舗適用） =====
        // 背景（実機症状・PHONE TURN FIXフェーズ）: 「最後に、ご連絡先の
        // お電話番号をお願いします」とAIが尋ねた後、お客様が電話番号を話した
        // にもかかわらずAIが応答を再開しないまま会話が停止することがある。
        // 監査の結果、PHONE用のExpected Answer Window自体（ANSWER_WINDOW_LIMITS_MS.
        // PHONE=10000msという上限値・classifyExpectedAnswerType()によるPHONE判定・
        // startAnswerWindowIfNeeded()によるタイマー開始）はすでに存在していたが、
        // そのタイマーが期限切れになった際に実際に手動commitを送信する関数が
        // NAME/YES_NO/SHORT_CHOICE分にしか存在せず、PHONEだけが「observed=タイム
        // ボックス超過をログに記録するだけで、その後は何もせず通常のsemantic_vad
        // に委ねる」という未実装のまま放置されていたことが判明した（ファイル
        // 冒頭のExpected Answer Window設計方針コメント：本来はPHONE/VISIT_REASON/
        // 通常店舗のNAMEは観測のみのままにする、という古い決定に基づく状態で
        // あり、その後NAME PoC・Short Choiceで安全性が実機検証された強制commit
        // パターンがPHONEにだけ反映されていなかった）。
        //
        // 本追加は、実機で安全性が確認済みのShort Choice 3-Second Turnと全く
        // 同じ安全パターン（世代管理・commitは1世代につき最大1回・送信直前に
        // 正常完了フラグを再確認・response.createは絶対に追加送信しない・
        // datachannel未オープンやsend例外は非fatal）を、PHONEに限定して
        // 全店舗（本番）へ適用するものである。NAME Forced Commit Observation PoC・
        // Conversation Takeover Observation PoCとは完全に独立した状態・関数として
        // 実装する（既存の設計方針である「1つの汎用機構にまとめない」を踏襲）。
        //
        // 「正常なsemantic_vadが優先」について: 下のmaybeSendPhoneCommit()は、
        // PHONE用Answer Window（10秒。あくまで安全上限であり待ち時間ではない）が
        // 期限切れになった時点、つまり通常のsemantic_vadがそれより先に
        // speech_stopped等へ進んでいない場合にのみ呼ばれる。通常のsemantic_vadが
        // 先に完了すれば、この仕組みは一切何もしない。
        //
        // 「電話番号ターン1回」を識別する世代カウンタ。classifyExpectedAnswerType()で
        // AIが新たにPHONEを尋ねたと判定した（NAMEと同じく、型が変化した瞬間のみ）
        // ときに+1する。
        let phoneAnswerGeneration = 0;
        // このPHONE世代について、既に手動commitを送信済みならその世代番号
        // （未送信ならnull）。同一世代につき手動commitは最大1回のみ。
        let phoneCommitSentGeneration = null;
        // このPHONE世代について、手動commitより前に正常なターン終了
        // （speech_stopped/committed/item_created）が先に届いたかどうか。
        // trueの場合、送信直前の最終確認で手動commitを見送る
        // （NAME PoC/Short Choiceと同じレース対策）。
        let phoneTurnNormalCompletionSeen = false;
        // 直近の手動commit送信時刻（elapsed_ms計測の起点。performance.now()）。
        // null＝現在手動commit送信待ち/観測対象ではない。
        let phoneCommitSentAt = null;
        // その送信がどのcallGeneration（通話）で行われたかを覚えておき、
        // 通話をまたいだ誤相関を防ぐ。
        let phoneCommitCallGeneration = null;
        // 手動commit送信後に観測したい4種の反応それぞれについて、一度だけ
        // elapsed_msを記録済みかどうか（二重ログ防止。NAME PoC/Short Choiceと
        // 同じ構造）。
        let phoneCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };

        // ===== SHORT_ANSWER Forced Commit（TIME/DATE/PARTY_SIZE専用。今回追加。全店舗適用） =====
        // 背景（FAST ANSWER TURN / CUSTOMER-FIRST CONVERSATIONフェーズ・実機症状）:
        // 予約希望時間が休憩時間等に該当しAIが「別のお時間は何時をご希望ですか？」
        // と尋ねた後、客が新しい時間を回答してもAIが応答を再開せず会話が停止する
        // ことがある。監査の結果、classifyExpectedAnswerType()にはTIME/DATE/
        // PARTY_SIZEを尋ねる質問（「何時」「何名」「何日」「いつ」等）を検出する
        // 分類が一切存在せず、これらの質問はすべてexpectedAnswerType='NONE'に
        // 分類されていたことが判明した（NONEの場合、startAnswerWindowIfNeeded()は
        // 即returnするため、Answer Window自体が一切開始しない＝PHONEの旧不具合
        // 〔タイマーはあるが送信関数が無い〕よりさらに手前の「土台すら無い」
        // 状態だった）。
        //
        // 本追加は、実機で安全性が確認済みのShort Choice 3-Second Turn・PHONE
        // Forced Commitと全く同じ安全パターン（世代管理・commitは1世代につき
        // 最大1回・送信直前に正常完了フラグを再確認・response.createは絶対に
        // 追加送信しない・datachannel未オープンやsend例外は非fatal）を、
        // TIME/DATE/PARTY_SIZE（内部分類名'SHORT_ANSWER'）に限定して全店舗
        // （本番）へ適用する。NAME/PHONE/Takeover PoC・Short Choiceとは完全に
        // 独立した状態・関数として実装する。
        //
        // 命名について（重要・混乱防止）: classifyExpectedAnswerType()が返す
        // type文字列は仕様どおり'SHORT_ANSWER'とするが、既存のmaybeSendShortAnswerCommit
        // /shortAnswerCommit*（実際はYES_NO/SHORT_CHOICE用のForced Commit実装で
        // あり、変数名にたまたま"ShortAnswer"を含む）と紛らわしいため、本機構の
        // 内部変数・関数名は意図的に「quickAnswer」という別の接頭辞を使う。
        // ログ上のタイムラインマーカーも既存のSHORT_ANSWER_COMMIT_*（Short Choice用）
        // と区別できるよう、QUICK_ANSWER_*という別の接頭辞にしている。
        //
        // 「型が変化した瞬間のみ」ではなくSHORT_CHOICEと同じ判定のたびに世代を
        // 進める方式にしている理由: 休憩時間/営業時間外/満席/過去時刻等により
        // AIが「別のお時間は何時ですか？」を複数回尋ね直す今回の中心ケースに
        // 対応するため（classifyExpectedAnswerType側の該当箇所を参照）。
        let quickAnswerGeneration = 0;
        // このSHORT_ANSWER世代について、既に手動commitを送信済みならその世代番号
        // （未送信ならnull）。同一世代につき手動commitは最大1回のみ。
        let quickAnswerCommitSentGeneration = null;
        // このSHORT_ANSWER世代について、Realtime server側が既に自律的にこの
        // ターンを処理した（input_audio_buffer.committed／conversation.item.created
        // をサーバー側から受信した）かどうか。trueの場合、送信直前の最終確認で
        // 手動commitを見送る（NAME PoC/Short Choice/PHONEと同じレース対策）。
        //
        // PHASE O5.8で意味を変更（重要）: 旧設計ではspeech_stoppedもこのフラグを
        // trueにしていたが、O5.8の新方式ではspeech_stoppedそのものがFinalization
        // Grace（下記）の起点になるため、speech_stoppedではもうこのフラグを
        // 立てない。あくまで「サーバーが実際にこのターンを処理した」という
        // イベント（committed/item_created/response.created/function_call）のみが
        // このフラグ（またはFinalization Timerの直接cancel）の対象。
        let quickAnswerTurnNormalCompletionSeen = false;
        // 直近の手動commit送信時刻（elapsed_ms計測の起点。performance.now()）。
        // null＝現在手動commit送信待ち/観測対象ではない。
        let quickAnswerCommitSentAt = null;
        // その送信がどのcallGeneration（通話）で行われたかを覚えておき、
        // 通話をまたいだ誤相関を防ぐ。
        let quickAnswerCommitCallGeneration = null;
        // 手動commit送信後に観測したい4種の反応それぞれについて、一度だけ
        // elapsed_msを記録済みかどうか（二重ログ防止。既存3機構と同じ構造）。
        let quickAnswerCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };

        // ===== PHASE O5.9.1: Forced Commit Error Correlation（診断専用） =====
        // 背景（O5.9 Auditで確認したギャップ）: QUICK_ANSWER_COMMIT_SENTは記録
        // されるが、その後OpenAIから非同期に届く'error'イベント（QUICK_ANSWER_
        // COMMIT_ERROR）は、同一callGeneration内であれば経過時間を問わず
        // 無条件で記録されていた。これでは「直前のSHORT_ANSWER Forced Commit
        // に本当に起因するerrorなのか、たまたま同じ通話内の別の原因による
        // errorなのか」を実ログだけから確実に判別できない。
        //
        // 今回追加するのは「原因の確定」ではなく、あくまで「時間的に直前の
        // commitと相関している可能性が高いerror」を診断目的でラベル付け
        // するだけの機構。Realtime制御（送信内容・タイミング・retry等）には
        // 一切影響しない。
        //
        // seq（quickAnswerCommitDiagSeq）: このSHORT_ANSWER Forced Commit機構が
        // 実際にdc.send()を実行した回数を数えるだけの、診断専用の単調増加
        // カウンタ。quickAnswerGeneration（質問1問ごとの世代）とは別軸で、
        // 「何回目の実送信か」を一意に識別するために存在する。
        let quickAnswerCommitDiagSeq = 0;
        // 直近に成功したSHORT_ANSWER Forced Commit送信1回分のスナップショット
        // （診断専用。Realtime制御判断には一切使用しない）。
        // { seq, sentAt(performance.now基準), epochMs(Date.now基準・実時計),
        //   callGeneration, expectedAnswerType } または未送信ならnull。
        let lastQuickAnswerCommitDiag = null;
        // 相関ウィンドウ（診断専用の閾値であり、Realtime側のタイムアウトや
        // retry等の制御には一切使用しない）。OpenAI Realtimeのcontrol-plane
        // errorイベント（音声生成を伴わない、リクエスト自体の妥当性検証系の
        // エラー）は、モデルの応答生成を待つ必要がないため、通常は送信から
        // 数百ms〜1、2秒程度で返ることが期待される。実測ログ（O5.6/O5.8の
        // 既存tests/comment群）では、これより十分に大きい既存の目安値として
        // PHONE Answer Windowの上限10000msや、CommitからCommitReaction
        // （committed/item_created等）までの実測待ち時間があるが、今回は
        // 「commitと無関係な、別ターンの後発errorを誤って相関させない」ことを
        // 優先し、それらより短い5000ms（5秒）を採用する。この値は診断の
        // 感度調整のみが目的であり、超過したerrorも既存のQUICK_ANSWER_
        // COMMIT_ERROR自体には引き続き記録される（隠さない。ユーザー指示6）。
        const QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS = 5000;

        // ===== PHASE O5.8: SHORT_ANSWER Finalization Grace（speech_stopped基準） =====
        // O5.7 Auditの結論（推測ではなく実測での確認は今後の実機ログで行うが、
        // コード構造上の欠陥として確定した点）: 旧SHORT_ANSWER Answer Window
        // （上のANSWER_WINDOW_LIMITS_MSから今回除外した3秒版）は
        // 「speech_startedを起点に3秒」だったため、(a) 3秒を超える一続きの
        // 発話を話している最中に強制commitしてしまう危険、(b) 環境雑音による
        // speech_started/speech_stopped の繰り返しでタイマーが一度も完走できず
        // Forced Commitが永久に発火しない危険、の両方を抱えていた。
        //
        // 新設計は「話し終わってからどれくらい経ったか」を基準にする:
        //   speech_started → (発話中は何もしない) → speech_stopped →
        //   SHORT_ANSWER_FINALIZE_GRACE_MS だけ待つ →
        //   その間に新たなspeech_startedが来なければfinalize（Forced Commit）。
        // 新たなspeech_startedが来た場合は、まだ話している途中（言い淀み・
        // 言い直し等）である可能性を優先し、grace timerを無条件でcancelする
        // （ユーザー指示4・6）。
        //
        // 対象はSHORT_ANSWER（TIME/DATE/PARTY_SIZE）のみ。NAME/PHONE/YES_NO/
        // SHORT_CHOICE/VISIT_REASONは今回のO5.8では一切変更していない
        // （引き続き上のANSWER_WINDOW_LIMITS_MS・startAnswerWindowIfNeeded/
        // cancelAnswerWindowをそのまま使用する）。
        //
        // 配置について: この定数はSHORT_ANSWER Forced Commit専用の値であり、
        // 汎用のANSWER_WINDOW_LIMITS_MSとは意図的に別テーブル・別定数にした
        // （SHORT_ANSWERを同テーブルから除外したことと対応させるため）。
        // magic numberとしてタイマー呼び出し箇所へ直接埋め込まず、他の
        // タイミング系定数（SILENCE_TIMEOUT_MS等）と同じくnamed constとして
        // このSHORT_ANSWER Forced Commitセクション内に配置する。
        const SHORT_ANSWER_FINALIZE_GRACE_MS = 1200;
        // 現在armされているFinalization Grace timerのsetTimeout ID
        // （null＝非アクティブ）。
        let quickAnswerFinalizeTimerId = null;
        // 現在のFinalization Grace timerがarmされた時刻（performance.now()。
        // timerAgeMs/actualElapsedMs算出用）。
        let quickAnswerFinalizeArmedAt = null;
        // 現在のFinalization Grace timerがどのquickAnswerGeneration（SHORT_ANSWER
        // 質問の世代）についてarmされたか。発火時にquickAnswerGenerationと
        // 一致するかを再確認し、既に次のSHORT_ANSWER質問へ進んでいた場合の
        // 誤発火を防ぐ（NAME/PHONE等と同じ世代ガードの考え方）。
        let quickAnswerFinalizeGeneration = null;

        // ===== NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加） =====
        // 監査結果（実装前に必ずコード上で確認した事実。以下は推測ではない）:
        //
        // (1) turn_detection設定（app/services/realtime_voice_ai.py）は
        //     { type: 'semantic_vad', eagerness: ... } のみで、create_response/
        //     interrupt_response は一切設定されていない（grepで確認済み・
        //     0件）。OpenAI公式ドキュメント（developers.openai.com/api/docs/
        //     guides/realtime-vad、developers.openai.com/api/reference/
        //     resources/realtime）によれば、interrupt_responseが未設定または
        //     trueの場合、AIの応答出力中にVADがspeech_startedを検知すると
        //     サーバー側が自動的にその応答をcancel（truncate）する。この
        //     自動truncateの発生は、既存コードのconversation.item.truncated/
        //     output_audio_buffer.cleared受信ハンドラ（PHASE6「無言化調査」・
        //     本ファイル内に既存）が実際に観測・記録している。
        // (2) OpenAI Realtime APIのspeech_started/speech_stoppedイベントには
        //     confidenceスコアや音量情報等、雑音と実際の人間発話を区別できる
        //     フィールドは一切存在しない（audio_start_ms/audio_end_ms/
        //     item_id/event_idのみ。公式リファレンスで確認済み）。またこの
        //     セッションはinput_audio_transcriptionを設定していない
        //     （realtime_voice_ai.pyをgrepし0件を確認。本ファイル内の既存
        //     コメント「input_audio_transcriptionが未設定のため発話内容に
        //     一切アクセスできない」とも整合）。したがって、現在のAPI設定
        //     だけでは「雑音による誤検知のspeech_started」と「人間の明確な
        //     発話によるspeech_started」をクライアント側で安全に区別する
        //     手段が存在しない（ユーザー指示どおり、この限界は最終報告に
        //     明記し、推測で区別ロジックを実装しない）。
        // (3) expectedAnswerType==='NONE'（AIの直前発話がPHONE/NAME/YES_NO/
        //     SHORT_CHOICE/VISIT_REASON/SHORT_ANSWERのいずれの分類語にも
        //     一致しなかった状態。第一声の挨拶「本日はどのようなご用件
        //     でしょうか？」の直後がまさにこの状態に該当する。
        //     classifyExpectedAnswerType()の正規表現を確認した結果、この
        //     文言はどの分類にも一致せず'NONE'のままになることを確認済み）
        //     には、startAnswerWindowIfNeeded()が`if (expectedAnswerType ===
        //     'NONE') return;`により即returnするため、既存のAnswer Window/
        //     Forced Commit系機構が一切働かない。つまり「1st turn（要件を
        //     聞く場面）」は、既存5機構（NAME/PHONE/YES_NO・SHORT_CHOICE/
        //     SHORT_ANSWER）のいずれの安全網も及ばない、唯一無防備な状態
        //     だった。今回のUSER_TURN_3S_FALLBACKは、この具体的な穴を
        //     埋めるために新設する、既存Forced Commit関数とは完全に独立した
        //     新しい仕組みである（ユーザー指示「既存Forced Commitを安易に
        //     流用・改造しない」に従い、既存のmaybeSendNameCommitPoc等は
        //     一切変更・呼び出ししない）。
        // (4) VISIT_REASON（30秒許容の自由回答）には、意図的にこの新機構を
        //     適用しない。O5.7 Auditの教訓（speech_started起点の固定
        //     タイマーは長い自然な発話を強制的に打ち切る危険がある）を
        //     踏まえ、長い自由回答を前提とするVISIT_REASONに3秒の区切りを
        //     持ち込むと同じ危険を再現しかねないため、スコープを意図的に
        //     'NONE'のみに限定する（VISIT_REASONの既存30秒Answer Window・
        //     既存の完全な無介入方針は一切変更しない）。
        //
        // 安全設計（既存のO5.8 Finalization Grace / NAME PoC等と同じ考え方を
        // 踏襲しつつ、コード自体は独立）:
        //   - 起点はspeech_started（発話開始）ではなく、必ずspeech_stopped
        //     （サーバー側VADが「発話が止まった」と判定した時点）にする。
        //     これにより、話している最中に強制的に区切られることは構造上
        //     起こらない（O5.7 Auditの教訓を守る）。
        //   - 新たなspeech_startedが来た時点で無条件にcancelする（言い淀み・
        //     言い直しの可能性を優先する。O5.8と同じ設計）。
        //   - 1世代（1回のarm）につき最大1回のみinput_audio_buffer.commitを
        //     送信し、response.createは絶対に追加送信しない（既存4機構と
        //     全く同じ安全パターン）。
        //   - 送信直前に正常完了フラグ（committed/item_created/
        //     response.created/function_callのいずれか）を再確認し、
        //     既に自然完了していれば送信しない（レース対策）。
        //   - Tool呼び出し中（toolContinuationTraceActive===true）は
        //     armもfireも行わない（Tool往復中の待ち時間を誤ってfallback
        //     対象にしないため）。
        //   - dc.send例外は非fatal（try/catchで包み、通話自体は継続する）。
        //
        // NOISE RECOVERY（AIの応答内容そのもの）について: このfallbackが
        // manual commitを送った後、AIが実際に何を話すか（「もう一度」と
        // 会話全体をやり直すのか、既知の情報を保持したまま次の未取得項目を
        // 具体的に尋ねるのか）は、client側のこの仕組みでは制御できない
        // （Realtime APIの応答内容はモデルとsession instructions側の責務）。
        // このため、app/services/realtime_voice_ai.pyのsession instructions
        // 側に、聞き取れなかった場合の望ましい復帰応答（既存の「既に分かって
        // いる情報を聞き直さない」「直前に尋ねた質問に対応する回答を優先する
        // （ノイズ耐性）」を補強する形の追加指示）を別途追加する
        // （詳細は同ファイルの追記コメント参照）。
        const USER_TURN_FALLBACK_GRACE_MS = 3000; // ユーザー指示: 「約3秒を上限の目安として」
        let userTurnFallbackGeneration = 0; // armされるたびに増分する、このfallback専用の世代カウンタ
        let userTurnFallbackTimerId = null;
        let userTurnFallbackArmedAt = null;
        let userTurnFallbackArmedForGeneration = null;
        let userTurnFallbackCommitSentGeneration = null;
        let userTurnFallbackNormalCompletionSeen = false;
        let userTurnFallbackCommitSentAt = null;
        let userTurnFallbackCommitCallGeneration = null;

        // speech_stoppedのたびに、expectedAnswerType==='NONE'の場合のみ
        // 呼ぶ。Tool呼び出し中は何もしない（誤発火防止）。
        function armUserTurnFallbackTimer(myGeneration) {
            if (expectedAnswerType !== 'NONE') return;
            if (toolContinuationTraceActive) return;
            cancelUserTurnFallbackTimer('rearm_on_speech_stopped');
            userTurnFallbackGeneration += 1;
            const armedForGeneration = userTurnFallbackGeneration;
            userTurnFallbackArmedForGeneration = armedForGeneration;
            userTurnFallbackArmedAt = performance.now();
            userTurnFallbackNormalCompletionSeen = false;
            pushTimelineEvent('USER_TURN_FALLBACK_ARMED (durationMs=' + USER_TURN_FALLBACK_GRACE_MS
                + ', callGeneration=' + myGeneration + ', generation=' + armedForGeneration + ')');
            userTurnFallbackTimerId = setTimeout(() => {
                userTurnFallbackTimerId = null;
                const actualElapsedMs = userTurnFallbackArmedAt === null ? null : msSince(userTurnFallbackArmedAt);
                userTurnFallbackArmedAt = null;
                pushTimelineEvent('USER_TURN_3S_FALLBACK (expectedDurationMs=' + USER_TURN_FALLBACK_GRACE_MS
                    + ', actualElapsedMs=' + (actualElapsedMs === null ? 'null' : actualElapsedMs)
                    + ', callGeneration=' + myGeneration + ', generation=' + armedForGeneration
                    + ', normalCompletionSeen=' + userTurnFallbackNormalCompletionSeen + ')');
                if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない
                if (armedForGeneration !== userTurnFallbackGeneration) return; // 既に次のarmへ進んでいれば何もしない
                if (toolContinuationTraceActive) {
                    pushTimelineEvent('USER_TURN_FALLBACK_SKIPPED (reason=tool_call_active)');
                    return;
                }
                maybeSendUserTurnFallbackCommit(myGeneration, armedForGeneration);
            }, USER_TURN_FALLBACK_GRACE_MS);
        }

        // 新たなspeech_startedが来た時・自然完了イベントが届いた時・通話終了時
        // のいずれかから呼ぶ。armされていなければ何もしない（no-op）。
        function cancelUserTurnFallbackTimer(reason) {
            if (userTurnFallbackTimerId === null) return;
            const timerAgeMs = userTurnFallbackArmedAt === null ? null : msSince(userTurnFallbackArmedAt);
            clearTimeout(userTurnFallbackTimerId);
            userTurnFallbackTimerId = null;
            userTurnFallbackArmedAt = null;
            pushTimelineEvent('USER_TURN_FALLBACK_CANCELLED (timerAgeMs=' + (timerAgeMs === null ? 'null' : timerAgeMs)
                + ', reason=' + reason + ', callGeneration=' + callGeneration + ')');
        }

        // Finalization Grace（3秒）満了時にのみ呼ばれる、実際の送信本体。
        // 既存4機構（NAME PoC/Short Choice/PHONE/SHORT_ANSWER）と全く同じ
        // 安全パターン（1世代最大1回・送信直前レース確認・response.create
        // 絶対不送信・dc未オープン/例外は非fatal）を踏襲するが、コード自体は
        // 独立（既存関数を呼び出さない・改造しない）。
        function maybeSendUserTurnFallbackCommit(myGeneration, forGeneration) {
            if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない（保険）
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('USER_TURN_FALLBACK_COMMIT_SKIPPED (reason=datachannel_not_open)');
                return;
            }
            if (userTurnFallbackCommitSentGeneration === forGeneration) {
                // この世代については既に手動commit送信済み（最大1回/世代）
                return;
            }
            if (userTurnFallbackNormalCompletionSeen) {
                pushTimelineEvent('USER_TURN_FALLBACK_COMMIT_SKIPPED (reason=normal_completion_won_race)');
                return;
            }
            pushTimelineEvent('USER_TURN_FALLBACK_COMMIT_REQUESTED (generation=' + forGeneration + ')');
            try {
                // 他4機構と全く同じくinput_audio_buffer.commitのみ。この直後に
                // response.createを送ることは絶対にしない。
                dc.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
                userTurnFallbackCommitSentGeneration = forGeneration;
                userTurnFallbackCommitCallGeneration = myGeneration;
                userTurnFallbackCommitSentAt = performance.now();
                // NOISE RECOVERY: 実際にAIがどう応答するか（既知情報を保持し
                // 未取得項目のみ具体的に尋ねるか）はsession instructions側の
                // 責務。ここではその復帰応答を期待している、という事実だけを
                // 診断用に記録する（PIIなし）。
                pushTimelineEvent('NOISE_RECOVERY (generation=' + forGeneration + ')');
            } catch (e) {
                pushTimelineEvent('USER_TURN_FALLBACK_COMMIT_ERROR (message=send_exception)');
            }
        }

        // ===== AI SPEAKING PROTECTION（今回追加） =====
        // 目的: AIの音声出力（output_audio_buffer.started〜stopped/cleared）
        // 中は、背景雑音によるspeech_startedがサーバー側の自動interrupt
        // （interrupt_response、上記監査(1)参照）を誤って引き起こし、AIの
        // 発話が途中で打ち切られる（＝「無言化調査」で観測済みの
        // conversation.item.truncated/output_audio_buffer.cleared）ことを
        // 防ぐ。
        //
        // 設計方針（監査(2)の結論を踏まえた選択。推測実装ではなく確定的な
        // 保証を選んだ）: OpenAI Realtime APIには雑音と人間発話を区別する
        // シグナルが存在しないため、「区別してから割り込みだけ弾く」という
        // 実装は不可能（推測になってしまう）。そこで、AIの音声出力中は
        // ローカルのマイクtrack自体をWebRTCの標準機能（MediaStreamTrack.
        // enabled = false）で一時的にミュートし、雑音・音声を問わずAIの
        // 発話中は一切サーバーへ音声を送らない、という確定的な保証を選ぶ。
        // track.enabled=falseはSDP再ネゴシエーション不要の標準的な
        // ミュート方法であり、無効化されている間はサーバー側でspeech_started
        // が物理的に発生し得ない（推測ではなく仕様上の保証）。
        //
        // トレードオフ（ユーザー指示どおり最終報告に明記する制約。ここでは
        // コード上の事実として明示しておく）: この設計では、AIが話している
        // 間は「背景雑音による誤割り込み」だけでなく「人間の明確な発話に
        // よる意図的なbarge-in」も同様に一切サーバーへ届かない。OpenAI側に
        // 両者を区別する既存シグナルが無い以上、現在のAPIだけでは安全に
        // 両立できない（今回のスコープでは、AIの発話を最後まで話し切らせる
        // ことを優先する）。
        //
        // スコープ（意図的な限定。Tool呼び出しの「AI思考中」区間には適用
        // しない）: 保護区間はoutput_audio_buffer.started〜stopped/cleared
        // （実際にAIの音声が再生されている区間）のみとし、response.created
        // 〜output_audio_buffer.started間（音声再生開始前）やTool往復中の
        // 待ち時間は対象外とする。既存のFAST TURN/T0-T10/Speak-Then-Work
        // ack fallback機構には一切触れない・影響しない。
        //
        // フェイルセーフ: 万一output_audio_buffer.stopped/cleared/response.
        // doneのいずれも受信できなかった場合に備え、AI_SPEAKING_PROTECTION_
        // MAX_MS経過で強制的にミュート解除する安全網タイマーを必ず併設する
        // （お客様のマイクが恒久的にミュートされたままになることは絶対に
        // 避ける）。
        const AI_SPEAKING_PROTECTION_MAX_MS = 20000;
        let aiSpeakingProtected = false;
        let aiSpeakingProtectionSafetyTimerId = null;
        // FAST TURN HOTFIX（FIRST ANSWER MUST COUNT・今回追加）:
        // 「人数・時間を2回言わないと反応しない」実機不具合の監査用。
        // AIの発話保護（マイクミュート）が実際にいつ解除されたか
        // （＝ユーザーが物理的に発話を送信できるようになった時刻）を
        // 記録しておき、次にspeech_startedが来た瞬間との差分を
        // 実機ログから直接読めるようにする（挙動は一切変更しない、
        // 純粋な観測専用の追加）。値はperformance.now()のみで、
        // 音声内容・PIIは一切含まない。
        let lastAiSpeakingEndAt = null;

        function engageAiSpeakingProtection(reason) {
            if (aiSpeakingProtected) return; // 既に保護中なら何もしない（多重engage防止）
            const track = (localStream && localStream.getAudioTracks) ? localStream.getAudioTracks()[0] : null;
            if (!track) return; // 保険: マイクtrackが無ければ何もしない（fail-open）
            try {
                track.enabled = false;
            } catch (e) {
                pushTimelineEvent('AI_SPEAKING_PROTECTION_ERROR (reason=track_disable_failed)');
                return;
            }
            aiSpeakingProtected = true;
            pushTimelineEvent('AI_SPEAKING_START (reason=' + reason + ')');
            // 実機DEBUG（ユーザー指示）: AI_SPEAKING_STARTとは別に、マイクtrackが
            // 実際にミュートされ「保護区間に入った」という事実そのものを専用
            // マーカーとしても記録する（実機ログでAI_SPEAKING_STARTと紛れずに
            // 保護区間の開始だけを追いやすくするため。PIIや音声内容は含まない）。
            pushTimelineEvent('AI_SPEAKING_PROTECTED (reason=' + reason + ')');
            if (aiSpeakingProtectionSafetyTimerId !== null) clearTimeout(aiSpeakingProtectionSafetyTimerId);
            aiSpeakingProtectionSafetyTimerId = setTimeout(() => {
                aiSpeakingProtectionSafetyTimerId = null;
                pushTimelineEvent('AI_SPEAKING_PROTECTION_SAFETY_UNMUTE (reason=max_duration_exceeded)');
                releaseAiSpeakingProtection('safety_timeout');
            }, AI_SPEAKING_PROTECTION_MAX_MS);
        }

        function releaseAiSpeakingProtection(reason) {
            if (aiSpeakingProtectionSafetyTimerId !== null) {
                clearTimeout(aiSpeakingProtectionSafetyTimerId);
                aiSpeakingProtectionSafetyTimerId = null;
            }
            if (!aiSpeakingProtected) return; // 既に解除済みなら何もしない（多重release防止・no-op）
            const track = (localStream && localStream.getAudioTracks) ? localStream.getAudioTracks()[0] : null;
            if (track) {
                try { track.enabled = true; } catch (e) {}
            }
            aiSpeakingProtected = false;
            // FAST TURN HOTFIX（FIRST ANSWER MUST COUNT・今回追加）: マイクが
            // 実際に再度使用可能になった時刻を記録する（次のspeech_startedとの
            // 差分計算専用。観測のみ）。
            lastAiSpeakingEndAt = performance.now();
            pushTimelineEvent('AI_SPEAKING_END (reason=' + reason + ')');
            // FAST TURN HOTFIX 2（FIRST ANSWER MUST COUNT・今回追加、STEP12/22）:
            // マイクが実際に使用可能へ戻った唯一の地点（この関数はサーバー
            // イベント経路・ローカル音声レベル経路の両方から呼ばれる、
            // release処理の集約点）で、ユーザー入力を受け付け可能になった
            // ことを表す明示的なマーカーを1つだけ記録する。新しいstate変数は
            // 追加しない（既存のaiSpeakingProtected===falseがそのまま
            // 「ready」を意味するため、二重管理を避ける）。
            pushTimelineEvent('USER_LISTENING_READY (reason=' + reason + ')');
        }

        // ===== Conversation Takeover Observation PoC: 追加の最小状態（今回追加） =====
        // 目的・設計方針の詳細はファイル冒頭のtakeoverPocEnabled定義の
        // コメントを参照。NAME Forced Commit Observation PoCとは完全に独立した
        // 別の状態・別の関数として実装する（両者を1つの汎用「強制ターン終了」
        // 機構にまとめないことという明示的な指示に基づく）。
        //
        // 今回の監査結果（重要）: ブラウザJS側は現時点でユーザー発話の
        // 意味内容（transcript）に一切アクセスできない
        // （input_audio_transcriptionがsession_configで設定されておらず、
        // フロントエンドもsession.updateで追加設定していないことをコードで
        // 確認済み）。そのため本PoCは「発話内容の十分性」を判定する機能を
        // 一切持たない、時間ベースかつ観測中心の限定的なPoCである
        // （CONTENT SUFFICIENCY判定は今回実装しない）。
        //
        // 「発話エピソード」を識別する世代カウンタ。speech_startedのたびに
        // +1する（NAME PoCのnameAnswerGenerationに相当する概念だが、
        // Takeoverは「AIが特定の質問をした直後」ではなく「ユーザーが話し
        // 始めた瞬間」を起点にするため、独立したカウンタとして持つ）。
        let takeoverSpeechEpisodeId = 0;
        // 現在アクティブなTakeoverタイマーのsetTimeout ID（null＝非アクティブ）。
        let takeoverTimerId = null;
        // このエピソードについて、既に手動commitを送信済みならそのエピソード
        // 番号（未送信ならnull）。同一エピソードにつき手動commitは最大1回のみ。
        let takeoverCommitSentEpisode = null;
        // このエピソードについて、手動commitより前に正常なターン終了
        // （speech_stopped/committed/item_created）が先に届いたかどうか。
        // trueの場合、送信直前の最終確認で手動commitを見送る
        // （NAME PoCのnameTurnNormalCompletionSeenと同じレース対策）。
        let takeoverTurnNormalCompletionSeen = false;
        // 直近の手動commit送信時刻（elapsed_ms計測の起点。performance.now()）。
        // null＝現在手動commit送信待ち/観測対象ではない。
        let takeoverCommitSentAt = null;
        // その送信がどのcallGeneration（通話）で行われたかを覚えておき、
        // 通話をまたいだ誤相関を防ぐ。
        let takeoverCommitCallGeneration = null;
        // 手動commit送信後に観測したい4種の反応それぞれについて、一度だけ
        // elapsed_msを記録済みかどうか（二重ログ防止。NAME PoCと同じ構造）。
        let takeoverCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
        // Takeoverタイマーの長さ（ms）。Intent Classificationの「約20秒は
        // あくまで目安」という設計方針と概念的に揃えた値だが、あくまで本PoC
        // 専用の別設定であり、Intent Classificationのプロンプト自体には
        // 一切手を加えない。
        const TAKEOVER_TIMER_MS = 20000;

        // ===== Mobile Real-Call Failure Investigation: 追加の観測状態 =====
        // すべてPIIを含まない（状態文字列・イベント名・カウンタのみ）。対処は
        // 行わず観測のみ（原因が実機ログで確定するまでVAD等は変更しない）。

        // PHASE11/12: ICE/DataChannelの現在状態表示用。
        let iceState = 'new';
        let dataChannelState = '未接続';
        // PHASE9: AI音声出力バッファがアクティブかどうかの表示用ラベル
        // （aiAudioOutputActive自体は既存のPHASE1変数をそのまま使う）。
        // PHASE3: このセッションのGreetingがどこから発生したか。
        // 'none' | 'zero_wait' | 'realtime_initial' | 'realtime_fallback'
        let greetingSource = 'none';
        // PHASE12（Greeting直後 Connection Failure再調査）: greetingSourceは
        // 「どちらの経路を使うと決定したか」（通話開始直後、まだ実際の音声が
        // 出る前に確定する）を表す。それに対しaudioSourceは「実際にどちらの
        // 音声トラックがアクティブになったかを事後的に観測した結果」を表す
        // 別軸の値。zero_wait決定でも実際の再生('playing'イベント)に至る前に
        // 通話が終了した場合はgreetingSource='zero_wait'のままaudioSourceは
        // 'none'のままとなり得るため、両者を分けて記録することで「実際に
        // ユーザーが聞いた音声がZero-WaitとRealtimeのどちらだったか」を
        // より正確に区別できる。'none' | 'zero_wait' | 'realtime'
        let audioSource = 'none';
        // PHASE6: 直近のAI発話中断（バージイン疑い）の簡潔な説明。無ければ'—'。
        let lastInterruptionInfo = '—';
        // PHASE9: 直近の接続失敗要因。無ければ'—'。
        // 'get_user_media' | 'token_request' | 'peer_connection_setup' |
        // 'sdp_exchange' | 'sdp_remote_description' | 'ice_failed' |
        // 'peer_connection_failed' | 'datachannel_closed' | 'remote_track_ended' |
        // 'cleanup' | 'unknown'
        let failureSource = '—';
        // startCall()内の現在の準備ステップ（PHASE9のFAILURE SOURCE分類に使う）。
        let currentSetupStep = 'idle';
        // PHASE14: 直近のTool呼び出し状態の簡潔な表示（Tool名+状態のみ、
        // 引数・結果内容は一切含めない）。
        let lastToolLabel = '—';
        // PHASE14: describeToolFetchError()が副作用として設定する、直近のTool
        // fetch失敗の種別（'timeout' | 'network_error' | null）。Toolの戻り値・
        // OpenAIへ送信する内容には一切影響しない、ローカル表示専用の変数。
        let lastToolFetchOutcome = null;
        // PHASE6: AI発話中に発生したspeech_startedの時刻（直近1件）。
        // truncated/clearedとの相関判定にのみ使う。
        let lastBargeInSpeechStartedAt = null;
        // PHASE8: 再生開始後に'pause'イベントが何度発生したか（「ブチブチ」の
        // 目安。自動判定はしない、カウンタとして表示するのみ）。
        let playbackPauseCount = 0;

        // ===== Greeting直後 Connection Failure緊急修正: 追加の観測・修正状態 =====

        // PHASE10（最重要）: WebRTCのconnectionState 'disconnected'は、
        // 'failed'/'closed'と異なり一時的な状態（ICEのconsent freshness check
        // 失敗により発生し、ネットワークの瞬断・NAT再割り当て等で自然に
        // 'connected'へ復帰することがある）。ブラウザはRFC 7675のconsent
        // freshness checkをおよそ5秒間隔で行っているため、この間隔を根拠に
        // 猶予期間を5秒とする（恣意的な数値ではなく、ICEの標準的な再確認
        // 間隔に合わせた値）。従来はfailed/disconnected/closedを同一に扱い、
        // 'disconnected'になった瞬間に即座にendCall()していた。Greeting音声の
        // 再生完了でトラフィックパターンが変化するタイミングは、一時的な
        // consent check失敗が起きやすいタイミングでもあるため、これが
        // 「Greeting直後に接続が切れる」症状の主要因の一つと強く疑われる。
        // 'failed'/'closed'は回復の見込みが無いため、引き続き即座にendCall()する
        // （猶予の対象はdisconnectedのみ）。
        const PC_DISCONNECT_GRACE_MS = 5000;
        let disconnectGraceTimer = null;

        // PHASE20/21/22/23（最重要）: 接続失敗が判定され、cleanupConnection()で
        // pc/dc/track等がnull化・reset される直前の「生きた状態」を保存する
        // スナップショット。cleanupConnection()実行後もそのまま画面に残す
        // （cleanupConnection側では意図的にリセットしない。新しい通話開始
        // 〔startCall()〕時にのみクリアする）。PIIは一切含めない
        // （状態文字列・enum値・boolean・カウンタのみ）。
        let failureSnapshot = null;

        // PHASE17: 直近の主要イベントをPIIなしで保持する簡易タイムライン
        // （テキスト本文・電話番号・氏名・店舗名・予約内容は一切
        // 含めない。イベント名・状態文字列・Tool名のみ）。
        // 再調査（第2ラウンド）: Audio Element Timeline（ZERO_WAIT_AUDIO:/
        // REALTIME_AUDIO:の各種イベント）を追加したことで、Greeting区間だけで
        // 記録されるイベント数が大幅に増えた。30件のままだとConnection Failure
        // までの間に古い（Greeting開始時点の）イベントが押し出されてしまい、
        // 「audio stalled → PC disconnected」のような前後関係の確認に必要な
        // 範囲が失われる可能性があるため60件に拡大する（表示・保持件数のみの
        // 変更であり、VAD等の挙動には一切影響しない）。
        const RECENT_EVENTS_MAX = 60;
        let recentEvents = [];
        function pushTimelineEvent(text) {
            const t = new Date().toLocaleTimeString('ja-JP', { hour12: false }) + '.' + String(new Date().getMilliseconds()).padStart(3, '0');
            recentEvents.push(t + ' ' + text);
            if (recentEvents.length > RECENT_EVENTS_MAX) recentEvents.shift();
            if (debugMode) {
                const el = document.getElementById('diagTimeline');
                if (el) el.textContent = recentEvents.join('\n');
            }
        }

        // FAST TURN 3.6B（Tool Continuation Proof）: function_call_output送信〜
        // AI音声再生開始までの一連のイベント（T0〜T10）を、既存のpushTimelineEvent
        // （Copy Debug Logに常時記録・#diagTimelineの可視表示はdebugMode限定という
        // 既存パターンをそのまま踏襲）に「T+経過ms・call_idの末尾8文字のみ」を
        // 付加して記録するだけの観測用ヘルパー。Realtime制御イベントの送信有無・
        // 内容・タイミングには一切影響しない（呼び出し側のロジックを変更しない）。
        // toolContinuationTraceActiveがfalseの間（Tool呼び出しが発生していない
        // 通常ターン中）は何も記録しない。
        function pushToolContinuationTrace(label) {
            if (!toolContinuationTraceActive || toolContinuationTraceT0 === null) return;
            const elapsedMs = Math.round(performance.now() - toolContinuationTraceT0);
            pushTimelineEvent('TOOL_TRACE ' + label + ' (call=' + toolContinuationTraceShortId + ', t+' + elapsedMs + 'ms)');
        }

        // ===== FAST TURN HOTFIX 3（今回追加）: FULL TURN LATENCY TRACE =====
        // 目的: 「明日、二人で12時に予約したいです」のような通常の1ユーザー
        // ターンについて、USER SPEECH START（speech_started）からAI音声の
        // 実再生開始（<audio>要素のplayingイベント）まで、どのsegmentで
        // 時間を使っているかをミリ秒単位で可視化する診断専用の追加。
        //
        // 重要な設計方針（監査結論に基づく）:
        // - 挙動は一切変更しない。既存のタイマー・閾値・commit/response.create
        //   送信ロジック・USER_TURN_3S_FALLBACK・AI SPEAKING PROTECTION等には
        //   一切触れない。純粋な観測（pushTimelineEvent呼び出しの追加）のみ。
        // - 発話内容・transcriptは一切保存しない。turnIdは単なる連番文字列。
        // - 1ターンにつき同じmarker名は1回だけ記録する（response.audio.delta等、
        //   1ターン中に何度も発火するイベントの重複記録を防ぐため）。これにより
        //   TURN_RESPONSE_CREATEDはTool呼び出しターンの中間応答（function_call
        //   のみ）ではなく最初のresponse.createdのみを捉える（既存の
        //   turnLatencyCommitToResponseMs計算と同じ「初回のみ」設計を踏襲）。
        // - TURN_RESPONSE_REQUESTED: CRITICAL QUESTION 2の監査結論
        //   （通常ターンのresponse.createは100%サーバー側semantic_vadの
        //   自動create_responseであり、クライアントからresponse.createを
        //   送信する専用イベントは存在しない）を反映し、「サーバーが応答生成を
        //   開始してよい状態になった」タイミングであるinput_audio_buffer.
        //   committedと同時刻で記録する（既存コードに新しい「要求」イベントを
        //   作り出すわけではないことを明示するコメント付き）。
        // - TURN_TRANSCRIPTION_READY: input_audio_transcriptionはセッション
        //   設定で有効化されていない（realtime_voice_ai.py監査済み、0件）ため、
        //   対応するconversation.item.input_audio_transcription.*イベントは
        //   そもそも発生しない。存在しないイベントへの空ハンドラを追加する
        //   ことは推測実装にあたるため追加しない（構造的にN/Aという audit結論を
        //   最終報告に記載する）。
        // - TURN_AUDIO_PLAYBACK_START: output_audio_buffer.started（OpenAI公式
        //   既知バグCase #09291741により遅延/欠落しうることがFAST TURN
        //   HOTFIX 2で判明済み）ではなく、実際に<audio>要素がブラウザで
        //   物理的な再生を開始した'playing'イベント（既存のT9計測と同じ
        //   物的証拠）を採用する。これにより「サーバーイベント配信遅延」と
        //   「実際の音声再生遅延」を区別できるようにする。
        // - 通話終了時・新しいターン開始時（前のターンが正常完了イベントを
        //   受け取れないまま次のspeech_startedが来た場合＝多分割発話の可能性の
        //   直接証拠）は、TURN_TRACE_RESETとして明示的に記録してから
        //   リセットする（データを黙って失わない）。
        let turnLatencyTraceId = null;           // 現在計測中のターンの識別子（PIIなし、単なる連番文字列）
        let turnLatencyTraceSeq = 0;             // ターンID生成用の連番カウンタ（通話単位ではなくページ単位）
        let turnLatencyTraceCallGeneration = null;
        let turnLatencyTraceT0 = null;           // このターンのTURN_INPUT_START時刻（基準点、performance.now()）
        let turnLatencyTraceLastAt = null;       // 直前マーカーの時刻（deltaFromPreviousMs計算用）
        let turnLatencyTraceMarkersSeen = {};    // 同一ターン内でのmarker重複記録防止

        function resetTurnLatencyTrace(reason) {
            if (turnLatencyTraceId !== null) {
                pushTimelineEvent('TURN_TRACE_RESET (turnId=' + turnLatencyTraceId + ', reason=' + reason + ')');
            }
            turnLatencyTraceId = null;
            turnLatencyTraceT0 = null;
            turnLatencyTraceLastAt = null;
            turnLatencyTraceMarkersSeen = {};
            // FAST TURN HOTFIX 4（今回追加・観測専用・安全網）: このターン用の
            // watchdogが残っていれば破棄する（次のターンへ誤って持ち越さない）。
            cancelPlainTurnResponseWatchdog('turn_latency_trace_reset');
        }

        function pushTurnLatencyTrace(marker, extraNote) {
            if (turnLatencyTraceId === null || turnLatencyTraceT0 === null) return;
            if (turnLatencyTraceMarkersSeen[marker]) return; // 1ターン1回のみ
            turnLatencyTraceMarkersSeen[marker] = true;
            const now = performance.now();
            const elapsedMs = Math.round(now - turnLatencyTraceT0);
            const deltaMs = turnLatencyTraceLastAt === null ? null : Math.round(now - turnLatencyTraceLastAt);
            turnLatencyTraceLastAt = now;
            pushTimelineEvent('TURN_TRACE ' + marker + ' (turnId=' + turnLatencyTraceId
                + ', elapsedMs=' + elapsedMs
                + ', deltaFromPreviousMs=' + (deltaMs === null ? 'null' : deltaMs)
                + ', expectedAnswerType=' + expectedAnswerType
                + ', responseState=' + responseState
                + ', toolContinuationActive=' + toolContinuationTraceActive
                + (extraNote ? (', ' + extraNote) : '') + ')');
        }

        function endTurnLatencyTrace(marker, extraNote) {
            pushTurnLatencyTrace(marker, extraNote);
            turnLatencyTraceId = null;
            turnLatencyTraceT0 = null;
            turnLatencyTraceLastAt = null;
            turnLatencyTraceMarkersSeen = {};
            // FAST TURN HOTFIX 4（今回追加・観測専用・安全網）
            cancelPlainTurnResponseWatchdog('turn_latency_trace_ended');
        }

        function startTurnLatencyTrace(myCallGeneration) {
            if (turnLatencyTraceId !== null) {
                // 前のターンがTURN_RESPONSE_DONEに到達しないまま次のspeech_started
                // が来た＝1発話が複数のVADターンに分割された可能性の直接証拠。
                // 黙って上書きせず、明示的にリセット理由を記録する。
                resetTurnLatencyTrace('new_speech_started_before_previous_turn_done');
            }
            turnLatencyTraceSeq += 1;
            turnLatencyTraceId = 'T' + turnLatencyTraceSeq;
            turnLatencyTraceCallGeneration = myCallGeneration;
            turnLatencyTraceT0 = performance.now();
            turnLatencyTraceLastAt = turnLatencyTraceT0;
            turnLatencyTraceMarkersSeen = {};
            pushTurnLatencyTrace('TURN_INPUT_START');
        }

        // ===== FAST TURN HOTFIX 4（今回追加）: PLAIN TURN RESPONSE WATCHDOG
        // （診断専用・観測のみ） =====
        // 背景: FAST TURN HOTFIX 3の監査で、「committed/item_createdは正常に
        // 届いたのに、その後response.createdが一度も届かない」という窓には
        // 現状フォールバックが存在しないことが判明していた（証拠不十分のため
        // 修正は見送り、観測を追加する方針だった）。今回のTOOL CONTINUATION
        // RESPONSE WATCHDOGと対になる、Tool呼び出しを伴わない通常ターン側の
        // 同じ窓を可視化するための追加。何も送信しない・何も再試行しない。
        const PLAIN_TURN_RESPONSE_WATCHDOG_MS = 8000; // Tool継続側と同じ目安値（挙動には無関係）
        let plainTurnResponseWatchdogTimerId = null;
        let plainTurnResponseWatchdogForTurnId = null;

        function armPlainTurnResponseWatchdog(forTurnId) {
            cancelPlainTurnResponseWatchdog('rearm');
            plainTurnResponseWatchdogForTurnId = forTurnId;
            plainTurnResponseWatchdogTimerId = setTimeout(() => {
                plainTurnResponseWatchdogTimerId = null;
                // 診断専用: 同じturnIdのトレースがまだ有効で、かつ
                // TURN_RESPONSE_CREATEDが一度も記録されていない場合にのみ
                // 観測用の1行を記録する（何もsendしない）。
                if (turnLatencyTraceId === forTurnId && turnLatencyTraceId !== null
                    && !turnLatencyTraceMarkersSeen['TURN_RESPONSE_CREATED']) {
                    pushTimelineEvent('PLAIN_TURN_WATCHDOG_NO_RESPONSE_CREATED_AFTER_COMMIT (turnId=' + forTurnId
                        + ', waitedMs=' + PLAIN_TURN_RESPONSE_WATCHDOG_MS + ', diagnostic_only_no_action_taken=true)');
                    // FAST TURN HOTFIX 5（今回追加）: TOOL CONTINUATION側と同じ理由
                    // （pushTimelineEventはConsoleへ出力しないことが判明したため）、
                    // Console検索でも見つけられるようconsole.logへも明示的に出す。
                    console.log('[PLAIN_TURN_WATCHDOG_NO_RESPONSE_CREATED_AFTER_COMMIT] turnId=' + forTurnId
                        + ' waitedMs=' + PLAIN_TURN_RESPONSE_WATCHDOG_MS);
                }
            }, PLAIN_TURN_RESPONSE_WATCHDOG_MS);
        }

        function cancelPlainTurnResponseWatchdog(reason) {
            if (plainTurnResponseWatchdogTimerId === null) return;
            clearTimeout(plainTurnResponseWatchdogTimerId);
            plainTurnResponseWatchdogTimerId = null;
            plainTurnResponseWatchdogForTurnId = null;
        }

        // CRITICAL INCIDENT調査（音声ブツブツ/ノイズ→応答停止、iPhone実機再現）:
        // iPhone実機ではSafari開発者ツールを開けず、これまでdebug=1の画面を
        // 見てもログをテキストとして取り出す手段が無かった。この関数は既存の
        // 音声診断グリッド（#audioDiagPanel .status-grid、PIIなし）・
        // FAILURE SNAPSHOT（あれば）・直近イベントタイムライン（recentEvents、
        // 最大RECENT_EVENTS_MAX件）・イベントログ全文（eventLogEl）という、
        // いずれも既存の・既にPII除外済みの表示内容をそのまま1つのテキストへ
        // 集約するだけで、新しいログ収集の仕組みや新しい記録項目は一切
        // 追加しない（concealedSamples等はstartStatsPolling側で別途追加済み。
        // ここではそれを含む既存表示を集約するのみ）。
        function buildDebugLogText() {
            const lines = [];
            lines.push('=== RECEPTRA Realtime Voice Debug Log ===');
            lines.push('取得時刻: ' + new Date().toLocaleString('ja-JP'));
            lines.push('shopId: ' + (shopId || '(不明)'));
            lines.push('callGeneration: ' + callGeneration);
            lines.push('');
            lines.push('--- 音声診断パネル（現在値） ---');
            const grid = document.querySelector('#audioDiagPanel .status-grid');
            if (grid) {
                const children = Array.from(grid.children);
                for (let i = 0; i + 1 < children.length; i += 2) {
                    lines.push(children[i].textContent + ': ' + children[i + 1].textContent);
                }
            }
            const failureContentEl = document.getElementById('failureSnapshotContent');
            if (failureContentEl && failureContentEl.textContent) {
                lines.push('');
                lines.push('--- FAILURE SNAPSHOT ---');
                lines.push(failureContentEl.textContent);
            }
            lines.push('');
            lines.push('--- 直近イベント（diagTimeline, 最大' + RECENT_EVENTS_MAX + '件） ---');
            lines.push(recentEvents.join('\n'));
            lines.push('');
            lines.push('--- イベントログ全文（eventLog） ---');
            lines.push(eventLogEl ? eventLogEl.textContent : '(取得不可)');
            return lines.join('\n');
        }

        function fallbackCopyToClipboard(text, showResult, err) {
            try {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.focus();
                ta.select();
                const ok = document.execCommand('copy');
                document.body.removeChild(ta);
                showResult(ok, ok ? null : (err && err.message));
            } catch (e2) {
                showResult(false, e2 && e2.message);
            }
        }

        function copyDebugLog() {
            const text = buildDebugLogText();
            const statusEl = document.getElementById('copyDebugLogStatus');
            const btn = document.getElementById('copyDebugLogBtn');
            function showResult(ok, extra) {
                if (statusEl) {
                    statusEl.textContent = ok
                        ? 'コピーしました'
                        : ('コピーに失敗しました' + (extra ? '（' + extra + '）' : '') + '。下の欄を長押しして手動でコピーしてください');
                }
                if (btn) {
                    const original = '📋 デバッグログをコピー';
                    btn.textContent = ok ? '✅ コピーしました' : '📋 デバッグログをコピー';
                    if (ok) setTimeout(() => { btn.textContent = original; }, 2000);
                }
            }
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(() => showResult(true)).catch((e) => {
                    fallbackCopyToClipboard(text, showResult, e);
                });
            } else {
                fallbackCopyToClipboard(text, showResult);
            }
        }

        // Expected Answer Window: AI自身の発話内容から、直後にお客様へ求めている
        // 回答の種類を軽量に推定する（PIIなし。判定結果のtype文字列のみ保持）。
        // NAME/PHONE/YES_NO/SHORT_CHOICE以外（日時・来店理由・自由回答等）は
        // 今回タイムボックス対象としないため、キーワードが一致しない場合は
        // NONEのままにする（大きな状態機械にしない・誤判定時は従来通りの
        // 通常VADのみで進む）。
        function classifyExpectedAnswerType(aiTranscript) {
            const t = aiTranscript || '';
            let type = 'NONE';
            if (/電話番号|お電話番号/.test(t)) {
                type = 'PHONE';
            } else if (/お名前|ご氏名/.test(t)) {
                type = 'NAME';
            } else if (/よろしいですか|でよろしい|間違いない|お間違い|合っています/.test(t)) {
                type = 'YES_NO';
            } else if ((t.match(/ですか/g) || []).length >= 2 || /どちらですか|どちらでしょうか/.test(t)) {
                // Short Choice 3-Second Turn（今回追加）: 「Xですか、Yですか」
                // 「AとB、どちらですか」のような明確な2択質問を検出する。
                // 内容（X/Yが何か）は一切見ない・保持しない、あくまで発話の
                // "形"（ですか が同一発話内に2回、または「どちら」を伴う）だけの
                // 軽量な判定。既存_CORE_RULES_TEMPLATEの「一度に複数のことを
                // 質問しない」という絶対ルールにより、AIの1発話に「ですか」が
                // 2回出てくるのは基本的に単一の2択質問（例:「午前ですか、
                // 午後ですか？」）の場合のみであるため、この単純な形状判定でも
                // 誤検知は少ないと判断した（PHASE1監査で確認。断定はできない
                // ため完了報告のopen itemとして明記する）。
                // 既存YES_NO（よろしいですか等の確認質問）の判定を優先させる
                // ため、このelse ifはYES_NO判定より後に置いている。
                type = 'SHORT_CHOICE';
            } else if (/ご来店の目的|ご来店について|ご希望やお悩み|ご相談内容|どのようなことでのご予約|来店理由/.test(t)) {
                // 追加要件（Conversation Time Control）: 来店理由確認の問いかけを
                // 検出する。NAME/PHONE/YES_NO/SHORT_CHOICEより優先度を下げている
                // （自由回答の来店理由の中に偶然「よろしいですか」等の語が
                // 含まれていても誤ってYES_NO等に上書きされないよう、
                // 既存のいずれにも一致しなかった場合にのみ判定する）。
                type = 'VISIT_REASON';
            } else if (/何時|何日|何名|いつをご希望|いつがよろしい|いつでしょうか|別のお?時間|ご希望のお?時間|ご希望の日(にち)?|お?時間.{0,6}(ご希望|いかが|ございます|よろしい)/.test(t)) {
                // SHORT_ANSWER（今回追加。FAST ANSWER TURN / CUSTOMER-FIRST
                // CONVERSATIONフェーズ）: 「何名様ですか？」「何日をご希望ですか？」
                // のほか、実際にapp/services/realtime_voice_ai.py側のTool説明文が
                // 使う「別のお時間をご希望ですか？」（time_in_past時）・「その時間帯で
                // ご都合のよいお時間はございますか？」（営業時間外時）のような、
                // TIME/DATE/PARTY_SIZEを尋ねる短答質問を検出する（実際のプロンプト
                // 文言を確認した上で正規表現を設計。憶測で作っていない）。
                // 最後の「お?時間.{0,6}(ご希望|いかが|ございます|よろしい)」は、
                // 「時間」と「ご希望/いかが/ございます/よろしい」の語順・間隔が
                // 発話ごとに揺れる（AIの自由生成のため一言一句は固定できない）
                // ことを踏まえた許容範囲付きの検出。VISIT_REASON（自由回答・
                // 長く話す可能性がある）判定より必ず後（優先度を下げて）に置く
                // ことで、仮に両方の語が同じ発話に含まれてしまった場合でも、より
                // 安全な側（3秒Forced CommitをかけないVISIT_REASON扱い）に倒れる
                // ようにしている。同じ理由でPHONE/NAME/YES_NO/SHORT_CHOICEの
                // いずれにも一致しなかった場合にのみ判定する（既存の優先順位
                // PHONE→NAME→YES_NO→SHORT_CHOICE→VISIT_REASONを壊さない）。
                //
                // 命名について（重要・混乱防止）: この分類のtype文字列は仕様どおり
                // 'SHORT_ANSWER'とするが、内部の状態変数・関数名は既存の
                // maybeSendShortAnswerCommit/shortAnswerCommit*（実際は
                // YES_NO/SHORT_CHOICE用のForced Commit関数）と紛らわしいため、
                // 意図的に別の接頭辞「quickAnswer」を使う（下記参照）。
                type = 'SHORT_ANSWER';
            }
            // PHASE O5.9.1（ユーザー指示8）: classifyExpectedAnswerType()が
            // 呼ばれるたび（＝AIの発話が1回完了するたび）に、その分類結果を
            // 無条件で記録する診断専用イベント。既存のEXPECTED_ANSWER_SETは
            // 「型が変化したときだけ」しか記録しないため、直前と同じ型が
            // 連続した場合（例: PARTY_SIZE質問の直後にTIME質問が続き、
            // どちらも'SHORT_ANSWER'のまま変化しない場合）に記録が抜ける。
            // O5.9 Auditで指摘された「TIME質問が実際にSHORT_ANSWERへ分類
            // されているか」を実ログだけで確実に確認できるよう、変化の
            // 有無に関わらず必ず1回記録する。AI発話全文（transcript）は
            // 一切含めず、分類結果の型文字列のみを記録する（PIIなし）。
            pushTimelineEvent('EXPECTED_ANSWER_DIAG (type=' + type + ')');
            if (type !== expectedAnswerType) {
                expectedAnswerType = type;
                if (type === 'NAME') {
                    // NAME Forced Commit Observation PoC: 新しいNAME質問ごとに
                    // 新しい世代として扱う（PHASE6）。この世代の「正常完了済み」
                    // フラグもここでリセットする。
                    nameAnswerGeneration += 1;
                    nameTurnNormalCompletionSeen = false;
                }
                if (type === 'PHONE') {
                    // PHONE Forced Commit（今回追加）: NAMEと同じく、型が変化して
                    // 新たにPHONEと判定された瞬間のみ新しい世代として扱う。この
                    // 世代の「正常完了済み」フラグもここでリセットする。
                    phoneAnswerGeneration += 1;
                    phoneTurnNormalCompletionSeen = false;
                    pushTimelineEvent('PHONE_TURN_DETECTED (generation=' + phoneAnswerGeneration + ')');
                }
                pushTimelineEvent('EXPECTED_ANSWER_SET (type=' + type + ')');
            }
            // Short Choice 3-Second Turn（今回追加）: 上のNAMEと異なり、
            // 「type文字列が変化したときだけ」ではなく、classifyExpectedAnswerType
            // が呼ばれてSHORT_CHOICE/YES_NOと判定されるたびに新しい世代として
            // 扱う。これは、この関数がAIの発話完了(response.output_audio_transcript.
            // done)ごとに1回だけ呼ばれるため（1通話中にAIが複数回、種類の異なる
            // 2択質問を尋ねる可能性がある。例:「午前/午後」の確認の後、別の
            // タイミングで「予約/折り返し」を尋ねる場合、どちらもtype文字列は
            // 同じ'SHORT_CHOICE'になるため、type!==expectedAnswerTypeの比較だけでは
            // 2問目の世代を検出できない）。この方式にすることで、2問目にも
            // 独立してForced Commitの機会を与える。
            if (type === 'YES_NO' || type === 'SHORT_CHOICE') {
                shortChoiceAnswerGeneration += 1;
                shortChoiceTurnNormalCompletionSeen = false;
            }
            // SHORT_ANSWER（今回追加）: Short Choiceと同じ理由で、「type文字列が
            // 変化したときだけ」ではなく判定されるたびに新しい世代として扱う。
            // 休憩時間/営業時間外/満席/過去時刻等で「別のお時間は何時ですか？」
            // を複数回尋ね直す今回の中心ケースに対応するために必須。
            if (type === 'SHORT_ANSWER') {
                quickAnswerGeneration += 1;
                quickAnswerTurnNormalCompletionSeen = false;
                pushTimelineEvent('QUICK_ANSWER_TURN_DETECTED (generation=' + quickAnswerGeneration + ')');
            }
        }

        // ユーザーが実際に話し始めた瞬間（input_audio_buffer.speech_started）に
        // 呼び出す。expectedAnswerTypeがNAME/PHONE/YES_NOのいずれかで、かつ
        // 既に同種のタイマーが動いていない場合にのみ観測用タイマーを開始する
        // （PHASE6: 起点はAIの発話ではなく、ユーザーの発話開始）。
        // 重要: このタイマーは現時点では観測のみで、OpenAIへは何も送信しない
        // （上部の設計方針コメント参照）。
        function startAnswerWindowIfNeeded(myGeneration) {
            if (expectedAnswerType === 'NONE') return;
            if (answerWindowTimerId !== null) return; // 同一ターン内での多重開始防止
            const limitMs = ANSWER_WINDOW_LIMITS_MS[expectedAnswerType];
            if (!limitMs) return;
            const type = expectedAnswerType;
            answerWindowType = type;
            pushTimelineEvent('ANSWER_WINDOW_STARTED (type=' + type + ', limit_ms=' + limitMs + ')');
            const startedAt = performance.now();
            answerWindowTimerId = setTimeout(() => {
                answerWindowTimerId = null;
                if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない
                const elapsedMs = Math.round(performance.now() - startedAt);
                pushTimelineEvent('ANSWER_WINDOW_EXPIRED (type=' + type + ', elapsed_ms=' + elapsedMs + ')');
                // 観測のみ: 上記の原則（OpenAIへは何も送信せず、既存の
                // semantic_vadによる通常のターン終了検知をそのまま待つ）は
                // PHONE/VISIT_REASON、および通常店舗のNAMEについては
                // 今回も一切変更しない。
                //
                // NAME Forced Commit Observation PoC（PoC店舗+debug=1限定）:
                // type==='NAME'かつ、このPoC専用のテスト店舗+debug=1が揃った
                // 場合に限り、maybeSendNameCommitPoc()が改めて全条件を確認した
                // うえでinput_audio_buffer.commitを最大1回だけ送信する（詳細は
                // 同関数のコメント参照）。それ以外の場合、この呼び出しは
                // 内部で何もせずに戻る。
                maybeSendNameCommitPoc(type, myGeneration);
                // Short Choice 3-Second Turn（今回追加・全店舗適用）:
                // type==='YES_NO'または'SHORT_CHOICE'の場合、
                // maybeSendShortAnswerCommit()が改めて全条件を確認したうえで
                // input_audio_buffer.commitを最大1回だけ送信する。それ以外の
                // 場合、この呼び出しは内部で何もせずに戻る。
                maybeSendShortAnswerCommit(type, myGeneration);
                // PHONE Forced Commit（今回追加・全店舗適用）: type==='PHONE'の
                // 場合、maybeSendPhoneCommit()が改めて全条件を確認したうえで
                // input_audio_buffer.commitを最大1回だけ送信する。それ以外の
                // 場合、この呼び出しは内部で何もせずに戻る。
                maybeSendPhoneCommit(type, myGeneration);
                // PHASE O5.8: SHORT_ANSWER（TIME/DATE/PARTY_SIZE）は
                // ANSWER_WINDOW_LIMITS_MSから除外したため、typeがここで
                // 'SHORT_ANSWER'になることはもう無い（maybeSendQuickAnswerCommit
                // は下のarmQuickAnswerFinalizeTimer経由・speech_stopped基準の
                // 新方式からのみ呼ばれる）。旧呼び出しはここでは行わない。
            }, limitMs);
        }

        // NAME Forced Commit Observation PoC（今回追加・観測専用。必ず本ファイル
        // 冒頭のnameCommitPocEnabled定義・設計方針コメントとあわせて読むこと）:
        //
        // NAME Answer Window（~5秒）が期限切れになった時点で、まだ正常な
        // ターン終了（speech_stopped/committed/item_created）が届いていない
        // 場合に限り、input_audio_buffer.commitを最大1回だけ手動送信する。
        // response.createは絶対に追加送信しない（サーバー側のsemantic_vadが
        // 既に内部的にauto-commit＋auto-responseを開始している可能性を排除
        // できず、手動response.createを重ねると二重応答を招くリスクがある
        // ため。今回はその実際の挙動を安全に観測することが唯一の目的）。
        //
        // 送信後にOpenAIが実際にどう反応するか（input_audio_buffer.committed/
        // conversation.item.created/response.created/AI音声出力まで自動的に
        // 進むのか、それとも"buffer is empty"等のerrorになるのか）は、
        // handleDataChannelEvent側の各イベントハンドラでmaybeLogPocReactionElapsed()
        // を通じてタイムラインに記録する。
        function maybeSendNameCommitPoc(answerType, myGeneration) {
            if (answerType !== 'NAME') return; // PHASE3: NAMEのみが対象
            if (!nameCommitPocEnabled) return; // PHASE2/4: PoC専用テスト店舗+debug=1のみ
            if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない（保険）
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('POC_COMMIT_SKIPPED (reason=datachannel_not_open)');
                return;
            }
            if (pocCommitSentGeneration === nameAnswerGeneration) {
                // このNAME世代については既に手動commit送信済み（PHASE6: 最大1回/世代）
                return;
            }
            pushTimelineEvent('POC_NAME_COMMIT_ELIGIBLE (generation=' + nameAnswerGeneration + ')');
            // PHASE7（最重要）: 送信直前にもう一度だけ、正常完了フラグが
            // その間に立っていないか最終確認する。
            if (nameTurnNormalCompletionSeen) {
                pushTimelineEvent('POC_COMMIT_SKIPPED (reason=normal_completion_won_race)');
                return;
            }
            pushTimelineEvent('POC_NAME_COMMIT_REQUESTED (generation=' + nameAnswerGeneration + ')');
            try {
                // 観測のみ: input_audio_buffer.commitのみを送信する。この直後に
                // response.createを送ることは絶対にしない。
                dc.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
                pocCommitSentGeneration = nameAnswerGeneration;
                pocCommitCallGeneration = myGeneration;
                pocCommitSentAt = performance.now();
                pocCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
                pushTimelineEvent('POC_NAME_COMMIT_SENT (generation=' + nameAnswerGeneration + ')');
            } catch (e) {
                // PHASE10: send自体が例外を投げた場合も非fatal扱い（endCall/
                // cleanupConnectionは呼ばない）。既存のsemantic_vadに以後を委ねる。
                pushTimelineEvent('POC_NAME_COMMIT_ERROR (send_exception): ' + (e && e.message));
            }
        }

        // NAME Forced Commit Observation PoC: 手動commit送信後に観測したい
        // 4種の反応（committed/item_created/response.created/AI音声開始）を、
        // 各イベントにつき一度だけelapsed_msとしてタイムラインに記録する
        // （PHASE9）。手動commitを送信していない通常の通話・通常のNAME応答
        // では pocCommitSentAt が null のままのため、何もしない。
        function maybeLogPocReactionElapsed(key, label) {
            if (pocCommitSentAt === null) return;
            if (callGeneration !== pocCommitCallGeneration) return;
            if (pocCommitPendingEvents[key]) return;
            pocCommitPendingEvents[key] = true;
            pushTimelineEvent('POC_NAME_COMMIT_REACTION (' + label + ', elapsed_ms=' + Math.round(performance.now() - pocCommitSentAt) + ')');
        }

        // ===== Short Choice 3-Second Turn: 2つの中核関数（今回追加・全店舗適用） =====
        // NAME Forced Commit Observation PoC・Conversation Takeover Observation PoCで
        // 実機検証済みの安全なパターン（commitは1世代につき最大1回・送信直前に
        // 正常完了フラグを再確認・response.createは絶対に追加送信しない・
        // datachannel未オープンやsend例外は非fatal）をそのまま踏襲する。
        // 両PoCと異なり、これは店舗gatingを一切行わない（全店舗の本番通話で
        // 動作する）。ユーザー(谷村様)の明示的な指示に基づく。

        // Expected Answer Window（startAnswerWindowIfNeeded）が期限切れになった
        // 時点で、answerType が YES_NO または SHORT_CHOICE で、かつまだ正常な
        // ターン終了（speech_stopped/committed/item_created）が届いていない
        // 場合に限り、input_audio_buffer.commitを最大1回だけ手動送信する。
        // response.createは絶対に追加送信しない（サーバー側のsemantic_vadが
        // 既に内部的にauto-commit＋auto-responseを開始している可能性を排除
        // できず、手動response.createを重ねると二重応答を招くリスクがあるため。
        // NAME PoCと全く同じ理由）。
        //
        // 「単純に3秒で内容を破棄しない」について（重要・監査結果）: この
        // 関数はExpected Answer Windowが期限切れになった時点、つまり通常の
        // semantic_vadがそれより先にspeech_stopped等へ進んでいない場合にのみ
        // 呼ばれる。お客様がまだ話し続けている自然な発話（例:「午後なんです
        // けど、できれば少し遅めで……」）と、単なる背景ノイズによる
        // speech_stopped遅延を、ブラウザJS側から発話内容ベースで区別する
        // 安全な手段は今回の監査でも見つかっていない（Conversation Takeover
        // Observation PoCのCONTENT SUFFICIENCY監査結果と同じ理由：
        // input_audio_transcriptionが未設定のため、発話内容に一切アクセス
        // できない）。そのため本関数も、NAME PoCと同じく時間ベースの判定に
        // とどまる。この構造的な限界は完了報告のopen itemとして明記すること。
        function maybeSendShortAnswerCommit(answerType, myGeneration) {
            if (answerType !== 'YES_NO' && answerType !== 'SHORT_CHOICE') return;
            if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない（保険）
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('SHORT_ANSWER_COMMIT_SKIPPED (reason=datachannel_not_open)');
                return;
            }
            if (shortChoiceCommitSentGeneration === shortChoiceAnswerGeneration) {
                // この世代についてはすでに手動commit送信済み（最大1回/世代）
                return;
            }
            pushTimelineEvent('SHORT_ANSWER_COMMIT_ELIGIBLE (type=' + answerType + ', generation=' + shortChoiceAnswerGeneration + ')');
            // 送信直前にもう一度だけ、正常完了フラグがその間に立っていないか
            // 最終確認する（NAME PoCと同じレース対策）。
            if (shortChoiceTurnNormalCompletionSeen) {
                pushTimelineEvent('SHORT_ANSWER_COMMIT_SKIPPED (reason=normal_completion_won_race)');
                return;
            }
            pushTimelineEvent('SHORT_ANSWER_COMMIT_REQUESTED (type=' + answerType + ', generation=' + shortChoiceAnswerGeneration + ')');
            try {
                // 観測のみではなく実運用のcommitだが、送信するのはNAME PoCと
                // 全く同じくinput_audio_buffer.commitのみ。この直後に
                // response.createを送ることは絶対にしない。
                dc.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
                shortChoiceCommitSentGeneration = shortChoiceAnswerGeneration;
                shortAnswerCommitCallGeneration = myGeneration;
                shortAnswerCommitSentAt = performance.now();
                shortAnswerCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
                pushTimelineEvent('SHORT_ANSWER_COMMIT_SENT (type=' + answerType + ', generation=' + shortChoiceAnswerGeneration + ')');
            } catch (e) {
                // send自体が例外を投げた場合も非fatal扱い（endCall/
                // cleanupConnectionは呼ばない）。既存のsemantic_vadに以後を委ねる。
                pushTimelineEvent('SHORT_ANSWER_COMMIT_ERROR (reason=send_exception): ' + (e && e.message));
            }
        }

        // Short Choice 3-Second Turn: 手動commit送信後に観測したい4種の反応
        // （committed/item_created/response.created/AI音声開始）を、各イベント
        // につき一度だけelapsed_msとしてタイムラインに記録する（NAME PoCの
        // maybeLogPocReactionElapsedと同じ構造）。手動commitを送信していない
        // 通常の通話では shortAnswerCommitSentAt が null のままのため、
        // 何もしない。
        function maybeLogShortAnswerReactionElapsed(key, label) {
            if (shortAnswerCommitSentAt === null) return;
            if (callGeneration !== shortAnswerCommitCallGeneration) return;
            if (shortAnswerCommitPendingEvents[key]) return;
            shortAnswerCommitPendingEvents[key] = true;
            pushTimelineEvent('SHORT_ANSWER_COMMIT_REACTION (' + label + ', elapsed_ms=' + Math.round(performance.now() - shortAnswerCommitSentAt) + ')');
        }

        // ===== PHONE Forced Commit: 2つの中核関数（今回追加・全店舗適用） =====
        // NAME Forced Commit Observation PoC・Short Choice 3-Second Turnで実機
        // 検証済みの安全なパターン（commitは1世代につき最大1回・送信直前に
        // 正常完了フラグを再確認・response.createは絶対に追加送信しない・
        // datachannel未オープンやsend例外は非fatal）をそのまま踏襲する。
        // Short Choiceと同じく店舗gatingを一切行わない（全店舗の本番通話で
        // 動作する）。

        // Expected Answer Window（startAnswerWindowIfNeeded）が期限切れになった
        // 時点で、answerType が PHONE で、かつまだ正常なターン終了
        // （speech_stopped/committed/item_created）が届いていない場合に限り、
        // input_audio_buffer.commitを最大1回だけ手動送信する。response.createは
        // 絶対に追加送信しない（NAME PoC/Short Choiceと全く同じ理由:
        // サーバー側semantic_vadが既に内部的にauto-commit＋auto-responseを
        // 開始している可能性を排除できず、手動response.createを重ねると
        // 二重応答を招くリスクがあるため）。
        //
        // 「10秒は待ち時間ではなく安全上限」について: この関数はExpected
        // Answer Window（PHONE=10秒）が期限切れになった時点、つまり通常の
        // semantic_vadがそれより先にspeech_stopped等へ進んでいない場合にのみ
        // 呼ばれる。通常のsemantic_vadが先に完了すればこの関数は呼ばれる前に
        // cancelAnswerWindow()でタイマー自体がキャンセルされているため、通常
        // ケースの応答速度には一切影響しない。
        function maybeSendPhoneCommit(answerType, myGeneration) {
            if (answerType !== 'PHONE') return; // PHONEのみが対象
            if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない（保険）
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('PHONE_COMMIT_SKIPPED (reason=datachannel_not_open)');
                return;
            }
            if (phoneCommitSentGeneration === phoneAnswerGeneration) {
                // このPHONE世代についてはすでに手動commit送信済み（最大1回/世代）
                return;
            }
            pushTimelineEvent('PHONE_COMMIT_ELIGIBLE (generation=' + phoneAnswerGeneration + ')');
            // 送信直前にもう一度だけ、正常完了フラグがその間に立っていないか
            // 最終確認する（NAME PoC/Short Choiceと同じレース対策。「semantic_vadの
            // 正常完了がタイマーとほぼ同時に確定した場合の二重commit防止」の
            // 中核ガード）。
            if (phoneTurnNormalCompletionSeen) {
                pushTimelineEvent('PHONE_COMMIT_SKIPPED (reason=normal_completion_won_race)');
                return;
            }
            pushTimelineEvent('PHONE_COMMIT_REQUESTED (generation=' + phoneAnswerGeneration + ')');
            try {
                // NAME PoC/Short Choiceと全く同じくinput_audio_buffer.commitのみ。
                // この直後にresponse.createを送ることは絶対にしない（既存の
                // Realtimeライフサイクルにそのまま委ねる）。
                dc.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
                phoneCommitSentGeneration = phoneAnswerGeneration;
                phoneCommitCallGeneration = myGeneration;
                phoneCommitSentAt = performance.now();
                phoneCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
                pushTimelineEvent('PHONE_COMMIT_SENT (generation=' + phoneAnswerGeneration + ')');
            } catch (e) {
                // send自体が例外を投げた場合も非fatal扱い（endCall/
                // cleanupConnectionは呼ばない）。既存のsemantic_vadに以後を委ねる。
                pushTimelineEvent('PHONE_COMMIT_ERROR (reason=send_exception): ' + (e && e.message));
            }
        }

        // PHONE Forced Commit: 手動commit送信後に観測したい4種の反応
        // （committed/item_created/response.created/AI音声開始）を、各イベント
        // につき一度だけelapsed_msとしてタイムラインに記録する（NAME PoC/Short
        // Choiceと同じ構造）。手動commitを送信していない通常の通話では
        // phoneCommitSentAt が null のままのため、何もしない。
        function maybeLogPhoneReactionElapsed(key, label) {
            if (phoneCommitSentAt === null) return;
            if (callGeneration !== phoneCommitCallGeneration) return;
            if (phoneCommitPendingEvents[key]) return;
            phoneCommitPendingEvents[key] = true;
            pushTimelineEvent('PHONE_COMMIT_REACTION (' + label + ', elapsed_ms=' + Math.round(performance.now() - phoneCommitSentAt) + ')');
        }

        // ===== PHASE O5.8: SHORT_ANSWER Finalization Grace の2つの中核関数 =====
        // O5.7 Auditの結論に基づき、SHORT_ANSWERの「いつForced Commit候補と
        // するか」の判断基準を、旧「speech_startedから3秒」から新
        // 「speech_stoppedからSHORT_ANSWER_FINALIZE_GRACE_MS(1200ms)」へ完全に
        // 置換する。以下の2関数がこの新方式の中核。maybeSendQuickAnswerCommit()
        // 自体（実際にinput_audio_buffer.commitを送る部分）は変更しない
        // （ユーザー指示9: Forced Commitの意味自体は変えない。timer起点のみ
        // 修正する）。
        //
        // PHASE O5.10 追記（重要）: O5.9.2 Auditの結果、SHORT_ANSWERの通常
        // 経路では本関数（armQuickAnswerFinalizeTimer）はもうどこからも
        // 呼ばれない（唯一の呼び出し元だったspeech_stoppedハンドラ内の
        // 呼び出しを削除した。理由は同ハンドラのコメント参照）。関数自体は
        // 削除せず、将来的に本当に異常なケース（サーバーが長時間何も
        // 反応しない場合等）専用のfallback候補として温存しているだけの、
        // 現時点でdead/unused状態のコードである。通常のSHORT_ANSWER会話
        // フローには一切影響しない。

        // speech_stoppedのたびに呼ぶ。expectedAnswerType==='SHORT_ANSWER'の
        // 場合のみ、既存のFinalization Grace timerがあれば安全にcancelしてから
        // （ユーザー指示5: 「最後のspeech_stoppedから1.2秒を取り直す」設計）、
        // 新たに1.2秒のgrace timerをarmする。それ以外のtypeでは何もしない
        // （NAME/PHONE/YES_NO/SHORT_CHOICE/VISIT_REASONは既存のAnswer Window
        // 機構をそのまま使い続けるため、本関数はSHORT_ANSWER専用）。
        //
        // PHASE O5.10: 上記の通り現在どこからも呼ばれていない
        // （dead/unused。fallback候補として温存）。
        function armQuickAnswerFinalizeTimer(myGeneration) {
            if (expectedAnswerType !== 'SHORT_ANSWER') return;
            cancelQuickAnswerFinalizeTimer('rearm_on_speech_stopped');
            const armedForGeneration = quickAnswerGeneration;
            quickAnswerFinalizeArmedAt = performance.now();
            quickAnswerFinalizeGeneration = armedForGeneration;
            pushTimelineEvent('SHORT_ANSWER_FINALIZE_ARMED (durationMs=' + SHORT_ANSWER_FINALIZE_GRACE_MS
                + ', callGeneration=' + myGeneration
                + ', expectedAnswerType=' + expectedAnswerType
                + ', elapsedSinceSpeechStoppedMs=' + (msSince(lastSpeechStoppedAt) === null ? 'null' : msSince(lastSpeechStoppedAt)) + ')');
            quickAnswerFinalizeTimerId = setTimeout(() => {
                quickAnswerFinalizeTimerId = null;
                const actualElapsedMs = quickAnswerFinalizeArmedAt === null ? null : msSince(quickAnswerFinalizeArmedAt);
                quickAnswerFinalizeArmedAt = null;
                quickAnswerFinalizeGeneration = null;
                pushTimelineEvent('SHORT_ANSWER_FINALIZE_FIRED (expectedDurationMs=' + SHORT_ANSWER_FINALIZE_GRACE_MS
                    + ', actualElapsedMs=' + (actualElapsedMs === null ? 'null' : actualElapsedMs)
                    + ', callGeneration=' + myGeneration
                    + ', dcReadyState=' + (dc ? dc.readyState : null)
                    + ', quickAnswerTurnNormalCompletionSeen=' + quickAnswerTurnNormalCompletionSeen + ')');
                if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない
                if (armedForGeneration !== quickAnswerGeneration) return; // 既に次のSHORT_ANSWER質問へ進んでいれば何もしない（世代ガード）
                maybeSendQuickAnswerCommit('SHORT_ANSWER', myGeneration);
            }, SHORT_ANSWER_FINALIZE_GRACE_MS);
        }

        // Finalization Grace timerを安全にcancelする。以下のいずれかから呼ばれる:
        // (a) 新たなspeech_startedが来た時（ユーザー指示4: まだ話している途中
        //     である可能性を優先）
        // (b) armQuickAnswerFinalizeTimer自身が再armする直前（ユーザー指示5）
        // (c) response.createdが正常に届いた時（ユーザー指示13）
        // (d) function_callへ正常に進んだ時（ユーザー指示14）
        // armされていなければ何もしない（no-op。既存のcancelAnswerWindow()と
        // 同じ形の安全設計）。
        //
        // PHASE O5.10 追記: armQuickAnswerFinalizeTimer()がSHORT_ANSWERの通常
        // 経路から呼ばれなくなったため（下記(b)の呼び出し元は既にdead）、
        // quickAnswerFinalizeTimerIdは通常フローでは常にnullのままとなり、
        // (a)(c)(d)からの呼び出しは全て無害なno-opになる。あえてこれらの
        // 呼び出し自体は削除していない（fallback timerを将来復活させた際に
        // そのまま機能する安全網として残し、変更差分も最小化するため）。
        function cancelQuickAnswerFinalizeTimer(reason) {
            if (quickAnswerFinalizeTimerId === null) return;
            const timerAgeMs = quickAnswerFinalizeArmedAt === null ? null : msSince(quickAnswerFinalizeArmedAt);
            clearTimeout(quickAnswerFinalizeTimerId);
            quickAnswerFinalizeTimerId = null;
            quickAnswerFinalizeArmedAt = null;
            quickAnswerFinalizeGeneration = null;
            pushTimelineEvent('SHORT_ANSWER_FINALIZE_CANCELLED (timerAgeMs=' + (timerAgeMs === null ? 'null' : timerAgeMs)
                + ', reason=' + reason + ', callGeneration=' + callGeneration + ')');
        }

        // ===== SHORT_ANSWER Forced Commit: 実際の送信本体（今回追加・全店舗適用） =====
        // NAME Forced Commit Observation PoC・Short Choice 3-Second Turn・PHONE
        // Forced Commitで実機検証済みの安全なパターン（commitは1世代につき最大
        // 1回・送信直前に正常完了フラグを再確認・response.createは絶対に追加
        // 送信しない・datachannel未オープンやsend例外は非fatal）をそのまま
        // 踏襲する。Short Choice/PHONEと同じく店舗gatingを一切行わない（全店舗の
        // 本番通話で動作する）。
        //
        // PHASE O5.8で呼び出し元を変更（本関数自体のロジックは無変更）: 旧
        // Expected Answer Window（speech_started起点3秒）ではなく、上の
        // armQuickAnswerFinalizeTimer()のFinalization Grace（speech_stopped起点
        // 1.2秒）が満了した時点で呼ばれるようになった。
        //
        // 「grace期間は客の発話を機械的に切るためのものではない」について
        // （重要・O5.7 Audit結果を踏まえた更新）: 本関数はspeech_stoppedが
        // 一度も届かない限り絶対に呼ばれない（旧設計の「speech_startedから
        // 3秒」と異なり、話している最中に強制commitされることは構造上
        // 起こらない。これはユーザー指示21の必須要件そのものである）。
        // 呼ばれるのはあくまで「客が話し終えたとサーバー側VADが判定した後、
        // 1.2秒待っても次のspeech_startedが来ない」場合のみ。ただし、
        // 「話し終えた」というVADの判定自体が短い言い淀み等でも一時的に
        // 成立し得ることは変わらないため（本関数はarmQuickAnswerFinalizeTimer
        // 側で新たなspeech_startedがあれば必ずcancelされる設計により、その
        // ケースは既にカバーされている）、この限界は既に本番で稼働している
        // Short Choice等と同一のものである。
        //
        // PHASE O5.10 追記（重要）: O5.9.2 Auditの結果、SHORT_ANSWERの通常
        // 経路からは、この関数を呼んでいた唯一の経路（armQuickAnswerFinalizeTimer
        // のgrace timer満了）自体が呼ばれなくなったため、本関数も現時点では
        // どこからも呼ばれないdead/unused状態である。関数自体・その中身
        // （実際にinput_audio_buffer.commitを送るロジック自体）はユーザー
        // 指示3により削除せず、将来的に本当に異常なケース専用のfallback
        // 候補としてそのまま温存している。
        function maybeSendQuickAnswerCommit(answerType, myGeneration) {
            if (answerType !== 'SHORT_ANSWER') return; // TIME/DATE/PARTY_SIZEのみが対象
            if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない（保険）
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('QUICK_ANSWER_COMMIT_SKIPPED (reason=datachannel_not_open)');
                return;
            }
            if (quickAnswerCommitSentGeneration === quickAnswerGeneration) {
                // このSHORT_ANSWER世代についてはすでに手動commit送信済み（最大1回/世代）
                return;
            }
            pushTimelineEvent('QUICK_ANSWER_COMMIT_ELIGIBLE (generation=' + quickAnswerGeneration + ')');
            // 送信直前にもう一度だけ、正常完了フラグがその間に立っていないか
            // 最終確認する（他3機構と同じレース対策）。
            if (quickAnswerTurnNormalCompletionSeen) {
                pushTimelineEvent('QUICK_ANSWER_COMMIT_SKIPPED (reason=normal_completion_won_race)');
                return;
            }
            pushTimelineEvent('QUICK_ANSWER_COMMIT_REQUESTED (generation=' + quickAnswerGeneration + ')');
            try {
                // 他3機構と全く同じくinput_audio_buffer.commitのみ。この直後に
                // response.createを送ることは絶対にしない（既存のRealtime
                // ライフサイクルにそのまま委ねる）。
                dc.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
                quickAnswerCommitSentGeneration = quickAnswerGeneration;
                quickAnswerCommitCallGeneration = myGeneration;
                quickAnswerCommitSentAt = performance.now();
                quickAnswerCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
                // PHASE O5.9.1（ユーザー指示2・3）: 診断専用のcommit correlation
                // stateを更新する。Realtime制御判断（retry・timer等）には一切
                // 使用せず、後続の'error'イベントハンドラが「時間的に直前の
                // このcommitと相関している可能性が高いか」を判定するためだけに
                // 参照する。
                quickAnswerCommitDiagSeq += 1;
                lastQuickAnswerCommitDiag = {
                    seq: quickAnswerCommitDiagSeq,
                    sentAt: quickAnswerCommitSentAt,
                    epochMs: Date.now(),
                    callGeneration: myGeneration,
                    expectedAnswerType: answerType,
                };
                pushTimelineEvent('QUICK_ANSWER_COMMIT_SENT (generation=' + quickAnswerGeneration
                    + ', seq=' + lastQuickAnswerCommitDiag.seq
                    + ', epochMs=' + lastQuickAnswerCommitDiag.epochMs
                    + ', callGeneration=' + lastQuickAnswerCommitDiag.callGeneration
                    + ', expectedAnswerType=' + lastQuickAnswerCommitDiag.expectedAnswerType + ')');
            } catch (e) {
                // send自体が例外を投げた場合も非fatal扱い（endCall/
                // cleanupConnectionは呼ばない）。既存のsemantic_vadに以後を委ねる。
                pushTimelineEvent('QUICK_ANSWER_COMMIT_ERROR (reason=send_exception): ' + (e && e.message));
            }
        }

        // SHORT_ANSWER Forced Commit: 手動commit送信後に観測したい4種の反応
        // （committed/item_created/response.created/AI音声開始）を、各イベント
        // につき一度だけelapsed_msとしてタイムラインに記録する（他3機構と同じ
        // 構造）。手動commitを送信していない通常の通話では
        // quickAnswerCommitSentAt が null のままのため、何もしない。
        function maybeLogQuickAnswerReactionElapsed(key, label) {
            if (quickAnswerCommitSentAt === null) return;
            if (callGeneration !== quickAnswerCommitCallGeneration) return;
            if (quickAnswerCommitPendingEvents[key]) return;
            quickAnswerCommitPendingEvents[key] = true;
            pushTimelineEvent('QUICK_ANSWER_COMMIT_REACTION (' + label + ', elapsed_ms=' + Math.round(performance.now() - quickAnswerCommitSentAt) + ')');
        }

        // ===== Conversation Takeover Observation PoC: 4つの中核関数（今回追加） =====
        // NAME Forced Commit Observation PoCの安全なパターン
        // （startAnswerWindowIfNeeded/maybeSendNameCommitPoc/
        // maybeLogPocReactionElapsed）を忠実に踏襲しつつ、完全に独立した
        // 状態・関数として実装する（両者を1つの汎用機構にまとめない）。

        // ユーザーが実際に話し始めた瞬間（input_audio_buffer.speech_started）に
        // 呼び出す。Expected Answer Windowとは異なり、「直前にAIが特定の質問を
        // したかどうか」に依存せず、NAME/PHONE/YES_NOのような短答を期待して
        // いる場面（既存NAME PoC等と重複・競合させないための除外）以外であれば
        // 常に開始する。これにより、雑談的な冒頭挨拶の直後にお客様が自発的に
        // 長い症状説明を始めるケース（Expected Answer Windowが従来カバーして
        // いなかった空白）もカバーする。
        //
        // 重要: このタイマーは【観測を目的とした最小限のForced Commit PoC】
        // であり、発話内容の十分性は一切判定しない（CONTENT SUFFICIENCYは
        // 今回未実装。監査結果を参照）。あくまで「長い自由発話でも、ある程度の
        // 時間が経過したら安全にターンを強制成立させられるか」だけを検証する。
        function startTakeoverTimerIfNeeded(myGeneration) {
            if (!takeoverPocEnabled) return; // PoC専用テスト店舗+debug=1のみ
            // 既存NAME PoC・PHONE・YES_NO・SHORT_CHOICE（Short Choice
            // 3-Second Turn）・SHORT_ANSWER（TIME/DATE/PARTY_SIZE。今回追加）の
            // 短答待ちと重複させない（それらはそれぞれ自身のAnswer Window/
            // 専用Forced Commit関数で既に処理される場面のため、Takeover側が
            // 二重にタイマー・commitを発生させないようにする）。
            if (expectedAnswerType === 'NAME' || expectedAnswerType === 'PHONE' ||
                expectedAnswerType === 'YES_NO' || expectedAnswerType === 'SHORT_CHOICE' ||
                expectedAnswerType === 'SHORT_ANSWER') return;
            if (takeoverTimerId !== null) return; // 同一エピソード内での多重開始防止
            takeoverSpeechEpisodeId += 1;
            const myEpisodeId = takeoverSpeechEpisodeId;
            takeoverTurnNormalCompletionSeen = false;
            pushTimelineEvent('TAKEOVER_ELIGIBLE (episode=' + myEpisodeId + ', expectedAnswerType=' + expectedAnswerType + ')');
            pushTimelineEvent('TAKEOVER_TIMER_STARTED (episode=' + myEpisodeId + ', limit_ms=' + TAKEOVER_TIMER_MS + ')');
            const startedAt = performance.now();
            takeoverTimerId = setTimeout(() => {
                takeoverTimerId = null;
                if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない
                const elapsedMs = Math.round(performance.now() - startedAt);
                pushTimelineEvent('TAKEOVER_TIMER_EXPIRED (episode=' + myEpisodeId + ', elapsed_ms=' + elapsedMs + ')');
                maybeSendTakeoverCommitPoc(myEpisodeId, myGeneration);
            }, TAKEOVER_TIMER_MS);
        }

        // 通常のVADがspeech_stoppedを検知した場合など、正常にターンが終了した
        // ときに呼び出す。アクティブなTakeoverタイマーがあれば即キャンセルする
        // （NAME PoCのcancelAnswerWindowと同じ役割の、独立した関数）。
        function cancelTakeoverTimer(reason) {
            if (takeoverTimerId === null) return;
            clearTimeout(takeoverTimerId);
            takeoverTimerId = null;
            pushTimelineEvent('TAKEOVER_TIMER_CANCELLED (episode=' + takeoverSpeechEpisodeId + ', reason=' + reason + ')');
        }

        // Conversation Takeover Observation PoC（今回追加・観測専用。必ず本ファイル
        // 冒頭のtakeoverPocEnabled定義とあわせて読むこと）:
        //
        // Takeoverタイマーが期限切れになった時点で、まだ正常なターン終了
        // （speech_stopped/committed/item_created）が届いていない場合に限り、
        // input_audio_buffer.commitを最大1回だけ手動送信する。response.createは
        // 絶対に追加送信しない（NAME PoCと同じ理由：サーバー側semantic_vadが
        // 既に内部的にauto-commit＋auto-responseを開始している可能性を排除
        // できず、手動response.createを重ねると二重応答を招くリスクがあるため。
        // 今回はその実際の挙動を安全に観測することが唯一の目的）。
        //
        // NAME PoCとの二重commit防止: NAME PoC自身のmaybeSendNameCommitPocは
        // expectedAnswerType==='NAME'の場合にのみ動作し、本関数を呼び出す
        // startTakeoverTimerIfNeededはNAME/PHONE/YES_NOの場合は最初から
        // タイマーを開始しないため、両者が同一エピソードに対して二重にcommitを
        // 送信することは構造的に発生しない。
        function maybeSendTakeoverCommitPoc(myEpisodeId, myGeneration) {
            if (!takeoverPocEnabled) return; // PoC専用テスト店舗+debug=1のみ
            if (isStaleCallEvent(myGeneration)) return; // 通話終了後・別世代なら何もしない（保険）
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('TAKEOVER_COMMIT_SKIPPED (episode=' + myEpisodeId + ', reason=datachannel_not_open)');
                return;
            }
            if (myEpisodeId !== takeoverSpeechEpisodeId) {
                // 既に次の発話エピソードへ進んでいる（古いタイマーの残骸）。
                pushTimelineEvent('TAKEOVER_COMMIT_SKIPPED (episode=' + myEpisodeId + ', reason=stale_episode)');
                return;
            }
            if (takeoverCommitSentEpisode === myEpisodeId) {
                // このエピソードについては既に手動commit送信済み（最大1回/エピソード）
                return;
            }
            // 送信直前にもう一度だけ、正常完了フラグがその間に立っていないか
            // 最終確認する（NAME PoCと同じレース対策）。
            if (takeoverTurnNormalCompletionSeen) {
                pushTimelineEvent('TAKEOVER_COMMIT_SKIPPED (episode=' + myEpisodeId + ', reason=normal_completion_won_race)');
                return;
            }
            pushTimelineEvent('TAKEOVER_COMMIT_REQUESTED (episode=' + myEpisodeId + ')');
            try {
                // 観測のみ: input_audio_buffer.commitのみを送信する。この直後に
                // response.createを送ることは絶対にしない。
                dc.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
                takeoverCommitSentEpisode = myEpisodeId;
                takeoverCommitCallGeneration = myGeneration;
                takeoverCommitSentAt = performance.now();
                takeoverCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
                pushTimelineEvent('TAKEOVER_COMMIT_SENT (episode=' + myEpisodeId + ')');
            } catch (e) {
                // send自体が例外を投げた場合も非fatal扱い（endCall/
                // cleanupConnectionは呼ばない）。既存のsemantic_vadに以後を委ねる。
                pushTimelineEvent('TAKEOVER_ERROR (episode=' + myEpisodeId + ', reason=send_exception): ' + (e && e.message));
            }
        }

        // Conversation Takeover Observation PoC: 手動commit送信後に観測したい
        // 4種の反応（committed/item_created/response.created/AI音声開始）を、
        // 各イベントにつき一度だけelapsed_msとしてタイムラインに記録する
        // （NAME PoCのmaybeLogPocReactionElapsedと同じ構造）。手動commitを
        // 送信していない通常の通話では takeoverCommitSentAt が null のままの
        // ため、何もしない。
        function maybeLogTakeoverReactionElapsed(key, label) {
            if (takeoverCommitSentAt === null) return;
            if (callGeneration !== takeoverCommitCallGeneration) return;
            if (takeoverCommitPendingEvents[key]) return;
            takeoverCommitPendingEvents[key] = true;
            pushTimelineEvent('TAKEOVER_REACTION (' + label + ', elapsed_ms=' + Math.round(performance.now() - takeoverCommitSentAt) + ')');
        }

        // 通常のVADがspeech_stoppedを検知した場合など、正常にターンが終了した
        // ときに呼び出す。アクティブな観測用タイマーがあれば即キャンセルする
        // （PHASE10: 競合防止。二重に何かを送信するわけではないが、期限切れ後の
        // 無駄なログ出力を防ぐ）。
        function cancelAnswerWindow(reason) {
            if (answerWindowTimerId === null) return;
            clearTimeout(answerWindowTimerId);
            answerWindowTimerId = null;
            pushTimelineEvent('ANSWER_WINDOW_CANCELLED (type=' + answerWindowType + ', reason=' + reason + ')');
            answerWindowType = null;
        }

        // ===== Silence Timeout（完全無言による自動終話。Answer Windowとは別概念） =====
        //
        // 追加要件（Conversation Time Control）: Answer Window（上記）は「AIが
        // NAME/PHONE/YES_NO/VISIT_REASONのような回答を尋ねた直後」にのみ適用
        // される、しかも一切のcommit強制を行わない観測専用の仕組み。これに
        // 対しSilence Timeoutは、通話全体を通じて「AIがユーザーの回答を
        // 待っている状態で、約30秒間まったく有効な発話が確認できない」場合に、
        // 既存のendCall()を安全に再利用して通話を終了させるための、意図的に
        // 別実装・別タイマーにした機構（同一のtimerとして実装しないことという
        // 明示的な指示に基づく）。
        //
        // 「AIがユーザーの回答を待っている」状態の判定（重要・必ず守ること）:
        // - AI自身が発話中の時間、AI応答生成中の時間（Tool Call往復の待ち時間を
        //   含む）は、ユーザーの無言として一切カウントしない。そのため
        //   このタイマーは、function_callを含まない応答が完全に完了した
        //   （response.done、かつその回にfunction_callが無かった。
        //   responseHasFunctionCallで判定）時点で初めて開始する。Tool Call
        //   往復の中間応答（function_callのみのresponse.done）では開始しない。
        // - Zero-Wait Greeting（事前録音のローカル音声再生）はRealtimeの
        //   response.doneを経由しないため、その再生終了(onEnded)時点でも
        //   別途開始する（該当のonEnded内から呼び出す）。
        // - ユーザーの有効な発話開始（input_audio_buffer.speech_started）を
        //   検知した時点で即リセットする。
        // - AIが新しい応答の生成を開始した時点（response.created）でも、
        //   実行中の通常タイマー（'waiting'状態）は一旦キャンセルする。ただし
        //   本機構自身が発行した警告/終話案内アナウンスの応答
        //   （silenceState==='warned'/'goodbye'時）はこの限りではない
        //   （'waiting'状態のときのみキャンセル対象とすることで、自分自身の
        //   アナウンスで自分のタイマーを誤ってキャンセルしないようにする）。
        //
        // 状態遷移: 'idle' →(回答待ち開始)→ 'waiting' →(30秒無言)→ 'warned'
        // （予告アナウンス送信＋猶予タイマー開始）→(猶予中も無言)→ 'goodbye'
        // （終話案内アナウンス送信）→(案内音声の再生完了を確認)→ 既存の
        // endCall()。猶予中にユーザーが話せば'idle'へ戻り通話継続する
        // （'warned'状態のみキャンセル可能。'goodbye'に入った後は既に
        // アナウンス済みのため取り消さず、既存のcallGeneration/endedガードに
        // 委ねる＝終話中の競合防止・二重endCall防止は既存機構を再利用する。
        // ここから直接pc.close()/dc.close()/track.stop()を呼ぶことは絶対に
        // しない）。
        let silenceTimerId = null;
        let silenceWarningTimerId = null;
        let silenceState = 'idle'; // 'idle' | 'waiting' | 'warned' | 'goodbye'
        let responseHasFunctionCall = false; // 直近のresponse.created〜response.doneの間にfunction_callがあったか
        let pendingSilenceGoodbyeHangup = false; // 終話案内アナウンスの再生完了待ちかどうか
        const SILENCE_TIMEOUT_MS = 30000; // 約30秒間、有効な発話が無ければ予告（PHASE O5.6: 値は無変更。挙動を変えない）
        const SILENCE_WARNING_GRACE_MS = 8000; // 予告後、約5〜10秒の猶予（中間値を採用）（PHASE O5.6: 値は無変更）

        // ===== PHASE O5.6: Silence Timeout Diagnostics（診断専用・挙動は一切変更しない） =====
        //
        // 目的: 実機症状「2時でお願いします」→約30秒沈黙→AI「何名様ですか？」→
        // 会話途中で通話終了、の原因を実測で確定するための追加計測。
        // 絶対原則: ここに追加する変数・関数はすべて「読み取り専用の観測」であり、
        // silenceState自体の値・遷移条件・タイマーの発火/解除条件・
        // response.create/endCallの送信有無やタイミングには一切影響しない。
        // 既存のpushTimelineEvent()（copyDebugLog()に自動的に含まれる、
        // debugMode=falseでも常時recentEventsへ記録される既存の仕組み）を
        // そのまま再利用するだけで、新しいログ収集UIやコンソール出力は追加しない。
        // 診断コード自体が例外を投げて既存の終話処理を壊すことがないよう、
        // 全ての新規ロジックをtry/catchで包む。

        // 現在のsilenceStateに入った時刻（timerAgeMs算出用）。
        let silenceStateEnteredAt = null;
        // 30秒タイマー（SILENCE_TIMEOUT_MS）が実際にarmされた時刻。
        let silenceTimerArmedAt = null;
        // 8秒猶予タイマー（SILENCE_WARNING_GRACE_MS）が実際にarmされた時刻。
        let silenceWarningGraceArmedAt = null;
        // 直近にsendResponseCreate()が実際にdc.send()まで成功した際の、その
        // reason文字列（response.created受信時に一度だけ消費してカテゴリ化する。
        // 「このresponse.createdが、直前に自分が送ったreasonに対応するものか」を
        // ローカルでのみ相関させるための変数。OpenAI Realtime APIへは一切
        // 送信しない）。
        let lastResponseCreateReason = null;
        // response.createdごとに採番するローカル診断専用シーケンス番号
        // （Realtime APIの応答IDとは無関係。ブラウザ内の相関確認のみに使う）。
        let responseCreateDiagSeq = 0;
        // CALL_END_DIAG用に持ち越す「直近の応答理由カテゴリ」（response.created
        // のたびに更新。挙動制御には一切使わない・診断専用）。
        let lastResponseReasonCategoryForDiag = 'unknown';
        // 直近のTool呼び出し名・開始時刻（診断専用。既存のlastToolLabel等とは
        // 別に、CALL_END_DIAGが必要とする「Tool名」「経過ms」だけを単純に保持する）。
        let lastFunctionCallNameForDiag = null;
        let lastFunctionCallStartedAtForDiag = null;

        // 指定した過去のperformance.now()時刻からの経過msを返す（未計測ならnull）。
        // 診断表示専用のフォーマットヘルパーで、既存ロジックには使わない。
        function msSince(t) {
            return (t === null || t === undefined) ? null : Math.round(performance.now() - t);
        }

        // sendResponseCreate()のreason文字列を、診断用の粗いカテゴリへ分類する
        // （挙動制御には使わない・診断専用）。実際に存在する呼び出し箇所
        // （'silence_warning' / 'silence_final_goodbye' / 'initial_greeting'系 /
        // 'tool_result:...'）を実コードで確認した上で分類している。それ以外
        // （＝直近に明示的なsendResponseCreate()呼び出しが対応付けられない
        // response.created。OpenAI側のturn_detectionによる自動応答が該当し
        // 得る）はnormal_conversationとして扱う。
        function categorizeResponseReason(rawReason) {
            if (rawReason === null || rawReason === undefined) return 'normal_conversation';
            if (rawReason === 'silence_warning') return 'silence_warning';
            if (rawReason === 'silence_final_goodbye') return 'silence_goodbye';
            if (rawReason.indexOf('tool_result:') === 0) return 'tool_result';
            if (rawReason.indexOf('initial_greeting') === 0) return 'greeting';
            return 'unknown';
        }

        // silenceStateの変更を必ずこの関数経由にすることで、遷移のたびに
        // SILENCE_STATE_DIAGを1行記録する。silenceStateへの代入とその直後の
        // silenceStateEnteredAt更新以外、一切のロジックを追加しない
        // （既存の呼び出し元の条件分岐・ガードは変更前のまま維持する）。
        function setSilenceState(newState, reason) {
            const from = silenceState;
            // 診断ノイズ低減のためのガード（挙動には無関係）:
            // resetSilenceTimer()は既にidleの状態でも無条件に'idle'を再代入する
            // 既存仕様のため、from===newStateの場合はSILENCE_STATE_DIAGを記録
            // せず、silenceStateEnteredAtも更新しない（「現在の状態に実際に
            // 入った時刻」の意味を保つため）。silenceStateへの代入自体は
            // 従来どおり必ず行う（値は変わらないため実質的な影響はない）。
            if (from !== newState) {
                try {
                    pushTimelineEvent('SILENCE_STATE_DIAG (from=' + from + ', to=' + newState
                        + ', reason=' + (reason || '不明')
                        + ', timerAgeMs=' + (silenceStateEnteredAt === null ? 'null' : msSince(silenceStateEnteredAt))
                        + ', elapsedSinceSpeechStartedMs=' + (msSince(lastSpeechStartedAt) === null ? 'null' : msSince(lastSpeechStartedAt))
                        + ', elapsedSinceSpeechStoppedMs=' + (msSince(lastSpeechStoppedAt) === null ? 'null' : msSince(lastSpeechStoppedAt))
                        + ', responseState=' + responseState
                        + ', aiAudioOutputActive=' + aiAudioOutputActive + ')');
                } catch (diagErr) {
                    // 診断ログ自体の失敗が既存の状態遷移を妨げてはならない。
                }
                silenceStateEnteredAt = performance.now();
            }
            silenceState = newState;
        }

        // PeerConnection/DataChannel/マイクトラックの状態を1行にまとめて記録する
        // 診断専用ヘルパー（既存のlogConnectionSnapshot()とは別に、
        // pushTimelineEvent経由でcopyDebugLog()にも載る形で残す。読み取りのみ・
        // 副作用なし）。
        function pushPcStateDiag(label) {
            try {
                const micState = (localStream && localStream.getAudioTracks && localStream.getAudioTracks()[0])
                    ? localStream.getAudioTracks()[0].readyState : null;
                pushTimelineEvent('PC_STATE_DIAG (label=' + label
                    + ', connectionState=' + (pc ? pc.connectionState : null)
                    + ', iceConnectionState=' + (pc ? pc.iceConnectionState : null)
                    + ', dcReadyState=' + (dc ? dc.readyState : null)
                    + ', micTrackReadyState=' + micState + ')');
            } catch (diagErr) {
                // 診断ログ自体の失敗が既存の接続状態処理を妨げてはならない。
            }
        }

        // O5.6診断 項目11: window.error / unhandledrejection を?debug=1時のみ
        // 記録する。個人情報を含み得る本文は保存せず、name/messageのみ・
        // stackは保存しない（無制限のstack保存を避ける）。通話挙動には一切
        // 影響を与えない（preventDefault等は呼ばない。既存のエラー伝播・
        // ブラウザのデフォルト処理はそのまま）。
        if (debugMode) {
            try {
                window.addEventListener('error', (ev) => {
                    try {
                        const name = (ev && ev.error && ev.error.name) ? ev.error.name : 'Error';
                        const message = (ev && ev.message) ? String(ev.message).slice(0, 200) : '';
                        pushTimelineEvent('JS_ERROR_DIAG (name=' + name + ', message=' + message + ')');
                    } catch (innerErr) {
                        // 診断ログ自体の失敗を握りつぶす（通話挙動へは影響させない）。
                    }
                });
                window.addEventListener('unhandledrejection', (ev) => {
                    try {
                        const reason = ev && ev.reason;
                        const name = (reason && reason.name) ? reason.name : 'UnhandledRejection';
                        const message = (reason && reason.message) ? String(reason.message).slice(0, 200) : String(reason || '').slice(0, 200);
                        pushTimelineEvent('JS_UNHANDLED_REJECTION_DIAG (name=' + name + ', message=' + message + ')');
                    } catch (innerErr) {
                        // 診断ログ自体の失敗を握りつぶす（通話挙動へは影響させない）。
                    }
                });
            } catch (setupErr) {
                // リスナー登録自体の失敗も既存処理へ影響させない。
            }
        }

        const SILENCE_WARNING_TEXT = 'お声が確認できないため、このままですとお電話を終了します。';
        const SILENCE_GOODBYE_TEXT = 'お電話を終了させていただきます。ありがとうございました。';

        // 「AIがユーザーの回答を待っている」状態に入った瞬間に呼ぶ。既に
        // waiting/warned/goodbyeのいずれかであれば何もしない（多重開始防止）。
        function startSilenceTimerIfNeeded(myGeneration, armReasonForDiag) {
            if (silenceState !== 'idle') return;
            setSilenceState('waiting', armReasonForDiag || 'ai_waiting_for_user');
            pushTimelineEvent('SILENCE_TIMER_STARTED');
            // PHASE O5.6診断: armされた時刻を記録するのみ（発火条件・時間は無変更）。
            silenceTimerArmedAt = performance.now();
            try {
                pushTimelineEvent('SILENCE_TIMER_ARMED (durationMs=' + SILENCE_TIMEOUT_MS
                    + ', silenceState=waiting, reason=' + (armReasonForDiag || 'ai_waiting_for_user') + ')');
            } catch (diagErr) {}
            silenceTimerId = setTimeout(() => {
                silenceTimerId = null;
                // PHASE O5.6診断: 発火した事実と、実際の経過時間・その時点の
                // 発話タイミングを記録する（isStaleCallEventの判定・その後の
                // triggerSilenceWarning呼び出し自体は元のコードと完全に同じ）。
                try {
                    pushTimelineEvent('SILENCE_TIMER_FIRED (expectedDurationMs=' + SILENCE_TIMEOUT_MS
                        + ', actualElapsedMs=' + (silenceTimerArmedAt === null ? 'null' : msSince(silenceTimerArmedAt))
                        + ', silenceState=' + silenceState
                        + ', elapsedSinceSpeechStartedMs=' + (msSince(lastSpeechStartedAt) === null ? 'null' : msSince(lastSpeechStartedAt))
                        + ', elapsedSinceSpeechStoppedMs=' + (msSince(lastSpeechStoppedAt) === null ? 'null' : msSince(lastSpeechStoppedAt))
                        + ', stale=' + isStaleCallEvent(myGeneration) + ')');
                } catch (diagErr) {}
                if (isStaleCallEvent(myGeneration)) return;
                triggerSilenceWarning(myGeneration);
            }, SILENCE_TIMEOUT_MS);
        }

        // 有効なユーザー発話を検知した、またはAIが新しい応答生成を開始した等、
        // 「無言で待っている」状態ではなくなったときに呼ぶ。
        function resetSilenceTimer(reason) {
            if (silenceState === 'goodbye') {
                // 既に終話案内アナウンスを送信済み（間もなくendCallする既定路線）。
                // ここでは取り消さず、既存のcallGeneration/endedガードに委ねる
                // （終話中の競合防止・ユーザーの明示的な指示）。
                // PHASE O5.6診断: 「'goodbye'中はキャンセルされない」という
                // 既存仕様どおりの動作が実際に起きたことをログに残すのみ
                // （cancel機能自体は今回追加しない）。
                try {
                    pushTimelineEvent('SILENCE_TIMER_RESET_IGNORED (state=goodbye, reason=' + reason + ')');
                } catch (diagErr) {}
                return;
            }
            const wasWarned = (silenceState === 'warned');
            // PHASE O5.6診断: 実際にどちらのタイマーが（何ms経過時点で）
            // 解除されたかを記録する。clearTimeout自体・その後のsilenceState
            // 更新ロジックは元のコードと完全に同じ。
            if (silenceTimerId !== null) {
                const timerAgeMsForDiag = silenceTimerArmedAt === null ? null : msSince(silenceTimerArmedAt);
                clearTimeout(silenceTimerId); silenceTimerId = null;
                try {
                    pushTimelineEvent('SILENCE_TIMER_CANCELLED (timerAgeMs=' + (timerAgeMsForDiag === null ? 'null' : timerAgeMsForDiag) + ', reason=' + reason + ')');
                } catch (diagErr) {}
            }
            if (silenceWarningTimerId !== null) {
                const graceAgeMsForDiag = silenceWarningGraceArmedAt === null ? null : msSince(silenceWarningGraceArmedAt);
                clearTimeout(silenceWarningTimerId); silenceWarningTimerId = null;
                try {
                    pushTimelineEvent('SILENCE_WARNING_GRACE_CANCELLED (timerAgeMs=' + (graceAgeMsForDiag === null ? 'null' : graceAgeMsForDiag) + ', reason=' + reason + ')');
                } catch (diagErr) {}
            }
            if (silenceState !== 'idle') {
                pushTimelineEvent('SILENCE_TIMER_RESET (reason=' + reason + ', 直前state=' + silenceState + ')');
            }
            if (wasWarned) {
                // 終話予告後にユーザーが話した場合の終話キャンセル。
                pushTimelineEvent('SILENCE_WARNING_CANCELLED (reason=' + reason + ')');
            }
            setSilenceState('idle', reason);
        }

        function triggerSilenceWarning(myGeneration) {
            if (isStaleCallEvent(myGeneration)) return;
            if (silenceState !== 'waiting') return; // 既に別状態へ遷移済みなら何もしない（安全側）
            setSilenceState('warned', 'silence_timer_fired');
            pushTimelineEvent('SILENCE_WARNING_TRIGGERED');
            sendResponseCreate('silence_warning', SILENCE_WARNING_TEXT);
            // PHASE O5.6診断: 8秒猶予タイマーがarmされた時刻を記録する
            // （SILENCE_WARNING_GRACE_MS・setTimeoutの発火条件は無変更）。
            silenceWarningGraceArmedAt = performance.now();
            try {
                pushTimelineEvent('SILENCE_WARNING_GRACE_ARMED (durationMs=' + SILENCE_WARNING_GRACE_MS + ', silenceState=warned)');
            } catch (diagErr) {}
            silenceWarningTimerId = setTimeout(() => {
                silenceWarningTimerId = null;
                // PHASE O5.6診断: 今回のAudit対象の核心。この時点のsilenceStateが
                // 'warned'のまま（＝会話が正常に継続していても解除されていない）
                // かどうかを、そのまま記録する。判定・分岐ロジックは元のコードと
                // 完全に同じ（この直後のtriggerSilenceFinalGoodbye呼び出しの
                // ガード自体は変更していない）。
                try {
                    pushTimelineEvent('SILENCE_WARNING_GRACE_FIRED (expectedDurationMs=' + SILENCE_WARNING_GRACE_MS
                        + ', actualElapsedMs=' + (silenceWarningGraceArmedAt === null ? 'null' : msSince(silenceWarningGraceArmedAt))
                        + ', silenceState=' + silenceState
                        + ', elapsedSinceSpeechStartedMs=' + (msSince(lastSpeechStartedAt) === null ? 'null' : msSince(lastSpeechStartedAt))
                        + ', elapsedSinceSpeechStoppedMs=' + (msSince(lastSpeechStoppedAt) === null ? 'null' : msSince(lastSpeechStoppedAt))
                        + ', lastResponseReasonCategory=' + lastResponseReasonCategoryForDiag
                        + ', stale=' + isStaleCallEvent(myGeneration) + ')');
                } catch (diagErr) {}
                if (isStaleCallEvent(myGeneration)) return;
                triggerSilenceFinalGoodbye(myGeneration);
            }, SILENCE_WARNING_GRACE_MS);
        }

        function triggerSilenceFinalGoodbye(myGeneration) {
            if (isStaleCallEvent(myGeneration)) return;
            if (silenceState !== 'warned') return; // 猶予中にユーザーが話した等で既にキャンセル済みなら何もしない
            setSilenceState('goodbye', 'warning_grace_expired');
            pendingSilenceGoodbyeHangup = true;
            pushTimelineEvent('SILENCE_FINAL_GOODBYE_STARTED');
            sendResponseCreate('silence_final_goodbye', SILENCE_GOODBYE_TEXT);
        }

        // 終話案内アナウンスの音声再生が完了したことを確認してから、既存の
        // 安全なendCall()経路で通話を終了する（pc.close()/dc.close()/
        // track.stop()をここから直接個別に呼ぶことは絶対にしない）。
        // output_audio_buffer.stopped（通常はこちらが先に来る）とresponse.done
        // （安全網。他のAI音声完了処理と同じ既存パターンを踏襲）の両方から
        // 呼ぶが、pendingSilenceGoodbyeHangupを最初に呼ばれた時点で消費する
        // ため、二重にendCall()が呼ばれることはない（仮に呼ばれても既存の
        // ended guardによりendCall自体が安全に無視する）。
        function maybeHangUpAfterSilenceGoodbye(myGeneration, source) {
            if (!pendingSilenceGoodbyeHangup) return;
            pendingSilenceGoodbyeHangup = false;
            if (isStaleCallEvent(myGeneration)) return;
            pushTimelineEvent('SILENCE_END_CALL_REQUESTED (source=' + source + ')');
            endCall(SILENCE_GOODBYE_TEXT, 'silence_timeout');
        }

        // PHASE4（重要・response.create重複防止調査）: response.create送信の
        // 一元化ラッパー。これまでresponse.createの送信箇所が
        // maybeSendInitialGreeting()とhandleFunctionCallItem()の2箇所に
        // 分散しており、それぞれ理由が記録されていなかった。ここでは
        // reason文字列をローカルのタイムラインにのみ記録する（OpenAIへは
        // 送信しない・PIIなし）。
        // また、直前のresponse.createに対応するresponse.doneをまだ受信して
        // いない（responseState==='active'）状態で新たなresponse.createを
        // 送ろうとした場合、それを検知してログに残す。Realtime APIが
        // 多重response.createをどう扱うか（エラーになるか、キューされるか）
        // は実機/サーバー側の挙動に依存し断定できないため、ここでは送信を
        // 止めず観測のみ行う（既存の応答フロー自体は変更しない）。
        function sendResponseCreate(reason, instructionsOverride) {
            // PHASE8（会話停止調査）: タイムラインの表記をRESPONSE_CREATE_REQUESTED
            // という統一名に揃える（reasonで区別する）。挙動・送信内容は一切
            // 変更しない。ログ文言のみの変更。
            // 追加要件（Conversation Time Control・Silence Timeout）:
            // instructionsOverrideが指定された場合のみ、そのレスポンス1回に
            // 限りresponse.instructionsを付与する。第一声用response.createの
            // 直前コメントで確認済みの通り、response.instructionsはセッション
            // 全体のinstructionsを「追加」ではなく「置換」する仕様だが、
            // Silence Timeoutの予告/終話案内は固定の短い定型文をそのまま
            // 一字一句話させれば十分（人格・Tool呼び出し等は不要）なため、
            // ここでは意図的にこの置換仕様を利用する。省略時（既存の全呼び出し
            // 箇所）は従来通りresponseフィールド自体を付与しない。
            if (!dc || dc.readyState !== 'open') {
                pushTimelineEvent('RESPONSE_CREATE_SKIPPED (dc未接続, reason=' + reason + ')');
                logEvent('response.create送信スキップ: DataChannel未接続 (reason=' + reason + ')');
                return false;
            }
            if (responseState === 'active') {
                pushTimelineEvent('[要確認] RESPONSE_CREATE多重送信の可能性 (reason=' + reason + ')');
                logEvent('[要確認] response.createを送信しますが、直前のresponseがまだ完了していません'
                    + '(responseState=active, reason=' + reason + ')。多重応答の可能性があります（観測のみ・送信は継続します）');
                // FAST TURN HOTFIX 5（今回追加）: この条件（response.create while
                // another response still active / conversation already has an
                // active response）はOpenAI Realtime APIがresponse.doneを
                // status=failedで返す既知の原因候補の一つ。従来はpushTimelineEvent/
                // logEventのみでConsoleへは出ておらず、実機Console調査で見逃される
                // 可能性があったため、console.logへも明示的に出す（送信自体は
                // 従来どおり継続する＝挙動変更なし）。
                console.log('[RESPONSE_CREATE_WHILE_ACTIVE] reason=' + reason);
            }
            try {
                const payload = { type: 'response.create' };
                if (instructionsOverride) {
                    payload.response = { instructions: instructionsOverride };
                }
                dc.send(JSON.stringify(payload));
                pushTimelineEvent('RESPONSE_CREATE_REQUESTED (reason=' + reason + (instructionsOverride ? ', instructions_override=true' : '') + ')');
                // PHASE O5.6診断: 実際に送信できたresponse.createのreasonを、
                // 次に届くresponse.createdでローカル相関させるためだけに保持する
                // （OpenAI Realtime APIへは一切送信しない・挙動制御には使わない）。
                responseCreateDiagSeq += 1;
                lastResponseCreateReason = reason;
                try {
                    pushTimelineEvent('RESPONSE_CREATE_DIAG (seq=' + responseCreateDiagSeq + ', reason=' + reason
                        + ', category=' + categorizeResponseReason(reason) + ')');
                } catch (diagErr) {}
                return true;
            } catch (e) {
                pushTimelineEvent('RESPONSE_CREATE_FAILED (reason=' + reason + '): ' + e.message);
                throw e;
            }
        }

        // PHASE9/10: debug=1限定の音声診断パネルを更新する。通常Production
        // 利用者には#debugPanels自体が非表示のため、この関数はdebugMode=falseの
        // ときは即returnし、DOM更新コストも発生させない。
        function updateAudioDiagnosticsPanel() {
            if (!debugMode) return;
            const diagMicEl = document.getElementById('diagMic');
            if (!diagMicEl) return; // 念のため（要素が無い＝古いキャッシュ等）
            const diagWebrtcEl = document.getElementById('diagWebrtc');
            const diagSendEl = document.getElementById('diagSend');
            const diagAiAudioEl = document.getElementById('diagAiAudio');
            const diagPlaybackEl = document.getElementById('diagPlayback');
            const diagAudioContextMicEl = document.getElementById('diagAudioContextMic');
            const diagAudioContextRemoteEl = document.getElementById('diagAudioContextRemote');
            const diagMicLevelEl = document.getElementById('diagMicLevel');
            const diagAudioEnergyEl = document.getElementById('diagAudioEnergy');
            const diagVadEl = document.getElementById('diagVad');
            const diagResponseEl = document.getElementById('diagResponse');
            const diagIceEl = document.getElementById('diagIce');
            const diagDataChannelEl = document.getElementById('diagDataChannel');
            const diagAiOutputEl = document.getElementById('diagAiOutput');
            const diagToolEl = document.getElementById('diagTool');
            const diagGreetingSourceEl = document.getElementById('diagGreetingSource');
            const diagAudioSourceEl = document.getElementById('diagAudioSource');
            const diagLastInterruptionEl = document.getElementById('diagLastInterruption');
            const diagFailureSourceEl = document.getElementById('diagFailureSource');
            const diagInboundPacketsEl = document.getElementById('diagInboundPackets');
            const diagPacketsLostEl = document.getElementById('diagPacketsLost');
            const diagJitterEl = document.getElementById('diagJitter');
            const diagConcealedEl = document.getElementById('diagConcealed');
            const diagJitterBufferDelayEl = document.getElementById('diagJitterBufferDelay');
            const diagZeroWaitPlaybackEl = document.getElementById('diagZeroWaitPlayback');

            const micTrack = localStream ? localStream.getAudioTracks()[0] : null;
            if (!micTrack) {
                setStatus(diagMicEl, '未取得', null);
            } else if (micTrack.readyState === 'live' && micTrack.enabled && !micTrack.muted) {
                setStatus(diagMicEl, '● 正常', 'ok');
            } else {
                setStatus(diagMicEl, '× 問題(readyState=' + micTrack.readyState + ' muted=' + micTrack.muted + ')', 'bad');
            }

            if (diagMicLevelEl) {
                setStatus(diagMicLevelEl, (lastMicLevelPct === null ? '—' : lastMicLevelPct + '%'), null);
            }

            if (!pc) {
                setStatus(diagWebrtcEl, '未接続', null);
            } else {
                const cs = pc.connectionState;
                setStatus(diagWebrtcEl, '● ' + cs,
                    cs === 'connected' ? 'ok' : (['failed', 'disconnected', 'closed'].includes(cs) ? 'bad' : 'warn'));
            }

            if (outboundPacketsTrend === 'increasing') {
                setStatus(diagSendEl, '● 送信中(packets増加)', 'ok');
            } else if (outboundPacketsTrend === 'stopped') {
                setStatus(diagSendEl, '× 停止(packets増加なし)', 'bad');
            } else {
                setStatus(diagSendEl, debugMode && pc ? '確認中…' : '未確認', null);
            }

            // PHASE7: packetsSentとは別に音声エネルギーの増減も表示する。
            // 「packetsは増えているのに音声エネルギーが増えない」ケースを
            // 目視で区別できるようにするための診断用表示（自動判定はしない）。
            if (diagAudioEnergyEl) {
                if (audioEnergyTrend === 'increasing') {
                    setStatus(diagAudioEnergyEl, '● 増加中', 'ok');
                } else if (audioEnergyTrend === 'stopped') {
                    setStatus(diagAudioEnergyEl, '△ 増加なし', 'warn');
                } else if (audioEnergyTrend === 'unsupported') {
                    setStatus(diagAudioEnergyEl, 'N/A（未対応ブラウザ）', null);
                } else {
                    setStatus(diagAudioEnergyEl, debugMode && pc ? '確認中…' : '未確認', null);
                }
            }

            if (diagVadEl) {
                setStatus(diagVadEl, userVadState === 'speech' ? '● 発話検知中' : 'idle',
                    userVadState === 'speech' ? 'ok' : null);
            }
            if (diagResponseEl) {
                if (responseState === 'active') {
                    setStatus(diagResponseEl, '● 生成中', 'warn');
                } else if (responseState === 'done') {
                    setStatus(diagResponseEl, '● 完了', 'ok');
                } else if (responseState === 'error') {
                    setStatus(diagResponseEl, '× エラー', 'bad');
                } else {
                    setStatus(diagResponseEl, 'idle', null);
                }
            }

            if (!remoteTrackReceived) {
                setStatus(diagAiAudioEl, '未受信', null);
            } else if (remoteTrackReadyState === 'ended') {
                setStatus(diagAiAudioEl, '× track終了(ended)', 'bad');
            } else if (remoteTrackMuted) {
                setStatus(diagAiAudioEl, '△ 受信済み(muted)', 'warn');
            } else {
                setStatus(diagAiAudioEl, '● 受信済み', 'ok');
            }

            if (remotePlayState === 'playing') {
                setStatus(diagPlaybackEl, '● 再生中', 'ok');
            } else if (remotePlayState === 'blocked') {
                setStatus(diagPlaybackEl, '× ブロック（自動再生制限の可能性）', 'bad');
            } else if (remotePlayState === 'paused') {
                setStatus(diagPlaybackEl, '一時停止', 'warn');
            } else if (remotePlayState === 'waiting' || remotePlayState === 'stalled') {
                // 再調査（第2ラウンド）: バッファリング待ち/ストール中。
                // 「ブチッ」の候補としてもっとも直接的な状態。
                setStatus(diagPlaybackEl, '△ ' + remotePlayState + '（バッファリング/ストール中）', 'warn');
            } else {
                setStatus(diagPlaybackEl, '未確認', null);
            }

            // 再調査（第2ラウンド・Inbound Audio診断）: OpenAI→ブラウザへの
            // 音声パケット自体の到達状況。REALTIME PLAYBACK（上）がwaiting/
            // stalledなのにこちらがincreasingのままなら「パケットは届いて
            // いるのにHTMLAudioElement側の再生だけが途切れている」ことになり、
            // 逆にこちらもstoppedなら「そもそもパケットが届いていない
            // （ネットワーク/WebRTC側の問題）」ことになる、という切り分けに使う。
            if (diagInboundPacketsEl) {
                if (inboundPacketsTrend === 'increasing') {
                    setStatus(diagInboundPacketsEl, '● 増加中', 'ok');
                } else if (inboundPacketsTrend === 'stopped') {
                    setStatus(diagInboundPacketsEl, '× 停止(packets増加なし)', 'bad');
                } else {
                    setStatus(diagInboundPacketsEl, debugMode && pc ? '確認中…' : '未確認', null);
                }
            }
            if (diagPacketsLostEl) {
                setStatus(diagPacketsLostEl, (lastPacketsLost === null ? 'N/A' : String(lastPacketsLost)),
                    (lastPacketsLost !== null && lastPacketsLost > 0) ? 'warn' : null);
            }
            if (diagJitterEl) {
                setStatus(diagJitterEl, (lastJitter === null ? 'N/A' : lastJitter.toFixed(4)), null);
            }
            // CRITICAL INCIDENT調査（音声ブツブツ/ノイズ）: concealedSamplesが
            // ポーリング間隔ごとに増加している＝その間にWebRTCが受信欠落を
            // 代替音声で穴埋めした＝ブツブツ/プチプチ音が実際に発生した、
            // という最も直接的な技術的証拠になる。自動判定はせず数値を
            // そのまま表示するのみ（0より大きい場合はwarn表示）。
            if (diagConcealedEl) {
                setStatus(diagConcealedEl, (lastConcealedSamples === null ? 'N/A' : String(lastConcealedSamples)),
                    (lastConcealedSamples !== null && lastConcealedSamples > 0) ? 'warn' : null);
            }
            if (diagJitterBufferDelayEl) {
                setStatus(diagJitterBufferDelayEl, (lastJitterBufferDelay === null ? 'N/A' : lastJitterBufferDelay.toFixed(4)), null);
            }
            // 再調査（第2ラウンド）: Zero-Wait音声要素自体の生のメディア
            // イベント状態（アプリ側のzeroWaitState=ready/playing/ended等とは
            // 別軸）。「和風デモの」直後のwaiting/stalled/pauseを直接確認する。
            if (diagZeroWaitPlaybackEl) {
                if (zeroWaitPlaybackState === 'playing') {
                    setStatus(diagZeroWaitPlaybackEl, '● 再生中', 'ok');
                } else if (zeroWaitPlaybackState === 'waiting' || zeroWaitPlaybackState === 'stalled') {
                    setStatus(diagZeroWaitPlaybackEl, '△ ' + zeroWaitPlaybackState + '（バッファリング/ストール中）', 'warn');
                } else if (zeroWaitPlaybackState === 'pause' || zeroWaitPlaybackState === 'abort' || zeroWaitPlaybackState === 'error') {
                    setStatus(diagZeroWaitPlaybackEl, '× ' + zeroWaitPlaybackState, 'bad');
                } else if (zeroWaitPlaybackState === 'ended') {
                    setStatus(diagZeroWaitPlaybackEl, '再生終了', null);
                } else {
                    setStatus(diagZeroWaitPlaybackEl, zeroWaitPlaybackState, null);
                }
            }

            setStatus(diagAudioContextMicEl, micAudioCtx ? micAudioCtx.state : '未作成',
                micAudioCtx ? (micAudioCtx.state === 'running' ? 'ok' : 'warn') : null);
            setStatus(diagAudioContextRemoteEl, remoteAudioCtx ? remoteAudioCtx.state : '未作成',
                remoteAudioCtx ? (remoteAudioCtx.state === 'running' ? 'ok' : 'warn') : null);

            // PHASE11: ICE接続状態。
            if (diagIceEl) {
                setStatus(diagIceEl, iceState,
                    iceState === 'connected' || iceState === 'completed' ? 'ok'
                        : (['failed', 'disconnected', 'closed'].includes(iceState) ? 'bad' : 'warn'));
            }
            // PHASE12: DataChannel状態。
            if (diagDataChannelEl) {
                setStatus(diagDataChannelEl, dataChannelState,
                    dataChannelState === 'open' ? 'ok' : (dataChannelState === '未接続' ? null : 'bad'));
            }
            // PHASE9: AI音声出力バッファのアクティブ状態（既存のaiAudioOutputActive
            // フラグをそのまま表示するだけ。対処なし・観測のみ）。
            if (diagAiOutputEl) {
                setStatus(diagAiOutputEl, aiAudioOutputActive ? '● active' : 'idle',
                    aiAudioOutputActive ? 'ok' : null);
            }
            // PHASE14: 直近のTool呼び出し状態（Tool名+状態のみ、PIIなし）。
            if (diagToolEl) {
                setStatus(diagToolEl, lastToolLabel, null);
            }
            // PHASE3: このセッションのGreeting発生源。
            if (diagGreetingSourceEl) {
                setStatus(diagGreetingSourceEl, greetingSource, null);
            }
            // PHASE12: 実際にどちらの音声トラックが鳴ったかの事後観測結果
            // （GREETING SOURCEとは別軸。決定時点ではなく実際の再生確認時点）。
            if (diagAudioSourceEl) {
                setStatus(diagAudioSourceEl, audioSource, null);
            }
            // PHASE6: 直近のAI発話中断（バージイン疑い）の簡潔な説明。
            if (diagLastInterruptionEl) {
                setStatus(diagLastInterruptionEl, lastInterruptionInfo, lastInterruptionInfo === '—' ? null : 'warn');
            }
            // PHASE9: 直近の接続失敗要因。
            if (diagFailureSourceEl) {
                setStatus(diagFailureSourceEl, failureSource, failureSource === '—' ? null : 'bad');
            }
        }

        // PHASE20/21（最重要・Greeting直後 Connection Failure緊急修正）: 接続失敗が
        // 判定された直後・cleanupConnection()実行前に呼び出す。その時点の生きた
        // pc/dc/track等の状態をコピーして保存する（cleanupConnection()は
        // pc.close()・dc各種null化・remoteTrack系resetを行うため、その後に
        // 状態を見てもpc.connectionState='closed'等、既に何もかも終わった状態
        // しか見えず、本当の原因が失われてしまう。必ずcleanup前に呼ぶこと）。
        // PIIは一切含めない（状態文字列・enum値・boolean・カウンタのみ）。
        function captureFailureSnapshot(source, failureReason) {
            const localTrack = localStream ? localStream.getAudioTracks()[0] : null;
            failureSnapshot = {
                capturedAt: new Date().toLocaleTimeString('ja-JP', { hour12: false }),
                source: source || '不明',
                failureReason: failureReason || '(理由未設定)',
                failureSource: failureSource,
                pcConnectionState: pc ? pc.connectionState : '(pc無し)',
                pcIceConnectionState: pc ? pc.iceConnectionState : '(pc無し)',
                pcSignalingState: pc ? pc.signalingState : '(pc無し)',
                dataChannelReadyState: dc ? dc.readyState : '(dc無し)',
                remoteTrackReadyState: remoteTrackReadyState,
                remoteTrackMuted: remoteTrackMuted,
                localTrackReadyState: localTrack ? localTrack.readyState : '(track無し)',
                localTrackMuted: localTrack ? localTrack.muted : null,
                localTrackEnabled: localTrack ? localTrack.enabled : null,
                responseState: responseState,
                userVadState: userVadState,
                aiAudioOutputActive: aiAudioOutputActive,
                greetingSource: greetingSource,
                audioSource: audioSource,
                zeroWaitState: zeroWaitEnabled ? zeroWaitState : 'not_applicable',
                // 再調査（第2ラウンド・Audio Element Timeline/Inbound Audio診断）:
                // cleanup直前の生のメディア再生状態・inbound RTP統計もあわせて
                // 保存する。cleanupConnection()はremotePlayState等をリセットする
                // ため、これを保存しておかないと「ブチッ」の瞬間の再生状態が
                // 失われてしまう。
                zeroWaitPlaybackState: zeroWaitPlaybackState,
                realtimePlaybackState: remotePlayState,
                inboundPacketsTrend: inboundPacketsTrend,
                lastPacketsLost: lastPacketsLost,
                lastJitter: lastJitter,
                lastConcealedSamples: lastConcealedSamples,
                lastSilentConcealedSamples: lastSilentConcealedSamples,
                lastJitterBufferDelay: lastJitterBufferDelay,
                lastJitterBufferEmittedCount: lastJitterBufferEmittedCount,
                playbackPauseCount: playbackPauseCount,
                lastInterruptionInfo: lastInterruptionInfo,
                currentToolLabel: lastToolLabel,
                // PHASE3: このスナップショットが取得された通話の世代番号。
                // 次の通話がこのスナップショット取得後に始まっていないかを
                // 事後的に確認できるようにする。
                callGenerationAtCapture: callGeneration,
            };
            pushTimelineEvent('FAILURE SNAPSHOT取得 (source=' + source + ')');
            updateFailureSnapshotPanel();
            // PHASE16（Greeting直後 Connection Failure再調査）: debug=1限定で、
            // 折りたたみパネルの外（失敗画面自体の直下）にもFailure Source /
            // Failure Reasonを表示する。PIIは含まない（enum値・短い技術説明のみ）。
            updateOnScreenFailureInfo();
            // PHASE23: debug=1の場合のみ、音声診断パネルを強制的に開いた状態にする
            // （iPhone単体でスクリーンショットを撮る際に、タップして展開する手間を
            // 無くすため）。通常お客様には#debugPanels自体が非表示のため影響しない。
            if (debugMode) {
                const panelEl = document.getElementById('audioDiagPanel');
                if (panelEl) panelEl.open = true;
            }
        }

        // PHASE22: failureSnapshotの内容を画面へ描画する。debugMode=falseでは
        // 何もしない（通常お客様には表示しない）。cleanupConnection()からは
        // 意図的に呼ばれない・クリアされない設計（cleanup後も表示し続けるため）。
        function updateFailureSnapshotPanel() {
            if (!debugMode) return;
            const blockEl = document.getElementById('failureSnapshotBlock');
            const contentEl = document.getElementById('failureSnapshotContent');
            if (!blockEl || !contentEl) return;
            if (!failureSnapshot) {
                blockEl.style.display = 'none';
                return;
            }
            const s = failureSnapshot;
            contentEl.textContent = [
                '取得時刻: ' + s.capturedAt,
                'source: ' + s.source,
                'failureReason: ' + s.failureReason,
                'failureSource: ' + s.failureSource,
                'pc.connectionState: ' + s.pcConnectionState,
                'pc.iceConnectionState: ' + s.pcIceConnectionState,
                'pc.signalingState: ' + s.pcSignalingState,
                'dataChannel.readyState: ' + s.dataChannelReadyState,
                'remoteTrack: readyState=' + s.remoteTrackReadyState + ' muted=' + s.remoteTrackMuted,
                'localTrack: readyState=' + s.localTrackReadyState + ' muted=' + s.localTrackMuted + ' enabled=' + s.localTrackEnabled,
                'responseState: ' + s.responseState,
                'userVadState: ' + s.userVadState,
                'aiAudioOutputActive: ' + s.aiAudioOutputActive,
                'greetingSource: ' + s.greetingSource,
                'audioSource（実際に鳴った音声）: ' + s.audioSource,
                'zeroWaitState: ' + s.zeroWaitState,
                'ZERO-WAIT PLAYBACK（生イベント）: ' + s.zeroWaitPlaybackState,
                'REALTIME PLAYBACK（生イベント）: ' + s.realtimePlaybackState,
                'INBOUND PACKETS trend: ' + s.inboundPacketsTrend,
                'PACKETS LOST: ' + (s.lastPacketsLost === null ? 'N/A' : s.lastPacketsLost),
                'JITTER: ' + (s.lastJitter === null ? 'N/A' : s.lastJitter),
                'CONCEALED SAMPLES: ' + (s.lastConcealedSamples === null ? 'N/A' : s.lastConcealedSamples),
                'SILENT CONCEALED SAMPLES: ' + (s.lastSilentConcealedSamples === null ? 'N/A' : s.lastSilentConcealedSamples),
                'JITTER BUFFER DELAY: ' + (s.lastJitterBufferDelay === null ? 'N/A' : s.lastJitterBufferDelay),
                'JITTER BUFFER EMITTED COUNT: ' + (s.lastJitterBufferEmittedCount === null ? 'N/A' : s.lastJitterBufferEmittedCount),
                'playbackPauseCount（累計）: ' + s.playbackPauseCount,
                'lastInterruptionInfo: ' + s.lastInterruptionInfo,
                '直近Tool: ' + s.currentToolLabel,
                'callGeneration(取得時点): ' + s.callGenerationAtCapture,
            ].join('\n');
            blockEl.style.display = 'block';
        }

        // PHASE16（Greeting直後 Connection Failure再調査・重要）: debug=1の場合
        // のみ、失敗画面自体の直下（折りたたみパネルを開かなくても見える場所）に
        // Failure Source / Failure Reasonを表示する。PHASE15のDEBUG MODE
        // インジケーターと同様、通常のお客様には一切表示しない
        // （#onScreenFailureInfo自体がdebugPanels配下でない場合に備え、
        // ここでもdebugMode判定を明示的に行う）。
        function updateOnScreenFailureInfo() {
            const el = document.getElementById('onScreenFailureInfo');
            if (!el) return;
            if (!debugMode || !failureSnapshot) {
                el.style.display = 'none';
                return;
            }
            el.textContent = 'Failure Source: ' + failureSnapshot.failureSource
                + ' / Failure Reason: ' + failureSnapshot.failureReason;
            el.style.display = 'block';
        }

        // PHASE11/15（重要）: これはdebugMode判定を含まない。play()が実際に
        // ブロックされた場合は、通常のお客様向け画面にもボタンを表示する必要が
        // あるため、updateAudioDiagnosticsPanel（debug限定）とは別の独立した
        // 関数にしている。ブロックされていない限りボタンは常に非表示。
        function updatePlaybackRecoveryButton() {
            if (!playbackRecoverBtn) return;
            playbackRecoverBtn.style.display = (remotePlayState === 'blocked') ? 'inline-block' : 'none';
        }

        // PHASE1: 画面ロック/バックグラウンド移行がマイク・WebRTC状態に影響して
        // いないかを、推測ではなくログで確認できるようにする（対処は行わない、
        // 観測のみ）。通話の有無に関わらずページ全体で1回だけ登録する。
        document.addEventListener('visibilitychange', () => {
            const micTrack = localStream ? localStream.getAudioTracks()[0] : null;
            logEvent('visibilitychange: document.visibilityState=' + document.visibilityState
                + (pc ? (' / pc.connectionState=' + pc.connectionState) : ' / pc=なし')
                + (micTrack ? (' / micTrack.readyState=' + micTrack.readyState + ' muted=' + micTrack.muted) : ' / micTrackなし'));
            updateAudioDiagnosticsPanel();
        });

        // PHASE3: debug=1限定のWebRTC送信統計ポーリング。RTCPeerConnection.
        // getStats()から、PIIを含まないaudio outbound-rtpの技術的カウンタ
        // （packetsSent/bytesSent等）だけを安全に取得する。ブラウザにより
        // 存在しないfieldは無理に使わずN/A表記に留める。Production通常利用者
        // （debugMode=false）ではこの関数自体が呼ばれない設計にしている
        // （呼び出し元のstartCall内でif (debugMode)ガード済み）。
        function stopStatsPolling() {
            if (statsPollIntervalId) { clearInterval(statsPollIntervalId); statsPollIntervalId = null; }
            lastOutboundAudioStats = null;
            outboundPacketsTrend = 'unknown';
            audioEnergyTrend = 'unknown';
            lastOutboundAudioEnergy = null;
            inboundPacketsTrend = 'unknown';
            lastInboundAudioStats = null;
            lastPacketsLost = null;
            lastJitter = null;
            lastConcealedSamples = null;
            lastSilentConcealedSamples = null;
            lastJitterBufferDelay = null;
            lastJitterBufferEmittedCount = null;
        }

        function startStatsPolling() {
            if (!debugMode || !pc) return;
            stopStatsPolling();
            statsPollIntervalId = setInterval(async () => {
                if (!pc) { stopStatsPolling(); return; }
                try {
                    const stats = await pc.getStats();
                    let outboundAudio = null;
                    // 再調査（第2ラウンド・Inbound Audio診断）: inbound-rtpは
                    // isRemoteフィールドを持たない実装が多いため、outbound側と
                    // 異なりisRemoteでの絞り込みは行わない（kind==='audio'のみで
                    // 十分に一意に特定できる。受信側は通常1本のみのため）。
                    let inboundAudio = null;
                    stats.forEach((report) => {
                        if (report.type === 'outbound-rtp' && report.kind === 'audio' && !report.isRemote) {
                            outboundAudio = report;
                        }
                        if (report.type === 'inbound-rtp' && report.kind === 'audio') {
                            inboundAudio = report;
                        }
                    });
                    if (inboundAudio) {
                        const packetsReceived = (typeof inboundAudio.packetsReceived === 'number') ? inboundAudio.packetsReceived : null;
                        const bytesReceived = (typeof inboundAudio.bytesReceived === 'number') ? inboundAudio.bytesReceived : null;
                        const packetsLost = (typeof inboundAudio.packetsLost === 'number') ? inboundAudio.packetsLost : null;
                        const jitter = (typeof inboundAudio.jitter === 'number') ? inboundAudio.jitter : null;
                        if (lastInboundAudioStats && packetsReceived !== null && lastInboundAudioStats.packetsReceived !== null) {
                            inboundPacketsTrend = (packetsReceived > lastInboundAudioStats.packetsReceived) ? 'increasing' : 'stopped';
                        }
                        lastInboundAudioStats = { packetsReceived, bytesReceived, ts: performance.now() };
                        lastPacketsLost = packetsLost;
                        lastJitter = jitter;
                        // CRITICAL INCIDENT調査（音声ブツブツ/ノイズ）: concealedSamples/
                        // silentConcealedSamplesは「受信側で欠落を取り繕うために代替
                        // 音声で穴埋めした量」＝ブツブツ音の最も直接的な技術的シグナル。
                        // jitterBufferDelay/jitterBufferEmittedCountは受信バッファの
                        // 遅延の指標。既存のpacketsLost/jitterと同じtypeof安全ガードで
                        // 追加するのみ（存在しないfieldはN/Aのまま。仮定しない）。
                        const concealedSamples = (typeof inboundAudio.concealedSamples === 'number') ? inboundAudio.concealedSamples : null;
                        const silentConcealedSamples = (typeof inboundAudio.silentConcealedSamples === 'number') ? inboundAudio.silentConcealedSamples : null;
                        const jitterBufferDelay = (typeof inboundAudio.jitterBufferDelay === 'number') ? inboundAudio.jitterBufferDelay : null;
                        const jitterBufferEmittedCount = (typeof inboundAudio.jitterBufferEmittedCount === 'number') ? inboundAudio.jitterBufferEmittedCount : null;
                        lastConcealedSamples = concealedSamples;
                        lastSilentConcealedSamples = silentConcealedSamples;
                        lastJitterBufferDelay = jitterBufferDelay;
                        lastJitterBufferEmittedCount = jitterBufferEmittedCount;
                        logEvent('[音声受信統計] packetsReceived=' + (packetsReceived === null ? 'N/A' : packetsReceived)
                            + ' bytesReceived=' + (bytesReceived === null ? 'N/A' : bytesReceived)
                            + ' packetsLost=' + (packetsLost === null ? 'N/A' : packetsLost)
                            + ' jitter=' + (jitter === null ? 'N/A' : jitter)
                            + ' concealedSamples=' + (concealedSamples === null ? 'N/A' : concealedSamples)
                            + ' silentConcealedSamples=' + (silentConcealedSamples === null ? 'N/A' : silentConcealedSamples)
                            + ' jitterBufferDelay=' + (jitterBufferDelay === null ? 'N/A' : jitterBufferDelay)
                            + ' jitterBufferEmittedCount=' + (jitterBufferEmittedCount === null ? 'N/A' : jitterBufferEmittedCount)
                            + ' trend=' + inboundPacketsTrend);
                    } else {
                        logEvent('[音声受信統計] inbound audio RTP統計がこのブラウザでは取得できませんでした(N/A)');
                    }
                    if (outboundAudio) {
                        const packetsSent = (typeof outboundAudio.packetsSent === 'number') ? outboundAudio.packetsSent : null;
                        const bytesSent = (typeof outboundAudio.bytesSent === 'number') ? outboundAudio.bytesSent : null;
                        // audioLevel/totalAudioEnergy/totalSamplesDurationはブラウザに
                        // よって存在しない場合があるため、無い場合はN/Aのまま扱う
                        // （無理に代替計算はしない）。
                        const audioLevelStr = (typeof outboundAudio.audioLevel === 'number') ? outboundAudio.audioLevel.toFixed(3) : 'N/A';
                        const totalAudioEnergyStr = (typeof outboundAudio.totalAudioEnergy === 'number') ? outboundAudio.totalAudioEnergy.toFixed(4) : 'N/A';
                        const totalSamplesDurationStr = (typeof outboundAudio.totalSamplesDuration === 'number') ? outboundAudio.totalSamplesDuration.toFixed(2) : 'N/A';
                        if (lastOutboundAudioStats && packetsSent !== null && lastOutboundAudioStats.packetsSent !== null) {
                            outboundPacketsTrend = (packetsSent > lastOutboundAudioStats.packetsSent) ? 'increasing' : 'stopped';
                        }
                        lastOutboundAudioStats = { packetsSent, bytesSent, ts: performance.now() };

                        // PHASE7: totalAudioEnergyはpacketsSentとは独立に増減を追う
                        // （「packetは増えているが音声エネルギーは増えていない」を
                        // 区別するため）。ブラウザが対応していない場合はunsupported。
                        if (typeof outboundAudio.totalAudioEnergy === 'number') {
                            if (lastOutboundAudioEnergy !== null) {
                                audioEnergyTrend = (outboundAudio.totalAudioEnergy > lastOutboundAudioEnergy) ? 'increasing' : 'stopped';
                            }
                            lastOutboundAudioEnergy = outboundAudio.totalAudioEnergy;
                        } else {
                            audioEnergyTrend = 'unsupported';
                        }
                        logEvent('[音声送信統計] packetsSent=' + (packetsSent === null ? 'N/A' : packetsSent)
                            + ' bytesSent=' + (bytesSent === null ? 'N/A' : bytesSent)
                            + ' audioLevel=' + audioLevelStr
                            + ' totalAudioEnergy=' + totalAudioEnergyStr
                            + ' totalSamplesDuration=' + totalSamplesDurationStr
                            + ' trend=' + outboundPacketsTrend);
                    } else {
                        logEvent('[音声送信統計] outbound audio RTP統計がこのブラウザでは取得できませんでした(N/A)');
                    }
                } catch (e) {
                    logEvent('[音声送信統計] getStats()呼び出しに失敗: ' + (e && e.message));
                }
                updateAudioDiagnosticsPanel();
            }, 3000);
        }

        function setupLatencyMeter(remoteStream) {
            if (!remoteStream) {
                logEvent('[Mobile Audio] remote MediaStreamが取得できなかったため、レイテンシ自動計測は無効です');
                return;
            }
            let audioCtx;
            try {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            } catch (e) {
                logEvent('Web Audio APIが利用できないため、レイテンシ自動計測は無効です: ' + e.message);
                return;
            }
            remoteAudioCtx = audioCtx;
            logEvent('リモート音声用AudioContext作成 state=' + audioCtx.state);
            audioCtx.onstatechange = () => {
                logEvent('リモート音声用AudioContext state変化: ' + audioCtx.state);
                updateAudioDiagnosticsPanel();
            };
            const source = audioCtx.createMediaStreamSource(remoteStream);
            const analyser = audioCtx.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);
            const data = new Uint8Array(analyser.frequencyBinCount);
            const THRESHOLD = 12; // 0-255スケールの平均音量しきい値（環境ノイズにより要調整）

            function tick() {
                analyser.getByteFrequencyData(data);
                const avg = data.reduce((a, b) => a + b, 0) / data.length;
                const nowSpeaking = avg > THRESHOLD;
                if (nowSpeaking && !aiSpeakingNow) {
                    aiSpeakingNow = true;
                    // FAST TURN HOTFIX 2（FIRST ANSWER MUST COUNT・今回追加）:
                    // AI SPEAKING PROTECTIONのengage/releaseを、これまでの
                    // output_audio_buffer.started/stopped/cleared/response.done
                    // （サーバー側イベント）だけに依存させず、ここで既に稼働中の
                    // リモート音声（AIの実際の発話音声そのもの）のローカル
                    // AnalyserNode計測（aiSpeakingNow、上のUIバッジと同じ既存
                    // シグナル）にも連動させる。
                    // 根拠（推測ではなく公式情報・実例で確認済み）: OpenAI
                    // Realtime APIのoutput_audio_buffer.started/stoppedは、
                    // OpenAI公式コミュニティで「実際の音声終了から6〜10秒遅延
                    // する」「started/stoppedイベント自体が届かないことがある」
                    // とOpenAIサポート自身が調査中の既知事象として報告されて
                    // いる(community.openai.com、Case #09291741として受理)。
                    // このイベント遅延・欠落が起きた場合、track.enabled=falseの
                    // ままユーザーの1回目の正常回答が始まってしまう
                    // （ROOT CAUSE TREEの分類A: 話した時点でmic無効）ことが、
                    // 「2回言わないと反応しない」症状の最有力候補である。
                    // ここでのAnalyserNode計測はAIの既知の音声ストリームのみを
                    // 見ており、周囲の雑音（ローカルmic入力）は一切含まれない
                    // ため、「雑音と人間発話を音量で分類する」ことには当たらない
                    // （STEP9の禁止事項には抵触しない。あくまで「AI自身の音声が
                    // 実際に鳴っているか」という既知信号の直接計測）。
                    // engage/release自体は完全に冪等（既にprotected/releaseの
                    // 状態なら即return）なので、既存のサーバーイベント経路と
                    // 純粋に「早い方が勝つ」レースになるだけで、既存経路を
                    // 破壊・変更しない。
                    engageAiSpeakingProtection('ai_audio_level_active');
                    bigMic.classList.add('ai-speaking');
                    setStatus(stAiSpeakEl, '発話中', 'ok');
                    if (greetingTiming.firstAiAudioPlaybackStarted === null) {
                        // Phase3C計測用: 通話中で最初にAI音声が実際に聞こえ始めた瞬間
                        // （第一声の体感開始時刻。既存のlastSpeechStoppedAtベースの
                        // ターンレイテンシー計測とは独立して記録する）。
                        greetingTiming.firstAiAudioPlaybackStarted = performance.now();
                        logGreetingLatenciesIfReady();
                    }
                    if (lastSpeechStoppedAt !== null) {
                        const totalAfterSpeechMs = performance.now() - lastSpeechStoppedAt;
                        recordLatencySample(totalAfterSpeechMs);
                        // FAST TURN 2（Section3/20）: 内訳(B)(C)(D)と合計(E)を
                        // 1行にまとめてデバッグログへ出す（既存のCopy Debug Logに
                        // そのまま含まれる。通常のお客様向け画面には影響しない）。
                        // 値が取れなかった内訳は'?'で表示し、推測の数値は入れない。
                        if (debugMode) {
                            const responseToAudioMs = lastResponseCreatedAtForLatency !== null
                                ? Math.round(performance.now() - lastResponseCreatedAtForLatency)
                                : null;
                            const fmt = (v) => (v === null || v === undefined ? '?' : v + 'ms');
                            // FAST TURN 3.1（実機レイテンシ診断・計測のみ）: tool=フィールドを
                            // 追加。Tool Callが無いターンは'NONE'、あったターンは
                            // '<tool名>:<所要ms>'。既存フィールドの意味・並び順は変更しない
                            // （末尾に追記するのみ）。
                            const toolField = turnLatencyToolOccurred
                                ? (turnLatencyToolName || '?') + ':' + fmt(turnLatencyToolDurationMs)
                                : 'NONE';
                            logEvent('TURN LATENCY speech=' + fmt(turnLatencySpeechDurationMs)
                                + ' vad_tail=' + fmt(turnLatencyVadTailMs)
                                + ' commit_to_response=' + fmt(turnLatencyCommitToResponseMs)
                                + ' response_to_audio=' + fmt(responseToAudioMs)
                                + ' total_after_speech=' + Math.round(totalAfterSpeechMs) + 'ms'
                                + ' fast_turn=' + (turnLatencyFastTurnLabel || 'NONE')
                                + ' tool=' + toolField);
                        }
                        lastSpeechStoppedAt = null;
                        lastCommittedAtForLatency = null;
                        lastResponseCreatedAtForLatency = null;
                        toolCallStartedAtForLatency = null;
                    }
                } else if (!nowSpeaking && aiSpeakingNow) {
                    aiSpeakingNow = false;
                    // FAST TURN HOTFIX 2（FIRST ANSWER MUST COUNT・今回追加）:
                    // 上のengage側コメント参照。AIの実際の音声が（サーバー側
                    // イベントを待たず）ローカルで無音になったと分かった瞬間に
                    // 保護解除する。output_audio_buffer.stopped/cleared/
                    // response.doneが遅延・欠落しても、ここが独立した経路として
                    // ミュート解除を保証する。1文中の短いポーズ等で瞬間的に
                    // nowSpeaking=falseへ振れても、releaseAiSpeakingProtection
                    // 自体は「track.enabledをtrueに戻すだけ」で即座に有害な
                    // 副作用は起こさず、直後にAIが発話を再開すればengage側が
                    // 次のtickで即座に再度保護をかけ直す（毎フレーム評価される
                    // ため露出時間は最大で1フレーム分に収まる）。この設計上の
                    // トレードオフは実機テストのAI_SPEAKING_START/ENDログで
                    // 検証すること（STEP30）。
                    releaseAiSpeakingProtection('ai_audio_level_silent');
                    bigMic.classList.remove('ai-speaking');
                    setStatus(stAiSpeakEl, '待機', null);
                }
                if (pc) requestAnimationFrame(tick);
            }
            tick();
        }

        // ===== WebRTC接続 =====
        let pc = null;
        let localStream = null;
        let dc = null;
        let ended = false;
        let maxDurationTimer = null;
        const MAX_CALL_DURATION_MS = 30 * 60 * 1000; // 30分（テスト中の暴走・コスト安全装置。恒久的な上限ではない）

        // PHASE1-4（最重要・Greeting直後 Connection Failure再調査）: 「接続に
        // 失敗しました」表示後に「待機中（お話しください）」等へ書き戻される
        // 実機不具合の対策。startCall()ごとにインクリメントする世代番号。
        // DataChannelメッセージ・Zero-Wait audio要素のイベント等、非同期に
        // 遅延して届くコールバックが、(a) 既にfatal failure確定済み（ended=true）
        // の同一世代、または(b) 既に新しい通話が開始された別世代、である場合に、
        // UIの状態表示（「待機中」「通話中」等）を書き戻さないようにするための
        // 判定に使う。'disconnected'のgrace期間中はended=falseのままなので、
        // このガードの対象にはならない（fatal確定＝endCall()実行後のみ対象）。
        let callGeneration = 0;
        function isStaleCallEvent(eventGeneration) {
            return ended || callGeneration !== eventGeneration;
        }

        // ===== Outbound AI Phase 4B: Customer Context用のsession状態 =====
        // voiceSessionId: /session発行時にサーバーから受け取る、この通話を
        // 識別するopaqueな値。find_customer/confirm_customer_identity/
        // get_customer_contextの各リクエストに自動的に付与する（AIの引数には
        // 一切含めない。常にこのページのJS変数からのみ読む）。
        // currentCandidateRef: find_customerがcandidate_foundを返した際に
        // サーバーから受け取るopaqueな一時参照値。confirm_customer_identity
        // 呼び出し時に自動転送するためだけにJS側で保持し、AIへ渡す
        // function_call_output（Realtimeモデルが実際に読む内容）には
        // 絶対に含めない（下のcallFindCustomerTool参照）。
        let voiceSessionId = null;
        let currentCandidateRef = null;

        // ===== Phase3C: AI第一声の自動開始 =====
        //
        // 設計方針（重要）:
        // - 第一声用のresponse.createは1通話につき最大1回だけ送る
        //   （initialGreetingSentフラグで保証。session.created/session.updatedが
        //   複数回発生しても、あるいは将来イベントの発生順序が変わっても
        //   二重送信しない）。
        // - タイミングはsession.created（およびsession.updated。ただし現行実装は
        //   client側からsession.updateを一切送らないため通常は発生しない）を採用。
        //   理由: instructions/voice/tools/turn_detectionはこのページが
        //   fetchSession()で受け取るephemeralトークンの発行時点
        //   （realtime_voice_ai.create_realtime_session）で既にOpenAI側
        //   セッションへ設定済みであり、DataChannel経由でsession.created
        //   イベントが届いた時点で既に有効になっている。したがって
        //   dc.onopen（セッション内容が反映されているか不明な最も早い時点）
        //   より安全で、かつ「session.updated完了後」を待つ設計は
        //   （client側がsession.updateを送らない現行実装では）そのイベント自体が
        //   発生せず永久に第一声が始まらない不具合になるため採用しない。
        // - お客様が接続直後に先に話し始めた場合（第一声より先にspeech_startedを
        //   検知した場合）は、こちらから第一声用response.createを送らず、
        //   既存のturn_detection（semantic_vad, create_response既定動作）による
        //   自動応答にそのまま委ねる。第一声とお客様発話が同時に競合する状況を
        //   そもそも作らない、という考え方（独自の状態機械や割り込み制御を
        //   新設せず、既存のRealtime API標準動作を最大限利用する）。
        // - 第一声のresponse.create送信後にお客様が話し始めた場合（第一声再生中の
        //   割り込み）は、OpenAI Realtime API標準の割り込み処理
        //   （speech_started検知時のoutput_audio_buffer.cleared /
        //   conversation.item.truncated）と、既存instructions（Core Rulesの
        //   「お客様が話し始めたら、あなたの発話は途中でも止めてください」）に
        //   委ねる。Phase3B.2までの通常会話でも同じ仕組みで割り込みが正しく
        //   動作していることを確認済みのため、第一声だけを特別扱いする独自の
        //   response.cancel処理等は追加しない。
        // - 第一声の具体的な文言はハードコードしない。既存のsession instructions
        //   （AIスタッフ設定のgreetingを反映する_build_greeting_section等、
        //   Phase2で完成済み）にそのまま従わせる。第一声用response.createには
        //   response-level instructionsを一切付与しない（本番E2Eで判明: OpenAI
        //   Realtime APIはresponse.instructionsを「補足」ではなく、その
        //   レスポンス限りでセッション全体のinstructionsを「置き換える」仕様
        //   のため、付与するとAIスタッフ設定のgreeting/人格が第一声から消えて
        //   しまう。詳細はmaybeSendInitialGreeting()内のコメントと
        //   realtime_voice_ai.pyのbuild_realtime_instructions/
        //   _build_greeting_sectionのPhase3C追記を参照）。
        let initialGreetingSent = false;
        let userSpokeBeforeGreeting = false;

        // Phase3C: 第一声レイテンシー計測用（DB保存なし、この通話中のみメモリ保持）
        const greetingTiming = {
            callStart: null,
            tokenFetched: null,
            sdpAnswerSet: null,
            dcOpen: null,
            sessionCreated: null,
            greetingResponseCreateSent: null,
            firstResponseCreated: null,
            firstOutputAudioStarted: null,
            firstAiAudioPlaybackStarted: null,
            loggedA: false,
            loggedB: false,
            loggedC: false,
        };

        function resetGreetingState() {
            initialGreetingSent = false;
            userSpokeBeforeGreeting = false;
            greetingTiming.callStart = null;
            greetingTiming.tokenFetched = null;
            greetingTiming.sdpAnswerSet = null;
            greetingTiming.dcOpen = null;
            greetingTiming.sessionCreated = null;
            greetingTiming.greetingResponseCreateSent = null;
            greetingTiming.firstResponseCreated = null;
            greetingTiming.firstOutputAudioStarted = null;
            greetingTiming.firstAiAudioPlaybackStarted = null;
            greetingTiming.loggedA = false;
            greetingTiming.loggedB = false;
            greetingTiming.loggedC = false;
            if (zeroWaitEnabled) resetZeroWaitCallState();
        }

        function logGreetingLatenciesIfReady() {
            const t = greetingTiming;
            if (!t.loggedB && t.greetingResponseCreateSent !== null && t.sessionCreated !== null) {
                t.loggedB = true;
                logEvent('第一声計測B: Realtime準備完了(session.created)→第一声response.create送信 = '
                    + Math.round(t.greetingResponseCreateSent - t.sessionCreated) + 'ms');
            }
            if (!t.loggedC && t.firstOutputAudioStarted !== null && t.greetingResponseCreateSent !== null) {
                t.loggedC = true;
                logEvent('第一声計測C: 第一声response.create送信→最初のaudio受信(output_audio_buffer.started) = '
                    + Math.round(t.firstOutputAudioStarted - t.greetingResponseCreateSent) + 'ms');
            }
            if (!t.loggedA && t.firstAiAudioPlaybackStarted !== null && t.callStart !== null) {
                t.loggedA = true;
                logEvent('第一声計測A: 通話開始操作→第一声の実際の音声再生開始 = '
                    + Math.round(t.firstAiAudioPlaybackStarted - t.callStart) + 'ms');
            }
        }

        // ===== Phase3C.1 PoC → Phase3G 本番一般化: Zero-Wait Greeting =====
        //
        // 目的（重要）: OpenAI Realtimeそのものの接続を速くすることではなく、
        // 接続に必要な約3秒（Phase3C実測）をGreetingの裏に隠すこと。通話開始
        // クリックの直後、事前生成済みの第一声音声(mp3)をほぼ即座に再生しつつ、
        // 裏でRealtime接続(startCall内の既存処理)を並行して進める。
        //
        // Phase3C.1〜3Fまでは、フロントエンドにハードコードした固定shop_id
        // ホワイトリスト（ZERO_WAIT_GREETING_SHOP_IDS）で対象店舗を限定していた。
        // Phase3Fで旧固定E2Eテスト店舗を削除した際にこの配列を空にしたため、
        // Zero-Wait Greetingが全店舗で無効化されたままになっていた。
        //
        // Phase3G: 固定shop_idホワイトリストを廃止し、ページ読み込み時に
        // GET /realtime-voice/greeting-text のレスポンス（zero_wait_eligible）
        // で対象可否をバックエンドから毎回動的に判定する方式に変更した。
        // 判定基準はAIStaffSettings行の有無（新しいBooleanカラムを追加せず、
        // 既存の状態から判断する。詳細はapp/routers/realtime_voice.pyの
        // get_realtime_voice_greeting_text docstring参照）。
        // AI電話受付自体はAIStaffSettingsの有無に関わらず全店舗で利用可能な
        // ままで、Zero-Wait（体感速度の最適化）のみが対象を限定される。
        //
        // zeroWaitEnabledはページ読み込み時のfetch完了まで暫定的にfalseで
        // 開始する。fetch失敗時もfalseのまま（＝Phase3C第一声のみにフォール
        // バック）となり、既存動作を壊さない安全側に倒す。
        let zeroWaitEnabled = false;
        // Phase3E-3: 静的ファイル(/greeting_audio/{shop_id}.mp3の手動配置運用)から、
        // DBキャッシュ・staff_name/voice/greeting変更時に自動再生成される
        // バックエンドエンドポイントへ切り替えた（app/routers/realtime_voice.pyの
        // get_realtime_voice_greeting_audio参照）。フェッチしてBlob化してから
        // 再生する既存の仕組み自体は変更していないため、レイテンシ特性
        // （プリロードはページ読み込み時、再生はクリック時にキャッシュ済み
        // Audioオブジェクトを再生するだけ）は変わらない。
        const ZERO_WAIT_AUDIO_URL = '/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/greeting-audio';
        // 第一声response.create送信可否の判定を、Zero-Waitの結果確定まで待つ
        // 最大時間。Zero-Wait再生開始(KPI-1)は数百ms以内に確定する想定のため、
        // これより十分大きい値にしておけば通常は待たされない。
        const ZERO_WAIT_DECISION_GRACE_MS = 800;
        // play()呼び出し後、'playing'イベントも失敗も一切発生しないまま
        // ハングし続けることを防ぐ安全装置（発生した場合は失敗として扱う）。
        const ZERO_WAIT_PLAY_TIMEOUT_MS = 3000;

        // 状態遷移: 'idle' -> 'preloading' -> 'ready' | 'preload_failed'
        //          -> (通話開始後) 'playing' -> 'ended' | 'play_failed'
        let zeroWaitState = 'idle';
        let zeroWaitAudioEl = null;       // Audioオブジェクト本体（Fetch→Blob→ObjectURL方式）
        let zeroWaitObjectUrl = null;     // 明示的なrevokeObjectURLのために保持
        let zeroWaitGreetingText = null;  // /realtime-voice/greeting-text で取得した第一声文言（会話履歴通知用）
        let zeroWaitOutcomeResolve = null;
        let zeroWaitOutcomePromise = null; // 通話ごとに作り直す。'success'|'failure'|'not_applicable'|'timeout'に解決
        let zeroWaitAwaitingFirstPostGreetingLatency = false; // KPI-5計測用
        // 再調査（第2ラウンド・Audio Element Timeline）: Zero-Wait音声要素
        // そのもののメディア再生状態（アプリ側の判断=zeroWaitStateとは別軸）。
        // HTMLMediaElementの生イベント(playing/waiting/stalled/suspend/pause/
        // ended/abort/error)をそのまま反映するだけの値。診断パネルの
        // 「ZERO-WAIT PLAYBACK」表示に使う。PIIなし。
        let zeroWaitPlaybackState = 'unknown';

        const zeroWaitTiming = {
            playingEventAt: null,   // 'playing'イベント実測（KPI-1の分子）
            endedAt: null,
            realtimeReadyAt: null,  // session.created実測時刻のコピー（KPI-2/3比較用）
        };

        function resetZeroWaitCallState() {
            // プリロード成功済みなら'ready'に戻す。プリロード自体が未完了/失敗の
            // 場合はその状態を維持する（ページ読み込み時に一度だけ行う）。
            if (zeroWaitState === 'playing' || zeroWaitState === 'ended' || zeroWaitState === 'play_failed') {
                zeroWaitState = 'ready';
            }
            if (zeroWaitAudioEl) {
                // 再調査（第2ラウンド）: 明示的にpause()を呼ぶ唯一の箇所。
                // これは次の通話の準備（resetZeroWaitCallState自体は
                // startCall()より前のloadGreetingTextAndDetermineEligibility内
                // でのみ呼ばれ、通話進行中には一切呼ばれない）としてのみ
                // 実行される。通話進行中にこの経路が実行されていないかを
                // タイムラインで確認できるようにする。
                pushTimelineEvent('ZERO_WAIT_PAUSE_REQUESTED (source=resetZeroWaitCallState)');
                try { zeroWaitAudioEl.pause(); zeroWaitAudioEl.currentTime = 0; } catch (e) {}
            }
            zeroWaitTiming.playingEventAt = null;
            zeroWaitTiming.endedAt = null;
            zeroWaitTiming.realtimeReadyAt = null;
            zeroWaitAwaitingFirstPostGreetingLatency = false;
            zeroWaitOutcomePromise = new Promise((resolve) => { zeroWaitOutcomeResolve = resolve; });
        }

        // ページ読み込み時（通話開始クリックより前）に音声本体と第一声テキストを
        // 並行してプリロードする。片方が失敗してももう片方には影響しない。
        async function preloadZeroWaitGreetingAudio() {
            if (!zeroWaitEnabled) return;
            zeroWaitState = 'preloading';
            const t0 = performance.now();
            logEvent('[ZeroWait] 音声プリロード開始: ' + ZERO_WAIT_AUDIO_URL);
            try {
                const res = await fetch(ZERO_WAIT_AUDIO_URL);
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const blob = await res.blob();
                zeroWaitObjectUrl = URL.createObjectURL(blob);
                zeroWaitAudioEl = new Audio();
                zeroWaitAudioEl.preload = 'auto';
                zeroWaitAudioEl.src = zeroWaitObjectUrl;
                zeroWaitAudioEl.load();
                // 再調査（第2ラウンド・最重要）: この要素はページ読み込み時に一度だけ
                // 作られ、通話をまたいで再利用されるため、ライフサイクルイベントの
                // 購読もここで一度だけ行う（通話ごとに再登録しない＝多重登録を防ぐ）。
                // 「和風デモの」直後の「ブチッ」が、Zero-Wait音声要素自体の
                // バッファリング停止(waiting/stalled)によるものか、何らかの理由で
                // 一時停止(pause)されたのかを、ミリ秒単位のタイムスタンプ付きで
                // 区別できるようにする。UI・ビジネスロジックには一切影響しない
                // 観測専用の記録。PIIなし。
                ['playing', 'waiting', 'stalled', 'suspend', 'pause', 'ended', 'abort', 'error'].forEach((evt) => {
                    zeroWaitAudioEl.addEventListener(evt, () => {
                        zeroWaitPlaybackState = evt;
                        pushTimelineEvent('ZERO_WAIT_AUDIO: ' + evt
                            + ' (currentTime=' + zeroWaitAudioEl.currentTime.toFixed(2) + 's)');
                        updateAudioDiagnosticsPanel();
                    });
                });
                zeroWaitState = 'ready';
                logEvent('[ZeroWait] プリロード完了 (' + Math.round(performance.now() - t0) + 'ms, '
                    + blob.size + 'bytes)');
            } catch (e) {
                zeroWaitState = 'preload_failed';
                logEvent('[ZeroWait] プリロード失敗（通話時はPhase3Cの第一声にフォールバックします）: ' + e.message);
            }
        }

        // ===== PHASE O5.5: Speak-Then-Work Acknowledgement Fallback Audio =====
        //
        // 設計方針（重要・必ず守ること）:
        // - これはZero-Wait Greetingとは完全に独立した別機能。Zero-Waitの
        //   変数・関数（zeroWait*）には一切触れず、専用の新しい変数・関数
        //   （ackFallback*）のみを使う（Greeting関連コード変更禁止の指示を守る）。
        // - Realtime APIのresponse.create/response.done/DataChannelには一切
        //   関与しない、完全にローカルな<audio>要素の再生であり、FAST TURNの
        //   response lifecycle・T0-T10計測・turn_detectionには一切影響しない。
        // - 全店舗共通の固定文言（voiceだけが店舗ごとに異なる）のため、
        //   Zero-Waitのような「対象店舗かどうか」の判定は不要で、ページ読み込み時に
        //   常にプリロードする。
        // - 実際に再生するかどうかの判定（AI自身が既に一言発話していたら鳴らさない
        //   等）は、この関数ではなくhandleFunctionCallItem呼び出し直前の
        // 　response.output_item.done(function_call)ハンドラ側で行う
        //   （aiAudioOutputActiveを見て判定。下記参照）。
        const ACK_FALLBACK_AUDIO_URL = '/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/ack-fallback-audio';
        // 状態遷移: 'idle' -> 'preloading' -> 'ready' | 'preload_failed'
        let ackFallbackState = 'idle';
        let ackFallbackAudioEl = null;
        let ackFallbackObjectUrl = null;
        // PHASE O5.5 Speak-Then-Work計測（Section9）専用の観測変数。
        // 直近のUSER_SPEECH_STOPPED発生時刻(performance.now())を保持するだけで、
        // 既存T0-T10(toolContinuationTraceT0等)には一切触れない・参照もしない、
        // 完全に独立した追加計測。Realtime制御イベントの送信判断には使わない。
        let lastUserSpeechStoppedAt = null;
        function formatElapsedSinceSpeechStopped() {
            if (lastUserSpeechStoppedAt === null) return '?';
            return Math.round(performance.now() - lastUserSpeechStoppedAt) + 'ms';
        }
        // Speak-Then-Work対象のTool名一覧（Section6の方針: 何でも復唱しない。
        // Backend/DB確認で待ち時間が生じる場面、かつ副作用の無いToolのみを対象に
        // 限定する。create_reservation等の副作用があるToolは対象外
        // （barge-in時の二重実行リスクが質的に異なるため、O5.5では見送り、
        // 完了報告で明示的にopen itemとして報告する）。
        const SPEAK_THEN_WORK_ACK_TOOLS = new Set(['check_availability']);

        async function preloadAckFallbackAudio() {
            ackFallbackState = 'preloading';
            const t0 = performance.now();
            logEvent('[SpeakThenWork] ack fallback音声プリロード開始: ' + ACK_FALLBACK_AUDIO_URL);
            try {
                const res = await fetch(ACK_FALLBACK_AUDIO_URL);
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const blob = await res.blob();
                ackFallbackObjectUrl = URL.createObjectURL(blob);
                ackFallbackAudioEl = new Audio();
                ackFallbackAudioEl.preload = 'auto';
                ackFallbackAudioEl.src = ackFallbackObjectUrl;
                // PHASE O5.5 Speak-Then-Work計測（Section9）: play()呼び出し
                // （リクエスト時刻）だけでなく、実際にブラウザが再生を開始した
                // 瞬間（'playing'イベント）を「安全網音声のfirst byte相当」として
                // 記録する。既存のRealtime/T0-T10ロジックには一切影響しない、
                // 読み取り専用のイベントリスナー追加のみ。
                ackFallbackAudioEl.addEventListener('playing', () => {
                    pushTimelineEvent('SPEAK_THEN_WORK_ACK_FALLBACK_AUDIO_PLAYING (speech_stopped以降='
                        + formatElapsedSinceSpeechStopped() + ')');
                });
                ackFallbackAudioEl.load();
                ackFallbackState = 'ready';
                logEvent('[SpeakThenWork] ack fallbackプリロード完了 (' + Math.round(performance.now() - t0) + 'ms, '
                    + blob.size + 'bytes)');
            } catch (e) {
                ackFallbackState = 'preload_failed';
                // プリロード失敗時は、単に安全網が機能しないだけ（会話自体は
                // 従来通り進む）。fatalにはしない。
                logEvent('[SpeakThenWork] ack fallbackプリロード失敗（安全網が無効になりますが通話は継続します）: ' + e.message);
            }
        }

        // 通話開始のたびに再生位置をリセットする（前回通話の再生位置を
        // 持ち越さない）。Zero-WaitのresetZeroWaitCallState()とは独立した
        // 別関数（Zero-Wait側のコードには一切触れない）。
        function resetAckFallbackCallState() {
            if (ackFallbackAudioEl) {
                try { ackFallbackAudioEl.pause(); ackFallbackAudioEl.currentTime = 0; } catch (e) {}
            }
        }

        // response.output_item.done(function_call)ハンドラから呼ばれる。
        // 対象Toolかつ、このresponse内でAI自身がまだ一度も音声を発していない
        // （aiAudioOutputActive===false）場合にのみ、固定文言の安全網音声を
        // 再生する。Realtime側が既に一言話していた場合は絶対に重ねて再生しない
        // （二重acknowledgement防止）。
        function maybeSendSpeakThenWorkAckFallback(item, myGeneration) {
            if (!item || !SPEAK_THEN_WORK_ACK_TOOLS.has(item.name)) return;
            // 二重再生防止（Section5の「二重実行しない」方針の延長）: この
            // call_idについてToolの実処理が既に完了扱い（processedToolCallIds
            // 済み）の場合、同じfunction_callイベントが重複して届いたケースと
            // 判断し、安全網音声を重ねて再生しない。既存Setを読み取るのみで
            // 書き込みはこの関数からは行わない（書き込みはhandleFunctionCallItem
            // 側の既存ロジックのまま）。
            if (item.call_id && processedToolCallIds.has(item.call_id)) {
                pushTimelineEvent('SPEAK_THEN_WORK_ACK_SKIPPED (tool=' + item.name + ', reason=duplicate_call_id)');
                return;
            }
            if (aiAudioOutputActive) {
                pushTimelineEvent('SPEAK_THEN_WORK_ACK_SKIPPED (tool=' + item.name + ', reason=ai_already_speaking)');
                return;
            }
            if (ackFallbackState !== 'ready' || !ackFallbackAudioEl) {
                pushTimelineEvent('SPEAK_THEN_WORK_ACK_SKIPPED (tool=' + item.name + ', reason=fallback_not_ready:' + ackFallbackState + ')');
                return;
            }
            if (isStaleCallEvent(myGeneration)) return;
            try {
                ackFallbackAudioEl.currentTime = 0;
                const playPromise = ackFallbackAudioEl.play();
                pushTimelineEvent('SPEAK_THEN_WORK_ACK_FALLBACK_PLAY_REQUESTED (tool=' + item.name
                    + ', speech_stopped以降=' + formatElapsedSinceSpeechStopped() + ')');
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch((e) => {
                        pushTimelineEvent('SPEAK_THEN_WORK_ACK_FALLBACK_PLAY_FAILED (tool=' + item.name + '): ' + (e && e.message));
                    });
                }
            } catch (e) {
                pushTimelineEvent('SPEAK_THEN_WORK_ACK_FALLBACK_PLAY_FAILED (tool=' + item.name + '): ' + (e && e.message));
            }
        }

        // Phase3G: 第一声テキスト取得とZero-Wait対象可否判定(zero_wait_eligible)
        // を同じレスポンスから行う（ハードコードshop_id一覧の代わり）。
        // 常にこの店舗の第一声テキストは取得する（zeroWaitEnabledが後で
        // falseと判明した場合でも、この値自体は他の用途に影響しない）。
        async function loadGreetingTextAndDetermineEligibility() {
            try {
                const res = await fetch('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/greeting-text');
                const data = await res.json().catch(() => null);
                if (res.ok && data && typeof data.greeting_text === 'string') {
                    zeroWaitGreetingText = data.greeting_text;
                }
                if (res.ok && data && data.zero_wait_eligible === true) {
                    zeroWaitEnabled = true;
                    logEvent('[ZeroWait] この店舗はZero-Wait Greeting対象と判定されました（AIStaffSettings設定済み）');
                } else {
                    logEvent('[ZeroWait] この店舗はZero-Wait Greeting非対象です（Phase3Cの第一声のみで動作します）');
                }
            } catch (e) {
                // 判定自体が失敗した場合も安全側（Zero-Wait無効=Phase3Cのみ）に倒す。
                logEvent('[ZeroWait] 対象可否判定に失敗（Phase3Cの第一声のみで動作します）: ' + e.message);
            }

            if (zeroWaitEnabled) {
                resetZeroWaitCallState();
                await preloadZeroWaitGreetingAudio();
            }
        }

        loadGreetingTextAndDetermineEligibility();
        // PHASE O5.5: Speak-Then-Work安全網音声のプリロード。Zero-Waitの対象可否
        // 判定とは無関係に、全店舗で常にページ読み込み時にプリロードする
        // （固定文言のため、店舗ごとの対象判定が不要）。
        preloadAckFallbackAudio();

        // startCall()の同期チェーン内（getUserMediaより前・awaitを挟まない）から
        // 呼び出すこと（section11相当: ユーザー操作由来のジェスチャーとして
        // 扱われるうちにplay()を呼び、自動再生制限を回避するため）。
        function startZeroWaitGreeting() {
            if (!zeroWaitEnabled) {
                if (zeroWaitOutcomeResolve) zeroWaitOutcomeResolve('not_applicable');
                return;
            }
            if (zeroWaitState !== 'ready' || !zeroWaitAudioEl) {
                logEvent('[ZeroWait] 再生スキップ（状態=' + zeroWaitState + '）。Phase3Cの第一声にフォールバックします');
                pushTimelineEvent('ZeroWait skip (state=' + zeroWaitState + ')');
                zeroWaitOutcomeResolve('failure');
                return;
            }

            pushTimelineEvent('ZeroWait started');
            zeroWaitAudioEl.currentTime = 0;
            const playCalledAt = greetingTiming.callStart;
            // PHASE1/3（Greeting直後 Connection Failure再調査・最重要）: この
            // startZeroWaitGreeting()はstartCall()内でcallGeneration確定
            // 直後に同期的に呼ばれるため、ここで捕捉するcallGenerationの値が
            // そのままこの通話のmyGenerationとなる。onPlaying/onEndedは
            // {once:true}のイベントリスナーであり、通話がfatal failureに
            // 至った後（ended=true）や、既に次の通話が始まった後
            // （callGenerationが進んでいる後）に遅延して発火する可能性が
            // あるため、UIを書き換える行のみをisStaleCallEvent()で保護する
            // （zeroWaitState更新・KPIログ・タイムライン記録・
            // zeroWaitOutcomeResolveはデバッグ上有用なため無条件のまま）。
            const myGeneration = callGeneration;

            const onPlaying = () => {
                zeroWaitAudioEl.removeEventListener('playing', onPlaying);
                zeroWaitState = 'playing';
                // PHASE12: 実際にZero-Wait音声要素の再生が開始したことを
                // 事後的に確認できた時点でのみAUDIO SOURCEを確定する
                // （generation不一致等でUI表示自体はskipする場合でも、この
                // 事実の記録＝audioSource設定自体は常に行う。デバッグ上の
                // 事実観測であり、ユーザー向けUIの書き換えではないため）。
                audioSource = 'zero_wait';
                zeroWaitTiming.playingEventAt = performance.now();
                logEvent('[ZeroWait] KPI-1 通話開始→Greeting実際の再生開始 = '
                    + Math.round(zeroWaitTiming.playingEventAt - playCalledAt) + 'ms');
                pushTimelineEvent('ZeroWait playing');
                if (isStaleCallEvent(myGeneration)) {
                    pushTimelineEvent('ZeroWait playing: stale event, UI更新skip (generation=' + myGeneration + ')');
                } else {
                    bigMic.classList.add('ai-speaking');
                    statusText.textContent = '通話中';
                    subStatusText.textContent = '（ご案内音声を再生中）';
                }
                zeroWaitOutcomeResolve('success');
            };
            const onEnded = () => {
                zeroWaitAudioEl.removeEventListener('ended', onEnded);
                zeroWaitState = 'ended';
                zeroWaitTiming.endedAt = performance.now();
                pushTimelineEvent('ZeroWait ended');
                if (zeroWaitTiming.realtimeReadyAt !== null) {
                    logEvent('[ZeroWait] KPI-3 Greeting終了→Realtime準備完了 = '
                        + Math.round(zeroWaitTiming.realtimeReadyAt - zeroWaitTiming.endedAt) + 'ms'
                        + '（負値=Realtimeの方が先に準備完了済み）');
                } else {
                    logEvent('[ZeroWait] Greeting再生終了。Realtimeはまだ準備中です（KPI-3はRealtime準備完了時に記録されます）');
                }
                // PHASE1（最重要）: このonEndedが、fatal failure確定後（ended=true）
                // または既に新しい通話が開始された後（世代不一致）に遅延発火した
                // 場合、無条件に「待機中（お話しください）」へ書き戻していたことが
                // 「接続に失敗しました」表示との同時表示バグの2つの原因候補の
                // 一つだった。isStaleCallEventで保護する。
                if (isStaleCallEvent(myGeneration)) {
                    pushTimelineEvent('ZeroWait ended: stale event, UI書き戻しskip (generation=' + myGeneration + ')');
                } else {
                    bigMic.classList.remove('ai-speaking');
                    subStatusText.textContent = '待機中（お話しください）';
                    // Silence Timeout: Zero-Wait Greetingは事前録音のローカル音声を
                    // 再生しているだけでRealtimeのresponse.doneを経由しないため、
                    // 通常の（response.done起点の）開始経路では最初のターンだけ
                    // 開始されない。ここで代わりに開始する。ただし再生中に既に
                    // お客様が話し始めていた場合（userVadState==='speech'）は
                    // 明らかに無言ではないため開始しない。
                    if (userVadState !== 'speech') {
                        startSilenceTimerIfNeeded(myGeneration, 'zero_wait_greeting_ended');
                    }
                }
            };
            zeroWaitAudioEl.addEventListener('playing', onPlaying, { once: true });
            zeroWaitAudioEl.addEventListener('ended', onEnded, { once: true });

            const failIfStillUnresolved = (reason) => {
                if (zeroWaitState !== 'playing' && zeroWaitState !== 'ended') {
                    zeroWaitState = 'play_failed';
                    logEvent('[ZeroWait] 再生失敗（Phase3Cの第一声にフォールバックします）: ' + reason);
                    pushTimelineEvent('ZeroWait failed (' + reason + ')');
                    zeroWaitOutcomeResolve('failure');
                }
            };

            try {
                pushTimelineEvent('ZERO_WAIT_AUDIO: play requested');
                const playPromise = zeroWaitAudioEl.play();
                if (playPromise && typeof playPromise.catch === 'function') {
                    playPromise.catch((e) => failIfStillUnresolved(e.message || e.name || String(e)));
                }
            } catch (e) {
                failIfStillUnresolved(e.message || String(e));
                return;
            }
            setTimeout(() => failIfStillUnresolved('再生開始タイムアウト(' + ZERO_WAIT_PLAY_TIMEOUT_MS + 'ms)'), ZERO_WAIT_PLAY_TIMEOUT_MS);
        }

        function waitForZeroWaitOutcome() {
            if (!zeroWaitEnabled) return Promise.resolve('not_applicable');
            return Promise.race([
                zeroWaitOutcomePromise,
                new Promise((resolve) => setTimeout(() => {
                    // 緊急調査PHASE2（重要・二重Greeting修正）: グレース時間
                    // (ZERO_WAIT_DECISION_GRACE_MS)経過時点で、Promiseの解決順序
                    // だけを見て単純に'timeout'扱いにすると、実際にはZero-Wait音声の
                    // 'playing'イベントがこの直後に発火するだけの、ごく僅かな
                    // タイミング差でも「タイムアウト」と誤判定され、Realtime側の
                    // 第一声（同一の自己紹介文言）が追加で送信されてしまう
                    // ＝実機で報告された「スタッフの佐藤です」二重発話の原因と
                    // なり得る。そのため、タイムアウト時点の実際のzeroWaitStateを
                    // 確認し、既に'playing'/'ended'（＝実際には成功している）なら
                    // 'success'として扱う。
                    if (zeroWaitState === 'playing' || zeroWaitState === 'ended') {
                        pushTimelineEvent('ZeroWait grace timeout だがstate=' + zeroWaitState + 'のためsuccess扱い');
                        resolve('success');
                    } else {
                        pushTimelineEvent('ZeroWait grace timeout (state=' + zeroWaitState + ')');
                        resolve('timeout');
                    }
                }, ZERO_WAIT_DECISION_GRACE_MS)),
            ]);
        }

        async function maybeSendInitialGreeting() {
            if (initialGreetingSent) return;
            if (userSpokeBeforeGreeting) {
                // お客様が第一声より先に話し始めたため、こちらからは送らない。
                // 既存のturn_detectionによる自動応答にそのまま任せる
                // （Zero-Wait再生中の割り込みも同じ扱い。section17: 複雑な
                // 独自の打ち切り制御は追加せず、既存の標準動作に委ねる）。
                initialGreetingSent = true;
                greetingSource = 'none';
                pushTimelineEvent('initialGreetingSuppressed(userSpoke)');
                logEvent('第一声: お客様が先に発話したため、自動送信をスキップします');
                return;
            }
            if (!dc || dc.readyState !== 'open') return;
            initialGreetingSent = true;

            const zeroWaitOutcome = await waitForZeroWaitOutcome();
            if (zeroWaitEnabled) {
                logEvent('[ZeroWait] 第一声方式の判定: outcome=' + zeroWaitOutcome + '（再生状態=' + zeroWaitState + '）');
            }

            if (zeroWaitOutcome === 'success') {
                // 二重発話防止（最重要・section12）: Zero-Wait Greetingが実際に
                // 再生開始したことを'playing'イベントで確認済みのため、
                // Phase3Cのresponse.createは絶対に送らない。
                greetingSource = 'zero_wait';
                updateAudioDiagnosticsPanel();
                pushTimelineEvent('GREETING SOURCE=zero_wait (response.createは送信しません)');
                if (zeroWaitGreetingText) {
                    try {
                        // response.createを伴わないconversation.item.createは
                        // それ自体が発話を誘発しない（再度喋らせることにはならない）。
                        // これによりRealtimeの会話履歴に「既にこの第一声を話した」
                        // という文脈だけを与え、お客様の直後の発言を正しく解釈
                        // できるようにする（section19）。
                        // 重要（本番E2Eで判明・修正済み）: role:'assistant'の
                        // message item に対しては、content[0].typeは'text'ではなく
                        // 'output_text'を指定する必要がある。'text'を指定すると
                        // サーバーから invalid_value エラー（"Value must be
                        // 'output_text'"）が返る（user側のinput_text/input_audioと、
                        // assistant側のoutput_text/output_audioが区別されている）。
                        dc.send(JSON.stringify({
                            type: 'conversation.item.create',
                            item: {
                                type: 'message',
                                role: 'assistant',
                                content: [{ type: 'output_text', text: zeroWaitGreetingText }],
                            },
                        }));
                        logEvent('[ZeroWait] Realtime会話履歴へ第一声テキストを通知しました（response.createは送信しません＝二重発話防止）');
                    } catch (e) {
                        logEvent('[ZeroWait] 会話履歴通知に失敗（通話は継続します。二重発話は発生しません）: ' + e.message);
                    }
                } else {
                    logEvent('[ZeroWait] 第一声テキスト未取得のため会話履歴通知は省略します（response.createは送信しません）');
                }
                zeroWaitAwaitingFirstPostGreetingLatency = true;
                return;
            }

            // Zero-Wait対象外 / 失敗 / タイムアウト: Phase3Cの第一声にフォールバック。
            greetingSource = zeroWaitEnabled ? 'realtime_fallback' : 'realtime_initial';
            updateAudioDiagnosticsPanel();
            pushTimelineEvent('GREETING SOURCE=' + greetingSource + ' / initialGreetingRequested(zeroWaitOutcome=' + zeroWaitOutcome + ')');
            greetingTiming.greetingResponseCreateSent = performance.now();
            logEvent('第一声用response.createを送信します（1通話につき1回のみ'
                + (zeroWaitEnabled ? '、Zero-Waitフォールバック経路' : '') + '）');
            try {
                // 重要（本番E2Eで判明・修正済み）: OpenAI Realtime APIの仕様上、
                // response.create の response.instructions を指定すると、その
                // レスポンス1回に限りセッション全体のinstructionsが完全に
                // 上書きされてしまう（「補足」ではなく「置き換え」になる）。
                // そのため、ここでresponse.instructionsを付与すると、AIスタッフ
                // 設定のgreeting/人格/Phase1言語ルールがすべて無視された状態で
                // 第一声が生成されてしまうことが確認された（Test F）。
                // 対策として、第一声用response.createにはinstructionsを一切
                // 付与せず、既存のセッションinstructions（_build_greeting_section
                // により「通話開始時に自分から話し始める」旨が常時含まれる。
                // realtime_voice_ai.pyのbuild_realtime_instructions参照）に
                // そのまま従わせる。
                sendResponseCreate(zeroWaitEnabled ? 'initial_greeting_fallback' : 'initial_greeting');
            } catch (e) {
                // 第一声の送信に失敗しても通話全体は継続する。お客様が話しかければ
                // 通常のturn_detectionによる応答フローに問題なく移行できる。
                logEvent('第一声用response.create送信に失敗しました（通話は継続します）: ' + e.message);
            }
            logGreetingLatenciesIfReady();
        }

        async function fetchSession() {
            const res = await fetch('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/session', {
                method: 'POST',
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                const err = new Error(data.detail || ('セッション発行エラー: ' + res.status));
                // Human Handoff基盤: AI電話受付がOFFの店舗では、通常の接続失敗
                // （ネットワーク不調・サーバーエラー等）とは異なるメッセージを
                // 表示するため、reason_codeで区別できるようにする。
                if (data.reason_code === 'ai_phone_reception_disabled') {
                    err.aiReceptionDisabled = true;
                }
                throw err;
            }
            return data;
        }

        // ===== Phase2.6: usage観測（一時計測用。DB保存なし、会話内容・音声は扱わない） =====
        // response.doneイベントのresponse.usageだけを抜き出し、通話中はメモリ上に
        // 貯めて合計を画面表示・console出力する。ページを閉じる/リロードすると消える。
        let callStartedAt = null;
        let callEndedAt = null;
        let elapsedTimerId = null;
        const usageResponses = [];

        function resetUsageObservation() {
            usageResponses.length = 0;
            if (usageLogEl) usageLogEl.textContent = '';
            renderUsageSummary();
        }

        function renderUsageSummary() {
            const sums = usageResponses.reduce((acc, e) => {
                acc.count += 1;
                acc.inputAudio += e.input_audio_tokens;
                acc.inputText += e.input_text_tokens;
                acc.cached += e.input_cached_tokens;
                acc.outputAudio += e.output_audio_tokens;
                acc.outputText += e.output_text_tokens;
                acc.total += e.total_tokens;
                return acc;
            }, { count: 0, inputAudio: 0, inputText: 0, cached: 0, outputAudio: 0, outputText: 0, total: 0 });
            usageResponseCountEl.textContent = String(sums.count);
            usageInputAudioEl.textContent = String(sums.inputAudio);
            usageInputTextEl.textContent = String(sums.inputText);
            usageCachedEl.textContent = String(sums.cached);
            usageOutputAudioEl.textContent = String(sums.outputAudio);
            usageOutputTextEl.textContent = String(sums.outputText);
            usageTotalTokensEl.textContent = String(sums.total);
            return sums;
        }

        function updateElapsedDisplay() {
            if (!callStartedAt) { usageElapsedEl.textContent = '—'; return; }
            const end = callEndedAt || Date.now();
            usageElapsedEl.textContent = Math.round((end - callStartedAt) / 1000) + '秒';
        }

        function recordUsageEvent(response) {
            if (!response) return;
            const usage = response.usage || {};
            const inTD = usage.input_token_details || {};
            const outTD = usage.output_token_details || {};
            const entry = {
                response_id: response.id || null,
                status: response.status || null,
                input_tokens: usage.input_tokens || 0,
                output_tokens: usage.output_tokens || 0,
                total_tokens: usage.total_tokens || 0,
                input_text_tokens: inTD.text_tokens || 0,
                input_audio_tokens: inTD.audio_tokens || 0,
                input_cached_tokens: inTD.cached_tokens || 0,
                output_text_tokens: outTD.text_tokens || 0,
                output_audio_tokens: outTD.audio_tokens || 0,
                output_reasoning_tokens: (outTD.reasoning_tokens !== undefined ? outTD.reasoning_tokens : null),
                timestamp: new Date().toISOString()
            };
            usageResponses.push(entry);
            // 会話内容・音声・個人情報は一切含めない、数値とresponse.idのみ。
            console.log('[Phase2.6 usage]', entry);
            renderUsageSummary();
            if (usageLogEl) {
                const idTail = (entry.response_id || '').slice(-8);
                usageLogEl.textContent += `[${entry.timestamp.split('T')[1].replace('Z', '')}] `
                    + `id=…${idTail} status=${entry.status} `
                    + `in=${entry.input_tokens}(txt${entry.input_text_tokens}/aud${entry.input_audio_tokens}/cache${entry.input_cached_tokens}) `
                    + `out=${entry.output_tokens}(txt${entry.output_text_tokens}/aud${entry.output_audio_tokens}`
                    + (entry.output_reasoning_tokens !== null ? `/reason${entry.output_reasoning_tokens}` : '')
                    + `) total=${entry.total_tokens}\n`;
                usageLogEl.scrollTop = usageLogEl.scrollHeight;
            }
        }

        function logFinalUsageSummary() {
            if (!callStartedAt) return;
            const durationSeconds = Math.round(((callEndedAt || Date.now()) - callStartedAt) / 1000);
            const sums = renderUsageSummary();
            const summary = {
                duration_seconds: durationSeconds,
                response_count: sums.count,
                input_audio_tokens_total: sums.inputAudio,
                input_text_tokens_total: sums.inputText,
                cached_tokens_total: sums.cached,
                output_audio_tokens_total: sums.outputAudio,
                output_text_tokens_total: sums.outputText,
                total_tokens_total: sums.total,
                responses: usageResponses
            };
            console.log('[Phase2.6 usage summary]', summary);
            logEvent('通話終了サマリー: duration_seconds=' + durationSeconds
                + ', responses=' + sums.count + ', total_tokens=' + sums.total);
        }

        // ===== Realtime Tool Calling（Phase3A: check_availability / Phase3B: create_reservation） =====
        //
        // 設計方針（重要）:
        // - RECEPTRAのサーバーはRealtimeのDataChannelを一切見ていない
        //   （ブラウザ⇔OpenAI直結のため）。したがってfunction_callの往復
        //   （OpenAIからの呼び出し受信→FastAPIへの照会→結果を送り返す）は、
        //   このページ（ブラウザ側JavaScript）が仲介するしかない。
        // - shop_idはToolの引数に含めない。常にこのページのshopId
        //   （通話中の店舗）を使う。AIが引数として別のshop_idを送ってきても
        //   無視する（そもそもTool定義にshop_idという引数自体が存在しない）。
        // - Tool結果はFastAPI（Reservation DB / ShopHours / ShopClosure）を
        //   唯一の正とする。fetch失敗・タイムアウト・不正レスポンス等、
        //   どんな失敗であってもavailable:trueを合成してはならない。必ず
        //   available:falseを返し、AIに「確認できなかった」と案内させる
        //   （満席とは言わせない）。reason_codeは、FastAPI側のバリデーション
        //   エラー(HTTP 422、引数自体が不正)はinvalid_requestに、それ以外
        //   （レート制限429・ネットワーク断・5xx等、入力は正しいが確認
        //   できない場合）はtemporarily_unavailableに分けて返す
        //   （ユーザー指摘によりPhase3A完了後に修正）。
        // - 同一call_idの二重処理を防止する（Phase3Aは読み取りのみなので
        //   実害は無いが、Phase3Bのcreate_reservationに備えて今のうちに
        //   仕組みを入れておく）。
        //
        // イベント構造は実際のgpt-realtime-2.1との通信で実測確認済み
        // （response.output_item.done の item.type==="function_call" が
        // id/call_id/name/arguments/statusを持つ。SDKの型定義には無い
        // "phase"フィールドが実際には存在する等、細部は実測を優先している）。
        const processedToolCallIds = new Set();

        function resetToolCallState() {
            processedToolCallIds.clear();
        }

        // ===== Fix A: Tool fetchの無限ハング対策 =====
        //
        // 設計方針（重要）:
        // - 調査で判明した問題: 7つのTool呼び出し用fetch()のいずれにもタイムアウトが
        //   一切無く、Railwayの一時的な遅延やネットワークの瞬断でfetchが応答しない
        //   まま止まると、function_call_output自体が送られず、AIが「Tool結果待ち」
        //   のまま無言で停止し続ける経路が存在した。
        // - この関数は既存のfetch呼び出しをAbortControllerでラップするだけで、
        //   URL・method・headers・body等のリクエスト内容は一切変更しない。
        //   タイムアウト時はfetchが投げる例外(DOMException name="AbortError")を
        //   そのまま呼び出し元へ伝播させ、各Tool関数の既存のcatch(e)ブロック
        //   （ネットワーク断時と同じ安全側フォールバック: available:false /
        //   success:false + reason_code:"temporarily_unavailable"等）へ合流させる。
        //   新しい失敗種別・新しいreason_code・instructions変更は一切行わない。
        // - タイムアウト秒数は、通常のDBクエリ応答（数十〜数百ms）に対して
        //   十分な余裕を持たせつつ、無言状態が長引きすぎないよう8秒とした。
        const TOOL_FETCH_TIMEOUT_MS = 8000;

        async function fetchToolWithTimeout(url, options) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), TOOL_FETCH_TIMEOUT_MS);
            try {
                const res = await fetch(url, Object.assign({}, options, { signal: controller.signal }));
                // FAST TURN 3.4（iPhone vs Mac 実機差診断・観測専用。fetch自体の
                // 呼び出し方・戻り値・タイムアウト挙動は一切変更しない）:
                // 既存のturnLatencyToolDurationMs（Tool呼び出し全体の所要時間）
                // だけでは、「ネットワーク経路（DNS/TCP/TLS/サーバー処理待ち）が
                // 遅いのか」「レスポンス受信後のJSON処理等が遅いのか」を切り分け
                // られない。debugMode時のみ、ブラウザが既に記録している
                // Resource Timing API（このfetchが完了した後に読み取るだけで、
                // 新しい計測・追加のリクエストは一切発生しない）から、この
                // fetch自体のDNS/TCP/TLS/TTFB(サーバー処理待ち)/ダウンロードの
                // 内訳を1回だけ記録する。取得できない場合
                // （ブラウザ非対応・エントリ未検出・バッファ超過等）は
                // 何もせず黙ってスキップする（fail-open。既存の計測・動作へは
                // 一切影響させない）。
                if (debugMode) {
                    try {
                        const entries = (performance.getEntriesByType && performance.getEntriesByType('resource')) || [];
                        const matches = entries.filter((e) => typeof e.name === 'string' && e.name.indexOf(url) !== -1);
                        const entry = matches.length ? matches[matches.length - 1] : null;
                        if (entry) {
                            const dns = Math.round(entry.domainLookupEnd - entry.domainLookupStart);
                            const tcp = Math.round(entry.connectEnd - entry.connectStart);
                            const tls = (entry.secureConnectionStart > 0)
                                ? Math.round(entry.connectEnd - entry.secureConnectionStart) : 0;
                            const ttfb = Math.round(entry.responseStart - entry.requestStart);
                            const download = Math.round(entry.responseEnd - entry.responseStart);
                            pushTimelineEvent('TOOL_FETCH_TIMING dns=' + dns + 'ms tcp=' + tcp + 'ms tls=' + tls
                                + 'ms ttfb=' + ttfb + 'ms download=' + download + 'ms');
                        }
                    } catch (timingErr) {
                        // 観測専用のため、取得に失敗しても既存の計測・動作には
                        // 一切影響させない（意図的に握りつぶす）。
                    }
                }
                return res;
            } finally {
                clearTimeout(timeoutId);
            }
        }

        // Tool呼び出し失敗ログに、通常のネットワーク断とタイムアウトを区別する
        // 一言を添えるためだけのヘルパー（PIIは一切含まない。reason_code等
        // AIへ渡す内容には影響しない。ログ表示のみの変更）。
        // PHASE14: 副作用として、直近のTool fetch失敗種別をlastToolFetchOutcome
        // （'timeout' | 'network_error'）へローカル表示専用で記録する。戻り値・
        // AIへ送信する内容には一切影響しない。
        function describeToolFetchError(e) {
            const timedOut = !!(e && e.name === 'AbortError');
            lastToolFetchOutcome = timedOut ? 'timeout' : 'network_error';
            return (timedOut ? ('（' + (TOOL_FETCH_TIMEOUT_MS / 1000) + '秒でタイムアウト）: ') : ': ') + (e && e.message);
        }

        // PHASE14: Tool結果オブジェクトの形から「失敗扱いかどうか」を大まかに
        // 判定する（PIIなし。内容そのものは見ず、決められたキー・値のみを見る）。
        // 表示用の分類にのみ使い、AIへ送る出力そのものやRealtime APIへの
        // 応答フローには一切影響しない。
        function isToolOutputFailure(output) {
            if (!output || typeof output !== 'object') return false;
            if (output.success === false) return true;
            if (output.available === false && (output.reason_code === 'temporarily_unavailable' || output.reason_code === 'invalid_request')) return true;
            if (output.status === 'error' || output.status === 'timeout' || output.status === 'not_found') return true;
            return false;
        }

        async function callCheckAvailabilityTool(args) {
            const body = {
                date: args && args.date,
                time: args && args.time,
                party_size: args && args.party_size,
            };
            if (args && args.service_id) body.service_id = args.service_id;
            // Phase R5 Part B: staff_id（内部ID）はTool定義から意図的に除外済み
            // （AIはstaff_idを一切知らない）。名指しはstaff_name（自然文の氏名）
            // 経由でのみ転送する。万一argsに古いstaff_idが含まれていても、
            // ここで一切転送しない（AIによるID発明を転送層でも二重に防ぐ）。
            if (args && args.staff_name) body.staff_name = args.staff_name;
            // Generic Resource Foundation Phase R4: 省略可能。AIが明示的に
            // 指定した場合のみ転送する（他のtoolと全く同じ「明示指定のみ
            // 転送」パターン）。
            if (args && args.resource_type) body.resource_type = args.resource_type;

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/check-availability', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.available !== 'boolean') {
                    // FastAPI側は原則常に200+安全なavailable:falseを返す設計だが、
                    // ここに来るのは主に以下のいずれか:
                    // - HTTP 422: FastAPI自身のリクエストバリデーションエラー
                    //   （party_sizeの型/範囲など、引数自体が不正）→ invalid_request
                    // - それ以外（429レート制限・5xx・ネットワーク断でJSON自体が
                    //   取得できない等）→ temporarily_unavailable
                    return {
                        available: false,
                        date: body.date || null,
                        time: body.time || null,
                        party_size: body.party_size || null,
                        reason_code: (res.status === 422) ? 'invalid_request' : 'temporarily_unavailable',
                    };
                }
                return data;
            } catch (e) {
                // fetch自体が例外を投げるのはネットワーク断等のみのため、
                // 入力の誤りではなくtemporarily_unavailable。
                logEvent('check_availability Tool呼び出しに失敗' + describeToolFetchError(e));
                return {
                    available: false,
                    date: body.date || null,
                    time: body.time || null,
                    party_size: body.party_size || null,
                    reason_code: 'temporarily_unavailable',
                };
            }
        }

        // ===== Phase3B: create_reservation =====
        //
        // 設計方針（重要）:
        // - shop_idはcheck_availabilityと同じくAIの引数に含めない。加えて、
        //   call_idもAIの出力JSONには一切含まれない（Toolのparametersに
        //   call_idという項目自体が存在しない）。ここでhandleFunctionCallItemが
        //   OpenAI Realtimeのfunction_callイベント本体(item.call_id)から
        //   直接読み取った値を、リクエストボディにcall_idとして追加で載せて
        //   FastAPI側へ転送する。AI自身にidempotency_keyやcall_idを
        //   生成させることは絶対にしない。
        // - FastAPI側がこのcall_idを "realtime_voice:{shop_id}:{call_id}" に
        //   namespace化し、DBの一意インデックスで同一call_idの二重予約作成を
        //   最終的に防止する（call_idの再送防止＝ここでのprocessedToolCallIds
        //   と、DB一意制約による冪等性保証は別の防御層であり、片方だけに
        //   依存しない設計）。
        // - guest_email・coupon_codeはPhase3Bのスコープ外のため送らない。
        async function callCreateReservationTool(args, callId) {
            const body = {
                date: args && args.date,
                time: args && args.time,
                party_size: args && args.party_size,
                guest_name: args && args.guest_name,
                guest_phone: args && args.guest_phone,
                call_id: callId,
                // Phase 5A: AIの引数には含まれない。この通話中にset_conversation_language
                // で記録された言語をCustomer Memoryのソフトなヒントとして残すためだけに
                // 使う（このページのJS変数からのみ付与する。他のToolと同じパターン）。
                session_id: voiceSessionId,
            };
            if (args && args.service_id) body.service_id = args.service_id;
            // Phase R5 Part B: check_availabilityと同じ理由でstaff_idは転送しない。
            if (args && args.staff_name) body.staff_name = args.staff_name;
            if (args && args.special_requests) body.special_requests = args.special_requests;
            // Generic Resource Foundation Phase R4: check_availabilityと同じ
            // 「明示指定のみ転送」パターン。
            if (args && args.resource_type) body.resource_type = args.resource_type;

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/create-reservation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    // check_availabilityと同じ方針: HTTP 422（FastAPI自身のリクエスト
                    // バリデーションエラー、引数自体が不正）はinvalid_request、
                    // それ以外（429レート制限・5xx・ネットワーク断でJSON自体が
                    // 取得できない等）はtemporarily_unavailableに分ける。
                    return {
                        success: false,
                        reason_code: (res.status === 422) ? 'invalid_request' : 'temporarily_unavailable',
                    };
                }
                return data;
            } catch (e) {
                // fetch自体が例外を投げるのはネットワーク断等のみのため、
                // 入力の誤りではなくtemporarily_unavailable。
                logEvent('create_reservation Tool呼び出しに失敗' + describeToolFetchError(e));
                return { success: false, reason_code: 'temporarily_unavailable' };
            }
        }

        // ===== Phase3D: get_shop_info =====
        //
        // 設計方針（重要）:
        // - check_availability/create_reservationと同じく、shop_idはAIの引数に
        //   含めない。常にこのページのshopId（通話中の店舗）を使う。
        // - Tool結果はFastAPI（ShopKnowledge/ShopFAQ DB）を唯一の正とする。
        //   fetch失敗・タイムアウト・不正レスポンス等、どんな失敗であっても
        //   known:trueやdataを合成してはならない。必ずsuccess:falseで返し、
        //   AIに「確認できなかった」と案内させる（情報の有無を勝手に断定させない）。
        async function callGetShopInfoTool(args) {
            const body = {
                topic: args && args.topic,
            };
            if (args && args.query) body.query = args.query;

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/get-shop-info', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    // check_availability/create_reservationと同じ方針: HTTP 422
                    // （FastAPI自身のリクエストバリデーションエラー、topic等の
                    // 引数自体が不正）はinvalid_request、それ以外（429レート制限・
                    // 5xx・ネットワーク断でJSON自体が取得できない等）は
                    // temporarily_unavailableに分ける。
                    return {
                        success: false,
                        reason_code: (res.status === 422) ? 'invalid_request' : 'temporarily_unavailable',
                    };
                }
                return data;
            } catch (e) {
                // fetch自体が例外を投げるのはネットワーク断等のみのため、
                // 入力の誤りではなくtemporarily_unavailable。
                logEvent('get_shop_info Tool呼び出しに失敗' + describeToolFetchError(e));
                return { success: false, reason_code: 'temporarily_unavailable' };
            }
        }

        // ===== Outbound AI Phase 4A: find_customer =====
        //
        // 設計方針（重要）:
        // - check_availability/create_reservation/get_shop_infoと同じく、shop_idは
        //   AIの引数に含めない。常にこのページのshopId（通話中の店舗）を使う。
        // - Tool結果はFastAPI（Customer Memory DB）を唯一の正とする。fetch失敗・
        //   タイムアウト・不正レスポンス等、どんな失敗であってもcandidate_foundを
        //   合成してはならない。必ずnot_found相当（status:'not_found'）で返し、
        //   AIには「初めてのお客様として通常どおり受付」を続けさせる
        //   （存在しない候補をでっち上げるより、常連認識を1回見逃す方が安全）。
        async function callFindCustomerTool(args) {
            const body = {
                phone: args && args.phone,
                // Outbound AI Phase 4B: AIの引数には含まれない。このページの
                // JS変数からのみ付与する（この通話を識別するopaqueな値）。
                session_id: voiceSessionId,
            };

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/find-customer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    // 他のToolと同じ方針: HTTP 422（引数自体が不正）はinvalid_request、
                    // それ以外（429レート制限・5xx・ネットワーク断）はtemporarily_unavailable。
                    currentCandidateRef = null;
                    return {
                        success: false,
                        status: 'not_found',
                        reason_code: (res.status === 422) ? 'invalid_request' : 'temporarily_unavailable',
                    };
                }
                // Outbound AI Phase 4B（重要・Privacy Gate）: candidate_referenceは
                // confirm_customer_identity呼び出し時にこのページが自動転送する
                // ためだけのopaqueな値であり、AIへ渡すfunction_call_output
                // （Realtimeモデルが実際に読む内容）には絶対に含めない。
                // ここでJS変数へ保持した上で、モデルへ返すオブジェクトからは
                // 取り除く（AIはこの値の存在自体を一切知らない）。
                currentCandidateRef = data.candidate_reference || null;
                const modelVisible = Object.assign({}, data);
                delete modelVisible.candidate_reference;
                return modelVisible;
            } catch (e) {
                // fetch自体が例外を投げるのはネットワーク断等のみのため、
                // 入力の誤りではなくtemporarily_unavailable。
                logEvent('find_customer Tool呼び出しに失敗' + describeToolFetchError(e));
                currentCandidateRef = null;
                return { success: false, status: 'not_found', reason_code: 'temporarily_unavailable' };
            }
        }

        // ===== Outbound AI Phase 4B: confirm_customer_identity =====
        //
        // 設計方針（重要）:
        // - session_id・candidate_referenceはAIの引数(args)には一切含まれない。
        //   常にこのページのJS変数（voiceSessionId / currentCandidateRef）から
        //   付与する。AIが渡せるのはargs.confirmed（お客様が肯定したかどうかの
        //   意味判断）のみであり、「どのcandidateを確認するか」自体をAIが
        //   選ぶ余地は無い。
        // - Tool結果はFastAPI（app.services.customer_context）を唯一の正とする。
        //   fetch失敗・タイムアウト・不正レスポンス等、どんな失敗であっても
        //   verifiedを合成してはならない。必ずno_pending_candidate相当で返す。
        async function callConfirmCustomerIdentityTool(args) {
            const body = {
                confirmed: !!(args && args.confirmed),
                session_id: voiceSessionId,
                candidate_reference: currentCandidateRef,
            };

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/confirm-customer-identity', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    return {
                        success: false,
                        status: 'no_pending_candidate',
                    };
                }
                return data;
            } catch (e) {
                logEvent('confirm_customer_identity Tool呼び出しに失敗' + describeToolFetchError(e));
                return { success: false, status: 'no_pending_candidate' };
            }
        }

        // ===== Outbound AI Phase 4B: get_customer_context =====
        //
        // 設計方針（重要）:
        // - AIからの引数は無い（Tool定義のparametersが空のobject）。session_idの
        //   みこのページのJS変数から付与する。
        // - Tool結果はFastAPI（本人確認状態＋Reservation DB）を唯一の正とする。
        //   fetch失敗・タイムアウト・不正レスポンス等、どんな失敗であっても
        //   context_availableを合成してはならない。必ずnot_verified相当で返す。
        async function callGetCustomerContextTool(args) {
            const body = {
                session_id: voiceSessionId,
            };

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/get-customer-context', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    return { success: false, status: 'not_verified' };
                }
                return data;
            } catch (e) {
                logEvent('get_customer_context Tool呼び出しに失敗' + describeToolFetchError(e));
                return { success: false, status: 'not_verified' };
            }
        }

        // ===== Phase 5A: set_conversation_language =====
        //
        // 設計方針（重要）:
        // - AIが渡せる引数はargs.language_codeのみ。shop_idはこのページの
        //   shopId（URLパス由来）、session_idはこのページのJS変数から付与する
        //   （他のToolと同じパターン。AIはどの店舗のどの通話かを指定できない）。
        // - Tool結果はFastAPI（app.services.conversation_language）を唯一の正とする。
        //   fetch失敗・タイムアウト・不正レスポンス等、どんな失敗であっても
        //   acceptedを合成してはならない。必ずnot_allowed相当で返す。
        async function callSetConversationLanguageTool(args) {
            const body = {
                language_code: args && args.language_code,
                session_id: voiceSessionId,
            };

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/set-conversation-language', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    return { success: false, status: 'not_allowed' };
                }
                return data;
            } catch (e) {
                logEvent('set_conversation_language Tool呼び出しに失敗' + describeToolFetchError(e));
                return { success: false, status: 'not_allowed' };
            }
        }

        // ===== Human Handoff基盤: request_callback =====
        //
        // 重要（バグ修正）: このToolはapp.services.realtime_voice_ai側の
        // _REALTIME_TOOLSに8番目のToolとして追加済みだったが、このページの
        // Tool呼び出し関数・handleFunctionCallItemのディスパッチ分岐が
        // 追加当時に更新されておらず、実際には一度も呼ばれていなかった
        // （handleFunctionCallItemのelse分岐＝「未知のTool」として処理され、
        // POST /tools/request-callbackへ到達せず、DB保存も行われないまま
        // {available:false, success:false, status:'not_found', ...}という
        // このToolの本来のレスポンス形（{success, reason_code}）とは異なる
        // 形の function_call_output が返っていた）。Realtimeモデルが期待する
        // 形と異なる結果を受け取ったことが、実際の音声テストで観測された
        // 「電話番号確認後にAIが無言になる」現象の原因である可能性が高い。
        //
        // 設計方針（create_reservation/set_conversation_languageと同じパターン）:
        // - shop_idはAIの引数に含めない（URLパス由来）。
        // - call_idはAIの引数ではなく、handleFunctionCallItemがOpenAI Realtimeの
        //   function_callイベント本体(item.call_id)から読み取った値をそのまま
        //   転送する（AI自身にcall_idを生成させない。create_reservationと同じ）。
        // - session_id（voiceSessionId）もこのページのJS変数からのみ付与する
        //   （他のToolと同じパターン。AIはどの通話かを指定できない）。
        // - fetch失敗・タイムアウト・不正レスポンス等、どんな失敗であっても
        //   「担当者から折り返します」を合成してはならない。必ず
        //   success:false + reason_code（invalid_request/temporarily_unavailable）
        //   のみを返す（このToolのレスポンス形はsuccess/reason_codeの2フィールドのみ。
        //   他のToolのavailable/status等のフィールドを混ぜない）。
        async function callRequestCallbackTool(args, callId) {
            const body = {
                customer_name: args && args.customer_name,
                customer_phone: args && args.customer_phone,
                inquiry_text: args && args.inquiry_text,
                call_id: callId,
                session_id: voiceSessionId,
            };
            if (args && args.desired_date) body.desired_date = args.desired_date;
            if (args && args.desired_time) body.desired_time = args.desired_time;
            if (args && args.party_size) body.party_size = args.party_size;
            if (args && args.service_id) body.service_id = args.service_id;
            if (args && args.reason_code) body.reason_code = args.reason_code;

            try {
                const res = await fetchToolWithTimeout('/api/v1/shops/' + encodeURIComponent(shopId) + '/realtime-voice/tools/request-callback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => null);
                if (!res.ok || !data || typeof data.success !== 'boolean') {
                    // check_availability/create_reservationと同じ方針: HTTP 422
                    // （FastAPI自身のリクエストバリデーションエラー）はinvalid_request、
                    // それ以外（429レート制限・5xx・不正レスポンス等）はtemporarily_unavailable。
                    return {
                        success: false,
                        reason_code: (res.status === 422) ? 'invalid_request' : 'temporarily_unavailable',
                    };
                }
                return data;
            } catch (e) {
                // fetch自体が例外を投げるのはネットワーク断・タイムアウト等のみのため、
                // 入力の誤りではなくtemporarily_unavailable。
                logEvent('request_callback Tool呼び出しに失敗' + describeToolFetchError(e));
                return { success: false, reason_code: 'temporarily_unavailable' };
            }
        }

        async function handleFunctionCallItem(item) {
            const callId = item.call_id;
            if (!callId) return;
            if (processedToolCallIds.has(callId)) {
                logEvent('call_id ' + callId + ' は処理済みのためスキップ（二重実行防止）');
                return;
            }
            processedToolCallIds.add(callId);

            // FAST TURN 3.6B（Tool Continuation Proof）: このTool呼び出し1件分の
            // T0〜T10相関トレースを開始する。call_id全体ではなく末尾8文字のみを
            // 表示・記録に使う（call_id自体はUUID相当でPIIではないが、表示は
            // 最小限に留める）。1呼び出しにつき1トレース（既存のlastToolLabel等と
            // 同じ単純なnull/上書き管理）。
            toolContinuationTraceCallId = callId;
            toolContinuationTraceShortId = String(callId).slice(-8);
            toolContinuationTraceT0 = performance.now();
            toolContinuationTraceActive = true;
            // PHASE O5.6診断: CALL_END_DIAGが必要とする「直近のTool呼び出し名・
            // 経過ms」だけを保持する（既存のtoolContinuationTrace*系とは別の
            // 単純な変数。読み取り専用・挙動には無関係）。
            lastFunctionCallNameForDiag = item.name || null;
            lastFunctionCallStartedAtForDiag = toolContinuationTraceT0;
            pushToolContinuationTrace('T0_FUNCTION_CALL_RECEIVED (tool=' + (item.name || '(不明)') + ')');

            // PHASE14/PHASE8（会話停止調査）: Tool Timeline。Tool名+状態のみを
            // 記録し、引数・結果内容（電話番号・氏名・予約内容等）は一切
            // 含めない（PII厳禁）。TOOL_CALL_RECEIVED/TOOL_FETCH_STARTED/
            // TOOL_FETCH_SUCCESS(ERROR)/TOOL_OUTPUT_SENT/TOOL_CONTINUATION_REQUESTED
            // という統一名で記録し、実機テストでcheck_availability等の
            // Tool Calling continuationの各段階が実際に到達しているかを
            // 確定できるようにする（挙動そのものは一切変更しない）。
            lastToolLabel = (item.name || '(不明)') + ': requested';
            updateAudioDiagnosticsPanel();
            pushTimelineEvent('TOOL_CALL_RECEIVED (tool=' + (item.name || '(不明)') + ')');

            let args = null;
            try {
                args = JSON.parse(item.arguments || '{}');
            } catch (e) {
                args = null;
            }

            // PHASE14: fetch開始前にリセットしておき、このTool呼び出し自身の
            // 失敗種別（timeout/network_error）だけを正しく拾えるようにする。
            lastToolFetchOutcome = null;
            lastToolLabel = (item.name || '(不明)') + ': fetching';
            updateAudioDiagnosticsPanel();
            pushTimelineEvent('TOOL_FETCH_STARTED (tool=' + (item.name || '(不明)') + ')');
            pushToolContinuationTrace('T1_TOOL_FETCH_STARTED (tool=' + (item.name || '(不明)') + ')');
            // FAST TURN 3.1（実機レイテンシ診断・計測のみ）: Tool呼び出し開始
            // 時刻とTool名を記録する。動作（呼び出し自体）は変更しない。
            toolCallStartedAtForLatency = performance.now();
            turnLatencyToolOccurred = true;
            turnLatencyToolName = item.name || '(不明)';

            let output;
            if (item.name === 'check_availability') {
                logEvent('Tool呼び出し受信: check_availability ' + JSON.stringify(args));
                output = await callCheckAvailabilityTool(args || {});
            } else if (item.name === 'create_reservation') {
                logEvent('Tool呼び出し受信: create_reservation ' + JSON.stringify(args));
                output = await callCreateReservationTool(args || {}, callId);
            } else if (item.name === 'get_shop_info') {
                logEvent('Tool呼び出し受信: get_shop_info ' + JSON.stringify(args));
                output = await callGetShopInfoTool(args || {});
            } else if (item.name === 'find_customer') {
                logEvent('Tool呼び出し受信: find_customer ' + JSON.stringify(args));
                output = await callFindCustomerTool(args || {});
            } else if (item.name === 'confirm_customer_identity') {
                logEvent('Tool呼び出し受信: confirm_customer_identity ' + JSON.stringify(args));
                output = await callConfirmCustomerIdentityTool(args || {});
            } else if (item.name === 'get_customer_context') {
                logEvent('Tool呼び出し受信: get_customer_context ' + JSON.stringify(args));
                output = await callGetCustomerContextTool(args || {});
            } else if (item.name === 'set_conversation_language') {
                logEvent('Tool呼び出し受信: set_conversation_language ' + JSON.stringify(args));
                output = await callSetConversationLanguageTool(args || {});
            } else if (item.name === 'request_callback') {
                // バグ修正: これまでこの分岐が存在せず、request_callbackが
                // 常にelse（未知のTool）へ落ちていた（詳細はcallRequestCallbackTool
                // の直前のコメント参照）。
                logEvent('Tool呼び出し受信: request_callback ' + JSON.stringify(args));
                output = await callRequestCallbackTool(args || {}, callId);
            } else {
                // 現在宣言しているToolはcheck_availability / create_reservation /
                // get_shop_info / find_customer / confirm_customer_identity /
                // get_customer_context / set_conversation_language /
                // request_callbackの8つ。
                // 未知の関数名が来た場合も、応答せずに放置するとAIが待ち続けて
                // しまうため、安全側の失敗として返す。
                logEvent('未知のTool呼び出し: ' + item.name);
                output = { available: false, success: false, status: 'not_found', reason_code: 'invalid_request' };
            }

            // PHASE14: Tool結果の状態を分類する（PIIなし・Tool名+状態のみ）。
            // lastToolFetchOutcomeが設定されていればHTTPタイムアウト/ネットワーク
            // エラー、それ以外はisToolOutputFailure()による戻り値の形からの
            // 大まかな成功/エラー判定。
            const toolResultState = lastToolFetchOutcome
                ? lastToolFetchOutcome
                : (isToolOutputFailure(output) ? 'error' : 'success');
            // FAST TURN 3.1（実機レイテンシ診断・計測のみ）: Tool呼び出し所要時間
            // （fetch開始→完了）を確定する。
            if (toolCallStartedAtForLatency !== null) {
                turnLatencyToolDurationMs = Math.round(performance.now() - toolCallStartedAtForLatency);
            }
            lastToolLabel = (item.name || '(不明)') + ': ' + toolResultState;
            updateAudioDiagnosticsPanel();
            pushTimelineEvent((toolResultState === 'success' ? 'TOOL_FETCH_SUCCESS' : 'TOOL_FETCH_ERROR')
                + ' (tool=' + (item.name || '(不明)') + (toolResultState === 'success' ? '' : ', outcome=' + toolResultState) + ')');
            pushToolContinuationTrace('T2_' + (toolResultState === 'success' ? 'TOOL_FETCH_SUCCESS' : 'TOOL_FETCH_ERROR')
                + ' (tool=' + (item.name || '(不明)') + ')');

            logEvent('Tool結果送信: ' + JSON.stringify(output));
            // FAST TURN 3.6A（会話継続停止の修正・最重要）: このdc.send()だけが、
            // ファイル内の他すべてのdc.send()呼び出し（input_audio_buffer.commit系・
            // sendResponseCreate・Zero-Wait会話履歴通知）と異なり、try/catchで
            // 保護されていなかった。DataChannelが何らかの理由で送信不可能な
            // 状態（例: readyStateがopenでない、切断寸前等）だった場合、ここで
            // 例外が投げられると、この関数はこの時点で即座に中断し、以降の
            // TOOL_OUTPUT_SENT記録・sendResponseCreate()呼び出し・
            // TOOL_CONTINUATION_REQUESTED記録が一切実行されないまま終わる
            // （呼び出し元のhandleFunctionCallItem(...).catch(e => logEvent(...))は
            // 例外をログに残すだけで、Realtime側には何も送られないため、
            // check_availability等のTool自体は正常終了しているにもかかわらず、
            // AIが二度と応答を再開できず会話が無音のまま停止する＝実機で確認
            // された症状と一致する）。他のdc.send()と同じtry/catchパターンに
            // 揃え、失敗時は非fatal（通話は継続）としてログに残す。
            let functionCallOutputSendFailed = false;
            pushToolContinuationTrace('T3_FUNCTION_CALL_OUTPUT_SEND_ATTEMPT (tool=' + (item.name || '(不明)') + ')');
            try {
                dc.send(JSON.stringify({
                    type: 'conversation.item.create',
                    item: {
                        type: 'function_call_output',
                        call_id: callId,
                        output: JSON.stringify(output),
                    },
                }));
                lastToolLabel = (item.name || '(不明)') + ': output_sent';
                updateAudioDiagnosticsPanel();
                pushTimelineEvent('TOOL_OUTPUT_SENT (tool=' + (item.name || '(不明)') + ')');
                pushToolContinuationTrace('T4_FUNCTION_CALL_OUTPUT_SENT (tool=' + (item.name || '(不明)') + ')');
            } catch (e) {
                functionCallOutputSendFailed = true;
                lastToolLabel = (item.name || '(不明)') + ': output_send_failed';
                updateAudioDiagnosticsPanel();
                pushTimelineEvent('TOOL_OUTPUT_SEND_FAILED (tool=' + (item.name || '(不明)') + '): ' + (e && e.message));
                logEvent('Tool結果送信(dc.send)に失敗しました（tool=' + (item.name || '(不明)') + '）: ' + (e && e.message));
                // このdc.send自体が例外を投げた時点で、function_call_outputは
                // OpenAI側へ到達し得ない＝Tool継続チェーンはここで確定的に
                // 途切れている。以降のT5〜T10は（sendResponseCreateが安全に
                // no-opするため）到達しないので、トレースをここで終了する
                // （「証明できていないことを証明済みと誤表示しない」ため）。
                pushToolContinuationTrace('T4_FUNCTION_CALL_OUTPUT_SEND_FAILED (tool=' + (item.name || '(不明)') + ')');
                toolContinuationTraceActive = false;
                cancelToolContinuationResponseWatchdog('t4_send_failed');
            }
            // function_call_output自体の送信に失敗した場合でも、sendResponseCreate()
            // 自体は既存どおりdc.readyStateを確認して安全にno-opするため
            // （呼び出しても新たな例外や二重送信のリスクは無い）、ここで
            // return等はせず既存の継続要求フローへそのまま進める。dc自体が
            // 生きていて今回のsendだけがたまたま失敗したケースでは、これにより
            // AIが（Tool結果無しの状態にはなるが）完全に無応答のまま停止する
            // ことだけは避けられる。dc自体が閉じている場合はsendResponseCreate()
            // 側のRESPONSE_CREATE_SKIPPEDが記録され、挙動は変化しない。
            pushToolContinuationTrace('T5_CONTINUATION_RESPONSE_CREATE_ATTEMPT (tool=' + (item.name || '(不明)') + ')');
            const toolContinuationResponseCreateSent = sendResponseCreate('tool_result:' + (item.name || 'unknown') + (functionCallOutputSendFailed ? ':output_send_failed' : ''));
            pushTimelineEvent('TOOL_CONTINUATION_REQUESTED (tool=' + (item.name || '(不明)') + ')');
            // sendResponseCreate()の戻り値（dc.readyState === 'open'だった場合のみ
            // trueで、実際にresponse.createを送信したことを意味する。false＝
            // dc未接続によりRESPONSE_CREATE_SKIPPEDで既にログ済み、送信していない）
            // をそのままT6として記録する。「呼び出したこと」と「実際に送ったこと」
            // を混同しない（STEP: dc.send成功≠OpenAI処理成功、と同様の理由で
            // sendResponseCreate呼び出し≠送信成功も区別する）。
            pushToolContinuationTrace('T6_' + (toolContinuationResponseCreateSent ? 'CONTINUATION_RESPONSE_CREATE_SENT' : 'CONTINUATION_RESPONSE_CREATE_SKIPPED')
                + ' (tool=' + (item.name || '(不明)') + ')');
            if (!toolContinuationResponseCreateSent) {
                // dcが開いていないためresponse.createそのものを送れなかった
                // ケース。以降のT7〜T10は発生し得ないため、ここでトレースを
                // 終了する。
                toolContinuationTraceActive = false;
            } else {
                // FAST TURN HOTFIX 4（今回追加・観測専用）: dc.send()自体は
                // 成功したが、OpenAI側がresponse.createdを一度も返さない
                // ケースがないかを次の実機テストで可視化するため、watchdogを
                // arm する（何も送信しない、記録のみ）。
                armToolContinuationResponseWatchdog(toolContinuationTraceCallId);
            }
            lastToolLabel = (item.name || '(不明)') + ': continuation_requested';
            updateAudioDiagnosticsPanel();
        }

        // 発話前の無言化調査用（PIIなし）: 「今AIの音声出力がアクティブか」を
        // 表すだけのフラグ。output_audio_buffer.started で true、
        // output_audio_buffer.stopped/cleared や response.done で false に戻す
        // （response.doneはあくまで安全網。通常は stopped/cleared が先に来る想定）。
        // これにより、input_audio_buffer.speech_startedが「AIがまだ話している
        // 最中」に発生したかどうか（＝雑音等をユーザー発話と誤検知して
        // 割り込み扱いになっている可能性のシグナル）をログだけで確認できる。
        // 音声内容そのものは一切扱わない、true/falseのフラグのみ。
        let aiAudioOutputActive = false;
        // PHASE5/6（Greeting直後 Connection Failure再調査）: AI音声出力関連の
        // イベント（output_audio_buffer.started/stopped/cleared、response.done）
        // が最後に観測された時刻。END_CALL_REQUESTED/CLEANUP_STARTEDのタイム
        // ライン記録時に「直前のAI音声イベントからの経過時間」を計算し、
        // Greeting終了トリガーによる切断なのか、それ以外（ネットワーク切断等）
        // による切断なのかを区別する材料とする。PIIは含まない。
        let lastAiAudioEventAt = null;

        function handleDataChannelEvent(raw) {
            let msg;
            try {
                msg = JSON.parse(raw);
            } catch (e) {
                return;
            }
            const type = msg.type || '(不明)';
            logEvent(type);

            if (type === 'output_audio_buffer.started' && greetingTiming.firstOutputAudioStarted === null) {
                // Phase3C計測用: session/response種別を問わず、通話中最初に音声出力
                // バッファが開始した時刻（第一声のケースのみB/C計測に使用する）。
                greetingTiming.firstOutputAudioStarted = performance.now();
                logGreetingLatenciesIfReady();
                // PHASE12: Realtime側の音声出力が実際に開始したことの事後観測。
                // Zero-Waitのonplayingと同じ役割（実際にどちらの音声が
                // 鳴ったかの事実記録）。
                audioSource = 'realtime';
            }

            if (type === 'output_audio_buffer.started') {
                aiAudioOutputActive = true;
                lastAiAudioEventAt = performance.now();
                // AI SPEAKING PROTECTION（今回追加）: 実際にAIの音声が再生
                // され始めた瞬間から、マイクtrackを一時的にミュートする
                // （詳細設計は本ファイル冒頭のengageAiSpeakingProtection定義
                // コメント参照）。
                engageAiSpeakingProtection('output_audio_buffer_started');
                // FAST TURN 3.6A（UI_STATE整合性・UXのみ、latencyへは無影響）:
                // 実際にAIの音声出力が始まった瞬間を「AI_SPEAKING」として
                // 明示する。Tool Callありターンでは、この直前まで
                // 「確認しています」（下のresponse.done分岐を参照）が
                // 表示されているはずで、ここで初めて「応答中」に切り替わる。
                subStatusText.textContent = 'AIスタッフが応答中';
                pushTimelineEvent('AI_AUDIO_STARTED');
                pushToolContinuationTrace('T8_CONTINUATION_AUDIO_FIRST_DELTA');
                // FAST TURN HOTFIX 3（今回追加・観測専用・11markerの必須項目
                // ではない補助marker）: FAST TURN HOTFIX 2で判明済みの
                // OpenAI公式既知バグ（output_audio_buffer.started/stoppedが
                // 6〜10秒遅延、または届かない場合があるCase #09291741）が
                // 「実機での応答が遅い」症状にどの程度寄与しているかを
                // 切り分けるため、このサーバーイベント自体の到達タイミングも
                // 記録する（実際の物理再生開始＝TURN_AUDIO_PLAYBACK_STARTとは
                // 別の値として比較できるようにする）。
                pushTurnLatencyTrace('TURN_OUTPUT_AUDIO_BUFFER_STARTED_DIAG', 'note=server_event_may_be_delayed_per_case_09291741');
                // NAME Forced Commit Observation PoC（PHASE9）
                maybeLogPocReactionElapsed('aiAudioStarted', 'AI_AUDIO_STARTED');
                // Conversation Takeover Observation PoC（NAME PoCとは独立）
                maybeLogTakeoverReactionElapsed('aiAudioStarted', 'AI_AUDIO_STARTED');
                // Short Choice 3-Second Turn（今回追加。全店舗適用・独立）
                maybeLogShortAnswerReactionElapsed('aiAudioStarted', 'AI_AUDIO_STARTED');
                // PHONE Forced Commit（今回追加。全店舗適用・独立）
                maybeLogPhoneReactionElapsed('aiAudioStarted', 'AI_AUDIO_STARTED');
                // SHORT_ANSWER Forced Commit（今回追加。全店舗適用・独立）
                maybeLogQuickAnswerReactionElapsed('aiAudioStarted', 'AI_AUDIO_STARTED');
            } else if (type === 'output_audio_buffer.stopped') {
                aiAudioOutputActive = false;
                lastAiAudioEventAt = performance.now();
                pushTimelineEvent('AI_AUDIO_STOPPED');
                // AI SPEAKING PROTECTION（今回追加）: AIの音声再生が正常に
                // 終了したため、マイクのミュートを解除する（USER LISTENING
                // 状態へ復帰）。
                releaseAiSpeakingProtection('output_audio_buffer_stopped');
                // Silence Timeout: 終話案内アナウンスの再生完了を検知する主経路
                // （通常はresponse.doneより先にこちらが来る）。
                maybeHangUpAfterSilenceGoodbye(callGeneration, 'ai_audio_stopped');
            } else if (type === 'output_audio_buffer.cleared') {
                lastAiAudioEventAt = performance.now();
                // 無言化調査用: サーバー側がAIの音声出力バッファを破棄した
                // タイミング。多くの場合、直前のspeech_started（＝ユーザー発話 or
                // 誤検知）による割り込みの結果として発生する。この時点では
                // 「なぜclearedになったか」は断定せず、事実（イベント名と直前の
                // aiAudioOutputActive状態）のみを記録する。
                logEvent('[無言化調査] output_audio_buffer.cleared を検知（直前のaiAudioOutputActive='
                    + aiAudioOutputActive + '）。AIの音声出力が中断されました。');
                // PHASE6（INTERRUPTION相関）: AI音声出力中に発生したclearedは、
                // 直前のspeech_started（バージイン候補）と組み合わせて
                // lastInterruptionInfoへ1行で要約する。ここではあくまで事実の
                // 組み合わせを記録するだけで、「これが原因」と断定はしない
                // （実機ログを見て人間が判断するための材料）。
                if (aiAudioOutputActive) {
                    const sinceSpeechMs = (lastBargeInSpeechStartedAt !== null)
                        ? Math.round(performance.now() - lastBargeInSpeechStartedAt) : null;
                    lastInterruptionInfo = 'output_audio_buffer.cleared (AI発話中'
                        + (sinceSpeechMs !== null ? '、直近speech_startedから' + sinceSpeechMs + 'ms' : '') + ')';
                    updateAudioDiagnosticsPanel();
                }
                pushTimelineEvent('AI_AUDIO_CLEARED (直前aiAudioOutputActive=' + aiAudioOutputActive + ')');
                aiAudioOutputActive = false;
                // AI SPEAKING PROTECTION（今回追加・安全網）: 何らかの理由で
                // AIの音声出力バッファがクリアされた場合も、マイクのミュートを
                // 解除する（お客様が話しかけられない状態のまま残ることを防ぐ）。
                releaseAiSpeakingProtection('output_audio_buffer_cleared');
            } else if (type === 'conversation.item.truncated') {
                // 無言化調査用: AIの発話アイテムが（割り込み等により）途中で
                // 打ち切られたことを示すイベント。output_audio_buffer.clearedと
                // ほぼ同時に発生することが多い。原因の断定はせず事実のみ記録。
                logEvent('[無言化調査] conversation.item.truncated を検知（直前のaiAudioOutputActive='
                    + aiAudioOutputActive + '）。AIの発話アイテムが途中で打ち切られました。');
                if (aiAudioOutputActive) {
                    lastInterruptionInfo = 'conversation.item.truncated (AI発話中)';
                    updateAudioDiagnosticsPanel();
                }
                pushTimelineEvent('conversation.item.truncated (直前aiAudioOutputActive=' + aiAudioOutputActive + ')');
            }

            // FAST TURN HOTFIX 3（今回追加・観測専用）: PHASE1監査で判明した
            // 「専用ハンドラが存在しない」2イベント（response.output_item.added、
            // response.audio.delta）に、既存挙動へは一切影響しない新規の
            // 独立if文としてTURN_TRACEマーカーのみを追加する（既存のif/else-if
            // チェーンの構造・分岐条件は変更しない）。response.content_part.added
            // （PHASE1監査項目9）は、この2つに対して追加の時系列情報を持たない
            // ことを確認済みのため、専用markerを追加しない（不要な複雑化を
            // 避ける）。
            if (type === 'response.output_item.added') {
                pushTurnLatencyTrace('TURN_FIRST_OUTPUT_ITEM');
            }
            if (type === 'response.audio.delta') {
                // このイベントはAI発話1回につき多数回発火するが、
                // pushTurnLatencyTrace内のmarker重複排除により最初の1回のみ
                // 記録される。
                pushTurnLatencyTrace('TURN_FIRST_AUDIO_DELTA');
            }

            if (type === 'session.created' || type === 'session.updated') {
                setStatus(stSessionEl, type, 'ok');
                if (greetingTiming.sessionCreated === null) {
                    greetingTiming.sessionCreated = performance.now();
                    if (zeroWaitEnabled) {
                        zeroWaitTiming.realtimeReadyAt = greetingTiming.sessionCreated;
                        logEvent('[ZeroWait] KPI-2 Realtime準備完了時点のGreeting再生状態: ' + zeroWaitState
                            + '（Greeting中に準備完了=' + (zeroWaitState === 'playing' ? 'Y' : 'N') + '）');
                        if (zeroWaitState === 'ended' && zeroWaitTiming.endedAt !== null) {
                            logEvent('[ZeroWait] KPI-3 Greeting終了→Realtime準備完了 = '
                                + Math.round(zeroWaitTiming.realtimeReadyAt - zeroWaitTiming.endedAt) + 'ms');
                        }
                    }
                }
                maybeSendInitialGreeting().catch((e) => logEvent('第一声処理中にエラー: ' + e.message));
            } else if (type === 'input_audio_buffer.committed') {
                // PHASE8（会話停止調査・重要）: ユーザーの発話がinput_audio_bufferから
                // 実際にコミットされた（＝OpenAI側が発話終了を認識し、会話アイテム化
                // する準備ができた）ことを示すイベント。speech_stoppedは検知の合図に
                // すぎず、この後committedが来ない場合はサーバー側で発話が確定して
                // いない可能性があるため、両者を区別して記録する（PIIなし・
                // イベント発生の事実のみ）。
                pushTimelineEvent('USER_AUDIO_BUFFER_COMMITTED');
                // FAST TURN HOTFIX 3（今回追加・観測専用）: CRITICAL QUESTION 2
                // 監査結論により、通常ターンにはclient発の「response要求」
                // イベントが存在しない（semantic_vad有効時はcommit後、サーバーが
                // 自動でresponseを生成する設計）。そのためTURN_RESPONSE_REQUESTED
                // はこのcommitと同時刻の「サーバー側が応答生成してよい状態になった
                // 時点」として記録する（新しい送信を追加するものではない）。
                pushTurnLatencyTrace('TURN_COMMITTED');
                pushTurnLatencyTrace('TURN_RESPONSE_REQUESTED', 'source=server_auto_create_response_on_commit');
                // FAST TURN HOTFIX 4（今回追加・観測専用）: この時点でarmし、
                // TURN_RESPONSE_CREATEDが一定時間届かない場合のみ記録する
                // watchdog（何も送信しない）。
                if (turnLatencyTraceId !== null) armPlainTurnResponseWatchdog(turnLatencyTraceId);
                // FAST TURN 2（Section3/20）: (B) speech_stopped→committedの
                // 差分（VADテール、意味的判定が「発話継続中」と見なしていた
                // 追加の待ち時間）を記録する。lastSpeechStoppedAtは、AIが実際に
                // 話し始めた時点（tick()内）でのみnullにリセットされるため、
                // このタイミングではまだ有効な値が入っている。
                if (lastSpeechStoppedAt !== null) {
                    turnLatencyVadTailMs = Math.round(performance.now() - lastSpeechStoppedAt);
                }
                lastCommittedAtForLatency = performance.now();
                // NAME Forced Commit Observation PoC（PHASE7/9）: 正常完了扱いに
                // し、まだ送信していない手動commitがあればここで見送らせる。
                // 送信済みの手動commitへの反応であればelapsed_msを記録する。
                nameTurnNormalCompletionSeen = true;
                maybeLogPocReactionElapsed('committed', 'input_audio_buffer.committed');
                // Conversation Takeover Observation PoC（NAME PoCとは独立）
                takeoverTurnNormalCompletionSeen = true;
                maybeLogTakeoverReactionElapsed('committed', 'input_audio_buffer.committed');
                // Short Choice 3-Second Turn（今回追加。全店舗適用・独立）
                shortChoiceTurnNormalCompletionSeen = true;
                maybeLogShortAnswerReactionElapsed('committed', 'input_audio_buffer.committed');
                // PHONE Forced Commit（今回追加。全店舗適用・独立）
                phoneTurnNormalCompletionSeen = true;
                maybeLogPhoneReactionElapsed('committed', 'input_audio_buffer.committed');
                // SHORT_ANSWER Forced Commit（今回追加。全店舗適用・独立）
                quickAnswerTurnNormalCompletionSeen = true;
                maybeLogQuickAnswerReactionElapsed('committed', 'input_audio_buffer.committed');
                // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加。全店舗
                // 適用・独立）: 正常にcommitされたため、USER_TURN_3S_FALLBACK
                // はもう不要。
                userTurnFallbackNormalCompletionSeen = true;
                cancelUserTurnFallbackTimer('committed');
            } else if (type === 'conversation.item.created' && msg.item && msg.item.role === 'user') {
                // PHASE8（会話停止調査・最重要）: ユーザー発話が会話アイテムとして
                // 確定したことを示すイベント。「名前を答えた後に止まる」等の症状で、
                // このイベント自体が来ているか（＝発話は認識・確定されているのに
                // 応答が生成されていない）、来ていないか（＝そもそも発話が会話に
                // 反映されていない）を切り分けるための最重要ログ。item本体の
                // content（発話内容・電話番号・氏名等）は一切参照・記録しない。
                pushTimelineEvent('USER_ITEM_COMMITTED');
                // FAST TURN HOTFIX 3（今回追加・観測専用）
                pushTurnLatencyTrace('TURN_USER_ITEM_CREATED');
                // NAME Forced Commit Observation PoC（PHASE7/9）
                nameTurnNormalCompletionSeen = true;
                maybeLogPocReactionElapsed('item', 'conversation.item.created');
                // Conversation Takeover Observation PoC（NAME PoCとは独立）
                takeoverTurnNormalCompletionSeen = true;
                maybeLogTakeoverReactionElapsed('item', 'conversation.item.created');
                // Short Choice 3-Second Turn（今回追加。全店舗適用・独立）
                shortChoiceTurnNormalCompletionSeen = true;
                maybeLogShortAnswerReactionElapsed('item', 'conversation.item.created');
                // PHONE Forced Commit（今回追加。全店舗適用・独立）
                phoneTurnNormalCompletionSeen = true;
                maybeLogPhoneReactionElapsed('item', 'conversation.item.created');
                // SHORT_ANSWER Forced Commit（今回追加。全店舗適用・独立）
                quickAnswerTurnNormalCompletionSeen = true;
                maybeLogQuickAnswerReactionElapsed('item', 'conversation.item.created');
                // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加。全店舗
                // 適用・独立）
                userTurnFallbackNormalCompletionSeen = true;
                cancelUserTurnFallbackTimer('item');
            } else if (type === 'input_audio_buffer.speech_started') {
                // FAST TURN HOTFIX 3（今回追加）: このイベントを新しい1ユーザー
                // ターンの起点として、FULL TURN LATENCY TRACEを開始する
                // （挙動には無関係の観測専用呼び出し）。
                startTurnLatencyTrace(callGeneration);
                if (!initialGreetingSent) userSpokeBeforeGreeting = true;
                // ISSUE2調査用（PIIなし）: speech_started発生時点のローカルマイク
                // レベルメーターの値（0-100の相対音量パーセンテージのみ。音声内容・
                // 周波数の詳細等は含まない）をログへ残す。これにより、実機テストの
                // 再現時に「その瞬間、実際どれくらいの音量だったか」を体感と
                // 突き合わせられるようにする。VAD/turn_detection自体は変更しない
                // 観測用ログの追加のみ。
                // PHASE6相関用: この時点のタイムスタンプとAI発話中フラグを
                // スナップショットしておき、対応するspeech_stoppedで「発話継続
                // 時間」を計算できるようにする（人間が目視で「非常に短い時間で
                // speech_stoppedになっているか」を判断できるようにするだけで、
                // ここでノイズかどうかを自動判定・断定はしない）。
                lastSpeechStartedAt = performance.now();
                speechStartedDuringAiOutput = aiAudioOutputActive;
                if (aiAudioOutputActive) lastBargeInSpeechStartedAt = lastSpeechStartedAt;
                logEvent('speech_started時点のマイク入力レベル: ' + (lastMicLevelPct === null ? '(未取得)' : lastMicLevelPct + '%')
                    + ' / AI音声出力中(aiAudioOutputActive)=' + aiAudioOutputActive);
                pushTimelineEvent('USER_SPEECH_STARTED (AI音声出力中=' + aiAudioOutputActive + ', mic=' + (lastMicLevelPct === null ? '?' : lastMicLevelPct + '%') + ')');
                // FAST TURN HOTFIX（FIRST ANSWER MUST COUNT・今回追加）:
                // 「人数・時間を2回言わないと反応しない」実機不具合の監査用。
                // このspeech_startedが発生した瞬間、(1)AI SPEAKING PROTECTIONが
                // まだ保護中（aiSpeakingProtected===true）のままではないか、
                // (2)直前にマイクが再度使用可能になってから何ms後にこの発話が
                // 検知されたか、の2点を毎回・全てのexpectedAnswerType（NAME/
                // PHONE/TIME/PARTY_SIZE等すべて）について記録する。挙動は
                // 一切変更しない、純粋な観測専用ログ（PIIなし）。
                // もしこのログでaiSpeakingProtected=trueの状態でspeech_startedが
                // 記録された場合、マイクミュートが仕様どおりに機能していない
                // （＝雑音対策自体に別の問題がある）ことの直接証拠になる。
                // 一方、aiSpeakingProtected=falseかつmsSinceMicReadyが非常に
                // 小さい場合は、ミュート解除直後の発話の冒頭が失われている
                // 可能性を疑う材料になる（ただし断定はしない）。
                pushTimelineEvent('USER_SPEECH_STARTED_MIC_STATE (expectedAnswerType=' + expectedAnswerType
                    + ', aiSpeakingProtected=' + aiSpeakingProtected
                    + ', msSinceMicReady=' + (lastAiSpeakingEndAt === null ? 'null' : Math.round(performance.now() - lastAiSpeakingEndAt)) + ')');
                if (aiAudioOutputActive) {
                    // 無言化調査用（最優先A）: AIがまだ話している最中に
                    // speech_startedが発生した＝割り込み（バージイン）として
                    // 扱われる可能性がある事象。これ自体が誤検知かどうかは
                    // このログだけでは断定できないため、続くoutput_audio_buffer.
                    // cleared / conversation.item.truncatedの有無と合わせて
                    // 確認する（原因を推測で確定させない）。
                    logEvent('[無言化調査] AI音声出力中にspeech_startedを検知しました（バージインとして扱われる可能性。'
                        + '雑音等による誤検知かどうかはこの時点では未確認です）');
                    // 実機DEBUG（ユーザー指示・BARGE_IN_ACCEPTED）: この時点で
                    // speech_startedがサーバーへ実際に届いた（＝マイクtrackが
                    // ミュートされておらず物理的に音声が送信された）という
                    // 事実のみを記録する。「人間の発話か雑音か」は現在のAPIでは
                    // 区別できないため断定しない（推測実装しない、という監査結論
                    // どおり）。aiSpeakingProtected===trueの間は仕様上この
                    // speech_started自体がサーバー側で発生し得ないため、ここに
                    // 到達した場合はAI SPEAKING PROTECTIONの保護区間外
                    // （response.created〜output_audio_buffer.started間の
                    // 意図的な非対象区間、または保護failed-open時）である
                    // ことを示す。
                    pushTimelineEvent('BARGE_IN_ACCEPTED (aiSpeakingProtected=' + aiSpeakingProtected + ')');
                }
                if (zeroWaitEnabled && zeroWaitState === 'playing') {
                    // section18: 対処は行わず観測のみ（エコーによるVAD誤反応の可能性）。
                    logEvent('[ZeroWait] 観測: Greeting再生中にspeech_startedを検知（エコー等による誤検知の可能性。対処は行わずログのみ記録）');
                }
                subStatusText.textContent = 'こちらの発話を認識中…';
                setStatus(stUserSpeakEl, '発話中（認識中）', 'ok');
                userVadState = 'speech';
                updateAudioDiagnosticsPanel();
                // Expected Answer Window（PHASE6: 起点はユーザーの発話開始）。
                // NAME/PHONE/YES_NO/SHORT_CHOICE/VISIT_REASON専用（SHORT_ANSWERは
                // PHASE O5.8でこの機構から除外済み。ANSWER_WINDOW_LIMITS_MSに
                // SHORT_ANSWERが存在しないため、type==='SHORT_ANSWER'ではこの
                // 呼び出しは何もせず即returnする）。
                startAnswerWindowIfNeeded(callGeneration);
                // PHASE O5.8: SHORT_ANSWER Finalization Grace timerが動いている
                // 最中に新たなspeech_startedが来た場合、まだ話している途中
                // （言い淀み・言い直し等）である可能性を優先し、無条件でcancelする
                // （ユーザー指示4・6）。expectedAnswerTypeがSHORT_ANSWER以外の
                // 場合、このtimerは元々armされていないため無害なno-op。
                cancelQuickAnswerFinalizeTimer('speech_started_again');
                // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加）:
                // 新たなspeech_startedが来た場合、まだ話している途中
                // （言い淀み・言い直し等）である可能性を優先し、armされている
                // USER_TURN_3S_FALLBACKタイマーがあれば無条件でcancelする
                // （O5.8と同じ設計思想。expectedAnswerTypeが'NONE'以外の
                // 場合、このtimerは元々armされていないため無害なno-op）。
                cancelUserTurnFallbackTimer('speech_started_again');
                if (expectedAnswerType === 'NONE') {
                    pushTimelineEvent('USER_TURN_START (callGeneration=' + callGeneration + ')');
                }
                // PHONE Forced Commit（今回追加）: PHONEターン中のユーザー発話
                // 開始を専用マーカーとしても記録する（既存の汎用USER_SPEECH_STARTED
                // に加え、実機ログでPHONEターンだけを追いやすくするため）。
                if (expectedAnswerType === 'PHONE') pushTimelineEvent('PHONE_SPEECH_STARTED');
                // Conversation Takeover Observation PoC（今回追加。NAME PoCとは独立。
                // 起点も同じくユーザーの発話開始）。
                startTakeoverTimerIfNeeded(callGeneration);
                // Silence Timeout: 有効なユーザー発話を検知したため即リセット
                // （'warned'中であればここで終話をキャンセルし通常会話へ復帰する）。
                resetSilenceTimer('user_speech_started');
                // LOCAL SILENCE ASSIST — OBSERVATION PoC（観測専用。PoC店舗+debug=1
                // のみ有効。Realtime APIへは何も送信しない）。
                startLocalSilenceTurn();
            } else if (type === 'input_audio_buffer.speech_stopped') {
                // FAST TURN HOTFIX 3（今回追加・観測専用）
                pushTurnLatencyTrace('TURN_INPUT_STOP');
                userVadState = 'idle';
                lastSpeechStoppedAt = performance.now();
                // 正常にターンが終了した（＝雑音等で長時間化していない）ため、
                // Answer Windowの観測用タイマーは不要になる。
                cancelAnswerWindow('speech_stopped_normally');
                // NAME Forced Commit Observation PoC（PHASE7）: 正常にターンが
                // 終了したため、この世代についてはもう手動commitを送らない。
                nameTurnNormalCompletionSeen = true;
                // Conversation Takeover Observation PoC（今回追加。NAME PoCとは
                // 独立）: 同じく正常終了したため、このエピソードの強制commitは
                // もう不要。
                cancelTakeoverTimer('speech_stopped_normally');
                takeoverTurnNormalCompletionSeen = true;
                // Short Choice 3-Second Turn（今回追加。全店舗適用・独立）:
                // 同じく正常終了したため、この2択質問の世代についてはもう
                // 手動commitを送らない。Answer Window自体は上のcancelAnswerWindow()
                // で既にキャンセル済み（NAME/YES_NO/SHORT_CHOICE等で共有している
                // 同一のanswerWindowTimerIdのため、専用のキャンセル関数は不要）。
                shortChoiceTurnNormalCompletionSeen = true;
                // PHONE Forced Commit（今回追加。全店舗適用・独立）: 同じく正常
                // 終了したため、この電話番号ターンの世代についてはもう手動commitを
                // 送らない。Answer Window自体は上のcancelAnswerWindow()で既に
                // キャンセル済み（PHONE/NAME/YES_NO/SHORT_CHOICEで共有している
                // 同一のanswerWindowTimerIdのため、専用のキャンセル関数は不要）。
                phoneTurnNormalCompletionSeen = true;
                // PHASE O5.10（SHORT_ANSWER — SEMANTIC VAD PRIMARY TURN
                // COMPLETION。O5.9.2 Auditの結論を採用）: O5.8では、ここで
                // Finalization Grace（1.2秒）をarmし、猶予後にmanual
                // input_audio_buffer.commitを送る設計にしていた
                // （armQuickAnswerFinalizeTimer参照）。しかしO5.9.2の監査で、
                // (a) VISIT_REASON等の他の自由回答は手動commitを一切経由せず
                // semantic_vadの自然完了だけで正常に進んでいること、
                // (b) OpenAI公式ドキュメント・Developer Communityの実例でも、
                // server-side VAD（semantic_vad）有効時はサーバーが自動で
                // commit・response生成まで行う設計であり、手動commitは
                // 不要・むしろ競合の原因になり得ると確認されたこと、
                // (c) 実機で「1回目は進まず同じ回答を2回言うと進む」現象が
                // 「1回目はmanual commitがsemantic_vadの自然完了とrace/干渉し、
                // 2回目はquickAnswerCommitSentGenerationガードによりmanual
                // commitが送られずsemantic_vad単体で成功する」という構造と
                // 整合すること、の3点が判明した。そのためSHORT_ANSWERの通常
                // 経路からは、このFinalization Grace timerを起動する呼び出し
                // 自体を外し、speech_stopped後は他の自由回答（VISIT_REASON等）
                // と同じく、semantic_vadの自然なターン完了
                // （committed/item_created/response.created/function_call）を
                // primaryの経路として待つ。armQuickAnswerFinalizeTimer()/
                // maybeSendQuickAnswerCommit()の関数自体は削除せず、将来的に
                // 本当に異常なケース（サーバーが長時間何も反応しない場合等）の
                // fallback候補として残す（現時点ではどこからも呼ばれない
                // dead/unused状態。下記の関数定義のコメントを参照）。
                // quickAnswerTurnNormalCompletionSeen（＝「サーバーが自律的に
                // このターンを処理した」フラグ）は、既存どおりcommitted/
                // item_created/response.created/function_callの各イベントで
                // 立てる（下記参照。この検知ロジック自体は無変更）。
                // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加）:
                // expectedAnswerType==='NONE'（1st turn「要件を聞く」場面。
                // 上のUSER_TURN_FALLBACK監査コメント参照）の場合のみ、
                // USER_TURN_3S_FALLBACKタイマーをarmする。それ以外の
                // typeでは内部で何もせずに戻る（既存のNAME/PHONE/YES_NO/
                // SHORT_CHOICE/VISIT_REASON/SHORT_ANSWERの各機構には一切
                // 影響しない、完全に独立した新規パス）。
                armUserTurnFallbackTimer(callGeneration);
                // PHASE6相関用（PIIなし）: 対応するspeech_startedからの経過時間
                // （＝サーバーがユーザー発話と判定していた継続時間）を記録する。
                // 短時間の相槌（「はい」「違います」等）も本物の短い発話として
                // 存在するため、この数値だけで自動的にノイズと判定はしない。
                // あくまで実機ログを人間が確認する際の判断材料。
                let speechDurationMsForTimeline = null;
                if (lastSpeechStartedAt !== null) {
                    speechDurationMsForTimeline = Math.round(performance.now() - lastSpeechStartedAt);
                    logEvent('speech_stopped: 発話継続時間=' + speechDurationMsForTimeline + 'ms'
                        + '（speech_started時点でAI音声出力中だった=' + speechStartedDuringAiOutput + '）');
                }
                pushTimelineEvent('USER_SPEECH_STOPPED (継続=' + (speechDurationMsForTimeline === null ? '?' : speechDurationMsForTimeline + 'ms') + ')');
                // PHASE O5.5 Speak-Then-Work計測（Section9・T0-T10とは別名の
                // 追加計測。既存T0-T10のtoolContinuationTraceT0/Active等には
                // 一切触れず、独立した変数に「直近のユーザー発話終了時刻」を
                // 記録するだけ。この値はmaybeSendSpeakThenWorkAckFallback内の
                // 安全網音声再生時・実際の再生開始(playingイベント)時に、
                // 「お客様の発話終了から何ms後に安全網音声が聞こえ始めたか」を
                // 算出するためだけに参照される。Realtime制御イベントの送信判断
                // には一切使わない（観測専用）。
                lastUserSpeechStoppedAt = performance.now();
                // LOCAL SILENCE ASSIST — OBSERVATION PoC（観測専用。PoC店舗+debug=1
                // のみ有効。Realtime APIへは何も送信しない。既存TURN LATENCY計測
                // より前に呼んでも後に呼んでも計算結果に影響しない独立処理だが、
                // ログの読みやすさのためUSER_SPEECH_STOPPEDの直後に置く）。
                finishLocalSilenceTurn(speechDurationMsForTimeline);
                // FAST TURN 2（Section3/20）: 今回のターンの(A)発話継続時間と、
                // その時点のexpectedAnswerType（＝どのFast Turnカテゴリの
                // 対象だったか。第一声直後で対象外だった場合は'NONE'のまま）を
                // 一時保存する。次のAI発話検知時にまとめて1行のTURN LATENCYログを
                // 出す（新しい表示UIは追加しない）。
                turnLatencySpeechDurationMs = speechDurationMsForTimeline;
                turnLatencyFastTurnLabel = expectedAnswerType;
                turnLatencyVadTailMs = null;
                turnLatencyCommitToResponseMs = null;
                lastCommittedAtForLatency = null;
                lastResponseCreatedAtForLatency = null;
                // FAST TURN 3.1（実機レイテンシ診断・計測のみ）: 次のターンの
                // Tool呼び出し計測用に、前のターンの値をリセットする。
                toolCallStartedAtForLatency = null;
                turnLatencyToolOccurred = false;
                turnLatencyToolName = null;
                turnLatencyToolDurationMs = null;
                subStatusText.textContent = 'AIが応答を準備しています…';
                setStatus(stUserSpeakEl, '待機', null);
                updateAudioDiagnosticsPanel();
            } else if (type === 'response.created') {
                if (greetingTiming.firstResponseCreated === null) {
                    greetingTiming.firstResponseCreated = performance.now();
                }
                // FAST TURN 2（Section3/20）: (C) committed→response.createdの
                // 差分を記録する。lastCommittedAtForLatencyが無い場合（PHONE/NAME等の
                // 強制commit直前にOpenAI側が自発的に応答を作った等）はnullのままにし、
                // 無理に数値を作らない。
                // FAST TURN 3.1（実機レイテンシ診断・計測のみ、修正）: Tool Callを
                // 含むターンではresponse.createdが1ターン中に2回発生する
                // （1回目=function_callのみの中間応答、2回目=Tool結果を踏まえた
                // 音声応答）。従来は毎回上書きしていたため、2回目のresponse.created
                // 時刻を使ってcommit_to_responseを計算してしまい、その差分に
                // Tool呼び出しの往復時間（fetch等）まで丸ごと合算されてしまい、
                // 「OpenAIの応答生成が遅い(C)」なのか「Tool/API呼び出しが遅い(E)」
                // なのかを区別できなくなっていた。turnLatencyCommitToResponseMsが
                // まだnull（＝このターンで初めてのresponse.created）の場合のみ
                // 記録するよう変更し、常に「commit→最初の応答開始」という本来の
                // (C)区間だけを表すようにする（Tool呼び出し時間は上で追加した
                // turnLatencyToolDurationMsとして別途ログに出す）。
                if (lastCommittedAtForLatency !== null && turnLatencyCommitToResponseMs === null) {
                    turnLatencyCommitToResponseMs = Math.round(performance.now() - lastCommittedAtForLatency);
                }
                lastResponseCreatedAtForLatency = performance.now();
                subStatusText.textContent = 'AIが応答を生成中…';
                responseState = 'active';
                updateAudioDiagnosticsPanel();
                pushTimelineEvent('RESPONSE_CREATED');
                // FAST TURN HOTFIX 3（今回追加・観測専用）: markerの重複排除により
                // Tool呼び出しターンの2回目のresponse.created（最終応答）では
                // 記録されない＝既存のturnLatencyCommitToResponseMsと同じ
                // 「初回のみ」計測になる。
                pushTurnLatencyTrace('TURN_RESPONSE_CREATED');
                // FAST TURN HOTFIX 4（今回追加・観測専用）: TURN_RESPONSE_CREATEDに
                // 到達した＝この窓は今回は発生しなかったことが確定したため解除。
                cancelPlainTurnResponseWatchdog('turn_response_created_arrived');
                pushToolContinuationTrace('T7_CONTINUATION_RESPONSE_CREATED');
                // FAST TURN HOTFIX 4（今回追加・観測専用）: T7に到達した＝
                // T6→T7の空白は今回は発生しなかったことが確定したため、
                // watchdogを解除する（何もsendしない、ただのclearTimeout）。
                cancelToolContinuationResponseWatchdog('t7_response_created_arrived');
                // PHASE O5.6診断（response.create correlation）: 直近に明示的に
                // 送信したsendResponseCreate()のreasonを、この時点で一度だけ
                // 消費してカテゴリ化する。これにより「silenceState='warned'の
                // 状態で届いたresponse.createdが、本当に自分が送ったsilence_
                // warningの応答なのか、それとも別の（通常会話やturn_detection
                // 自動生成の）応答なのか」を後から区別できる。消費後は
                // lastResponseCreateReasonをnullへ戻すため、対応するsend元が
                // 無い次のresponse.createdは自動的にnormal_conversation
                // （＝サーバー側turn_detectionによる自動応答の可能性）として
                // 記録される。挙動制御には一切使わない・純粋な観測用ログ。
                try {
                    const consumedReasonForDiag = lastResponseCreateReason;
                    const categoryForDiag = categorizeResponseReason(consumedReasonForDiag);
                    lastResponseReasonCategoryForDiag = categoryForDiag;
                    pushTimelineEvent('RESPONSE_CREATE_CORRELATION (category=' + categoryForDiag
                        + ', matchedPendingReason=' + (consumedReasonForDiag === null ? 'null' : consumedReasonForDiag)
                        + ', silenceStateAtArrival=' + silenceState + ')');
                    lastResponseCreateReason = null;
                } catch (diagErr) {}
                // NAME Forced Commit Observation PoC（PHASE9/12）: 手動commit後に
                // OpenAI側が自発的にresponse.createdを送ってきた場合、観測する
                // だけで、こちらから追加のresponse.createは絶対に送らない。
                maybeLogPocReactionElapsed('responseCreated', 'response.created');
                // Conversation Takeover Observation PoC（NAME PoCとは独立）
                maybeLogTakeoverReactionElapsed('responseCreated', 'response.created');
                // Short Choice 3-Second Turn（今回追加。全店舗適用・独立）
                maybeLogShortAnswerReactionElapsed('responseCreated', 'response.created');
                // PHONE Forced Commit（今回追加。全店舗適用・独立）
                maybeLogPhoneReactionElapsed('responseCreated', 'response.created');
                // SHORT_ANSWER Forced Commit（今回追加。全店舗適用・独立）
                maybeLogQuickAnswerReactionElapsed('responseCreated', 'response.created');
                // PHASE O5.8（ユーザー指示13・重要）: response.createdが正常に
                // 届いた時点で、まだSHORT_ANSWER Finalization Grace timerが
                // 残っていれば必ずcancelする。これにより、サーバー側が既に
                // 自力で応答を開始したにもかかわらず、1.2秒後に古いForced
                // Commitが遅れて発火することを防ぐ（二重commit・二重response
                // 防止。ユーザー指示12）。silenceStateの状態（'waiting'かどうか）
                // に関わらず常に評価する（Silence Timeoutの条件とは独立）。
                // 併せてquickAnswerTurnNormalCompletionSeenも立て、万一この
                // response.createdの直前にcommitted/item_createdが届いていない
                // 順序で届いた場合でも、送信直前の最終レース確認で二重に守れる
                // ようにする。
                cancelQuickAnswerFinalizeTimer('response_created');
                quickAnswerTurnNormalCompletionSeen = true;
                // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加。全店舗
                // 適用・独立）: response.createdが正常に届いた時点で、まだ
                // USER_TURN_3S_FALLBACKタイマーが残っていれば必ずcancelする
                // （二重commit・二重response防止。既存機構と同じ考え方）。
                cancelUserTurnFallbackTimer('response_created');
                userTurnFallbackNormalCompletionSeen = true;
                // Silence Timeout: この回の応答にfunction_callが含まれるかどうかを
                // 新しい応答サイクルの開始時点でリセットする（response.doneで判定に使う）。
                responseHasFunctionCall = false;
                // 'waiting'（通常の無言計測中）であればAIが新たに話し始める/
                // 生成を始めるためキャンセルする。'warned'/'goodbye'中に発生する
                // response.createdは本機構自身が発行したアナウンスの応答である
                // ため、ここではキャンセル対象にしない（個別の状態遷移で管理）。
                if (silenceState === 'waiting') {
                    resetSilenceTimer('ai_response_started');
                }
            } else if (type === 'response.done') {
                lastAiAudioEventAt = performance.now();
                // FAST TURN 3.6A（UI_STATE修正・UXのみ、latency/挙動は無変更）:
                // FAST TURN 3.4の観測で、Tool Callを含むターンではfunction_call
                // のみの中間応答についてもこのresponse.doneが1回発火し、
                // 実際にはToolの往復処理中（＝AIはまだ何も答えていない）に
                // もかかわらず画面が「待機中（お話しください）」に戻ってしまう
                // ことを確認済み（実機でも再現を確認）。この中間応答
                // （responseHasFunctionCall===true）では「待機中」へ戻さず、
                // 「確認しています」（PROCESSING）を表示し続ける。本当に
                // ユーザーの発話待ちに戻ったこと（function_callを含まない
                // 最終応答が完了したこと）が確定した場合のみ「待機中」に戻す。
                // dc.send等のRealtime制御イベントは一切変更しない（表示文言のみ）。
                if (responseHasFunctionCall) {
                    subStatusText.textContent = '確認しています';
                } else {
                    subStatusText.textContent = '待機中（お話しください）';
                }
                pushTimelineEvent('UI_STATE: ' + (responseHasFunctionCall ? '確認しています表示' : '待機中表示')
                    + ' (この応答のresponseHasFunctionCall=' + responseHasFunctionCall + ')');
                // 安全網: 通常はoutput_audio_buffer.stopped/clearedで既にfalseに
                // 戻っているはずだが、稀にそれらのイベントを取りこぼした場合でも
                // aiAudioOutputActiveが誤ってtrueのまま残らないようにする。
                aiAudioOutputActive = false;
                // AI SPEAKING PROTECTION（今回追加・安全網）: 上記と同じ理由で、
                // response.done到達時点でまだミュートが残っていれば必ず解除する
                // （お客様のマイクが恒久的にミュートされたまま残ることを防ぐ、
                // 最終防衛ライン）。
                releaseAiSpeakingProtection('response_done_fallback');
                // PHASE5（response lifecycle）: response.doneのmsg.response.statusは
                // OpenAI Realtime APIが実際に使用する値のみを参照する
                // （'completed' | 'cancelled' | 'failed' | 'incomplete'）。
                // 存在しない値を作らない。'cancelled'/'incomplete'は「音声のみ停止」
                // 「打ち切り」の可能性を示すが、ここでは事実の記録のみで断定しない。
                const respStatus = msg.response && msg.response.status;
                responseState = (respStatus === 'failed') ? 'error' : 'done';
                updateAudioDiagnosticsPanel();
                pushTimelineEvent('RESPONSE_DONE (status=' + (respStatus || '不明') + ')');
                // FAST TURN HOTFIX 5（今回追加・観測専用）: 実機で
                // 「[Phase2.6 usage] status: "failed"」が複数回観測されたが、
                // recordUsageEvent()はresponse.statusの文字列しか記録しておらず、
                // 実際の失敗理由（OpenAI Realtime API公式ドキュメントの
                // response.done: status_details = { type, reason, error:
                // { type, code, message } }）を完全に捨てていたことが監査で
                // 判明した。ここではそのstatus_details/errorのうち、
                // 実際にevent schemaに存在するフィールド（type/reason/
                // error.type/error.code）のみを追加で記録する（推測でfield名を
                // 作らない）。error.messageは要約や個人情報が含まれる可能性を
                // 否定できないため、Copy Debug Log/#diagTimelineには一切含めず、
                // debugMode（?debug=1）時のみconsole.logへ直接出す。
                // response.create再送信・retry・timeout変更等の動作は一切
                // 行わない（観測のみ）。
                if (respStatus === 'failed' || respStatus === 'incomplete') {
                    try {
                        const sd = (msg.response && msg.response.status_details) || null;
                        const sdType = sd ? (sd.type || null) : null;
                        const sdReason = sd ? (sd.reason || null) : null;
                        const sdErr = sd ? (sd.error || null) : null;
                        const errType = sdErr ? (sdErr.type || null) : null;
                        const errCode = sdErr ? (sdErr.code || null) : null;
                        const respIdTail = (msg.response && msg.response.id) ? String(msg.response.id).slice(-8) : 'null';
                        // 相関情報: 会話内容には一切触れず、既存の観測用変数
                        // （toolContinuationTraceActive/toolContinuationTraceCallId/
                        // turnLatencyTraceId/lastResponseReasonCategoryForDiag）を
                        // 読み取るのみ（新規の状態追跡は追加しない）。
                        const corrToolActive = toolContinuationTraceActive;
                        const corrCallIdTail = toolContinuationTraceCallId ? String(toolContinuationTraceCallId).slice(-8) : 'null';
                        const corrTurnId = (turnLatencyTraceId === null || turnLatencyTraceId === undefined) ? 'null' : turnLatencyTraceId;
                        const corrCategory = lastResponseReasonCategoryForDiag || 'unknown';
                        const failedLine = 'RESPONSE_DONE_FAILED (status=' + respStatus
                            + ', response_id_tail=' + respIdTail
                            + ', status_details_type=' + (sdType || 'null')
                            + ', status_details_reason=' + (sdReason || 'null')
                            + ', error_type=' + (errType || 'null')
                            + ', error_code=' + (errCode || 'null')
                            + ', toolContinuationActive=' + corrToolActive
                            + ', toolCallIdTail=' + corrCallIdTail
                            + ', turnLatencyTraceId=' + corrTurnId
                            + ', responseCreateCategory=' + corrCategory
                            + ')';
                        pushTimelineEvent(failedLine);
                        // pushTimelineEventはConsoleへ出力しないため（監査で判明）、
                        // 実機Console検索（"FAILED"）で見つけられるよう
                        // console.logへも明示的に出す（PIIを含まないフィールドのみ）。
                        console.log('[' + failedLine + ']');
                        if (debugMode && sdErr && sdErr.message) {
                            console.log('[RESPONSE_DONE_FAILED_ERROR_MESSAGE_DEBUG_ONLY]', sdErr.message);
                        }
                    } catch (diagErr) {
                        pushTimelineEvent('RESPONSE_DONE_FAILED_MARKER_ERROR (' + ((diagErr && diagErr.message) || '不明') + ')');
                    }
                }
                recordUsageEvent(msg.response);
                // Silence Timeout: この回にfunction_callが無かった場合のみ、
                // 「AIがユーザーの回答を待っている」状態に入ったとみなし開始する
                // （Tool Call往復中の中間応答では開始しない＝待ち時間を無言として
                // カウントしないための最重要ガード）。
                if (!responseHasFunctionCall) {
                    startSilenceTimerIfNeeded(callGeneration, 'response_done_no_function_call');
                }
                // FAST TURN 3.6B（Tool Continuation Proof）: function_callを含まない
                // 最終応答が完了した時点で初めてT10を記録し、Tool継続チェーン
                // （T0〜T10）を完了とみなしてトレースを終了する。中間応答
                // （responseHasFunctionCall===true）ではT10を記録しない
                // （まだ継続が完了していないため）。
                if (!responseHasFunctionCall) {
                    pushToolContinuationTrace('T10_CONTINUATION_RESPONSE_DONE (status=' + (respStatus || '不明') + ')');
                    toolContinuationTraceActive = false;
                    toolContinuationTraceCallId = null;
                    toolContinuationTraceT0 = null;
                    cancelToolContinuationResponseWatchdog('t10_response_done');
                }
                // FAST TURN HOTFIX 3（今回追加・観測専用）: function_callを含まない
                // 最終応答が完了した時点で、このターンのFULL TURN LATENCY TRACEを
                // TURN_RESPONSE_DONEで終了する（T10と同じ「中間応答では終了しない」
                // 条件を踏襲）。中間応答（Tool呼び出し中）ではトレースを終了せず、
                // 後続の最終応答まで継続する（toolContinuationActive=trueとして
                // 各markerに記録され続けるため、人間が後からTOOL_TRACEと突き合わせて
                // 確認できる）。
                if (!responseHasFunctionCall) {
                    endTurnLatencyTrace('TURN_RESPONSE_DONE', 'status=' + (respStatus || '不明'));
                }
                // Silence Timeout: 終話案内アナウンスの再生完了検知の安全網
                // （通常はoutput_audio_buffer.stoppedで既に処理済みのはず）。
                maybeHangUpAfterSilenceGoodbye(callGeneration, 'response_done_fallback');
            } else if (type === 'response.output_audio_transcript.done') {
                // Expected Answer Window: AI自身の発話内容から、直後にお客様へ
                // 求めている回答の種類を軽量に推定する（PIIはtype文字列にのみ
                // 反映し、transcript本文はどこにも保存・記録しない）。
                classifyExpectedAnswerType(msg.transcript);
            } else if (type === 'response.output_item.done' && msg.item && msg.item.type === 'function_call') {
                // Phase3A: Tool Calling。応答完了(response.done)を待たず、
                // このitemが確定した時点で処理を開始してよい
                // （実測で response.function_call_arguments.done とほぼ同時か
                // 直後に発火し、この時点でarguments/nameが確定していることを
                // 確認済み）。非同期で処理するが、handleDataChannelEvent自体は
                // 待たない（他のイベント処理をブロックしないため）。
                // Silence Timeout: このresponseにはfunction_callが含まれるため、
                // 続くresponse.doneではまだ「ユーザーの回答待ち」状態にしない
                // （Tool往復中の待ち時間を無言としてカウントしないためのフラグ）。
                responseHasFunctionCall = true;
                // PHASE O5.8（ユーザー指示14）: 正常なfunction_callへ進んだ場合、
                // 既にターン処理が前進しているため、残存するSHORT_ANSWER
                // Finalization Grace timerがあればここでcancelする（1.2秒後に
                // 古いForced Commitが遅れて発火することを防ぐ）。O5.5 ack
                // fallback・T0-T10トレースより前に評価しても、それらの処理には
                // 一切影響しない（本cancelは純粋にタイマー1個をclearするのみ）。
                cancelQuickAnswerFinalizeTimer('function_call_started');
                quickAnswerTurnNormalCompletionSeen = true;
                // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加。全店舗
                // 適用・独立）
                cancelUserTurnFallbackTimer('function_call_started');
                userTurnFallbackNormalCompletionSeen = true;
                // FAST TURN 3.6A（UI_STATE修正・UXのみ）: Tool呼び出しが確定した
                // この時点で「確認しています」（PROCESSING）へ切り替える
                // （続くresponse.doneのUI_STATE分岐がこの後も同じ文言を維持する）。
                subStatusText.textContent = '確認しています';
                // PHASE O5.5 Speak-Then-Work: Toolの実処理(handleFunctionCallItem)
                // を待たず、function_callがこの時点で確定したその場で安全網
                // acknowledgement音声の再生要否を判定する（最速で「無言」を
                // 埋めるため）。callGenerationは本スコープの外側クロージャに
                // 存在する既存の通話世代カウンタをそのまま利用し、新たな
                // 状態を追加しない。
                maybeSendSpeakThenWorkAckFallback(msg.item, callGeneration);
                handleFunctionCallItem(msg.item).catch((e) => {
                    logEvent('Tool処理中にエラー: ' + e.message);
                });
            } else if (type === 'error') {
                logEvent('サーバーエラー: ' + JSON.stringify(msg.error || msg));
                showErrorBanner('通話中にエラーが発生しました。お手数ですが、もう一度おかけ直しください。');
                responseState = 'error';
                updateAudioDiagnosticsPanel();
                pushTimelineEvent('サーバーerrorイベント受信 (type=' + ((msg.error && msg.error.type) || '不明') + ')');
                // FAST TURN 3.6B（Tool Continuation Proof・STEP9）: Tool継続
                // チェーンのトレース中にRealtime側からerrorイベントが届いた場合、
                // dc.send()が例外を投げていなくても、OpenAI側がfunction_call_output
                // またはresponse.createを何らかの理由で拒否/失敗させた可能性が
                // ある。これを見逃さないよう、トレース中であればT_ERRORとして
                // 記録し、以降のT7〜T10（未達成のまま）を待たずにトレースを
                // 終了する（code/typeのみ・PIIなし。既存のerrorハンドラ本体の
                // 挙動＝showErrorBanner/responseState='error'は一切変更しない）。
                pushToolContinuationTrace('T_ERROR_REALTIME_ERROR (type=' + ((msg.error && msg.error.type) || '不明')
                    + ', code=' + ((msg.error && msg.error.code) || '不明') + ')');
                toolContinuationTraceActive = false;
                cancelToolContinuationResponseWatchdog('realtime_error');
                // NAME Forced Commit Observation PoC（PHASE8/10）: 手動commit
                // 送信後に発生したerrorは、このPoCが観測したい重要な結果の
                // 一つ（例: "buffer is empty"等）であるため、専用のタイムライン
                // イベントとしても記録する。このハンドラ自体は元々fatal処理
                // （endCall/cleanupConnection）を一切行っていないため、PoCの
                // ためにここを非fatal化する必要はない（既存の挙動をそのまま
                // 維持するだけでPHASE10の要件を満たす）。APIキー等の秘密情報は
                // 一切含めない（code/type/messageのみ）。
                if (pocCommitSentAt !== null && callGeneration === pocCommitCallGeneration) {
                    const pocErrCode = (msg.error && msg.error.code) || null;
                    const pocErrType = (msg.error && msg.error.type) || null;
                    const pocErrMessage = (msg.error && msg.error.message) || null;
                    pushTimelineEvent('POC_NAME_COMMIT_ERROR (code=' + pocErrCode + ', type=' + pocErrType
                        + ', elapsed_ms=' + Math.round(performance.now() - pocCommitSentAt) + ', message=' + pocErrMessage + ')');
                }
                // Conversation Takeover Observation PoC（今回追加。NAME PoCとは独立）:
                // 同じ理由で、手動commit送信後に発生したerrorを専用イベントとして
                // 記録する。このハンドラ自体は元々fatal処理（endCall/
                // cleanupConnection）を一切行っていないため、そのままの挙動を
                // 維持するだけで非fatal要件を満たす。APIキー等の秘密情報は
                // 一切含めない（code/type/messageのみ）。
                if (takeoverCommitSentAt !== null && callGeneration === takeoverCommitCallGeneration) {
                    const toErrCode = (msg.error && msg.error.code) || null;
                    const toErrType = (msg.error && msg.error.type) || null;
                    const toErrMessage = (msg.error && msg.error.message) || null;
                    pushTimelineEvent('TAKEOVER_ERROR (code=' + toErrCode + ', type=' + toErrType
                        + ', elapsed_ms=' + Math.round(performance.now() - takeoverCommitSentAt) + ', message=' + toErrMessage + ')');
                }
                // Short Choice 3-Second Turn（今回追加。全店舗適用・独立）: 同じ
                // 理由で、手動commit送信後に発生したerrorを専用イベントとして
                // 記録する。このハンドラは元々fatal処理を一切行っていないため、
                // そのままの挙動を維持するだけで非fatal要件を満たす。
                if (shortAnswerCommitSentAt !== null && callGeneration === shortAnswerCommitCallGeneration) {
                    const scErrCode = (msg.error && msg.error.code) || null;
                    const scErrType = (msg.error && msg.error.type) || null;
                    const scErrMessage = (msg.error && msg.error.message) || null;
                    pushTimelineEvent('SHORT_ANSWER_COMMIT_ERROR (code=' + scErrCode + ', type=' + scErrType
                        + ', elapsed_ms=' + Math.round(performance.now() - shortAnswerCommitSentAt) + ', message=' + scErrMessage + ')');
                }
                // PHONE Forced Commit（今回追加。全店舗適用・独立）: 同じ理由で、
                // 手動commit送信後に発生したerrorを専用イベントとして記録する。
                // このハンドラは元々fatal処理を一切行っていないため、そのままの
                // 挙動を維持するだけで非fatal要件を満たす。
                if (phoneCommitSentAt !== null && callGeneration === phoneCommitCallGeneration) {
                    const phErrCode = (msg.error && msg.error.code) || null;
                    const phErrType = (msg.error && msg.error.type) || null;
                    const phErrMessage = (msg.error && msg.error.message) || null;
                    pushTimelineEvent('PHONE_COMMIT_ERROR (code=' + phErrCode + ', type=' + phErrType
                        + ', elapsed_ms=' + Math.round(performance.now() - phoneCommitSentAt) + ', message=' + phErrMessage + ')');
                }
                // SHORT_ANSWER Forced Commit（今回追加。全店舗適用・独立）: 同じ
                // 理由で、手動commit送信後に発生したerrorを専用イベントとして
                // 記録する。このハンドラは元々fatal処理を一切行っていないため、
                // そのままの挙動を維持するだけで非fatal要件を満たす。
                if (quickAnswerCommitSentAt !== null && callGeneration === quickAnswerCommitCallGeneration) {
                    const qaErrCode = (msg.error && msg.error.code) || null;
                    const qaErrType = (msg.error && msg.error.type) || null;
                    const qaErrMessage = (msg.error && msg.error.message) || null;
                    const qaElapsedSinceCommitMs = Math.round(performance.now() - quickAnswerCommitSentAt);
                    pushTimelineEvent('QUICK_ANSWER_COMMIT_ERROR (code=' + qaErrCode + ', type=' + qaErrType
                        + ', elapsed_ms=' + qaElapsedSinceCommitMs + ', message=' + qaErrMessage + ')');
                    // PHASE O5.9.1（ユーザー指示4・5・6）: 上のQUICK_ANSWER_COMMIT_ERROR
                    // （既存・無変更）はcallGenerationが一致してさえいれば経過時間を
                    // 問わず記録されるため、「本当に直前のSHORT_ANSWER Forced Commit
                    // に起因するerrorなのか」を実ログだけから断定できないという
                    // O5.9 Auditのギャップがあった。ここでは「原因の確定」では
                    // なく、あくまで診断目的で「時間的に直前のcommitと相関して
                    // いる可能性が高いerror」だけを別イベントとして追加記録する
                    // （既存QUICK_ANSWER_COMMIT_ERROR自体は一切変更・非表示化
                    // しない。ユーザー指示6）。ウィンドウ超過分は意図的に対象外
                    // とし（OLD-ERRORケース）、transcript/PIIは一切含めず、
                    // type/codeのみを記録する（message全文は含めない）。
                    if (lastQuickAnswerCommitDiag !== null
                        && lastQuickAnswerCommitDiag.callGeneration === callGeneration
                        && qaElapsedSinceCommitMs <= QUICK_ANSWER_COMMIT_ERROR_CORRELATION_MS) {
                        pushTimelineEvent('QUICK_ANSWER_COMMIT_ERROR_CORRELATED (seq=' + lastQuickAnswerCommitDiag.seq
                            + ', elapsedSinceCommitMs=' + qaElapsedSinceCommitMs
                            + ', callGeneration=' + callGeneration
                            + ', errorType=' + qaErrType
                            + ', errorCode=' + qaErrCode + ')');
                    }
                }
            }
        }

        // ===== マイク入力レベルメーター（あなたの声がブラウザに届いているかの確認用） =====
        // これはOpenAIへ送る音声とは別に、ローカルのマイクストリームだけを
        // Web Audio APIで解析して表示する。「マイク→ブラウザ」の区間と
        // 「マイク→WebRTC/OpenAI」の区間のどちらに問題があるかを切り分けるため、
        // OpenAI側の応答を待たずにマイクを掴んだ直後から動かす。
        let micLevelRafId = null;
        // ISSUE2調査用: 直近のマイクレベル（0-100%）をログ出力用に保持するだけの
        // 変数。表示用のDOM更新（micLevelFillEl）とは別に、speech_startedログが
        // 参照できるようにする。音声そのものやスペクトルの詳細は保持しない。
        let lastMicLevelPct = null;
        // LOCAL SILENCE ASSIST — OBSERVATION PoC: 既存のマイクレベルtick()から
        // 毎フレーム呼ばれる観測専用フック。Realtime APIへは何も送信しない。
        // 例外はここで必ず握りつぶし、通話そのものには一切影響させない
        // （fail-open。呼び出し元のtick()自体を壊さないことが最優先）。
        function observeLocalSilenceTick(pct) {
            if (!localSilenceObservationEnabled) return;
            try {
                const now = performance.now();
                if (!localSilenceActive) {
                    // ターン外: 背景ノイズの参考値としてゆるやかなEMAを更新するだけ
                    // （観測目的のみ。会話制御には一切使用しない）。
                    localSilenceBackgroundFloorPct = localSilenceBackgroundFloorPct === null
                        ? pct
                        : (localSilenceBackgroundFloorPct * 0.95 + pct * 0.05);
                    return;
                }
                localSilenceSampleCount += 1;
                localSilenceSumPct += pct;
                if (localSilencePeakPct === null || pct > localSilencePeakPct) localSilencePeakPct = pct;

                if (pct >= LOCAL_SILENCE_CANDIDATE_THRESHOLD_PCT) {
                    // 「音がある」と判定
                    if (localSilenceFirstSpeechDetectedAt === null) {
                        localSilenceFirstSpeechDetectedAt = now;
                        pushTimelineEvent('LOCAL_SILENCE speech_detected (mic=' + pct + '%)');
                    }
                    if (localSilenceCandidateStartedAt !== null) {
                        // 継続中だった無音候補が、声の復帰で終了した
                        // ＝本当の発話終了ではなく自然な言い淀み（pause candidate）
                        const pauseMs = Math.round(now - localSilenceCandidateStartedAt);
                        if (pauseMs >= LOCAL_SILENCE_MIN_PAUSE_MS) {
                            localSilencePauseCandidates.push(pauseMs);
                            pushTimelineEvent('LOCAL_SILENCE pause_candidate=' + pauseMs + 'ms');
                        }
                        localSilenceCandidateStartedAt = null;
                    }
                } else if (localSilenceFirstSpeechDetectedAt !== null && localSilenceCandidateStartedAt === null) {
                    // 話し始めた後で初めて無音状態に入った瞬間だけ候補を開始する
                    localSilenceCandidateStartedAt = now;
                    pushTimelineEvent('LOCAL_SILENCE silence_started (mic=' + pct + '%)');
                }
            } catch (e) {
                // 観測機能自体の失敗で通話に影響が出ないよう、ここで握りつぶす。
                try { logEvent('[LOCAL_SILENCE] 観測中に例外（無視して継続）: ' + e.message); } catch (e2) { /* no-op */ }
            }
        }

        // LOCAL SILENCE ASSIST — OBSERVATION PoC: OpenAI側のspeech_startedを
        // 起点に、今回のターンの観測状態をリセットする。Realtime APIへは
        // 何も送信しない（fail-open）。
        function startLocalSilenceTurn() {
            if (!localSilenceObservationEnabled) return;
            try {
                localSilenceGeneration += 1;
                localSilenceActive = true;
                localSilenceFirstSpeechDetectedAt = null;
                localSilenceCandidateStartedAt = null;
                localSilencePauseCandidates = [];
                localSilencePeakPct = null;
                localSilenceSumPct = 0;
                localSilenceSampleCount = 0;
            } catch (e) {
                try { logEvent('[LOCAL_SILENCE] ターン開始処理で例外（無視して継続）: ' + e.message); } catch (e2) { /* no-op */ }
            }
        }

        // LOCAL SILENCE ASSIST — OBSERVATION PoC: OpenAI側のspeech_stoppedを
        // 起点に、「ローカルで無音候補が始まった時点」からの経過時間
        // （local_to_server_gap）を計算してログへ記録するだけ。Realtime APIへは
        // 何も送信せず、既存のsemantic_vad・Forced Commit・TURN LATENCY計測にも
        // 一切干渉しない（fail-open）。speechDurationMs（既存のspeech_stopped
        // ハンドラが既に計算済みの発話継続時間。無ければnull）はSUMMARYログの
        // speech=フィールドとして、既存TURN LATENCYと突き合わせやすいよう
        // そのまま流用する。
        function finishLocalSilenceTurn(speechDurationMs) {
            if (!localSilenceObservationEnabled) return;
            try {
                if (!localSilenceActive) return;
                const now = performance.now();
                const fmt = (v) => (v === null || v === undefined ? '?' : v + 'ms');
                let localToServerGapMs = null;
                let localSilenceStartedOffsetMs = null;
                if (localSilenceCandidateStartedAt !== null) {
                    localToServerGapMs = Math.round(now - localSilenceCandidateStartedAt);
                    if (lastSpeechStartedAt !== null) {
                        localSilenceStartedOffsetMs = Math.round(localSilenceCandidateStartedAt - lastSpeechStartedAt);
                    }
                    pushTimelineEvent('LOCAL_SILENCE silence_duration=' + localToServerGapMs + 'ms');
                }
                pushTimelineEvent('LOCAL_SILENCE server_speech_stopped (local_to_server_gap=' + fmt(localToServerGapMs) + ')');
                const avgPct = localSilenceSampleCount > 0 ? Math.round(localSilenceSumPct / localSilenceSampleCount) : null;
                logEvent('LOCAL SILENCE SUMMARY speech=' + fmt(speechDurationMs)
                    + ' local_silence_started=' + fmt(localSilenceStartedOffsetMs)
                    + ' server_speech_stopped=' + fmt(speechDurationMs)
                    + ' local_to_server_gap=' + fmt(localToServerGapMs)
                    + ' pause_candidates=' + JSON.stringify(localSilencePauseCandidates)
                    + ' peak_mic=' + (localSilencePeakPct === null ? '?' : localSilencePeakPct + '%')
                    + ' avg_mic=' + (avgPct === null ? '?' : avgPct + '%')
                    + ' background_mic=' + (localSilenceBackgroundFloorPct === null ? '?' : Math.round(localSilenceBackgroundFloorPct) + '%'));
                localSilenceActive = false;
            } catch (e) {
                try { logEvent('[LOCAL_SILENCE] ターン終了処理で例外（無視して継続）: ' + e.message); } catch (e2) { /* no-op */ }
            }
        }

        function setupMicLevelMeter(stream) {
            let audioCtx;
            try {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            } catch (e) {
                logEvent('マイクレベルメーター初期化失敗: ' + e.message);
                return;
            }
            micAudioCtx = audioCtx;
            logEvent('マイク用AudioContext作成 state=' + audioCtx.state);
            audioCtx.onstatechange = () => {
                logEvent('マイク用AudioContext state変化: ' + audioCtx.state);
                updateAudioDiagnosticsPanel();
            };
            const source = audioCtx.createMediaStreamSource(stream);
            const analyser = audioCtx.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);
            const data = new Uint8Array(analyser.frequencyBinCount);

            function tick() {
                analyser.getByteFrequencyData(data);
                const avg = data.reduce((a, b) => a + b, 0) / data.length;
                const pct = Math.min(100, Math.round((avg / 80) * 100));
                micLevelFillEl.style.width = pct + '%';
                lastMicLevelPct = pct;
                observeLocalSilenceTick(pct);
                if (localStream) micLevelRafId = requestAnimationFrame(tick);
            }
            tick();
        }
        function stopMicLevelMeter() {
            if (micLevelRafId) { cancelAnimationFrame(micLevelRafId); micLevelRafId = null; }
            micLevelFillEl.style.width = '0%';
            lastMicLevelPct = null;
            // 音声診断パネル表示用の参照のみクリアする（AudioContext自体の
            // close()は行わない＝既存の挙動を変更しない。次回通話開始時に
            // setupMicLevelMeter()が新しいAudioContextを作成しmicAudioCtxへ
            // 再設定する）。
            micAudioCtx = null;
        }

        // getUserMediaのエラーは名前(DOMException.name)ごとに原因が異なるため、
        // 「動いているように見えるが実はマイクが使えていない」状態を防ぐべく、
        // 画面上にも原因ごとに具体的な文言を出す。
        function describeGetUserMediaError(e) {
            switch (e.name) {
                case 'NotAllowedError':
                    return 'マイクの使用が許可されていません。ブラウザの設定でこのサイトのマイクを許可してから、もう一度お試しください。';
                case 'NotFoundError':
                case 'OverconstrainedError':
                    return '使用できるマイクが見つかりません。マイクが接続されているか確認してください。';
                case 'NotReadableError':
                    return 'マイクを他のアプリが使用中の可能性があります。他のアプリ・タブを閉じてから、もう一度お試しください。';
                case 'SecurityError':
                    return 'セキュリティ上の理由でマイクを使用できませんでした（HTTPS接続か確認してください）。';
                case 'AbortError':
                    return 'マイクの起動が中断されました。もう一度お試しください。';
                default:
                    return 'マイクの起動に失敗しました: ' + (e.message || e.name || e);
            }
        }

        async function startCall() {
            // 前回の通話開始が失敗し audio要素が残っている可能性がある場合の
            // 防御的クリーンアップ（通常は発生しない。cleanupConnection済みなら
            // 何も無い状態）。
            document.querySelectorAll('audio').forEach((el) => {
                if (el !== zeroWaitAudioEl) el.remove();
            });
            resetGreetingState();
            greetingTiming.callStart = performance.now();
            // PHASE3（Greeting直後 Connection Failure再調査・重要）: この通話の
            // 世代番号を確定する。startZeroWaitGreeting()やDataChannel
            // messageリスナーなど、この通話に属するすべての非同期callbackは
            // ここで確定したmyGenerationをクロージャで保持し、後から
            // isStaleCallEvent(myGeneration)で「既にfatal確定済みの同一世代」
            // または「既に新しい通話が開始された別世代」かどうかを判定する。
            callGeneration += 1;
            const myGeneration = callGeneration;
            // Phase3C.1 section9相当（重要）: Zero-Wait Greetingの再生開始と
            // Realtime接続（この後に続く一連の処理）は並行に開始する。
            // ここではawaitを挟まず、下のgetUserMedia呼び出しへ同期的に進む
            // （両方ともクリックのユーザー操作ジェスチャーの中で開始される）。
            startZeroWaitGreeting();

            // PHASE3/4（Mobile Audio Reliability・重要）: 「通話開始」ボタン押下
            // というuser gestureの中で、AI音声用の<audio>要素を先に作成し、
            // play()を1回試みておく。この時点ではsrcObjectが無いため実際に
            // 音声が出ることは無く、外部音声ファイルの追加や無音の長時間再生も
            // 行わない（play()は即座に失敗/no-opになるだけで実害なし）。
            // 目的: iOS Safari/WebKit系ブラウザで知られる「一度でもuser gesture
            // 内でplay()を試みた要素は、その後の非同期処理を経た再生（srcObject
            // 設定＋再play()）がブロックされにくい」という挙動を期待した、
            // 安全なベストエフォート施策。効果自体は実機でしか確認できないため、
            // 完了報告では「未確認（要実機確認）」として明記する。Zero-Wait
            // Greeting（zeroWaitAudioEl、既存の別要素）とは完全に別の要素であり、
            // 干渉しない。
            remoteAudioEl = document.createElement('audio');
            remoteAudioEl.autoplay = true;
            document.body.appendChild(remoteAudioEl);
            try {
                const earlyPlayPromise = remoteAudioEl.play();
                if (earlyPlayPromise && typeof earlyPlayPromise.catch === 'function') {
                    earlyPlayPromise.catch((e) => {
                        logEvent('[Mobile Audio] 事前play()試行（srcObject未設定時点、想定内・実害なし）: '
                            + (e && e.name) + ' - ' + (e && e.message));
                    });
                }
            } catch (e) {
                logEvent('[Mobile Audio] 事前play()試行で例外（想定内・実害なし）: ' + (e && e.message));
            }
            remotePlayState = 'unknown';
            userVadState = 'idle';
            responseState = 'idle';
            audioEnergyTrend = 'unknown';
            lastOutboundAudioEnergy = null;
            // FAST TURN HOTFIX 3（今回追加）: 新しい通話の開始時に、前の通話から
            // 残留したFULL TURN LATENCY TRACE状態が万一あれば破棄する（安全網）。
            resetTurnLatencyTrace('new_call_setup');
            // FAST TURN HOTFIX 4（今回追加・観測専用・安全網）: 前の通話から
            // 残留したTOOL CONTINUATION RESPONSE WATCHDOGタイマーが万一あれば
            // 破棄する（次の通話の別のcall_idに対して誤発火しないように）。
            cancelToolContinuationResponseWatchdog('new_call_setup');
            updatePlaybackRecoveryButton();

            // Mobile Real-Call Failure Investigation: 新しい通話開始のたびに
            // 前回の状態を必ずクリアする（前回のfailureSource等が次の通話へ
            // 持ち越されないようにするため）。
            iceState = 'new';
            dataChannelState = '未接続';
            greetingSource = 'none';
            audioSource = 'none';
            lastInterruptionInfo = '—';
            failureSource = '—';
            currentSetupStep = 'idle';
            lastToolLabel = '—';
            lastToolFetchOutcome = null;
            lastBargeInSpeechStartedAt = null;
            playbackPauseCount = 0;
            recentEvents = [];
            // Expected Answer Window / Silence Timeout: 新しい通話ごとに必ず
            // リセットする（前回通話の状態やタイマーを持ち越さない）。
            expectedAnswerType = 'NONE';
            if (answerWindowTimerId !== null) { clearTimeout(answerWindowTimerId); answerWindowTimerId = null; }
            answerWindowType = null;
            if (silenceTimerId !== null) { clearTimeout(silenceTimerId); silenceTimerId = null; }
            if (silenceWarningTimerId !== null) { clearTimeout(silenceWarningTimerId); silenceWarningTimerId = null; }
            silenceState = 'idle';
            responseHasFunctionCall = false;
            pendingSilenceGoodbyeHangup = false;
            // PHASE O5.6診断: 新しい通話ごとに診断専用の状態も必ずリセットする
            // （前回通話の診断値を持ち越さない。挙動には無関係・観測値のみ）。
            silenceStateEnteredAt = null;
            silenceTimerArmedAt = null;
            silenceWarningGraceArmedAt = null;
            lastResponseCreateReason = null;
            responseCreateDiagSeq = 0;
            lastResponseReasonCategoryForDiag = 'unknown';
            lastFunctionCallNameForDiag = null;
            lastFunctionCallStartedAtForDiag = null;
            // NAME Forced Commit Observation PoC: 新しい通話ごとに必ずリセット
            // する（前回通話のPoC状態を持ち越さない）。
            nameAnswerGeneration = 0;
            pocCommitSentGeneration = null;
            nameTurnNormalCompletionSeen = false;
            pocCommitSentAt = null;
            pocCommitCallGeneration = null;
            pocCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
            // Conversation Takeover Observation PoC: 新しい通話ごとに必ず
            // リセットする（前回通話のPoC状態を持ち越さない。NAME PoCとは
            // 独立した状態のため個別にリセットする）。
            takeoverSpeechEpisodeId = 0;
            if (takeoverTimerId !== null) { clearTimeout(takeoverTimerId); takeoverTimerId = null; }
            takeoverCommitSentEpisode = null;
            takeoverTurnNormalCompletionSeen = false;
            takeoverCommitSentAt = null;
            takeoverCommitCallGeneration = null;
            takeoverCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
            // Short Choice 3-Second Turn: 新しい通話ごとに必ずリセットする
            // （前回通話の状態を持ち越さない。NAME PoC/Takeover PoCとは独立した
            // 状態のため個別にリセットする）。
            shortChoiceAnswerGeneration = 0;
            shortChoiceCommitSentGeneration = null;
            shortChoiceTurnNormalCompletionSeen = false;
            shortAnswerCommitSentAt = null;
            shortAnswerCommitCallGeneration = null;
            shortAnswerCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
            // PHONE Forced Commit: 新しい通話ごとに必ずリセットする（前回通話の
            // 状態を持ち越さない。NAME PoC/Takeover PoC/Short Choiceとは独立した
            // 状態のため個別にリセットする）。
            phoneAnswerGeneration = 0;
            phoneCommitSentGeneration = null;
            phoneTurnNormalCompletionSeen = false;
            phoneCommitSentAt = null;
            phoneCommitCallGeneration = null;
            phoneCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
            // SHORT_ANSWER Forced Commit: 新しい通話ごとに必ずリセットする
            // （前回通話の状態を持ち越さない。NAME PoC/Takeover PoC/Short
            // Choice/PHONEとは独立した状態のため個別にリセットする）。
            quickAnswerGeneration = 0;
            quickAnswerCommitSentGeneration = null;
            quickAnswerTurnNormalCompletionSeen = false;
            quickAnswerCommitSentAt = null;
            quickAnswerCommitCallGeneration = null;
            quickAnswerCommitPendingEvents = { committed: false, item: false, responseCreated: false, aiAudioStarted: false };
            // PHASE O5.9.1: Forced Commit Error Correlation用の診断専用stateも、
            // 新しい通話ごとに必ずリセットする（前回通話のseq/相関情報を
            // 持ち越さない）。
            quickAnswerCommitDiagSeq = 0;
            lastQuickAnswerCommitDiag = null;
            // PHASE O5.8: SHORT_ANSWER Finalization Grace timerも、新しい通話
            // ごとに必ずリセットする（前回通話のタイマーを持ち越さない。万一
            // 前回通話終了時にarmされたまま残っていた場合の防御的clearTimeoutも
            // 兼ねる）。
            if (quickAnswerFinalizeTimerId !== null) { clearTimeout(quickAnswerFinalizeTimerId); quickAnswerFinalizeTimerId = null; }
            quickAnswerFinalizeArmedAt = null;
            quickAnswerFinalizeGeneration = null;
            // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加）: USER_TURN_
            // 3S_FALLBACKの状態も、新しい通話ごとに必ずリセットする（前回通話の
            // タイマー・世代・完了フラグを持ち越さない。万一前回通話終了時に
            // armされたまま残っていた場合の防御的clearTimeoutも兼ねる）。
            if (userTurnFallbackTimerId !== null) { clearTimeout(userTurnFallbackTimerId); userTurnFallbackTimerId = null; }
            userTurnFallbackGeneration = 0;
            userTurnFallbackArmedAt = null;
            userTurnFallbackArmedForGeneration = null;
            userTurnFallbackCommitSentGeneration = null;
            userTurnFallbackNormalCompletionSeen = false;
            userTurnFallbackCommitSentAt = null;
            userTurnFallbackCommitCallGeneration = null;
            // AI SPEAKING PROTECTION（今回追加）: マイクのミュート状態・安全網
            // タイマーも、新しい通話ごとに必ずリセットする（前回通話のミュート
            // 状態を持ち越さない。track自体はこの後getUserMediaで新規取得
            // するため、ここでは状態変数のみリセットすれば十分）。
            if (aiSpeakingProtectionSafetyTimerId !== null) { clearTimeout(aiSpeakingProtectionSafetyTimerId); aiSpeakingProtectionSafetyTimerId = null; }
            aiSpeakingProtected = false;
            // FAST TURN HOTFIX（FIRST ANSWER MUST COUNT・今回追加）: 前回通話の
            // ミュート解除タイムスタンプを持ち越さない（診断専用値のリセット）。
            lastAiSpeakingEndAt = null;
            // PHASE20/22: 新しい通話を開始するタイミングでのみ、前回のFAILURE
            // SNAPSHOTと猶予タイマーをクリアする（cleanupConnection()側では
            // 意図的にクリアしない＝失敗直後もsnapshotを画面に残すため）。
            failureSnapshot = null;
            updateFailureSnapshotPanel();
            updateOnScreenFailureInfo();
            if (disconnectGraceTimer) { clearTimeout(disconnectGraceTimer); disconnectGraceTimer = null; }
            pushTimelineEvent('startCall() 開始');

            startBtn.disabled = true;
            hideErrorBanner();
            statusText.textContent = '接続しています…';
            bigMic.classList.remove('error');
            bigMic.classList.add('connecting');
            eventLogEl.textContent = '';
            latencySamples.length = 0;
            latestLatencyEl.textContent = '—';
            avgLatencyEl.textContent = '—';
            latencyCountEl.textContent = '0';
            setStatus(stMicTrackEl, 'なし', null);
            setStatus(stWebrtcEl, 'new', null);
            setStatus(stSessionEl, '未接続', null);
            setStatus(stUserSpeakEl, '待機', null);
            setStatus(stAiSpeakEl, '待機', null);
            // Phase2.6: 新しい通話の開始にあたり、前回の計測状態をクリアする
            // （実際の計測開始はWebRTC接続完了時点。下のconnectionstatechange参照）。
            callStartedAt = null;
            callEndedAt = null;
            if (elapsedTimerId) { clearInterval(elapsedTimerId); elapsedTimerId = null; }
            resetUsageObservation();
            resetToolCallState();
            // PHASE O5.5: Speak-Then-Work安全網音声を前回通話の再生位置から
            // 持ち越さない（Zero-Waitのresetとは独立。Greeting関連コードには
            // 一切触れない）。
            resetAckFallbackCallState();
            // Outbound AI Phase 4B: 新しい通話の開始にあたり、前回の通話の
            // session状態を必ずクリアする（別通話への流用を防ぐ）。
            // voiceSessionIdはこの直後のfetchSession()成功時に改めて設定する。
            voiceSessionId = null;
            currentCandidateRef = null;

            // 重要: クリック直後の「ユーザー操作」が有効なうちに、他の非同期処理
            // (トークン取得のfetch等)より先にgetUserMediaを呼び出す。
            // 先にawaitを挟んでからgetUserMediaを呼ぶと、ブラウザによっては
            // （特にiOS Safari）ユーザー操作から時間が空きすぎたと判断され、
            // マイク許可ダイアログ自体が出ないまま静かに失敗することがある。
            try {
                // Phase3C.1 section18で導入したecho対策（echoCancellation/
                // noiseSuppression/autoGainControl）を、次フェーズ仕様書
                // （騒音環境調査）STEP Aに基づき、Zero-Wait Greeting対象かどうかに
                // 関わらず全店舗で要求するよう変更。
                // 経緯: 従来はZero-Wait対象店舗（AIスタッフ設定を保存済みの店舗）に
                // のみこれらの制約を要求しており、それ以外の店舗はブラウザの初期値
                // 任せ（{ audio: true }）だった。この3つの制約はエコー対策として
                // 導入されたものだが、noiseSuppression/autoGainControlは一般的な
                // 環境音対策としても有効なはずであり、これをZero-Wait対象かどうかで
                // 出し分ける技術的な理由はない（両者は無関係な設定軸）。また、
                // Phase5B.2の実機テスト（環境音の誤検知＝VOICE-001）はZero-Wait対象
                // 店舗（＝これらの制約を要求済み）でも発生しており、要求するだけでは
                // 十分でない可能性が高いため、まずは全店舗で同じ土俵に揃えた上で、
                // 実際にブラウザが何を採用したかを下記のgetSupportedConstraints()/
                // getSettings()で確認できるようにする。
                // 重要（STEP Cとの切り分け）: これはturn_detection（VAD）の設定
                // 変更ではなく、あくまでgetUserMedia時点のマイク入力処理の要求
                // 内容の変更に留まる。semantic_vad/server_vadの選択やeagerness等の
                // パラメータには一切触れていない。
                // ?noagc=1（debug/test専用、上部のnoAgcMode参照）が付いている場合のみ
                // autoGainControlをfalseで比較要求する。それ以外は常にtrue
                // （Production defaultはこれまでと完全に同じ）。
                const requestedAutoGainControl = !noAgcMode;
                const micConstraints = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: requestedAutoGainControl } };
                localStream = await navigator.mediaDevices.getUserMedia(micConstraints);
            } catch (e) {
                const friendly = describeGetUserMediaError(e);
                logEvent('getUserMediaエラー: ' + e.name + ' - ' + e.message);
                failureSource = 'get_user_media';
                pushTimelineEvent('getUserMedia失敗 (' + e.name + ')');
                setStatus(stMicPermEl, '拒否/失敗: ' + e.name, 'bad');
                showErrorBanner(friendly);
                statusText.textContent = 'マイクを取得できませんでした';
                subStatusText.textContent = friendly;
                bigMic.classList.remove('connecting');
                bigMic.classList.add('error');
                startBtn.disabled = false;
                // getUserMedia失敗時は、事前作成した音声要素（PHASE3/4）を
                // 残さないようにする（次回startCall()呼び出し時の重複防止）。
                if (remoteAudioEl) {
                    pushTimelineEvent('REALTIME_REMOVE_REQUESTED (source=getUserMedia失敗)');
                    try { remoteAudioEl.remove(); } catch (cleanupErr) {} remoteAudioEl = null;
                }
                updatePlaybackRecoveryButton();
                return;
            }

            setStatus(stMicPermEl, '許可済み', 'ok');
            const audioTracks = localStream.getAudioTracks();
            if (audioTracks.length === 0) {
                logEvent('警告: getUserMediaは成功しましたが音声トラックが0件です');
                setStatus(stMicTrackEl, 'トラック0件', 'bad');
            } else {
                audioTracks.forEach((track, i) => {
                    logEvent(`マイクTrack[${i}]: label="${track.label}" enabled=${track.enabled} `
                        + `readyState=${track.readyState} muted=${track.muted}`);
                    track.addEventListener('ended', () => {
                        logEvent(`マイクTrack[${i}]がended状態になりました (readyState=${track.readyState})`);
                        setStatus(stMicTrackEl, 'ended', 'bad');
                    });
                    // 次フェーズ仕様書（通話切断原因調査）: mute/unmuteイベントには
                    // 従来リスナーが存在しなかった。OSレベルでのマイクミュート・
                    // 一時的な入力断絶などをここで確認できるようにする
                    // （音声内容そのものは記録しない）。
                    track.addEventListener('mute', () => {
                        logEvent(`マイクTrack[${i}]がmuteイベントを発火しました (readyState=${track.readyState})`);
                    });
                    track.addEventListener('unmute', () => {
                        logEvent(`マイクTrack[${i}]がunmuteイベントを発火しました (readyState=${track.readyState})`);
                    });
                });
                setStatus(stMicTrackEl, 'live', 'ok');

                // 次フェーズ仕様書（騒音環境調査）STEP A: 「ブラウザへ何を要求したか」
                // と「実際に何が採用されたか」をイベントログで確認できるようにする。
                // 個人の音声内容・電話番号・メール・CustomerMemory・医療情報等は
                // 一切含めない（getSettings()が返す技術的な処理設定値のみを対象とし、
                // 対象キーも明示的にホワイトリストで絞る。deviceId/groupIdのような
                // 機器識別情報もここでは記録しない）。既存のeventLogEl自体が
                // ?debug=1を付けた場合のみ表示される設計（本ファイル冒頭のdebugMode
                // 参照）のため、本番のお客様向け画面にこの情報が常時大量表示される
                // ことはない。
                try {
                    const supported = (navigator.mediaDevices.getSupportedConstraints
                        && navigator.mediaDevices.getSupportedConstraints()) || {};
                    const NOISE_RELEVANT_KEYS = ['echoCancellation', 'noiseSuppression', 'autoGainControl', 'sampleRate', 'channelCount'];
                    const supportedSummary = NOISE_RELEVANT_KEYS
                        .filter((k) => k in supported)
                        .map((k) => k + '=' + supported[k])
                        .join(', ');
                    logEvent('getSupportedConstraints(): ' + (supportedSummary || '(取得できませんでした)'));

                    audioTracks.forEach((track, i) => {
                        const settings = (track.getSettings && track.getSettings()) || {};
                        const actualSummary = NOISE_RELEVANT_KEYS
                            .filter((k) => k in settings)
                            .map((k) => k + '=' + settings[k])
                            .join(', ');
                        logEvent(`マイクTrack[${i}] 要求した制約: echoCancellation=true, noiseSuppression=true, `
                            + `autoGainControl=${requestedAutoGainControl}` + (noAgcMode ? '（?noagc=1による比較モード）' : ''));
                        logEvent(`マイクTrack[${i}] 実際の採用値(getSettings): ` + (actualSummary || '(取得できませんでした)'));
                    });
                } catch (diagErr) {
                    logEvent('マイク制約の診断ログ取得に失敗しました: ' + (diagErr && diagErr.message));
                }
            }
            setupMicLevelMeter(localStream);

            try {
                // PHASE9: 接続失敗時に「どの準備ステップで失敗したか」を分類する
                // ための現在地マーカー。実際に送信する内容やリクエスト自体は
                // 一切変更しない（ローカル表示専用）。
                currentSetupStep = 'token_request';
                logEvent('ephemeralトークンを取得中…');
                const session = await fetchSession();
                greetingTiming.tokenFetched = performance.now();
                pageShopName.textContent = session.shop_name + ' - Realtime音声AI';
                logEvent('トークン取得OK（model=' + session.model + '）');
                // Outbound AI Phase 4B: この通話のCustomer Context用session識別子。
                voiceSessionId = session.voice_session_id || null;

                currentSetupStep = 'peer_connection_setup';
                pc = new RTCPeerConnection();
                // PHASE11（cleanup競合対策）: pc.onconnectionstatechange等の
                // イベントハンドラはouter scopeの`pc`変数（let、cleanupConnection()で
                // null化・次の通話開始で新インスタンスに再代入される）を参照する
                // クロージャになっている。close()後や次の通話開始後に、この古い
                // RTCPeerConnectionインスタンスから遅延してイベントが発火した場合、
                // ハンドラ内の`pc`は既にnull（TypeErrorの危険）か、あるいは既に
                // 次の通話の新しいpcを指してしまっている可能性がある（＝別の通話の
                // 状態を誤って参照・endCallしてしまう危険）。このインスタンス自身を
                // thisPcとして固定し、outer scopeの`pc`と一致する場合のみ処理する
                // ことで、この種のstale closureレースを防ぐ。
                const thisPc = pc;

                pc.ontrack = (ev) => {
                    logEvent('リモート音声トラック受信');
                    remoteTrackReceived = true;
                    const remoteTrack = ev.track;
                    if (remoteTrack) {
                        remoteTrackMuted = remoteTrack.muted;
                        remoteTrackReadyState = remoteTrack.readyState;
                        logEvent('リモート音声Track: readyState=' + remoteTrack.readyState
                            + ' muted=' + remoteTrack.muted + ' enabled=' + remoteTrack.enabled);
                        // PHASE1/4: 通話途中でリモートTrackがended/mutedになっていないかを
                        // ローカルTrackと同様に観測できるようにする（対処は行わない）。
                        remoteTrack.addEventListener('ended', () => {
                            // PHASE11: 前の通話の（既にcleanupされた）remote trackから
                            // 遅延してendedが届いた場合、新しい通話の状態を誤って
                            // 汚染しないよう無視する。
                            if (typeof pc !== 'undefined' && typeof thisPc !== 'undefined' && pc !== thisPc) return;
                            remoteTrackReadyState = 'ended';
                            logEvent('リモート音声Trackがended状態になりました');
                            // PHASE9: 通話がまだ続いている（endCall未呼び出し）のに
                            // リモートtrackがended化した場合の失敗要因分類。
                            if (!ended && failureSource === '—') failureSource = 'remote_track_ended';
                            updateAudioDiagnosticsPanel();
                            pushTimelineEvent('remote track ended');
                        });
                        remoteTrack.addEventListener('mute', () => {
                            if (typeof pc !== 'undefined' && typeof thisPc !== 'undefined' && pc !== thisPc) return;
                            remoteTrackMuted = true;
                            logEvent('リモート音声Trackがmuteイベントを発火しました');
                            updateAudioDiagnosticsPanel();
                            pushTimelineEvent('remote track mute');
                        });
                        remoteTrack.addEventListener('unmute', () => {
                            if (typeof pc !== 'undefined' && typeof thisPc !== 'undefined' && pc !== thisPc) return;
                            remoteTrackMuted = false;
                            logEvent('リモート音声Trackがunmuteイベントを発火しました');
                            updateAudioDiagnosticsPanel();
                            pushTimelineEvent('remote track unmute');
                        });
                    }

                    // PHASE2（重要）: ev.streams[0]は常に存在する前提にしない。
                    // WebRTC仕様上、'track'イベントのstreamsは空配列になり得る。
                    // 空の場合はev.trackから自前でMediaStreamを構築する
                    // （音声トラック自体は正常に受信できているケースを、
                    // streams[0]依存だけで「未受信」に見せてしまわないため）。
                    const remoteMediaStream = (ev.streams && ev.streams[0]) ? ev.streams[0]
                        : (remoteTrack ? new MediaStream([remoteTrack]) : null);
                    if (!ev.streams || !ev.streams[0]) {
                        logEvent('[Mobile Audio] ev.streams[0]が空でした。'
                            + (remoteMediaStream ? 'ev.trackから自前でMediaStreamを構築しました。' : 'remoteTrack自体も取得できませんでした。'));
                    }

                    // PHASE3/4（重要）: startCall()内でuser gesture中に事前作成済みの
                    // <audio>要素（remoteAudioEl）をここで再利用する。新規要素を
                    // 作り直さないことで、「一度でもuser gesture内でplay()を試みた
                    // 要素」という状態を保つ（iOS Safari/WebKit系ブラウザで知られる
                    // 再生ロック解除の挙動を期待するもの。実機効果は未確認）。
                    // 想定外の経路でremoteAudioElが存在しない場合のみ新規作成する。
                    const audioEl = remoteAudioEl || document.createElement('audio');
                    if (!remoteAudioEl) {
                        audioEl.autoplay = true;
                        document.body.appendChild(audioEl);
                    }
                    audioEl.srcObject = remoteMediaStream;
                    remoteAudioEl = audioEl;
                    remotePlayState = 'unknown';
                    updatePlaybackRecoveryButton();

                    // PHASE4/5（最重要）: これまでaudioEl.autoplay=trueに任せるだけで、
                    // play()を明示的に呼んでいなかった。autoplay属性任せの場合、
                    // ブラウザが自動再生を許可しなかった（特にスマホのautoplay
                    // policy）としても、その失敗をJS側で検知・ログする手段が
                    // 一切存在しなかった＝「AIの声が聞こえないことがある」という
                    // 報告の有力な説明候補になり得るため、明示的にplay()を呼び、
                    // 返却されるPromiseの成功/失敗を必ず捕捉してログする。
                    // 通常UXへの見た目の変更は無い（autoplay=trueとほぼ同じ動作を
                    // 明示的に行っているだけ）。
                    try {
                        pushTimelineEvent('REALTIME_AUDIO: play requested');
                        const playPromise = audioEl.play();
                        if (playPromise && typeof playPromise.then === 'function') {
                            playPromise.then(() => {
                                remotePlayState = 'playing';
                                logEvent('リモート音声 play() 成功');
                                updateAudioDiagnosticsPanel();
                                updatePlaybackRecoveryButton();
                            }).catch((e) => {
                                remotePlayState = 'blocked';
                                logEvent('リモート音声 play() 失敗（自動再生制限の可能性）: '
                                    + (e && e.name) + ' - ' + (e && e.message));
                                updateAudioDiagnosticsPanel();
                                updatePlaybackRecoveryButton();
                            });
                        } else {
                            // Promiseを返さない古い実装のブラウザでは、成功したものとして扱う
                            // （それ以上の判定手段が無いため）。
                            remotePlayState = 'playing';
                        }
                    } catch (e) {
                        remotePlayState = 'blocked';
                        logEvent('リモート音声 play() 呼び出しで例外: ' + (e && e.message));
                        updatePlaybackRecoveryButton();
                    }
                    // 再調査（第2ラウンド・最重要）: Realtime側のremoteAudioEl
                    // （このpc.ontrack発火のたびに再利用/再アタッチされる要素）の
                    // ライフサイクルイベントを、Zero-Wait側と同じ命名規則
                    // （REALTIME_AUDIO:）でタイムラインに記録する。既存の
                    // playbackPauseCount・'audioEl pause'記録（PHASE8由来）は
                    // そのまま維持し、今回は不足していたwaiting/stalled/suspend/
                    // ended/abort/errorを追加する。「和風デモの」直後の「ブチッ」が
                    // Realtime側のtrack起因（waiting/stalled）かどうかを
                    // 区別できるようにする。UI・ビジネスロジックには影響しない。
                    audioEl.addEventListener('playing', () => {
                        remotePlayState = 'playing';
                        pushTimelineEvent('REALTIME_AUDIO: playing (currentTime=' + (audioEl.currentTime || 0).toFixed(2) + 's)');
                        // FAST TURN HOTFIX 3（今回追加・観測専用）: <audio>要素が
                        // 実際に物理再生を開始した、この最も確実な物的証拠の時点を
                        // TURN_AUDIO_PLAYBACK_STARTとして記録する（output_audio_
                        // buffer.startedというサーバーイベントではなく、実際の
                        // 再生開始そのものを採用する理由は本ファイル冒頭の
                        // FULL TURN LATENCY TRACEコメント参照）。
                        pushTurnLatencyTrace('TURN_AUDIO_PLAYBACK_START');
                        // FAST TURN 3.6B（Tool Continuation Proof）: dc.send()が
                        // 例外を投げなかったことではなく、実際に<audio>要素が
                        // 'playing'（＝ブラウザが実際に音声デコード・再生を
                        // 開始したこと）に到達したことをもってT9とする。これが
                        // Tool継続チェーンの「実際に音声が再生された」という
                        // 最終的な物的証拠にあたる。
                        pushToolContinuationTrace('T9_CONTINUATION_AUDIO_PLAYING (currentTime=' + (audioEl.currentTime || 0).toFixed(2) + 's)');
                        updateAudioDiagnosticsPanel();
                        updatePlaybackRecoveryButton();
                    });
                    audioEl.addEventListener('pause', () => {
                        if (remotePlayState !== 'blocked') remotePlayState = 'paused';
                        // PHASE8（「ブチブチ」調査）: audio要素側で'pause'が何度
                        // 発生したかをカウントするだけの指標。ブラウザがこれを
                        // 自動的に発火させるのは、リモートtrackが実際に切れて
                        // いる/ミュートされた場合や、要素自体が一時停止扱いに
                        // なった場合などで、原因はこの時点では断定しない
                        // （remote trackのmute/unmute・readyStateと合わせて
                        // 実機ログを確認する材料とする）。
                        playbackPauseCount++;
                        updateAudioDiagnosticsPanel();
                        pushTimelineEvent('audioEl pause (累計' + playbackPauseCount + '回, remoteTrack muted=' + remoteTrackMuted + ')');
                        pushTimelineEvent('REALTIME_AUDIO: pause (currentTime=' + (audioEl.currentTime || 0).toFixed(2) + 's)');
                    });
                    ['waiting', 'stalled', 'suspend', 'ended', 'abort', 'error'].forEach((evt) => {
                        audioEl.addEventListener(evt, () => {
                            if (evt === 'waiting' || evt === 'stalled') remotePlayState = evt;
                            pushTimelineEvent('REALTIME_AUDIO: ' + evt
                                + ' (currentTime=' + (audioEl.currentTime || 0).toFixed(2) + 's)');
                            updateAudioDiagnosticsPanel();
                        });
                    });

                    setupLatencyMeter(remoteMediaStream);
                    updateAudioDiagnosticsPanel();
                };

                localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));

                // PHASE1: addTrack後、実際にRTCPeerConnection側にaudio用の
                // senderが存在するかを確認する（存在しなければWebRTC送信経路
                // 自体が成立していないことになる）。
                const audioSenders = pc.getSenders().filter((s) => s.track && s.track.kind === 'audio');
                logEvent('RTCPeerConnection sender確認: 全sender数=' + pc.getSenders().length
                    + ' / audio sender数=' + audioSenders.length);
                // PHASE5: senderが保持しているtrackが、実際にgetUserMediaで取得した
                // localTrackと同一オブジェクトであることを確認する（sender.track
                // が別のtrackにすり替わっている等の異常が無いかの確認のみ。対処は
                // 行わない）。
                const localAudioTrack = localStream.getAudioTracks()[0] || null;
                if (localAudioTrack) {
                    const identityMatch = audioSenders.some((s) => s.track === localAudioTrack);
                    logEvent('sender.track === localStreamのaudio track: ' + identityMatch);
                }

                // PHASE3: debug=1のときだけ、getStats()からaudio outbound RTPの
                // 安全な統計（packetsSent等）を定期取得する。PIIは含まれない
                // （技術的なカウンタ値のみ）。通常Production利用者には
                // 追加の処理・ログを一切発生させない。
                if (debugMode) {
                    startStatsPolling();
                }

                dc = pc.createDataChannel('oai-events');
                dataChannelState = 'connecting';
                // PHASE11: thisPcと同じ理由で、古い/置き換わったdcインスタンスからの
                // 遅延イベントを無視するためのガードに使う。
                const thisDc = dc;
                // PHASE1/3（Greeting直後 Connection Failure再調査・最重要）:
                // response.done/speech_started/speech_stopped/response.created等、
                // DataChannel経由で届くすべてのイベントの唯一の入口をここで
                // 一元的にガードする。fatal failure確定後（ended=true）や、
                // 既に新しい通話が開始された後（callGeneration不一致）に
                // 遅延して届いたメッセージは、handleDataChannelEvent自体を
                // 一切呼ばない（=中でsubStatusText等を書き戻すこともなく、
                // sendResponseCreate()経由でdc.send()することも無い）。
                dc.addEventListener('message', (ev) => {
                    if (isStaleCallEvent(myGeneration)) {
                        pushTimelineEvent('DataChannel message: stale event, 処理skip (generation=' + myGeneration + ')');
                        return;
                    }
                    handleDataChannelEvent(ev.data);
                });
                dc.addEventListener('open', () => {
                    if (dc !== thisDc) return;
                    greetingTiming.dcOpen = performance.now();
                    logEvent('データチャネル接続 (DataChannel open, readyState=' + dc.readyState + ')');
                    dataChannelState = 'open';
                    pushTimelineEvent('DataChannel open');
                    updateAudioDiagnosticsPanel();
                });
                // 次フェーズ仕様書（通話切断原因調査）: DataChannelのclose/errorには
                // 従来イベントリスナーが存在せず、通話が途切れた際にDataChannel側で
                // 何が起きたのか（正常close/異常error）を一切確認できなかった。
                // 個人情報は含まれないため、readyStateとerror detailのみ記録する。
                // 注: RTCDataChannelには仕様上"closing"というイベントは存在しない
                // （'closing'はreadyStateの一状態であり専用イベントは無い）ため、
                // 存在しないイベントを追加することはしない。
                dc.addEventListener('close', () => {
                    // PHASE11: 既にcleanupされた/別の通話に置き換わった古いdc
                    // インスタンスからの遅延イベントは無視する。
                    if (dc !== thisDc) return;
                    logEvent('DataChannel close (readyState=' + dc.readyState + ')');
                    dataChannelState = 'closed';
                    pushTimelineEvent('DataChannel close');
                    // PHASE9: 通話がまだ終了扱いになっていない状態でDataChannelが
                    // 閉じた場合、失敗要因の候補として記録する（対処はしない）。
                    if (!ended && failureSource === '—') failureSource = 'datachannel_closed';
                    updateAudioDiagnosticsPanel();
                });
                dc.addEventListener('error', (ev) => {
                    if (dc !== thisDc) return;
                    const err = ev && ev.error;
                    const detail = err
                        ? (err.errorDetail || err.message || err.name || String(err))
                        : '(詳細不明)';
                    logEvent('DataChannel error: ' + detail + ' (readyState=' + dc.readyState + ')');
                    dataChannelState = 'error';
                    pushTimelineEvent('DataChannel error');
                    if (!ended && failureSource === '—') failureSource = 'datachannel_closed';
                    updateAudioDiagnosticsPanel();
                });

                pc.onconnectionstatechange = () => {
                    // PHASE11: 既にcleanupされた/別の通話に置き換わった古いpc
                    // インスタンスからの遅延イベントは無視する。
                    if (pc !== thisPc) return;
                    logEvent('接続状態(connectionState): ' + pc.connectionState);
                    pushTimelineEvent('PC connectionState=' + pc.connectionState);
                    // O5.6診断: connectionStateが変化するたびにPC_STATE_DIAGを残す
                    // （挙動には無関係。特にdisconnected/failed/closedを確実に残す）。
                    pushPcStateDiag('onconnectionstatechange:' + pc.connectionState);
                    setStatus(stWebrtcEl, pc.connectionState, pc.connectionState === 'connected' ? 'ok' : null);
                    if (pc.connectionState === 'connected') {
                        // PHASE10: disconnectedからの自然回復を含む。猶予タイマーが
                        // 残っていれば解除する（回復したのに誤ってfailure扱いに
                        // しないため）。
                        if (disconnectGraceTimer) {
                            clearTimeout(disconnectGraceTimer);
                            disconnectGraceTimer = null;
                            pushTimelineEvent('disconnectedから猶予期間内に回復（通話継続）');
                            logEvent('[PHASE10] connectionStateがdisconnectedから回復しました。通話を継続します');
                        }
                        bigMic.classList.remove('connecting');
                        bigMic.classList.add('connected');
                        statusText.textContent = '通話中';
                        subStatusText.textContent = '待機中（お話しください）';
                        startBtn.style.display = 'none';
                        endBtn.style.display = 'inline-block';
                        endBtn.disabled = false;
                        // Phase2.6: 実際にWebRTCが繋がり会話が始まりうるタイミングを
                        // 「通話開始」として計測開始する（ボタン押下時点ではまだ
                        // usageが発生しないため）。
                        if (!callStartedAt) {
                            callStartedAt = Date.now();
                            callEndedAt = null;
                            resetUsageObservation();
                            if (elapsedTimerId) clearInterval(elapsedTimerId);
                            elapsedTimerId = setInterval(updateElapsedDisplay, 1000);
                            updateElapsedDisplay();
                        }
                    } else if (pc.connectionState === 'disconnected') {
                        // PHASE10（最重要）: 'disconnected'は一時的な状態であり、
                        // 'failed'/'closed'と同一に即終了扱いしない。短い猶予期間
                        // （PC_DISCONNECT_GRACE_MS、根拠は上の変数宣言コメント参照）
                        // の間だけ様子を見て、その間に'connected'へ戻れば通話を継続する。
                        setStatus(stWebrtcEl, pc.connectionState, 'warn');
                        pushTimelineEvent('PC disconnected（' + (PC_DISCONNECT_GRACE_MS / 1000) + '秒間、回復を待ちます）');
                        if (!disconnectGraceTimer && !ended) {
                            disconnectGraceTimer = setTimeout(() => {
                                disconnectGraceTimer = null;
                                // PHASE11: このタイマー自体もthisPc固定のクロージャに
                                // している。猶予期間中にcleanupされた/別の通話に
                                // 置き換わった場合は何もしない。
                                if (ended || !pc || pc !== thisPc) return;
                                if (pc.connectionState !== 'connected') {
                                    if (failureSource === '—') {
                                        failureSource = (pc.iceConnectionState === 'failed') ? 'ice_failed' : 'peer_connection_failed';
                                    }
                                    updateAudioDiagnosticsPanel();
                                    pushTimelineEvent('disconnectedが猶予期間内に回復しなかったため終了します(state=' + pc.connectionState + ')');
                                    // O5.6診断: 猶予期間満了時点（endCall直前）のPC/DC/mic状態を確実に残す。
                                    pushPcStateDiag('disconnect_grace_expired:' + pc.connectionState);
                                    const reason = '通話が切断されました。電波状況の良い場所でもう一度おかけ直しください。';
                                    captureFailureSnapshot('pc.onconnectionstatechange:disconnected(grace_expired,state=' + pc.connectionState + ')', reason);
                                    if (!ended) endCall(reason, 'pc.onconnectionstatechange:disconnected(grace_expired,state=' + pc.connectionState + ')');
                                }
                            }, PC_DISCONNECT_GRACE_MS);
                        }
                    } else if (['failed', 'closed'].includes(pc.connectionState)) {
                        // 'failed'/'closed'は回復の見込みが無いため、従来通り猶予なしで
                        // 即座に終了する。
                        setStatus(stWebrtcEl, pc.connectionState, 'bad');
                        if (disconnectGraceTimer) { clearTimeout(disconnectGraceTimer); disconnectGraceTimer = null; }
                        // PHASE9: 接続済みの通話が切断された場合の失敗要因分類。
                        // ICE自体がfailedならice_failed、それ以外はpeer_connection_failed
                        // として記録する（最初に確定した理由を優先し、上書きしない）。
                        if (failureSource === '—') {
                            failureSource = (pc.iceConnectionState === 'failed') ? 'ice_failed' : 'peer_connection_failed';
                        }
                        updateAudioDiagnosticsPanel();
                        // O5.6診断: failed/closedを確実に残す（endCall直前のPC/DC/mic状態）。
                        pushPcStateDiag('failed_or_closed:' + pc.connectionState);
                        const reason = '通話が切断されました。電波状況の良い場所でもう一度おかけ直しください。';
                        if (!ended) {
                            captureFailureSnapshot('pc.onconnectionstatechange:' + pc.connectionState, reason);
                            endCall(reason, 'pc.onconnectionstatechange:' + pc.connectionState);
                        }
                    }
                };
                pc.oniceconnectionstatechange = () => {
                    if (pc !== thisPc) return;
                    logEvent('iceConnectionState: ' + pc.iceConnectionState);
                    iceState = pc.iceConnectionState;
                    pushTimelineEvent('ICE ' + iceState);
                    updateAudioDiagnosticsPanel();
                };
                pc.onicegatheringstatechange = () => logEvent('iceGatheringState: ' + pc.iceGatheringState);
                pc.onsignalingstatechange = () => logEvent('signalingState: ' + pc.signalingState);

                currentSetupStep = 'sdp_exchange';
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                const sdpResponse = await fetch('https://api.openai.com/v1/realtime/calls', {
                    method: 'POST',
                    body: offer.sdp,
                    headers: {
                        'Authorization': 'Bearer ' + session.client_secret,
                        'Content-Type': 'application/sdp',
                    },
                });
                if (!sdpResponse.ok) {
                    logEvent('接続確立エラー (HTTP ' + sdpResponse.status + ')');
                    throw new Error('接続の確立に失敗しました');
                }
                const answerSdp = await sdpResponse.text();
                currentSetupStep = 'sdp_remote_description';
                await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
                greetingTiming.sdpAnswerSet = performance.now();
                currentSetupStep = 'connected';

                maxDurationTimer = setTimeout(() => {
                    endCall('通話時間が上限（30分）に達したため終了しました', 'maxDurationTimer');
                }, MAX_CALL_DURATION_MS);
            } catch (e) {
                logEvent('エラー: ' + e.message);
                // PHASE9: 接続失敗UIに到達した際の要因を、直前の準備ステップから
                // 分類して記録する（実機ログでの原因切り分け用。対処はしない）。
                if (failureSource === '—') failureSource = currentSetupStep || 'unknown';
                pushTimelineEvent('接続失敗 (failureSource=' + failureSource + ')');
                // PHASE20/21（最重要）: cleanupConnection()でpc/dc/track等が
                // null化される前に、この時点の状態を保存する（このcatchが
                // 万一Greeting開始後のタイミングで発火した場合でも、原因調査の
                // 手がかりを失わないようにする）。
                const catchReason = (e && e.aiReceptionDisabled)
                    ? '現在、こちらの音声AI受付はご利用いただけません。'
                    : '接続に失敗しました。しばらくしてから、もう一度お試しください。';
                captureFailureSnapshot('startCall:setupFailed(step=' + (currentSetupStep || 'unknown') + ',msg=' + e.message + ')', catchReason);
                if (e && e.aiReceptionDisabled) {
                    // Human Handoff基盤: 技術的な接続失敗ではなく、店舗側がAI電話受付を
                    // OFFにしている状態であることを明確に区別して案内する
                    // （「接続に失敗しました」という誤解を招く表示にしない）。
                    showErrorBanner(catchReason);
                    statusText.textContent = 'AI受付は現在休止中です';
                    subStatusText.textContent = '';
                } else {
                    // PHASE20（任意・ユーザー事前承認済み）: 「お電話でお問い合わせ
                    // ください」は、本製品自体がAI電話受付であるため循環的な案内に
                    // なってしまっていた。大きなUI変更を伴わない文言修正のみ行う。
                    showErrorBanner(catchReason);
                    statusText.textContent = '接続に失敗しました';
                    subStatusText.textContent = 'もう一度お試しください。';
                }
                bigMic.classList.remove('connecting');
                bigMic.classList.add('error');
                startBtn.disabled = false;
                cleanupConnection('startCall:setupFailed(' + e.message + ')');
            }
        }

        // 次フェーズ仕様書（通話切断原因調査）: 「誰が・何を理由に通話終了処理を
        // 実行したのか」を追跡できるよう、cleanupConnection/endCallの呼び出し元を
        // 明示するsource引数を追加し、終了直前のPeerConnection/DataChannel/
        // 音声トラックの状態を1行のスナップショットとして記録する。
        // 個人の音声内容・電話番号・メール等は一切含めない（技術的な状態値のみ）。
        function logConnectionSnapshot(label) {
            try {
                const pcState = pc
                    ? ('connectionState=' + pc.connectionState + ' iceConnectionState=' + pc.iceConnectionState
                        + ' iceGatheringState=' + pc.iceGatheringState + ' signalingState=' + pc.signalingState)
                    : '(pcは既にnull)';
                const dcState = dc ? ('readyState=' + dc.readyState) : '(dcは既にnull)';
                const trackState = localStream
                    ? localStream.getAudioTracks().map((t, i) =>
                        `[${i}]readyState=${t.readyState},enabled=${t.enabled},muted=${t.muted}`).join(' ')
                    : '(localStreamは既にnull)';
                logEvent('[状態スナップショット:' + label + '] PC(' + pcState + ') DC(' + dcState + ') Track(' + trackState + ')');
            } catch (snapErr) {
                logEvent('状態スナップショット取得に失敗しました: ' + snapErr.message);
            }
        }

        function cleanupConnection(source) {
            logEvent('cleanupConnection実行（呼び出し元: ' + (source || '不明') + '）');
            // PHASE5（Greeting直後 Connection Failure再調査・重要）: cleanup開始の
            // 事実そのものをタイムラインに明示的な固定文字列で記録する。直前の
            // AI音声イベント（output_audio_buffer.started/stopped/cleared、
            // response.done）からの経過時間を併記することで、「Greeting終了に
            // 連動した切断」なのか「それとは無関係なタイミングでの切断（＝
            // ネットワーク切断等の可能性が高い）」なのかを後から区別できるようにする。
            const gapSinceAiAudioMs = (lastAiAudioEventAt !== null) ? Math.round(performance.now() - lastAiAudioEventAt) : null;
            pushTimelineEvent('CLEANUP_STARTED (source=' + (source || '不明')
                + ', gapSinceLastAiAudioEvent=' + (gapSinceAiAudioMs === null ? '不明(AI音声イベント未観測)' : gapSinceAiAudioMs + 'ms') + ')');
            pushTimelineEvent('cleanupConnection (source=' + (source || '不明') + ')');
            if (maxDurationTimer) { clearTimeout(maxDurationTimer); maxDurationTimer = null; }
            // PHASE11（cleanup競合対策）: disconnected猶予タイマーが残ったまま
            // cleanupが実行されると、タイマー発火時にはpcが既にclose/null化済み
            // （またはcloseされた古いpcインスタンス）になっており、意図しない
            // 二重endCall()やnullアクセスにつながり得る。cleanup時は必ず解除する。
            // （failureSnapshot自体はここではクリアしない＝失敗直後も画面に残す）
            if (disconnectGraceTimer) { clearTimeout(disconnectGraceTimer); disconnectGraceTimer = null; }
            // Expected Answer Window / Silence Timeout: 通話終了経路によらず、
            // 残存タイマーが遅延発火してログを汚さないよう防御的に解除する
            // （isStaleCallEventガードにより誤動作はしないが、タイマー自体は
            // ここで確実に止める。state自体はfailureSnapshotと同じ方針で
            // ここではリセットせず、次回startCall()でのみクリアする）。
            if (answerWindowTimerId) { clearTimeout(answerWindowTimerId); answerWindowTimerId = null; }
            if (silenceTimerId) { clearTimeout(silenceTimerId); silenceTimerId = null; }
            if (silenceWarningTimerId) { clearTimeout(silenceWarningTimerId); silenceWarningTimerId = null; }
            // NOISY ENVIRONMENT / 3-SECOND TURN BOUNDARY（今回追加）: 同じ理由で
            // USER_TURN_3S_FALLBACKタイマーもここで確実に止める。
            if (userTurnFallbackTimerId) { clearTimeout(userTurnFallbackTimerId); userTurnFallbackTimerId = null; }
            // AI SPEAKING PROTECTION（今回追加）: 安全網タイマーを止め、状態を
            // 解除しておく（この直後にlocalStreamのtrack自体をstopするため
            // track.enabled操作自体は不要だが、診断パネル上の状態を一貫させる
            // ために明示的にfalseへ戻す）。
            if (aiSpeakingProtectionSafetyTimerId) { clearTimeout(aiSpeakingProtectionSafetyTimerId); aiSpeakingProtectionSafetyTimerId = null; }
            aiSpeakingProtected = false;
            stopMicLevelMeter();
            stopStatsPolling();
            // 再調査（第2ラウンド・PHASE「ブチッ」とcleanupの順序）: cleanupが
            // 実際にpc.close()/track.stop()/audio要素の停止・除去を行う直前に
            // それぞれ個別のタイムラインエントリを記録する。目的は、音声が
            // 自然に途切れた"結果"connection failureになったのか、逆に
            // cleanupが先に実行されてaudioを強制停止したことで「ブチッ」と
            // 聞こえたのか、を時系列の前後関係から確定できるようにすること。
            // なお、zeroWaitAudioElはdocument.body.appendChild()されておらず
            // document.querySelectorAll('audio')には含まれない（new Audio()の
            // まま）ため、下のAUDIO_REMOVE_REQUESTEDはRealtime側の
            // remoteAudioElのみに影響する（Zero-Wait音声要素はここでは
            // 一切操作されない設計を維持）。
            if (localStream) {
                pushTimelineEvent('LOCAL_TRACK_STOP_REQUESTED (source=cleanupConnection)');
                localStream.getTracks().forEach((t) => t.stop());
                localStream = null;
            }
            if (pc) {
                pushTimelineEvent('PC_CLOSE_REQUESTED (source=cleanupConnection, connectionState=' + pc.connectionState + ')');
                try { pc.close(); } catch (e) {}
                pc = null;
            }
            if (document.querySelectorAll('audio').length > 0) {
                pushTimelineEvent('AUDIO_REMOVE_REQUESTED (source=cleanupConnection, 対象=remoteAudioElのみ・zeroWaitAudioElは対象外)');
            }
            document.querySelectorAll('audio').forEach((el) => el.remove());
            // 音声診断パネル表示用の参照・状態のみリセットする（次回通話開始時に
            // pc.ontrackが発火した際、改めて新しい値で更新される）。
            remoteAudioEl = null;
            remoteAudioCtx = null;
            remoteTrackReceived = false;
            remoteTrackMuted = null;
            remoteTrackReadyState = null;
            remotePlayState = 'unknown';
            userVadState = 'idle';
            responseState = 'idle';
            // FAST TURN HOTFIX 3（今回追加）: 通話終了時に進行中のFULL TURN
            // LATENCY TRACEがあれば、理由付きで明示的に終了させる。
            resetTurnLatencyTrace('cleanup_connection');
            // FAST TURN HOTFIX 4（今回追加・観測専用・安全網）: 通話終了時に
            // TOOL CONTINUATION RESPONSE WATCHDOGが動いていれば破棄する。
            cancelToolContinuationResponseWatchdog('cleanup_connection');
            updatePlaybackRecoveryButton();
            setStatus(stMicTrackEl, 'なし', null);
            setStatus(stWebrtcEl, 'closed', null);
            setStatus(stSessionEl, '未接続', null);
            setStatus(stUserSpeakEl, '待機', null);
            setStatus(stAiSpeakEl, '待機', null);
            updateAudioDiagnosticsPanel();
        }

        function endCall(reason, source) {
            // PHASE5（Greeting直後 Connection Failure再調査・重要）: endCall()が
            // 呼ばれた事実そのものを、ended状態のガードより前に記録する
            // （既にended=trueで即returnする多重呼び出しも含めて全て記録する。
            // 「誰が・いつ・何を理由にendCallを要求したか」の完全な履歴を残すため）。
            const gapSinceAiAudioMsAtRequest = (lastAiAudioEventAt !== null) ? Math.round(performance.now() - lastAiAudioEventAt) : null;
            pushTimelineEvent('END_CALL_REQUESTED (source=' + (source || '不明')
                + ', reason=' + (reason ? '有' : '無')
                + ', alreadyEnded=' + ended
                + ', gapSinceLastAiAudioEvent=' + (gapSinceAiAudioMsAtRequest === null ? '不明(AI音声イベント未観測)' : gapSinceAiAudioMsAtRequest + 'ms') + ')');
            if (ended) return;
            ended = true;
            pushTimelineEvent('endCall (source=' + (source || '不明') + ', reason=' + (reason ? '有' : '無') + ')');
            logConnectionSnapshot('endCall直前（呼び出し元: ' + (source || '不明') + '）');
            // ===== PHASE O5.6: CALL_END_DIAG（診断専用・挙動は一切変更しない） =====
            // endCall()が実際に処理を進める（ended=trueが確定した）この時点、
            // cleanupConnection()でpc/dc/localStreamがnull化される前に、通話
            // 終了の原因調査に必要な状態を1行にまとめて記録する。個人情報
            // （氏名・電話番号・予約内容・transcript全文・音声・トークン等）は
            // 一切含めない。reason引数自体の全文はログしない（既存の
            // END_CALL_REQUESTEDと同じ「有/無」表記の慣習を踏襲し、実際の
            // 終了理由の識別はsource引数側で行う）。例外が起きても既存の
            // 終話処理（この直後のusage確定・cleanupConnection等）を妨げない
            // よう、必ずtry/catchで包む。
            try {
                const micTrackStateForDiag = (localStream && localStream.getAudioTracks && localStream.getAudioTracks()[0])
                    ? localStream.getAudioTracks()[0].readyState : null;
                const elapsedFromCallStartMsForDiag = callStartedAt ? (Date.now() - callStartedAt) : null;
                const speechStartedElapsedForDiag = msSince(lastSpeechStartedAt);
                const speechStoppedElapsedForDiag = msSince(lastSpeechStoppedAt);
                const functionCallElapsedForDiag = msSince(lastFunctionCallStartedAtForDiag);
                pushTimelineEvent('CALL_END_DIAG (epochMs=' + Date.now()
                    + ', elapsedFromCallStartMs=' + (elapsedFromCallStartMsForDiag === null ? 'null' : elapsedFromCallStartMsForDiag)
                    + ', callGeneration=' + callGeneration
                    + ', source=' + (source || '不明')
                    + ', reasonPresent=' + !!reason
                    + ', silenceState=' + silenceState
                    + ', pendingSilenceGoodbyeHangup=' + pendingSilenceGoodbyeHangup
                    + ', pcConnectionState=' + (pc ? pc.connectionState : null)
                    + ', iceConnectionState=' + (pc ? pc.iceConnectionState : null)
                    + ', dcReadyState=' + (dc ? dc.readyState : null)
                    + ', micTrackReadyState=' + micTrackStateForDiag
                    + ', elapsedSinceLastSpeechStartedMs=' + (speechStartedElapsedForDiag === null ? 'null' : speechStartedElapsedForDiag)
                    + ', elapsedSinceLastSpeechStoppedMs=' + (speechStoppedElapsedForDiag === null ? 'null' : speechStoppedElapsedForDiag)
                    + ', lastResponseReason=' + lastResponseReasonCategoryForDiag
                    + ', lastFunctionCallName=' + (lastFunctionCallNameForDiag || 'null')
                    + ', lastFunctionCallElapsedMs=' + (functionCallElapsedForDiag === null ? 'null' : functionCallElapsedForDiag) + ')');
            } catch (diagErr) {
                // 診断ログ自体の失敗が既存の終話処理を妨げてはならない。
            }
            // Phase2.6: 通話終了時点のusage合計を確定させる（cleanupConnectionより前に
            // 実施し、状態表示のリセットの影響を受けないようにする）。
            if (callStartedAt && !callEndedAt) {
                callEndedAt = Date.now();
                if (elapsedTimerId) { clearInterval(elapsedTimerId); elapsedTimerId = null; }
                updateElapsedDisplay();
                logFinalUsageSummary();
            }
            cleanupConnection('endCall(' + (source || '不明') + ')');
            bigMic.classList.remove('connecting', 'connected', 'ai-speaking');
            statusText.textContent = '通話終了';
            subStatusText.textContent = reason || '';
            startBtn.style.display = 'inline-block';
            startBtn.disabled = false;
            endBtn.style.display = 'none';
            if (reason) logEvent('終了理由: ' + reason + '（呼び出し元: ' + (source || '不明') + '）');
        }

        startBtn.addEventListener('click', () => {
            ended = false;
            startCall();
        });
        endBtn.addEventListener('click', () => endCall('通話を終了しました', 'endBtn.click'));

        // PHASE11/15（Mobile Audio Reliability）: ボタンのクリック自体が新しい
        // user gestureのため、この中でのplay()再試行はブラウザの自動再生
        // ポリシー上、常に許可される（他のブロック解除策と異なり、これは
        // ブラウザ仕様として確実に効く経路）。
        if (playbackRecoverBtn) {
            playbackRecoverBtn.addEventListener('click', () => {
                if (!remoteAudioEl) return;
                logEvent('[Mobile Audio] 「音声を再生」ボタンがタップされました。play()を再試行します');
                try {
                    const p = remoteAudioEl.play();
                    if (p && typeof p.then === 'function') {
                        p.then(() => {
                            remotePlayState = 'playing';
                            logEvent('[Mobile Audio] 再試行play()成功');
                            updatePlaybackRecoveryButton();
                            updateAudioDiagnosticsPanel();
                        }).catch((e) => {
                            logEvent('[Mobile Audio] 再試行play()も失敗: ' + (e && e.name) + ' - ' + (e && e.message));
                            updatePlaybackRecoveryButton();
                        });
                    } else {
                        remotePlayState = 'playing';
                        updatePlaybackRecoveryButton();
                    }
                } catch (e) {
                    logEvent('[Mobile Audio] 再試行play()で例外: ' + (e && e.message));
                }
            });
        }

        window.addEventListener('beforeunload', () => {
            cleanupConnection('beforeunload');
            // Phase3C.1: プリロード用に確保したObjectURLをページ離脱時に解放する
            // （通話中の再利用に備え、通話終了(endCall)のたびには解放しない）。
            if (zeroWaitObjectUrl) {
                try { URL.revokeObjectURL(zeroWaitObjectUrl); } catch (e) {}
            }
        });
