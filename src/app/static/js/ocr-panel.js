/**
 * OCR 패널 — 레이아웃 모드에서 OCR 실행 + 결과 표시.
 *
 * Phase 10-1: OCR 엔진 연동.
 *
 * 의존 전역:
 *   viewerState  (sidebar-tree.js)  — docId, partId, pageNum
 *   layoutState  (layout-editor.js) — selectedBlockId, blocks
 *
 * 이 모듈이 제공하는 전역 함수:
 *   initOcrPanel()       — 앱 초기화 시 호출 (workspace.js)
 *   refreshOcrEngines()  — 엔진 목록 갱신
 *   loadOcrResults()     — 현재 페이지의 OCR 결과 로드
 */

/* ─── 상태 ─────────────────────────────────────── */

const ocrState = {
  engines: [], // [{engine_id, display_name, available}, ...]
  defaultEngine: null, // 기본 엔진 ID
  running: false, // OCR 실행 중 여부
  lastResults: null, // 마지막 OCR 결과 (to_summary 형식)
  verticalView: false, // OCR 결과 세로쓰기 표시 모드
  selectedResultIndex: -1, // OCR 목록에서 선택된 항목 index
};

/* ─── 초기화 ───────────────────────────────────── */

function initOcrPanel() {
  // 엔진 목록 로드
  refreshOcrEngines();

  // LLM 모델 행: llm_vision 엔진일 때만 표시
  // (모델 목록은 workspace.js의 _loadAllLlmModelSelects()가 일괄 로드)
  const engineSelect = document.getElementById("ocr-engine-select");
  if (engineSelect) {
    engineSelect.addEventListener("change", _toggleLlmModelRow);
  }

  // 버튼 이벤트
  const runPartBtn = document.getElementById("ocr-run-part");
  if (runPartBtn) runPartBtn.addEventListener("click", _runPartOcr);
  const runAllBtn = document.getElementById("ocr-run-all");
  const runSelectedBtn = document.getElementById("ocr-run-selected");
  const deleteOcrBtn = document.getElementById("ocr-delete-page");
  const fillOcrBtn = document.getElementById("corr-fill-ocr");

  if (runAllBtn) {
    runAllBtn.addEventListener("click", () => _runOcr(null));
  }
  if (runSelectedBtn) {
    runSelectedBtn.addEventListener("click", () => {
      if (typeof layoutState !== "undefined" && layoutState.selectedBlockId) {
        _runOcr([layoutState.selectedBlockId]);
      }
    });
  }
  if (deleteOcrBtn) {
    deleteOcrBtn.addEventListener("click", _deleteCurrentPageOcr);
    deleteOcrBtn.textContent = "선택 OCR 삭제";
    deleteOcrBtn.title = "선택한 OCR 1건 삭제 (block_id 강제 매칭)";
  }
  if (fillOcrBtn) {
    fillOcrBtn.addEventListener("click", _fillFromOcr);
  }

  // LLM 교정 패스 (D-082)
  const correctBtn = document.getElementById("ocr-llm-correct");
  const preciseBtn = document.getElementById("ocr-llm-precise");
  if (correctBtn) correctBtn.addEventListener("click", () => _runCorrection("fast", null));
  if (preciseBtn) {
    preciseBtn.addEventListener("click", () => {
      if (typeof layoutState !== "undefined" && layoutState.selectedBlockId) {
        _runCorrection("precise", [layoutState.selectedBlockId]);
      }
    });
  }

  // OCR 결과 세로쓰기 토글
  const ocrVertBtn = document.getElementById("ocr-vertical-btn");
  if (ocrVertBtn) ocrVertBtn.addEventListener("click", _toggleOcrVerticalView);

  // 선택 블록 변경 시 "선택 블록 OCR" 버튼 상태 업데이트
  // layout-editor.js에서 블록 선택 시 이벤트를 발생시키지 않으므로
  // MutationObserver나 주기적 체크 대신, 블록 선택 함수를 감싸는 방식 사용
  setInterval(() => {
    _updateSelectedBlockButton();
    _syncOcrSelectionWithLayout();
  }, 300);
}

/* ─── 엔진 목록 ────────────────────────────────── */

async function refreshOcrEngines() {
  const select = document.getElementById("ocr-engine-select");
  // 첫 호출은 PaddleOCR 모듈을 처음 읽느라 수 초가 걸린다. «로딩 중»만 있으면
  // 멈춘 것처럼 보이므로 무엇을 기다리는지 적는다.
  if (select && !ocrState.engines.length) {
    select.innerHTML = '<option value="">엔진 확인 중… (첫 실행은 수 초 걸립니다)</option>';
    select.disabled = true;
  }
  try {
    const resp = await fetch("/api/ocr/engines");
    if (!resp.ok) {
      // 서버가 500을 주는 경우는 둘이다: (1) 서고가 아직 없다, (2) 엔진 등록이나
      // LLM 라우터 초기화가 예외로 죽었다. 예전에는 둘을 구분하지 않고 «서고를
      // 선택하면…»만 적어 서고가 있는 사용자를 헷갈리게 했다. 서버가 준 문구를
      // 그대로 보이고, (2)는 토스트로도 알린다. 서고가 정해지면 loadLibraryInfo()가
      // 다시 부른다.
      const err = await resp.json().catch(() => ({}));
      const msg = err.error || `엔진 목록 요청 실패 (HTTP ${resp.status})`;
      const noLibrary = /서고가 설정되지/.test(msg);
      if (select) {
        select.innerHTML = "";
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = noLibrary
          ? "서고를 선택하면 엔진 목록이 표시됩니다"
          : `엔진 목록 실패: ${msg}`;
        opt.title = msg;
        select.appendChild(opt);
        select.disabled = true;
      }
      if (!noLibrary) {
        console.error("OCR 엔진 목록 실패:", msg);
        if (typeof showToast === "function") showToast(msg, "error");
      }
      return;
    }
    const data = await resp.json();

    ocrState.engines = data.engines || [];
    ocrState.defaultEngine = data.default_engine;

    _populateEngineSelect();
  } catch (e) {
    console.warn("OCR 엔진 목록 로드 실패:", e);
    if (select) {
      select.innerHTML = '<option value="">엔진 목록을 불러오지 못했습니다</option>';
      select.disabled = true;
    }
  }
}

function _populateEngineSelect() {
  const select = document.getElementById("ocr-engine-select");
  if (!select) return;

  select.innerHTML = "";

  if (ocrState.engines.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "사용 가능한 엔진 없음";
    select.appendChild(opt);
    select.disabled = true;
    return;
  }

  select.disabled = false;

  // 엔진별 사용 가이드: 마우스 올리면 툴팁으로 표시
  // 왜 필요한가: NDL 古典籍OCR 계열은 전체 페이지 분석에 최적화되어 있어
  //   좁은 블록 crop에서는 라인 탐지 성능이 떨어진다.
  //   사용자가 엔진 특성을 알고 선택할 수 있도록 안내한다.
  const engineHints = {
    "ndlkotenocr": "쪽 전체에서 행을 찾은 뒤 선택한 블록에 배정. 블록 크기 무관. 고전적 전용.",
    "ndlkotenocr-full": "쪽 전체에서 행을 찾은 뒤 선택한 블록에 배정 (TrOCR 인식). 고전적 전용.",
    "ndlocr": "쪽 전체에서 행을 찾은 뒤 선택한 블록에 배정. 근현대 인쇄물용.",
    "llm_vision": "블록 크기 무관. 좁은 영역도 정확. 네트워크 필요.",
    "paddleocr": "블록 크기 무관. 좁은 영역도 정확. 현대문에 최적.",
  };

  for (const eng of ocrState.engines) {
    const opt = document.createElement("option");
    opt.value = eng.engine_id;
    opt.textContent = eng.display_name + (eng.available ? "" : " (사용 불가)");
    opt.disabled = !eng.available;
    if (engineHints[eng.engine_id]) {
      opt.title = engineHints[eng.engine_id];
    }
    // 어떤 원본 저장소의 어떤 모델인지 — 이름만으로는 매칭을 믿기 어렵다.
    if (eng.model_source) {
      opt.title = (opt.title ? opt.title + "\n" : "") + "모델: " + eng.model_source;
    }
    // 사용 불가 이유가 있으면 힌트 대신 그 이유를 보인다 — 무엇을 고쳐야 하는지가 먼저다.
    if (!eng.available && eng.unavailable_reason) {
      opt.title = eng.unavailable_reason;
    }
    if (eng.engine_id === ocrState.defaultEngine) {
      opt.selected = true;
    }
    select.appendChild(opt);
  }

  // 사용 불가 엔진의 이유를 드롭다운 아래에 적는다 — 툴팁은 마우스를 올려야 보인다.
  // 왜: «PaddleOCR (사용 불가)»만 보이면 무엇을 고쳐야 하는지 알 수 없다(파이썬 3.13,
  // 옛 .venv-gpu, DLL 실패 …). 자세한 진단은 doctor.bat.
  const note = document.getElementById("ocr-engine-note");
  if (note) {
    const bad = ocrState.engines.filter((e) => !e.available && e.unavailable_reason);
    note.hidden = bad.length === 0;
    note.textContent = bad
      .map((e) => `${e.display_name.split(" ")[0]} 사용 불가: ${e.unavailable_reason}`)
      .join("\n") + (bad.length ? "\n(자세한 진단: 설치 폴더의 doctor.bat)" : "");
  }

  // 등록된 엔진이 2개 이상이면 엔진 선택 행을 표시한다.
  // available 여부와 무관하게 표시 — "사용 불가" 엔진도 보여줘야
  // 사용자가 설치 가능한 엔진이 있다는 것을 알 수 있다.
  const engineRow = document.getElementById("ocr-engine-row");
  if (engineRow) {
    engineRow.style.display = ocrState.engines.length <= 1 ? "none" : "";
  }

  // LLM 모델 행 표시/숨김 갱신
  _toggleLlmModelRow();
}

/* ─── OCR 실행 ─────────────────────────────────── */

async function _runOcr(blockIds) {
  if (ocrState.running) return;
  if (typeof viewerState === "undefined") return;

  const docId = viewerState.docId;
  const partId = viewerState.partId;
  const pageNum = viewerState.pageNum;

  if (!docId || !partId || !pageNum) {
    showToast("문헌과 페이지를 먼저 선택하세요.", 'warning');
    return;
  }

  const engineSelect = document.getElementById("ocr-engine-select");
  const engineId = engineSelect ? engineSelect.value || null : null;

  ocrState.running = true;
  _showProgress(true, "레이아웃 저장 확인 중...", 0, 0);
  _disableButtons(true);

  try {
    // OCR 전에 현재 레이아웃을 L3에 저장 (저장되지 않은 블록이 있을 수 있음)
    // layout-editor.js의 _saveLayout()이 전역이 아니므로 직접 호출
    if (
      typeof layoutState !== "undefined" &&
      layoutState.blocks &&
      layoutState.blocks.length > 0
    ) {
      await _ensureLayoutSaved(docId, partId, pageNum);
    }

    _showProgress(true, "OCR 실행 중...", 0, 0);

    // LLM 프로바이더/모델 선택 (llm_vision 엔진 전용)
    const llmSel =
      typeof getLlmModelSelection === "function"
        ? getLlmModelSelection("ocr-llm-model-select")
        : { force_provider: null, force_model: null };

    const reqBody = {
      engine_id: engineId,
      block_ids: blockIds,
    };
    if (llmSel.force_provider) reqBody.force_provider = llmSel.force_provider;
    if (llmSel.force_model) reqBody.force_model = llmSel.force_model;

    // PaddleOCR 엔진: 언어 선택
    const paddleLangSel = document.getElementById("ocr-paddle-lang-select");
    if (paddleLangSel && engineId === "paddleocr") {
      reqBody.paddle_lang = paddleLangSel.value;
    }

    // SSE 스트리밍 엔드포인트로 요청 — 블록별 진행률을 실시간으로 받는다.
    const result = await _runOcrWithStreaming(docId, partId, pageNum, reqBody);

    // 선택 블록 OCR의 응답은 부분 결과일 수 있다.
    // 저장된 L2 전체 결과를 다시 읽어와 화면에 반영해야
    // 기존 블록이 덮어써진 것처럼 보이지 않는다.
    let latest = result;
    try {
      const fullResp = await fetch(
        `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr`,
        { cache: "no-store" },
      );
      if (fullResp.ok) latest = await fullResp.json();
    } catch (_) {
      // 전체 재조회 실패 시에도 방금 결과는 유지
    }

    ocrState.lastResults = latest;

    _showProgress(false);
    _displayResults(latest);
  } catch (e) {
    _showProgress(false);
    showToast(`OCR 실패: ${e.message}`, 'error');
  } finally {
    ocrState.running = false;
    _disableButtons(false);
  }
}


/**
 * SSE 스트리밍으로 OCR을 실행하고 진행률을 실시간 업데이트한다.
 *
 * 왜 SSE를 사용하는가:
 *   OCR은 블록 수에 따라 수십 초가 걸릴 수 있다.
 *   기존 방식은 모든 블록이 완료될 때까지 아무런 피드백이 없었다.
 *   SSE를 사용하면 블록이 처리될 때마다 진행률을 표시할 수 있다.
 *
 * 입력: docId, partId, pageNum, reqBody (OCR 요청 본문)
 * 출력: OCR 완료 결과 (to_summary 형식)
 */
async function _runOcrWithStreaming(docId, partId, pageNum, reqBody) {
  const resp = await fetch(
    `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reqBody),
    },
  );

  if (!resp.ok) {
    // SSE 엔드포인트 미지원 시 기존 동기 방식으로 폴백
    const fallbackResp = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(reqBody),
      },
    );
    if (!fallbackResp.ok) {
      const err = await fallbackResp.json();
      throw new Error(err.error || `HTTP ${fallbackResp.status}`);
    }
    return await fallbackResp.json();
  }

  // ReadableStream으로 SSE 이벤트를 읽는다
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE 형식 파싱: "data: {...}\n\n"
    const lines = buffer.split("\n\n");
    // 마지막 요소는 아직 완성되지 않은 청크일 수 있다
    buffer = lines.pop() || "";

    for (const chunk of lines) {
      const dataLine = chunk.trim();
      if (!dataLine.startsWith("data: ")) continue;

      try {
        const data = JSON.parse(dataLine.slice(6));

        if (data.type === "progress") {
          // 블록별 진행률 업데이트
          const pct = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
          _showProgress(
            true,
            `OCR 블록 ${data.current}/${data.total} 처리 중... (${pct}%)`,
            data.current,
            data.total,
          );
        } else if (data.type === "complete") {
          finalResult = data;
          _showProgress(true, "OCR 완료, 결과 로드 중...", data.total_blocks, data.total_blocks);
        } else if (data.type === "error") {
          throw new Error(data.error || "OCR 스트리밍 오류");
        }
      } catch (parseErr) {
        // JSON 파싱 실패한 이벤트는 건너뛴다
        if (parseErr.message && !parseErr.message.includes("JSON")) throw parseErr;
        console.warn("SSE 파싱 실패:", dataLine, parseErr);
      }
    }
  }

  if (!finalResult) {
    throw new Error("OCR 스트리밍이 완료 이벤트 없이 종료되었습니다.");
  }

  return finalResult;
}

/* ─── OCR 결과 표시 ────────────────────────────── */

function _displayResults(result) {
  const preview = document.getElementById("ocr-results-preview");
  const list = document.getElementById("ocr-results-list");
  const summary = document.getElementById("ocr-results-summary");

  if (!preview || !list) return;

  preview.style.display = "";
  list.innerHTML = "";
  ocrState.selectedResultIndex = -1;

  const ocrResults = result.ocr_results || [];

  if (ocrResults.length === 0) {
    list.innerHTML = '<div class="ocr-result-empty">OCR 결과가 없습니다</div>';
    if (summary) summary.textContent = "";
    return;
  }

  for (let idx = 0; idx < ocrResults.length; idx++) {
    const block = ocrResults[idx];
    const blockId = String(block.layout_block_id || "").trim();
    const blockEl = document.createElement("div");
    blockEl.className = "ocr-result-item";
    blockEl.dataset.blockId = blockId;
    blockEl.dataset.ocrIndex = String(idx);
    blockEl.title = blockId
      ? `OCR #${idx + 1} · ${blockId}`
      : `OCR #${idx + 1} · block_id 없음`;
    blockEl.addEventListener("click", () => _selectOcrResultItem(blockId, idx));

    // 블록 ID
    const blockIdEl = document.createElement("span");
    blockIdEl.className = "ocr-result-block-id";
    blockIdEl.textContent = `#${idx + 1} ${blockId || "(block_id 없음)"}`;

    // 텍스트 (줄별)
    const textEl = document.createElement("span");
    textEl.className = "ocr-result-text";
    const lines = block.lines || [];
    const fullText = lines.map((l) => l.text).join("");
    textEl.textContent = fullText || "(비어있음)";

    // Confidence (평균)
    const avgConf = _calcAvgConfidence(lines);
    const confEl = document.createElement("span");
    confEl.className = "ocr-result-confidence " + _confidenceClass(avgConf);
    confEl.textContent = avgConf > 0 ? Math.round(avgConf * 100) + "%" : "—";
    confEl.title = `평균 신뢰도: ${(avgConf * 100).toFixed(1)}%`;

    blockEl.appendChild(blockIdEl);
    blockEl.appendChild(textEl);
    blockEl.appendChild(confEl);
    list.appendChild(blockEl);
  }

  _syncOcrSelectionWithLayout();
  _updateDeleteButtonState();

  // 요약
  if (summary) {
    const status = result.status || "completed";
    const elapsed = result.elapsed_sec || 0;
    const processed = result.processed_blocks || 0;
    const total = result.total_blocks || 0;
    const skipped = result.skipped_blocks || 0;
    const errors = result.errors || [];

    let text = `${processed}/${total} 블록 처리`;
    if (skipped > 0) text += `, ${skipped} 건너뜀`;
    text += ` (${elapsed}초)`;
    if (errors.length > 0) text += ` | 오류 ${errors.length}건`;

    summary.textContent = text;
    summary.className = errors.length > 0 ? "ocr-summary-partial" : "";
  }
}

function _calcAvgConfidence(lines) {
  let total = 0;
  let count = 0;
  for (const line of lines) {
    for (const ch of line.characters || []) {
      if (ch.confidence > 0) {
        total += ch.confidence;
        count++;
      }
    }
  }
  return count > 0 ? total / count : 0;
}

function _confidenceClass(conf) {
  if (conf >= 0.8) return "conf-high";
  if (conf >= 0.5) return "conf-mid";
  return "conf-low";
}

/* ─── OCR 결과 로드 (기존 L2) ──────────────────── */

async function loadOcrResults() {
  if (typeof viewerState === "undefined") return;

  const docId = viewerState.docId;
  const partId = viewerState.partId;
  const pageNum = viewerState.pageNum;

  if (!docId || !partId || !pageNum) return;

  try {
    const resp = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr`,
      { cache: "no-store" },
    );
    if (!resp.ok) {
      // 404 = OCR 결과 없음 (정상)
      ocrState.lastResults = null;
      const preview = document.getElementById("ocr-results-preview");
      if (preview) preview.style.display = "none";
      return;
    }

    const data = await resp.json();
    // L2 데이터를 to_summary 형식에 맞게 변환
    ocrState.lastResults = {
      status: "loaded",
      ocr_results: data.ocr_results || [],
      engine: data.ocr_engine || "",
      total_blocks: (data.ocr_results || []).length,
      processed_blocks: (data.ocr_results || []).length,
      skipped_blocks: 0,
      elapsed_sec: 0,
      errors: [],
    };
    _displayResults(ocrState.lastResults);
  } catch (e) {
    console.warn("OCR 결과 로드 실패:", e);
  }
}

/* ─── 교정 모드: OCR 결과로 채우기 ─────────────── */

async function _fillFromOcr() {
  if (typeof viewerState === "undefined") return;

  const docId = viewerState.docId;
  const partId = viewerState.partId;
  const pageNum = viewerState.pageNum;

  if (!docId || !partId || !pageNum) {
    showToast("문헌과 페이지를 먼저 선택하세요.", 'warning');
    return;
  }

  // OCR 결과는 항상 현재 페이지 기준으로 다시 로드한다.
  // 왜: 이전 페이지의 ocrState.lastResults가 남아있으면 다른 페이지 텍스트가 저장될 수 있다.
  let ocrData;
  try {
    const resp = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr`,
      { cache: "no-store" },
    );
    if (!resp.ok) {
      showToast(
        "이 페이지에 OCR 결과가 없습니다. 레이아웃 모드에서 OCR을 먼저 실행하세요.",
        'warning');
      return;
    }
    ocrData = await resp.json();
  } catch (e) {
    showToast(`OCR 결과 로드 실패: ${e.message}`, 'error');
    return;
  }

  // OCR 결과에서 전체 텍스트 추출
  const ocrResults = ocrData.ocr_results || [];
  if (ocrResults.length === 0) {
    showToast("OCR 결과가 비어있습니다.", 'warning');
    return;
  }

  // 블록별로 줄 텍스트를 합쳐서 전체 텍스트 생성
  const fullText = ocrResults
    .map((block) => {
      const lines = block.lines || [];
      return lines.map((l) => l.text).join("\n");
    })
    .join("\n\n");

  // 1. 텍스트 API에 저장 (교정 모드에서도 접근 가능하도록)
  try {
    const saveUrl = `/api/documents/${docId}/pages/${pageNum}/text?part_id=${partId}`;
    const saveResp = await fetch(saveUrl, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({ text: fullText }),
    });
    if (!saveResp.ok) {
      const errBody = await saveResp.text();
      throw new Error(errBody || `HTTP ${saveResp.status}`);
    }
  } catch (e) {
    showToast(`OCR 텍스트 저장 실패: ${e.message}`, 'error');
    return;
  }

  // 2. 열람 모드의 textarea에도 채우기 (열람 모드일 때)
  const textarea = document.getElementById("text-content");
  if (textarea) {
    textarea.value = fullText;

    // OCR 채우기는 이미 서버 저장이 완료된 상태이므로,
    // 텍스트 에디터 상태도 "저장됨"으로 동기화한다.
    // 왜: input 이벤트를 발생시키면 editorState.isDirty가 true가 되어
    //      페이지 이동 시 "저장되지 않았습니다" 경고가 잘못 뜰 수 있다.
    if (typeof editorState !== "undefined") {
      editorState.originalText = fullText;
      editorState.isDirty = false;
    }
    if (typeof _updateSaveStatus === "function") {
      _updateSaveStatus("saved");
    }
  }

  // 3. 교정 모드가 활성화되어 있으면 교정 뷰 리프레시
  if (typeof correctionState !== "undefined" && correctionState.active) {
    if (typeof loadPageCorrections === "function") {
      await loadPageCorrections(docId, partId, pageNum);
    }
  }

  showToast(`OCR 결과가 텍스트로 저장되었습니다. (${ocrResults.length}개 블록)`, 'success');
}

/* ─── OCR 결과 세로쓰기 토글 ─────────────────────── */

/**
 * OCR 결과 목록의 가로/세로 표시를 전환한다.
 *
 * 왜 이렇게 하는가: 고전 한문 텍스트는 세로로 읽으므로,
 *   OCR 결과도 세로로 표시하면 원본과 비교하기 쉽다.
 */
function _toggleOcrVerticalView() {
  ocrState.verticalView = !ocrState.verticalView;
  const list = document.getElementById("ocr-results-list");
  if (list) {
    list.classList.toggle("vertical-text-mode", ocrState.verticalView);
  }
  const btn = document.getElementById("ocr-vertical-btn");
  if (btn) {
    btn.classList.toggle("active", ocrState.verticalView);
    btn.title = ocrState.verticalView ? "가로쓰기로 전환" : "세로쓰기로 전환";
  }
}

/* ─── UI 헬퍼 ──────────────────────────────────── */

/**
 * OCR 진행률을 표시/숨김한다.
 *
 * 왜 이렇게 하는가:
 *   current/total이 0이면 불확정 진행률 (펄스 애니메이션),
 *   양수이면 확정 진행률 (채워지는 바 + 퍼센트 텍스트).
 *
 * 입력:
 *   show — 표시 여부
 *   text — 진행 상태 텍스트 (예: "블록 2/5 처리 중...")
 *   current — 현재 처리 완료 수 (0이면 불확정)
 *   total — 전체 처리 대상 수 (0이면 불확정)
 */
/**
 * 권(PDF) 전체를 한 번에 OCR한다 — 추출 패널의 일괄 OCR과 같은 엔드포인트.
 *
 * 왜 여기 있는가: 일괄 OCR은 추출(논문) 프로필의 패널에만 있어서 고서 흐름에서는
 * 쪽마다 눌러야 했다. 편성(글 단위 나누기)은 권 전체의 확정본이 있어야 시작되므로
 * 고서에서도 한 번에 돌릴 수 있어야 한다. 이미 결과가 있는 쪽은 건너뛰고(중단 뒤
 * 이어 돌리기), 레이아웃이 없는 쪽은 쪽 전면 1블록으로 돌린다 — 쪽 단위 엔진(NDL)은
 * 어차피 쪽 전체에서 행을 찾으므로(D-086) 고서에서도 손해가 없다.
 *
 * OCR 결과는 확정본(L4)에도 복사한다(fill_text_layer). 편성·자동 트리는 L4만 읽으므로
 * 이것을 끄면 OCR 77쪽을 돌려도 개요가 비어 나온다 — 浩齋辰巳日錄 실측(2026-09-06)에서
 * 날짜 340개를 두고 「후보 0」이 나온 원인이었다(이전에는 false였다). 사람이 이미 고친
 * L4는 서버가 덮지 않는다(새로 OCR 한 쪽만 쓴다).
 */
async function _runPartOcr() {
  const docId = viewerState.docId;
  const partId = viewerState.partId;
  if (!docId || !partId) {
    showToast("문헌과 권을 먼저 선택하세요.", "warning");
    return;
  }
  const engineSelect = document.getElementById("ocr-engine-select");
  const engineId = engineSelect ? engineSelect.value || null : null;
  const total = viewerState.documentInfo?.parts?.find((p) => p.part_id === partId)?.page_count;
  if (
    !confirm(
      `이 권${total ? ` ${total}쪽` : ""} 전체를 OCR합니다.\n` +
        "이미 결과가 있는 쪽은 건너뛰고, 레이아웃이 없는 쪽은 쪽 전면 1블록으로 돌립니다.\n" +
        "계속할까요?",
    )
  )
    return;

  const llmSel =
    typeof getLlmModelSelection === "function"
      ? getLlmModelSelection()
      : { force_provider: null, force_model: null };
  const body = {
    engine_id: engineId,
    skip_existing: true,
    redo_changed_layout: true,
    backup_before_overwrite: true,
    auto_full_page_block: true,
    writing_direction: "vertical_rtl",
    fill_text_layer: true,
    llm_correction: "off",
  };
  if (llmSel.force_provider) body.force_provider = llmSel.force_provider;
  if (llmSel.force_model) body.force_model = llmSel.force_model;

  _disableButtons(true);
  _showProgress(true, "권 전체 OCR 시작…", 0, 0);
  let summary = null;
  try {
    const res = await fetch(`/api/documents/${docId}/parts/${partId}/ocr/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let evt;
        try {
          evt = JSON.parse(line.slice(6));
        } catch (e) {
          continue;
        }
        if (evt.type === "start") {
          _showProgress(true, `${evt.total}쪽 처리 예정`, 0, evt.total);
          (evt.warnings || []).forEach((w) => showToast(w, "info"));
        } else if (evt.type === "page" || evt.type === "skip" || evt.type === "redo") {
          const label =
            evt.type === "skip" ? "건너뜀" : evt.type === "redo" ? "다시" : `${evt.lines || 0}줄`;
          _showProgress(true, `${evt.index + 1}/${evt.total}쪽 — ${evt.page}쪽 ${label}`, evt.index + 1, evt.total);
        } else if (evt.type === "complete") {
          summary = evt;
        } else if (evt.type === "error") {
          throw new Error(evt.error || "일괄 OCR 실패");
        }
      }
    }
    if (summary) {
      showToast(
        `권 전체 OCR 완료 — 처리 ${summary.processed}쪽, 건너뜀 ${summary.skipped}쪽, 실패 ${summary.failed}쪽`,
        summary.failed ? "warning" : "success",
      );
    }
    if (typeof loadOcrResults === "function") loadOcrResults();
  } catch (e) {
    showToast(`권 전체 OCR 실패: ${e.message}`, "error");
  } finally {
    _showProgress(false);
    _disableButtons(false);
  }
}

function _showProgress(show, text, current, total) {
  const el = document.getElementById("ocr-progress");
  const textEl = document.getElementById("ocr-progress-text");
  const fillEl = document.getElementById("ocr-progress-fill");

  if (el) el.style.display = show ? "" : "none";
  if (textEl && text) textEl.textContent = text;

  if (fillEl) {
    if (current > 0 && total > 0) {
      // 확정 진행률: 바를 실제 퍼센트로 채운다
      const pct = Math.min(100, Math.round((current / total) * 100));
      fillEl.style.width = pct + "%";
      fillEl.classList.add("ocr-progress-determinate");
      fillEl.classList.remove("ocr-progress-indeterminate");
    } else {
      // 불확정 진행률: 펄스 애니메이션
      fillEl.style.width = "100%";
      fillEl.classList.remove("ocr-progress-determinate");
      fillEl.classList.add("ocr-progress-indeterminate");
    }
  }
}

function _disableButtons(disabled) {
  const runAll = document.getElementById("ocr-run-all");
  const runSelected = document.getElementById("ocr-run-selected");
  const deleteBtn = document.getElementById("ocr-delete-page");
  const correctBtn = document.getElementById("ocr-llm-correct");
  const preciseBtn = document.getElementById("ocr-llm-precise");
  if (runAll) runAll.disabled = disabled;
  if (runSelected) runSelected.disabled = disabled || !_hasSelectedBlock();
  if (deleteBtn)
    deleteBtn.disabled = disabled || !_canDeleteSelectedOcrResult();
  if (correctBtn) correctBtn.disabled = disabled;
  if (preciseBtn) preciseBtn.disabled = disabled || !_hasSelectedBlock();
}

/* ─── LLM 교정 패스 (D-082) ─────────────────────────────── */

/**
 * LLM 교정 패스를 실행하고 초안을 표시한다. L2는 바뀌지 않는다.
 *
 * mode "fast"   : 기계적으로 선별된 블록(신뢰도 낮음·협주·한글 미지원 엔진)만, 사고 끔.
 * mode "precise": 지정 블록을 앞뒤 문맥과 함께, 사고를 켜서(예산 분리) 다시 읽는다.
 *                 행초·흘림체처럼 자형만으로 안 풀리는 곳에 쓴다.
 */
async function _runCorrection(mode, blockIds) {
  if (ocrState.running) return;
  if (typeof viewerState === "undefined") return;
  const { docId, partId, pageNum } = viewerState;
  if (!docId || !partId || !pageNum) {
    showToast("문헌과 페이지를 먼저 선택하세요.", "warning");
    return;
  }

  const llmSel =
    typeof getLlmModelSelection === "function"
      ? getLlmModelSelection("ocr-llm-model-select")
      : { force_provider: null, force_model: null };
  const body = { mode };
  if (blockIds) body.block_ids = blockIds;
  if (llmSel.force_provider) body.force_provider = llmSel.force_provider;
  if (llmSel.force_model) body.force_model = llmSel.force_model;

  ocrState.running = true;
  _disableButtons(true);
  _showProgress(
    true,
    mode === "precise" ? "정밀 판독 중 (추론 켬)..." : "LLM 교정 중 (선별 블록)...",
    0,
    0,
  );
  try {
    const res = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr/correct`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || "LLM 교정에 실패했습니다.", "error");
      return;
    }
    _renderCorrectionDraft(data);
    const n = (data.blocks || []).length;
    const accepted = (data.blocks || []).filter((b) => b.accepted).length;
    if (n === 0) {
      showToast(
        blockIds
          ? "지정한 블록에 L2 결과가 없습니다. 먼저 OCR을 실행하세요."
          : "다시 볼 블록이 없습니다. 엔진 신뢰도가 모두 기준 이상입니다.",
        "info",
      );
    } else {
      showToast(`LLM 교정 초안: ${n}블록 중 ${accepted}블록 자동 수용 기준 통과`, "success");
    }
  } catch (e) {
    showToast(`LLM 교정 실패: ${e.message}`, "error");
  } finally {
    _showProgress(false);
    ocrState.running = false;
    _disableButtons(false);
  }
}

/**
 * 교정 초안을 블록별로 보여 준다: 이유 · 앵커(엔진) → 교정본 · 일치율 · [적용].
 * 자동 수용 기준을 넘은 블록은 표시만 다르고, 적용은 사람이 누른다.
 */
function _renderCorrectionDraft(draft) {
  const list = document.getElementById("ocr-correction-list");
  if (!list) return;
  const blocks = draft.blocks || [];
  list.innerHTML = "";
  list.style.display = blocks.length ? "" : "none";
  if (!blocks.length) return;

  const head = document.createElement("div");
  head.className = "ocr-result-block-id";
  head.textContent = `LLM 교정 초안 (${draft.mode === "precise" ? "정밀 판독" : "교정"}) — L2는 그대로, 적용한 블록만 L4에 들어갑니다`;
  list.appendChild(head);

  for (const b of blocks) {
    const row = document.createElement("div");
    row.className = "ocr-result-item";
    row.title = (b.reasons || []).join(", ");

    const id = document.createElement("span");
    id.className = "ocr-result-block-id";
    id.textContent = `${b.block_id} · ${(b.reasons || []).join(", ")}`;

    const text = document.createElement("span");
    text.className = "ocr-result-text";
    if (b.error) {
      text.textContent = `실패: ${b.error}`;
    } else {
      text.textContent = `${b.anchor_text || "(비어있음)"} → ${b.corrected_text || "(비어있음)"}`;
    }

    const stat = document.createElement("span");
    stat.className =
      "ocr-result-confidence " + (b.accepted ? "conf-high" : b.error ? "conf-low" : "conf-mid");
    stat.textContent = b.error
      ? "—"
      : `${Math.round((b.agreement || 0) * 100)}%${b.uncertain_count ? ` [?]${b.uncertain_count}` : ""}`;
    stat.title = b.accepted ? "앵커와 일치율이 높고 불확실 표시가 없음 — 자동 수용 기준 통과" : "사람 확인 필요";

    row.appendChild(id);
    row.appendChild(text);
    row.appendChild(stat);

    if (!b.error && b.corrected_text) {
      const apply = document.createElement("button");
      apply.className = "text-btn text-btn-sm";
      apply.textContent = "적용";
      apply.title = "이 블록의 교정본을 L4(교정 텍스트)에 쓴다";
      apply.addEventListener("click", (ev) => {
        ev.stopPropagation();
        _applyCorrection([b.block_id]);
      });
      row.appendChild(apply);
    }
    list.appendChild(row);
  }
}

async function _applyCorrection(blockIds) {
  if (typeof viewerState === "undefined") return;
  const { docId, partId, pageNum } = viewerState;
  try {
    const res = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr/correct/apply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ block_ids: blockIds }),
      },
    );
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || "적용에 실패했습니다.", "error");
      return;
    }
    const nf = data.not_found_blocks || [];
    if (nf.length) {
      showToast(
        `적용 ${(data.applied_blocks || []).length}블록. ${nf.join(", ")}은(는) L4에서 엔진 원문을 찾지 못해 건너뛰었습니다 (이미 손으로 고친 자리일 수 있습니다).`,
        "warning",
      );
    } else {
      showToast(`교정본을 L4에 적용했습니다: ${(data.applied_blocks || []).join(", ")}`, "success");
    }
  } catch (e) {
    showToast(`적용 실패: ${e.message}`, "error");
  }
}

function _hasSelectedBlock() {
  return typeof layoutState !== "undefined" && !!layoutState.selectedBlockId;
}

function _updateSelectedBlockButton() {
  const btn = document.getElementById("ocr-run-selected");
  if (!btn) return;
  btn.disabled = ocrState.running || !_hasSelectedBlock();
  _updateDeleteButtonState();
}

/**
 * OCR 실행 전에 현재 레이아웃(블록)을 L3에 저장한다.
 *
 * 왜 필요한가:
 *   OCR 파이프라인은 L3_layout/{part_id}_page_{NNN}.json에서 블록 목록을 읽는다.
 *   사용자가 레이아웃 분석/수동 편집 후 저장 버튼을 누르지 않으면 L3 파일이 없다.
 *   그래서 OCR 실행 직전에 현재 블록 상태를 자동 저장한다.
 */
async function _ensureLayoutSaved(docId, partId, pageNum) {
  // layoutState.blocks가 없거나 비어있으면 저장할 것이 없음
  if (
    typeof layoutState === "undefined" ||
    !layoutState.blocks ||
    layoutState.blocks.length === 0
  ) {
    return;
  }

  // 이미지 크기 정보
  let imgW = layoutState.imageWidth || 0;
  let imgH = layoutState.imageHeight || 0;
  if (!imgW && typeof pdfState !== "undefined" && pdfState.pdfDoc) {
    try {
      const page = await pdfState.pdfDoc.getPage(pageNum);
      const vp = page.getViewport({ scale: 1.0 });
      imgW = Math.round(vp.width);
      imgH = Math.round(vp.height);
    } catch (_) {
      /* 무시 */
    }
  }

  // 블록에서 스키마에 없는 내부 전용 필드 제거
  const cleanBlocks = layoutState.blocks.map((b) => {
    const clean = { ...b };
    delete clean._draft;
    delete clean._draft_id;
    delete clean._confidence;
    delete clean.notes;
    return clean;
  });

  const hasLlmBlocks = layoutState.blocks.some((b) => b._draft);

  const payload = {
    part_id: partId,
    page_number: pageNum,
    image_width: imgW,
    image_height: imgH,
    analysis_method: hasLlmBlocks ? "llm" : "manual",
    blocks: cleanBlocks,
  };

  const url = `/api/documents/${docId}/pages/${pageNum}/layout?part_id=${partId}`;
  // 저장 실패 시 OCR을 중단해야 한다.
  // 왜: 저장이 실패하면 백엔드가 디스크의 옛 L3 데이터로 crop하여
  //      사용자가 편집한 블록과 다른 영역을 OCR하게 된다.
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(
      `레이아웃 저장 실패 (OCR 중단): ${errData.error || `HTTP ${res.status}`}`
    );
  }
}

/**
 * OCR 엔진에 따라 LLM 모델 행과 PaddleOCR 언어 행을 표시/숨김한다.
 *
 * - llm_vision 엔진: LLM 모델 선택 행 표시, PaddleOCR 언어 행 숨김
 * - paddleocr 엔진: PaddleOCR 언어 행 표시, LLM 모델 행 숨김
 * - 기타 엔진: 둘 다 숨김
 */
function _toggleLlmModelRow() {
  const engineSelect = document.getElementById("ocr-engine-select");
  const engineId = engineSelect ? engineSelect.value : "";

  const modelRow = document.getElementById("ocr-llm-model-row");
  const paddleLangRow = document.getElementById("ocr-paddle-lang-row");
  const ndlocrWarnRow = document.getElementById("ocr-ndlocr-warn-row");

  if (modelRow) {
    modelRow.style.display = engineId === "llm_vision" ? "" : "none";
  }
  if (paddleLangRow) {
    paddleLangRow.style.display = engineId === "paddleocr" ? "" : "none";
  }
  if (ndlocrWarnRow) {
    ndlocrWarnRow.style.display = engineId === "ndlocr" ? "" : "none";
  }
}

function _selectLayoutBlockFromOcr(blockId, ocrIndex = -1) {
  if (typeof layoutState === "undefined") return;

  const normalizedId = String(blockId || "").trim();
  const hasExact =
    normalizedId &&
    normalizedId !== "?" &&
    Array.isArray(layoutState.blocks) &&
    layoutState.blocks.some(
      (b) => String(b.block_id || "").trim() === normalizedId,
    );

  if (hasExact) {
    if (typeof _selectBlock === "function") {
      _selectBlock(normalizedId);
      return;
    }
    layoutState.selectedBlockId = normalizedId;
  } else {
    return;
  }
  if (typeof _redrawOverlay === "function") _redrawOverlay();
  if (typeof _updatePropsForm === "function") _updatePropsForm();
  if (typeof _updateBlockList === "function") _updateBlockList();
}

function _syncOcrSelectionWithLayout() {
  const list = document.getElementById("ocr-results-list");
  if (!list) return;

  list.querySelectorAll(".ocr-result-item").forEach((el) => {
    const index = Number(el.dataset.ocrIndex);
    const isSelected = index === ocrState.selectedResultIndex;
    el.classList.toggle("ocr-result-item-selected", isSelected);
  });
}

function _selectOcrResultItem(blockId, ocrIndex) {
  ocrState.selectedResultIndex = ocrIndex;
  _syncOcrSelectionWithLayout();
  _updateDeleteButtonState();
  _selectLayoutBlockFromOcr(blockId, ocrIndex);
}

function _canDeleteSelectedOcrResult() {
  const idx = ocrState.selectedResultIndex;
  if (!ocrState.lastResults || idx < 0) return false;
  const rows = ocrState.lastResults.ocr_results || [];
  if (idx >= rows.length) return false;
  const blockId = String(rows[idx].layout_block_id || "").trim();
  return !!blockId && blockId !== "?";
}

function _updateDeleteButtonState() {
  const deleteBtn = document.getElementById("ocr-delete-page");
  if (!deleteBtn) return;
  deleteBtn.disabled = ocrState.running || !_canDeleteSelectedOcrResult();
}

async function _deleteCurrentPageOcr() {
  if (ocrState.running) return;
  if (typeof viewerState === "undefined") return;

  const docId = viewerState.docId;
  const partId = viewerState.partId;
  const pageNum = viewerState.pageNum;

  if (!docId || !partId || !pageNum) {
    showToast("문헌과 페이지를 먼저 선택하세요.", 'warning');
    return;
  }

  if (!_canDeleteSelectedOcrResult()) {
    showToast(
      "삭제할 OCR 항목을 먼저 선택하세요. (block_id가 있는 항목만 삭제 가능)",
      'warning');
    return;
  }

  const idx = ocrState.selectedResultIndex;
  const row = (ocrState.lastResults.ocr_results || [])[idx];
  const blockId = String(row.layout_block_id || "").trim();
  const previewText = String(
    (row.lines || []).map((line) => line.text || "").join(""),
  );
  const shortText =
    previewText.length > 30 ? `${previewText.slice(0, 30)}…` : previewText;

  const ok = confirm(
    `선택한 OCR 1건을 삭제할까요?\n- OCR 항목: #${idx + 1}\n- block_id: ${blockId}\n- 내용: ${shortText || "(비어있음)"}`,
  );
  if (!ok) return;

  _disableButtons(true);
  _showProgress(true, "선택 OCR 삭제 중...", 0, 0);

  try {
    const resp = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr/${encodeURIComponent(blockId)}?index=${idx}`,
      { method: "DELETE" },
    );

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const refreshResp = await fetch(
      `/api/documents/${docId}/parts/${partId}/pages/${pageNum}/ocr`,
      { cache: "no-store" },
    );

    if (refreshResp.ok) {
      const latest = await refreshResp.json();
      ocrState.lastResults = {
        status: "loaded",
        ocr_results: latest.ocr_results || [],
        engine: latest.ocr_engine || "",
        total_blocks: (latest.ocr_results || []).length,
        processed_blocks: (latest.ocr_results || []).length,
        skipped_blocks: 0,
        elapsed_sec: 0,
        errors: [],
      };
      _displayResults(ocrState.lastResults);
    } else {
      ocrState.lastResults = null;
      const preview = document.getElementById("ocr-results-preview");
      const list = document.getElementById("ocr-results-list");
      const summary = document.getElementById("ocr-results-summary");
      if (preview) preview.style.display = "none";
      if (list) list.innerHTML = "";
      if (summary) summary.textContent = "";
    }

    showToast(`선택한 OCR 1건을 삭제했습니다. (block_id: ${blockId}, #${idx + 1})`, 'success');
  } catch (e) {
    showToast(`OCR 결과 삭제 실패: ${e.message}`, 'error');
  } finally {
    _showProgress(false);
    _disableButtons(false);
  }
}
