/**
 * 편성 에디터 — LayoutBlock → 단위 변환
 *
 * 기능:
 *   1. 교정된 텍스트를 블록별로 표시 (교정 적용 후)
 *   2. 각 LayoutBlock을 1:1로 단위 자동 생성 ("자동 편성")
 *   3. 여러 LayoutBlock을 합쳐서 하나의 단위 생성 ("합치기")
 *   4. 이미 생성된 단위 목록 표시
 *   5. 크로스 페이지 합치기 (시작~끝 페이지 범위)
 *
 * 의존성:
 *   - sidebar-tree.js (viewerState)
 *   - interpretation.js (interpState)
 *
 * 왜 이렇게 하는가:
 *   교정은 LayoutBlock(물리적 단위) 기반이지만,
 *   표점·현토·번역은 단위(논리적 단위) 기반이다.
 *   이 편성 단계에서 연구자가 LayoutBlock을 단위으로
 *   재편성(합치기·쪼개기)하여 후속 작업의 기본 단위를 정한다.
 *
 *   고전 텍스트에서 문장이 페이지 경계를 넘는 경우가 흔하므로,
 *   페이지 범위(예: 2~5)를 함께 보고 합칠 수 있어야 한다.
 */

/* ──────────────────────────
   상태 객체
   ────────────────────────── */

const compState = {
  active: false, // 편성 모드 활성화 여부
  currentBoundaries: [], // 지금 저장돼 있는 경계 — 편성 탭의 기본 화면
  // _page: 이 블록이 소속된 페이지 번호 (크로스 페이지 지원용)
  units: [], // 이미 생성된 단위 목록
  selectedTbId: null, // 쪼개기를 위해 선택된 단위 ID
  selectedTb: null, // 쪼개기를 위해 선택된 단위 객체
};

/* ──────────────────────────
   초기화
   ────────────────────────── */

/**
 * 편성 에디터를 초기화한다.
 * DOMContentLoaded에서 workspace.js가 호출한다.
 */
// eslint-disable-next-line no-unused-vars
function initCompositionEditor() {
  _bindCompEvents();
}

/**
 * 이벤트 바인딩.
 */
function _bindCompEvents() {
  const splitBtn = document.getElementById("comp-split-btn");
  const splitCancelBtn = document.getElementById("comp-split-cancel-btn");
  const splitTextarea = document.getElementById("comp-split-textarea");

  const splitExecBtn = document.getElementById("comp-split-exec-btn");
  const resetBtn = document.getElementById("comp-reset-btn");

  if (splitBtn) splitBtn.addEventListener("click", _executeSplit);
  if (splitExecBtn) splitExecBtn.addEventListener("click", _executeSplit);
  if (splitCancelBtn) splitCancelBtn.addEventListener("click", _cancelSplit);
  const splitClose = document.getElementById("comp-split-close");
  if (splitClose) splitClose.addEventListener("click", _cancelSplit);
  const splitOverlay = document.getElementById("comp-split-overlay");
  if (splitOverlay)
    splitOverlay.addEventListener("click", (ev) => {
      if (ev.target === splitOverlay) _cancelSplit(); // 겉막을 누르면 닫는다
    });
  if (resetBtn) resetBtn.addEventListener("click", _resetComposition);
  // 경계 제안 (D-088)
  const proposeBtn = document.getElementById("comp-propose-btn");
  if (proposeBtn) proposeBtn.addEventListener("click", _openProposePanel);
  const proposeCancel = document.getElementById("comp-propose-cancel-btn");
  if (proposeCancel) proposeCancel.addEventListener("click", _closeProposePanel);
  const proposeApply = document.getElementById("comp-propose-apply-btn");
  if (proposeApply) proposeApply.addEventListener("click", _applyProposals);
  // 신호 목록 (D-116): 고른 신호가 규칙이 된다 — 「후보 보기」는 저장 + 제안, 「전부 적용」은 저장 + 자동 트리
  const sigPropose = document.getElementById("comp-signals-propose-btn");
  if (sigPropose)
    sigPropose.addEventListener("click", (ev) => {
      ev.preventDefault(); // <summary> 안의 단추 — 접힘 토글을 막는다
      _saveRulesAndRepropose();
    });
  const applyAll = document.getElementById("comp-apply-all-btn");
  if (applyAll) applyAll.addEventListener("click", _applyAllFromSignals);
  const addBtn = document.getElementById("comp-signals-add-btn");
  const addInput = document.getElementById("comp-signals-add-word");
  if (addBtn) addBtn.addEventListener("click", _addManualWord);
  if (addInput)
    addInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") _addManualWord();
    });
  const curRefresh = document.getElementById("comp-current-refresh");
  if (curRefresh) curRefresh.addEventListener("click", _renderCurrentBoundaries);
  // 사이드바에서 고른 것을 여기서도 표시한다 — 양쪽이 어긋나 보이면 안 된다
  document.addEventListener("unit-selected", (ev) => {
    const list = document.getElementById("comp-current-list");
    if (!list) return;
    list.querySelectorAll(".comp-cur-row").forEach((el) => {
      el.classList.toggle("is-selected", el.dataset.unitId === ev.detail.id);
    });
  });
  const rulesSuggest = document.getElementById("comp-rules-suggest-btn");
  if (rulesSuggest) rulesSuggest.addEventListener("click", _suggestRules);
  // textarea 입력 시 쪼개기 미리보기 업데이트
  if (splitTextarea)
    splitTextarea.addEventListener("input", _updateSplitPreview);

}

/* ──────────────────────────
   모드 활성화 / 비활성화
   ────────────────────────── */

/**
 * 편성 모드를 활성화한다.
 */
// eslint-disable-next-line no-unused-vars
function activateCompositionMode() {
  compState.active = true;
  _loadCompositionData();
}

/**
 * 편성 모드를 비활성화한다.
 */
// eslint-disable-next-line no-unused-vars
function deactivateCompositionMode() {
  compState.active = false;
}

/* ──────────────────────────
   페이지 범위 결정
   ────────────────────────── */

/* ──────────────────────────
   데이터 로드
   ────────────────────────── */

/**
 * 교정된 텍스트 + 기존 단위를 로드한다.
 *
 * 왜 이렇게 하는가:
 *   편성 화면은 두 영역으로 나뉜다:
 *   (1) 위: 교정된 LayoutBlock 텍스트 (소스)
 *   (2) 아래: 이미 생성된 단위 (결과)
 *
 *   페이지 범위 모드가 켜져 있으면 인접 페이지의 블록도 함께 로드하여
 *   페이지 경계를 넘는 합치기를 지원한다.
 */
async function _loadCompositionData() {
  // 편성이 바뀌면 사이드바 「내용」 트리도 따라간다 (D-085)
  if (typeof refreshContentsTree === "function") refreshContentsTree();
  const { docId, partId, pageNum } = viewerState;
  if (!docId || !partId || !pageNum) {
    _renderUnits();
    return;
  }
  _updatePageIndicator(pageNum);

  // 단위 목록만 읽는다 — 옛 「자동 편성」이 보던 쪽별 교정 텍스트 조회는 D-116에서 없앴다
  const promises = [];
  const correctedPromises = [];
  if (interpState.interpId) {
    promises.push(
      fetch(`/api/interpretations/${interpState.interpId}/entities/unit?document_id=${docId}`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null),
    );
  }
  const results = await Promise.all(promises);

  // 단위 목록 — 권 전체
  compState.units = [];
  if (interpState.interpId) {
    const tbData = results[correctedPromises.length];
    for (const entity of (tbData && tbData.entities) || []) {
      // deprecated / archived는 목록에서 숨김
      if (entity.status === "deprecated" || entity.status === "archived") continue;
      compState.units.push(entity);
    }
  }

  _renderUnits();
  _updateBlockCount();
  _renderCurrentBoundaries();
}

/**
 * 페이지 표시기를 업데이트한다.
 */
function _updatePageIndicator(pageNum) {
  const el = document.getElementById("comp-page-indicator");
  // 범위 입력 옆에 「p.20」만 떠 있으면 그게 시작인지 지금인지 알 수 없다 — 말로 적는다
  if (el) el.textContent = `지금 ${pageNum}쪽`;
}

/**
 * 해석 저장소에 수동 git commit을 보낸다 (배치 작업 완료 후).
 *
 * 왜 이렇게 하는가:
 *   쪼개기·리셋 등 여러 API 호출이 필요한 배치 작업에서,
 *   개별 호출마다 git commit하면 10~60초씩 걸린다.
 *   no_commit=true로 변경을 모은 뒤 마지막에 한 번만 commit하면
 *   전체 작업이 1~2초에 끝난다.
 */
async function _commitBatch(message) {
  if (!interpState.interpId) return;
  try {
    await fetch(`/api/interpretations/${interpState.interpId}/git/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
  } catch (e) {
    console.error("배치 커밋 실패:", e);
  }
}

/**
 * 이미 생성된 단위 목록을 렌더링한다.
 */
function _renderUnits() {
  const container = document.getElementById("comp-textblock-list");
  if (!container) return;

  if (!viewerState.docId || !viewerState.partId) {
    container.innerHTML =
      '<div class="placeholder" style="padding:20px; text-align:center; color:var(--text-muted);">' +
      "사이드바에서 문헌과 권을 고르세요.<br>" +
      '<span style="font-size:11px;">(편성은 원본 저장소의 것입니다 — 해석 저장소는 표점 탭부터 씁니다)</span></div>';
    return;
  }

  if (compState.units.length === 0) {
    container.innerHTML =
      '<div class="placeholder" style="padding:20px; text-align:center; color:var(--text-muted);">' +
      '아직 단위가 없습니다. 「경계 제안」에서 신호를 골라 적용하거나, 사이드바 「내용」의 «경계 넣기»로 첫 경계를 놓으세요.</div>';
    return;
  }

  // 접기는 바깥 <details id="comp-manual">가 맡는다 — 여기서 또 접으면 두 겹이 된다.
  container.innerHTML = "";

  // sequence_index 순으로 정렬
  const sorted = [...compState.units].sort(
    (a, b) => (a.sequence_index || 0) - (b.sequence_index || 0),
  );
  // «기사인데 아래 단위를 품은» 것을 찾는다.
  // 단위의 끝은 «같은 깊이 이상의 다음 경계»다(D-092). 그래서 깊이가 이웃보다 얕은 기사는
  // 뒤따르는 기사들을 통째로 삼킨다 — 천진담초 실측에서 기사 하나가 30,906자(권 뒤쪽 전부)였다.
  // 논리로는 맞지만 사람이 알아채기 어려우므로 카드에 적어 준다.
  const swallows = new Map();
  sorted.forEach((u, i) => {
    const lv = Number(u.metadata?.level) || 2;
    let n = 0;
    for (let j = i + 1; j < sorted.length; j++) {
      if ((Number(sorted[j].metadata?.level) || 2) <= lv) break;
      n++;
    }
    if (n > 0 && (u.metadata?.role || "article") !== "container") swallows.set(u.id, n);
  });

  sorted.forEach((tb) => {
    const isSelectedTb = compState.selectedTbId === tb.id;
    const card = document.createElement("div");
    card.className = "comp-tb-card";
    card.style.cssText = `
      border: 1px solid ${isSelectedTb ? "var(--accent-primary, #3b82f6)" : "var(--accent-green, #22c55e)"};
      border-radius: 4px;
      padding: 8px;
      cursor: pointer;
      background: ${isSelectedTb ? "rgba(59,130,246,0.08)" : "rgba(34,197,94,0.03)"};
    `;
    card.addEventListener("click", (e) => {
      // 삭제 버튼 클릭 시에는 쪼개기 편집기를 열지 않음
      if (e.target.classList.contains("comp-tb-delete-btn")) return;
      _selectUnit(tb);
    });

    // 헤더: seq# + source 요약 + 삭제 버튼
    const header = document.createElement("div");
    header.style.cssText =
      "display:flex; align-items:center; gap:6px; margin-bottom:4px;";

    const seqBadge = document.createElement("span");
    seqBadge.style.cssText =
      "font-size:10px; font-weight:700; color:var(--accent-green, #22c55e); background:rgba(34,197,94,0.1); padding:1px 5px; border-radius:2px;";
    seqBadge.textContent = `#${tb.sequence_index}`;

    // 역할·글자 수 — 무엇을 고르는지 카드에서 바로 보이게
    const roleName = { container: "묶음", article: "기사", fragment: "조각" };
    const kindBadge = document.createElement("span");
    kindBadge.style.cssText = "font-size:10px; color:var(--text-muted);";
    const chars = (tb.original_text || "").length;
    kindBadge.textContent = `${roleName[tb.metadata?.role || "article"] || "기사"} · ${chars.toLocaleString()}자`;

    // 출처는 «몇 쪽에 걸쳐 있는가»만 보인다. v1.3부터 단위는 LayoutBlock을 기억하지 않아
    // 그 id는 전부 «?»로 나왔다 — 카드의 절반을 뜻 없는 문자열이 차지하고 있었다(D-092).
    const sourceInfo = document.createElement("span");
    sourceInfo.style.cssText = "font-size:10px; color:var(--text-muted);";
    const refs = (tb.source_refs || []).length
      ? tb.source_refs
      : tb.source_ref
        ? [tb.source_ref]
        : [];
    const pageNums = [...new Set(refs.map((r) => r.page).filter((n) => n != null))].sort(
      (a, b) => a - b,
    );
    if (pageNums.length === 1) sourceInfo.textContent = `${pageNums[0]}쪽`;
    else if (pageNums.length > 1)
      sourceInfo.textContent = `${pageNums[0]}~${pageNums[pageNums.length - 1]}쪽`;

    const statusBadge = document.createElement("span");
    statusBadge.style.cssText =
      "font-size:10px; color:var(--text-muted); margin-left:auto;";
    statusBadge.textContent = tb.status || "draft";

    // 삭제 버튼 (× 표시, hover 시에만 표시됨)
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "comp-tb-delete-btn";
    deleteBtn.title = "이 단위를 물리기 (경계를 deprecated로 — 되돌릴 수 있음)";
    deleteBtn.textContent = "\u00d7";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      _deleteUnit(tb);
    });

    header.appendChild(seqBadge);
    header.appendChild(sourceInfo);
    header.appendChild(statusBadge);

    // 「52페이지」 뱃지는 없앴다 — 바로 옆의 「11~62쪽」이 같은 것을 더 잘 말한다
    header.insertBefore(kindBadge, statusBadge);
    const swallowed = swallows.get(tb.id);
    if (swallowed) {
      const warn = document.createElement("span");
      warn.style.cssText =
        "font-size:10px; color:var(--accent-warning, #f59e0b); background:rgba(245,158,11,0.12); padding:1px 5px; border-radius:2px;";
      warn.textContent = `⚠ 아래 단위 ${swallowed}개를 품음`;
      warn.title =
        "이 단위의 깊이가 뒤따르는 단위들보다 얕아, 그것들을 통째로 삼키고 있습니다." +
        "\n" +
        "사이드바 「내용」에서 ⇥로 깊이를 한 단 내리거나, 역할을 «묶음»으로 바꾸세요.";
      header.insertBefore(warn, statusBadge);
    }
    // 삭제 버튼은 항상 맨 오른쪽에 위치
    header.appendChild(deleteBtn);

    // 텍스트 미리보기
    const preview = document.createElement("div");
    preview.style.cssText =
      "font-size:12px; line-height:1.6; white-space:pre-wrap; max-height:60px; overflow:hidden; color:var(--text-primary);";
    const text = tb.original_text || "";
    preview.textContent =
      text.length > 150 ? text.substring(0, 150) + "..." : text;

    card.appendChild(header);
    card.appendChild(preview);
    container.appendChild(card);
  });
}

/**
 * 블록 카운트 표시 업데이트.
 */
function _updateBlockCount() {
  const el = document.getElementById("comp-block-count");
  if (el) {
    el.textContent = `단위 ${compState.units.length}개`;
  }
}

/**
 * 저장 상태 표시.
 */
function _updateCompStatus(text, isError) {
  const el = document.getElementById("comp-save-status");
  if (el) {
    el.textContent = text;
    el.style.color = isError
      ? "var(--accent-error, #ef4444)"
      : "var(--accent-green, #22c55e)";
  }
}

/* ──────────────────────────
   편성 액션: 자동 편성
   ────────────────────────── */

/**
 * 단위를 선택하고 쪼개기 편집기를 연다.
 *
 * 왜 이렇게 하는가:
 *   크로스 페이지 합치기로 만든 큰 단위를
 *   연구자가 수동으로 단락별로 나눌 수 있어야 한다.
 *   단위 카드를 클릭하면 쪼개기 편집기가 열리고,
 *   텍스트 중간에 === 구분선을 넣어 쪼갤 위치를 지정한다.
 *
 * 입력: tb — 단위 객체 ({id, original_text, source_refs, ...})
 */
function _selectUnit(tb) {
  compState.selectedTbId = tb.id;
  compState.selectedTb = tb;
  // 쪼개기는 가운데 모달로 뜬다 — 패널 맨 밑으로 내려가면 긴 본문을 다루기 어렵다(사용자 요청)
  const editor = document.getElementById("comp-split-overlay");
  const textarea = document.getElementById("comp-split-textarea");
  const splitBtn = document.getElementById("comp-split-btn");

  if (editor) editor.style.display = "flex";
  const info = document.getElementById("comp-split-info");
  if (info) {
    const role = { container: "묶음", article: "기사", fragment: "조각" }[tb.metadata?.role || "article"];
    const title = tb.metadata?.title || "";
    info.textContent = `#${tb.sequence_index} ${role}${title ? " · " + title : ""} · ${(tb.original_text || "").length.toLocaleString()}자`;
  }
  if (textarea) {
    textarea.value = tb.original_text || "";
    textarea.focus();
  }
  if (splitBtn) splitBtn.disabled = false;

  _updateSplitPreview();

  // 단위 목록에서 선택 표시 갱신
  _renderUnits();
}

/* ──────────────────────────
   편성 액션: 개별 단위 삭제
   ────────────────────────── */

/**
 * 개별 단위를 deprecated 상태로 전환하여 삭제한다.
 *
 * 왜 이렇게 하는가:
 *   잘못 편성된 단위 하나만 골라서 삭제하고 싶을 때,
 *   전체 리셋 없이 개별 단위로 deprecated 전환할 수 있게 한다.
 *   deprecated된 단위는 목록에서 숨겨지지만 이력은 보존된다.
 *
 * 입력: tb — 단위 객체 ({id, original_text, sequence_index, ...})
 */
async function _deleteUnit(tb) {
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return;
  }

  // 텍스트 미리보기 (확인 대화상자에 표시)
  const previewText = (tb.original_text || "").substring(0, 50);
  const displayText =
    previewText +
    (tb.original_text && tb.original_text.length > 50 ? "..." : "");

  if (
    !confirm(
      `단위 #${tb.sequence_index} 을(를) 삭제하시겠습니까?\n\n"${displayText}"\n\n(deprecated 전환 — 이력은 보존됩니다)`,
    )
  ) {
    return;
  }

  _updateCompStatus("삭제 중...", false);

  try {
    // 배치 리셋 엔드포인트를 1개 ID로 호출 (단일 git commit)
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/composition/reset`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ part_id: viewerState.partId, unit_ids: [tb.id] }),
      },
    );

    if (res.ok || res.status === 207) {
      _updateCompStatus(`단위 #${tb.sequence_index} 삭제 완료`, false);

      // 삭제한 단위가 현재 쪼개기 편집기에 열려 있으면 닫기
      if (compState.selectedTbId === tb.id) {
        _cancelSplit();
      }
    } else {
      const err = await res.json().catch(() => ({}));
      const msg = err.error || err.detail || `HTTP ${res.status}`;
      showToast(`삭제 실패: ${msg}`, 'error');
      return;
    }
  } catch (e) {
    showToast(`삭제 실패: ${e.message}`, 'error');
    return;
  }

  // 데이터 새로고침
  await _loadCompositionData();
}

/* ──────────────────────────
   편성 리셋
   ────────────────────────── */

/**
 * 현재 페이지의 단위를 모두 deprecated 상태로 전환한다.
 *
 * 왜 이렇게 하는가:
 *   편성을 처음부터 다시 하고 싶을 때, 기존 단위를 삭제하는 대신
 *   deprecated 상태로 전환하여 이력을 보존한다.
 *   deprecated된 단위는 목록에서 숨겨지므로 깨끗하게 재시작할 수 있다.
 */
async function _resetComposition() {
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return;
  }

  const targets = compState.units.filter(
    (tb) => tb.status !== "deprecated" && tb.status !== "archived",
  );

  if (targets.length === 0) {
    showToast("물릴 단위가 없습니다.", 'warning');
    return;
  }

  if (
    !confirm(
      `현재 표시된 단위 ${targets.length}개를 모두 리셋(deprecated)하시겠습니까?\n\n이력은 보존되며, 나중에 복원할 수 있습니다.`,
    )
  ) {
    return;
  }

  _updateCompStatus(`리셋 중... (${targets.length}개)`, false);

  try {
    // 배치 리셋 엔드포인트: 한 번의 API 호출 + 한 번의 git commit
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/composition/reset`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          part_id: viewerState.partId,
          unit_ids: targets.map((tb) => tb.id),
        }),
      },
    );

    if (!res.ok && res.status !== 207) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    const done = data.deprecated_count || 0;
    const errors = data.errors || [];

    if (errors.length > 0) {
      showToast(
        `${done}개 리셋 완료, ${errors.length}개 실패:\n${errors.join("\n")}`,
        'error',
      );
    }

    _updateCompStatus(`리셋 완료: ${done}개 deprecated`, false);
  } catch (e) {
    showToast(`리셋 실패: ${e.message}`, 'error');
    _updateCompStatus("리셋 실패", true);
    return;
  }

  // 쪼개기 편집기 닫기
  _cancelSplit();

  // 데이터 새로고침
  await _loadCompositionData();
}

/**
 * 쪼개기 편집기를 닫고 선택을 해제한다.
 */
function _cancelSplit() {
  compState.selectedTbId = null;
  compState.selectedTb = null;

  const editor = document.getElementById("comp-split-overlay");
  const textarea = document.getElementById("comp-split-textarea");
  const splitBtn = document.getElementById("comp-split-btn");
  const preview = document.getElementById("comp-split-preview");

  if (editor) editor.style.display = "none";
  if (textarea) textarea.value = "";
  if (splitBtn) splitBtn.disabled = true;
  if (preview) preview.textContent = "";

  _renderUnits();
}

/**
 * 쪼개기 미리보기를 업데이트한다.
 *
 * 왜 이렇게 하는가:
 *   === 구분선으로 텍스트를 나눴을 때 몇 조각이 되는지
 *   실시간으로 보여주어 연구자가 확인할 수 있게 한다.
 */
function _updateSplitPreview() {
  const textarea = document.getElementById("comp-split-textarea");
  const preview = document.getElementById("comp-split-preview");
  if (!textarea || !preview) return;

  const pieces = _parseSplitPieces(textarea.value);
  const nonEmpty = pieces.filter((p) => p.trim().length > 0);

  if (nonEmpty.length <= 1) {
    preview.textContent = "구분선(===)을 넣으면 여러 문단으로 나뉩니다.";
    preview.style.color = "var(--text-muted)";
  } else {
    // 첫 조각은 원래 기사의 자리에 남는다 — 새로 서는 경계는 둘째 조각부터다
    preview.textContent = `→ 이 기사 안이 문단 ${nonEmpty.length}개로 나뉩니다 (조각 경계 ${nonEmpty.length - 1}개가 들어갑니다).`;
    preview.style.color = "var(--accent-primary, #3b82f6)";
  }
}

/**
 * 텍스트를 === 구분선으로 나눈다.
 *
 * 파싱 규칙:
 *   - ===만 있는 줄을 기준으로 split
 *   - 각 조각의 앞뒤 공백은 trim
 *
 * 입력: text — 전체 텍스트
 * 출력: 문자열 배열 (쪼개진 조각들)
 */
function _parseSplitPieces(text) {
  return text
    .split(/\n\s*===\s*\n|^===\s*\n|\n\s*===$/gm)
    .map((piece) => piece.trim());
}

/**
 * 쪼개기를 실행한다.
 *
 * 처리 순서:
 *   1. 텍스트를 === 구분선으로 파싱
 *   2. 비어 있는 조각 제거
 *   3. 각 조각마다 새 단위 생성 (source_refs는 원본 전체를 상속)
 *   4. 원본 단위를 deprecated 상태로 전환
 *   5. 데이터 새로고침
 *
 * 왜 source_refs를 전체 상속하는가:
 *   쪼개기는 이미 합쳐진 단위를 단락별로 나누는 작업이다.
 *   나눠진 각 조각이 어느 원본 LayoutBlock에서 왔는지
 *   정확한 char_range를 자동 계산하기 어렵다.
 *   전체 source_refs를 상속하되 char_range를 null로 두면
 *   "이 블록들에서 유래했다"는 추적성은 유지된다.
 */
async function _executeSplit() {
  if (!compState.selectedTb || !compState.selectedTbId) {
    showToast("나눌 단위를 먼저 선택하세요.", 'warning');
    return;
  }
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return;
  }

  const textarea = document.getElementById("comp-split-textarea");
  if (!textarea) return;

  const pieces = _parseSplitPieces(textarea.value);
  const nonEmpty = pieces.filter((p) => p.trim().length > 0);

  if (nonEmpty.length <= 1) {
    showToast(
      "=== 구분선을 넣어 2개 이상으로 나눠야 합니다.\n\n예시:\n첫 번째 텍스트\n===\n두 번째 텍스트",
      'warning',
    );
    return;
  }

  _updateCompStatus(`쪼개는 중... (${nonEmpty.length}개)`, false);

  try {
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/composition/split`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          original_unit_id: compState.selectedTbId,
          pieces: nonEmpty,
          part_id: viewerState.partId,
        }),
      },
    );

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(
        err.error || err.detail || `쪼개기 실패: HTTP ${res.status}`,
      );
    }

    _updateCompStatus(
      `쪼개기 완료: ${nonEmpty.length}개 단위 생성`,
      false,
    );
  } catch (e) {
    showToast(`쪼개기 실패:\n${e.message}`, 'error');
    _updateCompStatus("쪼개기 실패", true);
    return;
  }

  // 쪼개기 편집기 닫기
  _cancelSplit();

  // 데이터 새로고침
  await _loadCompositionData();
}


/* ──────────────────────────
   경계 제안 (D-088)
   권 전체 확정본에서 글 단위 경계 후보를 받아 보이고, 승인한 것만 단위으로.
   합치기·쪼개기를 블록 단위로 반복하는 대신 «어디서 글이 바뀌는가»만 정한다.
   ────────────────────────── */

/* ──────────────────────────
   이 책의 신호 (D-116) — 전문에서 센 규약 후보. 고른 것이 규칙이 된다
   ────────────────────────── */

const signalState = {
  data: null, // /segmentation/signals 응답
  docId: null, // 어느 문헌·권의 신호인가 — 다른 문헌으로 바꾼 뒤 옛 설정을 저장하면 안 된다
  partId: null,
  seq: 0, // 요청 세대. 늦게 끝난 옛 요청이 새 상태를 덮지 않게
  checked: new Set(), // 켜진 신호 id
  manual: [], // 사람이 더한 어휘 행 [{id, toggle, label}]
  touched: false, // 권고에서 하나라도 바꿨는가 → origin "manual"
};

/** 신호 상태가 지금 고른 문헌·권의 것인가. */
function _signalsCurrent() {
  return !!signalState.data && signalState.docId === viewerState.docId && signalState.partId === viewerState.partId;
}

// 신호 id → 사람 말. 서버(core.rule_induction.SIGNAL_LABELS)와 같은 말을 쓴다
const _SIGNAL_LABELS = {
  date: "날짜가 행 첫머리에", mark: "○ 권점 + 날짜", volume: "卷頭 (卷之一 …)",
  short_line: "짧은 행", after_short: "행갈음 뒤의 행", indent: "내려쓰기",
};
function _signalLabel(id) {
  if (_SIGNAL_LABELS[id]) return _SIGNAL_LABELS[id];
  if (id.startsWith("title_word:")) return `짧은 행이 「${id.slice(11)}」로 끝남`;
  if (id.startsWith("head_word:")) return `행 첫머리 「${id.slice(10)}」`;
  return id;
}

/** 서버가 센 행 + 사람이 더한 행. 렌더·규칙 조립이 같은 목록을 본다. */
function _signalRows() {
  const rows = signalState.data ? signalState.data.signals.slice() : [];
  return rows.concat(signalState.manual);
}

/**
 * 전문에서 신호를 센다(규칙만, 저장 없음). 체크 상태는 저장된 규칙이 있으면 그것, 없으면 권고.
 *
 * 왜 저장된 규칙을 우선하는가: 사람이 한 번 고른 것을 다음에 열 때 권고로 되돌리면 안 된다.
 * 저장된 어휘가 이번 셈에 없으면(표본이 바뀌었거나 손으로 넣은 것) 「손으로 넣음」 행으로 남긴다.
 */
async function _loadSignals() {
  const list = document.getElementById("comp-signals-list");
  const summary = document.getElementById("comp-signals-summary");
  if (!list) return;
  list.innerHTML = '<div class="placeholder">전문을 세는 중…</div>';
  signalState.data = null;
  signalState.manual = [];
  signalState.touched = false;
  const seq = ++signalState.seq;
  const docId = viewerState.docId;
  const partId = viewerState.partId;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(docId)}/segmentation/signals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part_id: partId }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    if (seq !== signalState.seq) return; // 그 사이 다른 문헌·권으로 다시 불렀다
    signalState.data = d;
    signalState.docId = docId;
    signalState.partId = partId;
    const saved = d.saved_rules;
    const ids = new Set(d.signals.map((s) => s.id));
    // «저장된 설정»은 origin이 있을 때만이 아니다 — D-116 전에 저장한 표제 어휘·스위치도 그렇다
    // (서버 rules_are_empty와 같은 판정). 없으면 서버가 권고에서 만든 recommended_rules를 따른다 —
    // 주 신호를 하나도 권고하지 못한 표본에서 서버는 주 신호를 기본값(켬)으로 두는데, 화면이
    // recommended 표시만 보고 다 끄면 「전부 적용」이 «적용할 구간이 없습니다»로 끝난다(Codex 지적).
    const hasSaved = !!saved && !!(saved.origin || saved.title_words?.length || saved.head_words?.length || Object.keys(saved.signals || {}).length);
    const base = hasSaved ? saved : d.recommended_rules;
    if (base) {
      signalState.checked = new Set();
      for (const s of d.signals) {
        if (s.toggle.startsWith("signals.")) {
          const k = s.toggle.slice(8);
          if (base.signals?.[k] !== false) signalState.checked.add(s.id);
        } else if (s.toggle === "title_words") {
          if ((base.title_words || []).includes(s.id.slice(11))) signalState.checked.add(s.id);
        } else if (s.toggle === "head_words") {
          if ((base.head_words || []).includes(s.id.slice(10))) signalState.checked.add(s.id);
        }
      }
      for (const w of base.title_words || []) {
        const id = `title_word:${w}`;
        if (!ids.has(id)) signalState.manual.push({ id, toggle: "title_words", label: `「${w}」로 끝남 (손으로 넣음)`, manual: true });
        signalState.checked.add(id);
      }
      for (const w of base.head_words || []) {
        const id = `head_word:${w}`;
        if (!ids.has(id)) signalState.manual.push({ id, toggle: "head_words", label: `행 첫머리 「${w}」 (손으로 넣음)`, manual: true });
        signalState.checked.add(id);
      }
      // 목록에 없는데 저장에서 꺼 둔 스위치(예: bbox가 없어 세지 못한 내려쓰기) — 화면에 행으로 보여
      // 켤 수 있게 한다. 안 보이면 저장할 때 조용히 되살아난다(Codex 지적).
      for (const [k, on] of Object.entries(base.signals || {})) {
        const id = k;
        if (on === false && !ids.has(id) && _SIGNAL_LABELS[k]) {
          signalState.manual.push({ id, toggle: `signals.${k}`, label: `${_SIGNAL_LABELS[k]} (이번 셈에는 없음 — 저장에서 꺼 둠)`, manual: true, group: ["short_line", "after_short", "indent"].includes(k) ? "aux" : "primary" });
        }
      }
      signalState.touched = hasSaved && saved.origin === "manual";
    } else {
      signalState.checked = new Set(d.signals.filter((s) => s.recommended).map((s) => s.id));
    }
    _rulesToForm(hasSaved ? saved : d.recommended_rules);
    if (summary) {
      const src = d.source || {};
      const where = src.l2_pages
        ? `확정본 ${src.l4_pages}쪽 + OCR ${src.l2_pages}쪽`
        : `확정본 ${src.l4_pages}쪽`;
      summary.textContent = `${d.lines}행 (${where}) · ` + (hasSaved ? (saved.origin === "manual" || !saved.origin ? "저장된 설정(손봄)" : "저장된 설정(자동 도출)") : "아직 저장 안 됨 — 권고 상태");
    }
    _renderSignals();
  } catch (e) {
    list.innerHTML = `<div class="placeholder">신호를 세지 못했습니다: ${e.message}</div>`;
  }
}

/** 저장이 끝났다는 것을 요약 줄이 말하게 한다 — 「후보 보기」 뒤에도 «아직 저장 안 됨»이 남아 있었다. */
function _markRulesSaved(saved) {
  if (signalState.data) signalState.data.saved_rules = saved;
  const summary = document.getElementById("comp-signals-summary");
  if (!summary || !signalState.data) return;
  const src = signalState.data.source || {};
  const where = src.l2_pages ? `확정본 ${src.l4_pages}쪽 + OCR ${src.l2_pages}쪽` : `확정본 ${src.l4_pages}쪽`;
  summary.textContent =
    `${signalState.data.lines}행 (${where}) · ` +
    (saved?.origin === "manual" ? "저장된 설정(손봄)" : "저장된 설정(자동 도출)");
}

function _renderSignals() {
  const list = document.getElementById("comp-signals-list");
  if (!list) return;
  list.innerHTML = "";
  // 주 신호(혼자 후보를 만드는 것)를 앞에, 보조를 뒤에 — 점수 순서대로 섞이면 «무엇이 규약인가»가 안 보인다
  const rows = _signalRows().sort((a, b) => (a.group === "aux") - (b.group === "aux"));
  if (!rows.length) {
    list.innerHTML = '<div class="placeholder">되풀이되는 표지를 찾지 못했습니다. 어휘를 직접 더하거나 목차를 쓰세요.</div>';
  }
  const maxScore = Math.max(0.01, ...rows.map((r) => r.score || 0));
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "comp-sig-row" + (r.group === "aux" ? " is-aux" : "") + (r.manual ? " is-manual" : "");
    const main = document.createElement("label");
    main.className = "comp-sig-main";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = signalState.checked.has(r.id);
    cb.addEventListener("change", () => {
      if (cb.checked) signalState.checked.add(r.id);
      else signalState.checked.delete(r.id);
      signalState.touched = true;
    });
    const label = document.createElement("span");
    label.className = "comp-sig-label";
    label.textContent = r.label || _signalLabel(r.id);
    const count = document.createElement("span");
    count.className = "comp-sig-count";
    count.textContent = r.count != null ? `${r.count}회` : "";
    main.appendChild(cb);
    main.appendChild(label);
    main.appendChild(count);
    if (r.group === "aux") {
      const aux = document.createElement("span");
      aux.className = "comp-sig-aux";
      aux.textContent = "보조";
      aux.title = "혼자서는 후보를 만들지 않고, 날짜·어휘가 있는 행의 신뢰도만 올립니다";
      main.appendChild(aux);
    }
    row.appendChild(main);
    if (r.score != null) {
      const bar = document.createElement("span");
      bar.className = "comp-sig-bar";
      bar.title = `점수 ${r.score} — 횟수 × 간격의 고름` + (r.chain != null ? ` × 날짜 사슬 ${r.chain}` : "");
      const fill = document.createElement("span");
      fill.style.width = `${Math.round((r.score / maxScore) * 100)}%`;
      bar.appendChild(fill);
      row.appendChild(bar);
    }
    if (r.examples && r.examples.length) {
      const ex = document.createElement("span");
      ex.className = "comp-sig-ex";
      ex.textContent = r.examples[0];
      ex.title = r.examples.join("\n");
      row.appendChild(ex);
    }
    if (r.manual) {
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "comp-sig-remove";
      rm.textContent = "×";
      rm.title = "이 어휘 행을 지웁니다";
      rm.addEventListener("click", () => {
        signalState.manual = signalState.manual.filter((m) => m.id !== r.id);
        signalState.checked.delete(r.id);
        // 목록 밖 스위치 행을 지우면 «저장에서 꺼 둠»도 지운다 — 기본(켬)으로 돌아간다
        if (r.toggle.startsWith("signals.") && signalState.data?.saved_rules?.signals) delete signalState.data.saved_rules.signals[r.id];
        signalState.touched = true;
        _renderSignals();
      });
      row.appendChild(rm);
    }
    list.appendChild(row);
  }
  const d = signalState.data;
  if (d && (d.dropped?.length || d.furniture?.length)) {
    const note = document.createElement("div");
    note.className = "comp-sig-dropped";
    const parts = [];
    if (d.dropped?.length) parts.push("쪽마다 같은 자리라 뺀 것: " + d.dropped.map((x) => `${x.label} ${x.count}회`).join(" · "));
    if (d.furniture?.length) parts.push("판심·엽수로 본 행: " + d.furniture.slice(0, 6).join(" · ") + (d.furniture.length > 6 ? ` … (${d.furniture.length})` : ""));
    note.textContent = parts.join("  |  ");
    note.title = "종이의 규약(판심·엽수·인쇄소 도장)은 글의 시작이 아니므로 후보에서 뺍니다";
    list.appendChild(note);
  }
}

/** 어휘 행을 목록에 더한다(켜진 채로). 이미 있으면 켜기만. */
function _addWordRow(id, toggle, label) {
  const exists = _signalRows().some((r) => r.id === id);
  if (!exists) signalState.manual.push({ id, toggle, label, manual: true });
  signalState.checked.add(id);
  signalState.touched = true;
  _renderSignals();
}

function _addManualWord() {
  const input = document.getElementById("comp-signals-add-word");
  if (!input) return;
  let w = input.value.trim();
  if (!w) return;
  if (!signalState.data) {
    showToast("먼저 「경계 제안」으로 신호를 세세요.", "warning");
    return;
  }
  if (w.startsWith("^")) {
    w = w.slice(1).trim();
    if (w) _addWordRow(`head_word:${w}`, "head_words", `행 첫머리 「${w}」 (손으로 넣음)`);
  } else {
    _addWordRow(`title_word:${w}`, "title_words", `「${w}」로 끝남 (손으로 넣음)`);
  }
  input.value = "";
}

/**
 * 「전부 적용해 새로 세우기」 — 신호 설정을 저장하고 그 규칙이 승인한 후보 전부로 개요를 세운다.
 * 사이드바 「자동 트리」와 같은 서버 경로(/segmentation/auto). 확인창 없음: 결과는 Git으로 되돌릴 수 있고,
 * 사용자가 신호를 골라 놓은 상태에서 누르는 단추라 «정말요?»는 되묻는 것이다.
 */
async function _applyAllFromSignals() {
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return;
  }
  if (!_signalsCurrent()) {
    await _openProposePanel();
    if (!_signalsCurrent()) return;
  }
  const rules = _rulesFromForm();
  const btn = document.getElementById("comp-apply-all-btn");
  if (btn) { btn.disabled = true; btn.textContent = "세우는 중…"; }
  try {
    let res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation-rules`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const llmSel = typeof getLlmModelSelection === "function" ? getLlmModelSelection("comp-llm-model-select") : {};
    const useToc = document.getElementById("comp-toc-use")?.checked !== false;
    res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation/auto`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part_id: viewerState.partId,
        use_llm_toc: rules.toc_llm,
        force_provider: rules.toc_llm ? llmSel.force_provider || null : null,
        force_model: rules.toc_llm ? llmSel.force_model || null : null,
        replace: "all",
        use_toc: useToc, // 목차 줄을 껐으면 서버도 목차 감지·대조·LLM을 모두 건너뛴다
        toc_pages: useToc ? _tocPagesFromInput() : null,
        toc_only: useToc ? null : false,
      }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    if (typeof describeAutoTreeResult === "function") showToast(describeAutoTreeResult(d, rules.toc_llm), "success");
    else showToast(`후보 ${d.proposals} 중 ${d.applied}개로 개요를 세웠습니다`, "success");
    _closeProposePanel();
    await _loadCompositionData();
    if (typeof refreshContentsTree === "function") refreshContentsTree();
  } catch (e) {
    showToast(`새로 세우기 실패: ${e.message}`, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "전부 적용해 새로 세우기"; }
  }
}

const proposeState = {
  data: null, // /segmentation/propose 응답
  checked: new Set(), // 승인한 제안 index
  tocOnly: false, // 목차 대응만 기본 선택 중인가
  showRejected: false, // 문턱 아래 후보도 보이는가
  levels: new Map(), // 제안 index → 사람이 바꾼 깊이
  roles: new Map(), // 제안 index → 사람이 바꾼 역할
  toc: null, // {pages, entries} — 「목차 감지」로 확인한 것. null이면 서버가 규칙으로 자동
};

function _tocPagesFromInput() {
  const raw = document.getElementById("comp-toc-pages")?.value || "";
  const pages = raw.split(/[,，\s]+/).map((x) => Number(x)).filter((n) => Number.isInteger(n) && n > 0);
  return pages.length ? pages : null;
}

/**
 * 목차 쪽을 판별하고 항목을 뽑는다 (D-089). 규칙 또는 LLM. 결과는 다음 제안에 신호로 들어간다.
 */
async function _detectToc(useLlm) {
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return null;
  }
  const summary = document.getElementById("comp-toc-summary");
  if (summary) summary.textContent = useLlm ? "목차: LLM이 읽는 중…" : "목차: 규칙으로 찾는 중…";
  // 결과 요약에 «규칙»인지 «LLM»인지 남긴다 — 제안 목록의 「목차 …」 근거가 어디서 왔는지 보이도록
  proposeState.tocSource = useLlm ? "LLM" : "규칙";
  try {
    const llmSel =
      typeof getLlmModelSelection === "function"
        ? getLlmModelSelection("comp-llm-model-select")
        : { force_provider: null, force_model: null };
    const res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation/toc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part_id: viewerState.partId,
        toc_pages: _tocPagesFromInput(),
        use_llm: !!useLlm,
        force_provider: useLlm ? llmSel.force_provider : null,
        force_model: useLlm ? llmSel.force_model : null,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    if (!data.toc_pages.length) {
      proposeState.toc = null;
      if (summary) summary.textContent = "없음 (쪽 번호를 직접 넣어 보세요)";
      return null;
    }
    proposeState.toc = { pages: data.toc_pages, entries: data.entries };
    if (summary)
      summary.textContent = `${data.toc_pages.join(",")}쪽 · ${data.entries.length}항목 (${proposeState.tocSource})`;
    const input = document.getElementById("comp-toc-pages");
    if (input && !input.value) input.value = data.toc_pages.join(",");
    if (data.meta?.error) showToast(`LLM 실패로 규칙 추출을 썼습니다: ${data.meta.error}`, "warning");
    return proposeState.toc;
  } catch (e) {
    if (summary) summary.textContent = `실패 — ${e.message}`;
    return null;
  }
}

/**
 * 신호 목록의 체크 상태 + 참고·억제 칸 → 이 문헌의 segmentation_rules (D-116).
 *
 * 스위치 신호(날짜·○권점·卷頭·짧은 행·행갈음·내려쓰기)는 signals.*로, 어휘 행은
 * title_words·head_words로 간다. 목록에 없는 스위치(예: bbox가 없어 세지 못한 내려쓰기)는
 * 적지 않는다 — 빠진 키는 켜진 것이라(normalize_rules) L2가 생기면 저절로 살아난다.
 * origin: 권고 그대로면 "induced", 사람이 하나라도 바꿨으면 "manual".
 */
function _rulesFromForm() {
  const suppress = (document.getElementById("comp-rules-suppress")?.value || "")
    .split("\n")
    .map((w) => w.trim())
    .filter(Boolean);
  const maxChars = Number(document.getElementById("comp-rules-maxchars")?.value || 14);
  const reference = (document.getElementById("comp-rules-reference")?.value || "").trim();
  // 바탕은 저장된 규칙 — 목록에 없는 스위치·min_confidence·옛 판심 목록을 잃지 않는다.
  // use_date·use_layout(옛 굵은 스위치)은 버린다: 이제 signals가 낱낱이 적히고, 남겨 두면
  // normalize_rules가 그것으로 하위 스위치를 다시 끈다.
  const saved = signalState.data?.saved_rules || {};
  const signals = { ...(saved.signals || {}) };
  const title_words = [];
  const head_words = [];
  let touched = signalState.touched;
  for (const row of _signalRows()) {
    const on = signalState.checked.has(row.id);
    if (row.toggle.startsWith("signals.")) signals[row.toggle.slice(8)] = on;
    else if (on && row.toggle === "title_words") title_words.push(row.id.slice("title_word:".length));
    else if (on && row.toggle === "head_words") head_words.push(row.id.slice("head_word:".length));
    if (row.manual || on !== !!row.recommended) touched = true;
  }
  const induced = signalState.data?.furniture || [];
  const furniture = [...new Set([...(saved.furniture || []), ...induced])];
  const { use_date, use_layout, origin, ...rest } = saved; // eslint-disable-line no-unused-vars
  return {
    ...rest,
    signals,
    title_words,
    head_words,
    furniture,
    suppress,
    max_title_chars: maxChars,
    reference_text: reference,
    toc_llm: !!document.getElementById("comp-toc-llm")?.checked,
    origin: touched ? "manual" : "induced",
  };
}

function _rulesToForm(rules) {
  const s = document.getElementById("comp-rules-suppress");
  const m = document.getElementById("comp-rules-maxchars");
  if (s) s.value = (rules?.suppress || []).join("\n");
  const tocLlm = document.getElementById("comp-toc-llm");
  if (tocLlm && rules && typeof rules.toc_llm === "boolean") tocLlm.checked = rules.toc_llm;
  if (m) m.value = rules?.max_title_chars || 14;
  const r = document.getElementById("comp-rules-reference");
  if (r) {
    r.value = rules?.reference_text || "";
    _updateReferenceCount();
    if (!r._countBound) {
      r.addEventListener("input", _updateReferenceCount);
      r._countBound = true;
    }
  }
}

/**
 * 해제 글자수를 알려 준다. 길면 «골라서 넘긴다»는 것도 함께.
 *
 * 왜 필요한가: 한국고전종합DB 해제는 2만 자가 넘는다(운양집 23,894자 실측). 통째로 붙여
 * 넣는 것이 맞는데, 그러면 «너무 길어서 잘리지 않나»가 걱정된다. 저장은 통째로 하고
 * 프롬프트에 넣을 때만 권별 서술을 골라 8,000자로 간추린다는 것을 여기서 알린다.
 */
function _updateReferenceCount() {
  const r = document.getElementById("comp-rules-reference");
  const out = document.getElementById("comp-rules-reference-count");
  if (!r || !out) return;
  const n = r.value.length;
  if (!n) {
    out.textContent = "";
  } else if (n <= 8000) {
    out.textContent = `${n.toLocaleString()}자 — 그대로 넘깁니다`;
  } else {
    out.textContent = `${n.toLocaleString()}자 — 통째로 저장하고, LLM에는 권별 내용이 있는 데를 골라 8,000자로 간추려 넘깁니다`;
  }
}

/**
 * «지금 경계» — 이미 저장된 경계를 편성 탭에도 띄운다.
 *
 * 왜 필요한가: 편성 탭을 열면 「경계 제안」을 누르기 전까지 화면이 비어 있었다. 저장된 것은
 * 사이드바 「내용」에만 있어서, 양쪽이 어긋나 보이고 «매번 새로 제안해서 적용해야 하나»로
 * 읽혔다(사용자 지적 2026-09-04). 제안은 «새로 훑어 보는 것»이고, 이미 한 일은 여기 있다.
 *
 * 사이드바와 같은 것을 가리킨다 — 행을 누르면 같은 단위가 골라지고(unit-selected) 그 쪽으로 간다.
 */
async function _renderCurrentBoundaries() {
  const list = document.getElementById("comp-current-list");
  const stats = document.getElementById("comp-current-stats");
  if (!list) return;
  if (!viewerState.docId || !viewerState.partId) {
    list.innerHTML = '<div class="placeholder">문헌과 권을 고르면 지금 경계가 보입니다.</div>';
    if (stats) stats.textContent = "";
    return;
  }
  try {
    const q = `part_id=${encodeURIComponent(viewerState.partId)}`;
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/boundaries?${q}`,
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    const rows = (data.boundaries || []).filter((b) => b.status !== "deprecated");
    compState.currentBoundaries = rows;
    const roleName = { container: "묶음", article: "기사", fragment: "조각" };
    const byRole = rows.reduce((a, b) => {
      const r = b.role || "article";
      a[r] = (a[r] || 0) + 1;
      return a;
    }, {});
    if (stats) {
      stats.textContent = rows.length
        ? `${rows.length}개 — ` +
          ["container", "article", "fragment"]
            .filter((r) => byRole[r])
            .map((r) => `${roleName[r]} ${byRole[r]}`)
            .join(" · ")
        : "";
    }
    list.innerHTML = "";
    if (!rows.length) {
      list.innerHTML =
        '<div class="placeholder">아직 경계가 없습니다. 「경계 제안」으로 찾아 보거나, 사이드바 「내용」의 «＋ 경계 넣기»로 첫 경계를 놓으세요.</div>';
      return;
    }
    const selected = typeof currentUnitId === "function" ? currentUnitId() : null;
    for (const b of rows) {
      const row = document.createElement("div");
      row.className = "comp-cur-row" + (b.id === selected ? " is-selected" : "");
      row.dataset.unitId = b.id;
      const role = b.role || "article";
      const title = document.createElement("span");
      title.className = "comp-cur-title";
      const mark = { container: "▣ ", article: "", fragment: "· " }[role] || "";
      const stale = b.anchor_status === "stale";
      title.textContent = `${stale ? "⚠ " : ""}${mark}${b.title || "(제목 없음)"}`;
      if (stale) title.classList.add("comp-cur-stale");
      const meta = document.createElement("span");
      meta.className = "comp-cur-meta";
      const p0 = b.start ? b.start.page : null;
      const p1 = b.end ? b.end.page : null;
      const pages = p0 == null ? "" : p1 && p1 !== p0 ? `${p0}~${p1}쪽` : `${p0}쪽`;
      meta.textContent = `${roleName[role]}${b.role_estimated ? "(추정)" : ""} · ${b.level}단 · ${pages}`;
      if (b.role_estimated) meta.classList.add("is-estimated");
      row.appendChild(title);
      row.appendChild(meta);
      row.title = stale
        ? "확정본이 바뀐 뒤 자리를 못 찾았습니다 — 사이드바 「내용」에서 ▲▼로 옮겨 주세요"
        : "누르면 그 기사로 갑니다 (사이드바 「내용」과 같은 선택)";
      row.addEventListener("click", () => {
        // 사이드바와 같은 것을 가리킨다 — 트리 행을 눌렀을 때와 똑같이 동작한다
        const tree = document.querySelector(
          `#contents-tree .contents-block[data-block-id="${CSS.escape(b.id)}"]`,
        );
        if (tree) tree.click();
        list.querySelectorAll(".comp-cur-row.is-selected").forEach((el) => {
          el.classList.remove("is-selected");
        });
        row.classList.add("is-selected");
      });
      list.appendChild(row);
    }
  } catch (e) {
    list.innerHTML = `<div class="placeholder">지금 경계를 읽지 못했습니다: ${e.message}</div>`;
  }
}

/**
 * 「경계 제안」 — 편성 흐름의 입구 (D-116).
 *
 * 순서: 전문에서 신호를 센다(규칙만) → 목차 쪽을 찾는다(규칙만) → 저장된 규칙(없으면 권고)으로
 * 후보를 보인다. 여기서는 아무것도 저장하지 않는다 — 「후보 보기」·「전부 적용」이 저장한다.
 */
async function _openProposePanel() {
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return;
  }
  const panel = document.getElementById("comp-propose-panel");
  const list = document.getElementById("comp-propose-list");
  if (!panel || !list) return;
  panel.style.display = "";
  list.innerHTML = '<div class="placeholder">전문을 세는 중…</div>';
  proposeState.toc = null;
  const summary = document.getElementById("comp-toc-summary");
  if (summary) summary.textContent = "찾는 중…";
  await Promise.all([_loadSignals(), _detectToc(false)]);
  await _proposeBoundaries();
}

async function _proposeBoundaries() {
  if (!viewerState.docId || !viewerState.partId) {
    showToast("사이드바에서 문헌과 권을 먼저 고르세요.", "warning");
    return;
  }
  const panel = document.getElementById("comp-propose-panel");
  const list = document.getElementById("comp-propose-list");
  if (!panel || !list) return;
  panel.style.display = "";
  list.innerHTML = '<div class="placeholder">권 전체 확정본을 읽어 경계를 찾는 중…</div>';
  const useToc = document.getElementById("comp-toc-use")?.checked !== false;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation/propose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part_id: viewerState.partId,
        rules: signalState.data ? _rulesFromForm() : null,
        use_toc: useToc,
        toc: useToc ? proposeState.toc : null,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    proposeState.data = data;
    // 목차가 있는 책은 목차 항목만 기본 선택한다(사용자 요청). 날짜·형식 후보는 목차 없는
    // 일기류를 위한 것이다. 「전부 선택」 단추로 언제든 넓힐 수 있다.
    const hasToc = !!(data.toc && data.toc.matches && data.toc.matches.length);
    proposeState.tocOnly = hasToc;
    proposeState.levels = new Map();
    proposeState.roles = new Map();
    _resetChecked(hasToc);
    _rulesToForm(data.rules);
    _renderProposals();
  } catch (e) {
    list.innerHTML = `<div class="placeholder">${_treeEscHtml ? _treeEscHtml(e.message) : e.message}</div>`;
  }
}

// 근거 토큰 → 사람 말 (양수 신호는 초록, 감점은 붉게)
const _REASON_LABELS = {
  date: ["날짜", "pos"], mark: ["○ 표지", "pos"], short_line: ["짧은 행", "pos"], indent: ["내려쓰기", "pos"],
  same_day: ["같은 날", "pos"], month_rolled: ["달 넘김", ""], long_line: ["긴 행", "neg"],
  no_title_word: ["어휘 없음", "neg"], same_day_repeat: ["같은 날짜 되풀이", "neg"],
  word_in_clause: ["문장 속 어휘", "neg"], date_jump: ["날짜 역행", "neg"], suppressed: ["억제", "neg"],
  volume_repeat: ["卷 되풀이(판심)", "neg"], furniture: ["판심·엽수", "neg"],
  date_wrap: ["행 넘긴 날짜", "pos"], after_short: ["행갈음 시작", "pos"],
  indent_shallow: ["얕은 들여쓰기 → 묶음", ""], indent_deep: ["깊은 들여쓰기 → 조각", ""],
};
function _reasonChip(r) {
  let label = r, cls = "";
  if (r.startsWith("toc:")) { label = `목차 ${r.slice(4)}`; cls = "pos"; }
  else if (r.startsWith("volume:")) { label = `卷 ${r.slice(7)}`; cls = "pos"; }
  else if (r.startsWith("title_word:")) { label = `어휘 ${r.slice(11)}`; cls = "pos"; }
  else if (r.startsWith("head_word:")) { label = `행머리 ${r.slice(10)}`; cls = "pos"; }
  else if (_REASON_LABELS[r]) [label, cls] = _REASON_LABELS[r];
  const s = document.createElement("span");
  s.className = `prop-reason ${cls}`;
  s.textContent = label;
  return s;
}

/** 목차 대응이거나 卷 표제 — «목차 항목만» 기본 선택에서 살아남는 것들. */
function _isTocProposal(p) {
  if (p.kind === "volume") return true; // 卷이 빠지면 트리에 묶음이 없다
  return (p.reasons || []).some((r) => r.startsWith("toc:"));
}

/** 기본 체크를 다시 놓는다. tocOnly면 목차 대응만, 아니면 승인된 것 전부. */
function _resetChecked(tocOnly) {
  const data = proposeState.data;
  proposeState.tocOnly = !!tocOnly;
  proposeState.checked = new Set(
    data.proposals
      .map((p, i) => (p.accepted && (!tocOnly || _isTocProposal(p)) ? i : -1))
      .filter((i) => i >= 0),
  );
}

function _renderProposals() {
  const data = proposeState.data;
  const list = document.getElementById("comp-propose-list");
  const stats = document.getElementById("comp-propose-stats");
  if (!data || !list) return;
  const acceptedCount = proposeState.checked.size;
  if (stats) {
    stats.textContent = `${data.stats.lines}행 · 후보 ${data.proposals.length} · 승인 ${acceptedCount}` +
      (data.stats.suppressed ? ` · 억제 ${data.stats.suppressed}` : "");
    const hasToc = !!(data.toc && data.toc.matches && data.toc.matches.length);
    if (hasToc) {
      const sw = document.createElement("button");
      sw.type = "button";
      sw.className = "text-btn";
      sw.style.cssText = "font-size:11px; margin-left:6px;";
      sw.textContent = proposeState.tocOnly ? "목차 항목만 선택 중 → 전부 선택" : "전부 선택 중 → 목차 항목만";
      sw.title = "목차가 있는 책은 목차 항목만 단위로 삼는 것이 기본입니다. 날짜·형식 후보까지 넓히려면 누르세요";
      sw.addEventListener("click", () => {
        _resetChecked(!proposeState.tocOnly);
        _renderProposals();
      });
      stats.appendChild(sw);
    }
  }
  // 목차 신호 요약 (D-089)
  const tocSummary = document.getElementById("comp-toc-summary");
  const unmatchedBox = document.getElementById("comp-toc-unmatched");
  if (data.toc && data.toc.entries?.length) {
    const n = data.toc.entries.length;
    const m = data.toc.matches?.length || 0;
    if (tocSummary)
      tocSummary.textContent =
        `${data.toc.pages.join(",")}쪽 · ${n}항목 중 ${m} 대조` +
        (proposeState.tocSource ? ` (${proposeState.tocSource})` : "");
    if (unmatchedBox) {
      const un = data.toc.unmatched || [];
      unmatchedBox.style.display = un.length ? "" : "none";
      unmatchedBox.textContent = un.length
        ? `목차에는 있으나 본문에서 못 찾음 (${un.length}): ` + un.map((u) => u.title).join(" · ")
        : "";
    }
  } else {
    if (tocSummary && !proposeState.toc) tocSummary.textContent = "없음";
    if (unmatchedBox) unmatchedBox.style.display = "none";
  }
  list.innerHTML = "";
  if (!data.proposals.length) {
    list.innerHTML =
      '<div class="placeholder">경계 후보가 없습니다. 위 「이 책의 신호」에서 신호를 더 켜거나 어휘를 더해 보세요 (예: 談草, ^有).</div>';
    return;
  }
  // 문턱 아래 후보는 기본으로 숨긴다 — 보이는 목록은 «승인 후보»여야 읽힌다
  const rejected = data.proposals.filter((p) => !p.accepted).length;
  if (rejected && stats) {
    const tg = document.createElement("button");
    tg.type = "button";
    tg.className = "text-btn";
    tg.style.cssText = "font-size:11px; margin-left:6px;";
    tg.textContent = proposeState.showRejected ? `문턱 아래 ${rejected}개 숨기기` : `문턱 아래 ${rejected}개 보기`;
    tg.addEventListener("click", () => {
      proposeState.showRejected = !proposeState.showRejected;
      _renderProposals();
    });
    stats.appendChild(tg);
  }
  data.proposals.forEach((p, i) => {
    if (!p.accepted && !proposeState.showRejected && !proposeState.checked.has(i)) return;
    const row = document.createElement("div");
    row.className = "comp-propose-row" + (p.suppressed ? " suppressed" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = proposeState.checked.has(i);
    cb.disabled = p.suppressed;
    cb.addEventListener("change", () => {
      if (cb.checked) proposeState.checked.add(i);
      else proposeState.checked.delete(i);
      _renderProposals();
    });
    const body = document.createElement("div");
    const title = document.createElement("div");
    title.className = "prop-title";
    title.textContent = p.title;
    if (p.reasons.some((r) => r.startsWith("toc:"))) {
      const badge = document.createElement("span");
      badge.className = "prop-badge-toc";
      badge.textContent = p.kind === "volume" ? "목차·권" : "목차";
      title.appendChild(badge);
    }
    title.title = "누르면 그 쪽으로 이동";
    title.addEventListener("click", () => {
      if (typeof goToPage === "function") goToPage(p.page);
    });
    const meta = document.createElement("div");
    meta.className = "prop-meta";
    const d = p.date || {};
    const dateTxt = d.month || d.day
      ? `${d.ganzhi ? d.ganzhi + " " : ""}${d.month ? d.month + "월" : "?월"} ${d.day ? d.day + "일" : ""}` +
        (d.month_inferred ? (d.month_rolled ? " (달 넘김 추정)" : " (달 물려받음)") : "")
      : "";
    // 행 중간 경계(D-090 2단계): 「○七日」처럼 열 중간에서 날이 바뀌는 판식은 몇째 글자인지도 보인다
    const where = `${p.page}쪽 ${p.line_index + 1}행` + (p.char_offset ? ` ${p.char_offset + 1}자째` : "");
    meta.textContent = [where, dateTxt, p.place ? `장소·상대: ${p.place}` : ""].filter(Boolean).join("  |  ") + "  ";
    for (const r of p.reasons) meta.appendChild(_reasonChip(r));
    body.appendChild(title);
    body.appendChild(meta);
    // 신뢰도·역할·깊이·억제를 한 줄로 (세로로 쌓으면 행마다 60px을 먹었다)
    const right = document.createElement("div");
    right.className = "prop-actions";
    const conf = document.createElement("span");
    const cls = p.confidence >= 0.8 ? "high" : p.confidence >= 0.5 ? "mid" : "low";
    conf.className = `prop-conf ${cls}`;
    conf.textContent = `${Math.round(p.confidence * 100)}%`;
    right.appendChild(conf);
    // 역할(뜻)과 깊이(구조)는 따로(D-092). 들여쓰기·목차로 추정한 값이고 적용 전에 바꿀 수 있다
    const rl = document.createElement("select");
    rl.className = "prop-level";
    rl.title = "역할 — 묶음(卷·集·編) / 기사(번역·주석 단위) / 조각(기사 안 문단)";
    for (const [v, label] of [["container", "묶음"], ["article", "기사"], ["fragment", "조각"]]) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = label;
      rl.appendChild(o);
    }
    rl.value = proposeState.roles.get(i) ?? p.role ?? "article";
    rl.addEventListener("click", (ev) => ev.stopPropagation());
    rl.addEventListener("change", () => proposeState.roles.set(i, rl.value));
    right.appendChild(rl);
    const lv = document.createElement("input");
    lv.type = "number";
    lv.min = "1";
    lv.className = "prop-level";
    lv.title = "깊이(중첩 단계, 1부터) — 들여쓰기·목차로 추정";
    lv.value = String(proposeState.levels.get(i) ?? p.level ?? 2);
    lv.addEventListener("click", (ev) => ev.stopPropagation());
    lv.addEventListener("change", () => proposeState.levels.set(i, Math.max(1, Number(lv.value) || 2)));
    right.appendChild(lv);
    if (!p.suppressed) {
      const sup = document.createElement("button");
      sup.type = "button";
      sup.className = "prop-suppress";
      sup.textContent = "억제";
      sup.title = "이 행을 표제로 보지 않도록 문헌 규칙에 추가";
      sup.addEventListener("click", async () => {
        const rules = _rulesFromForm();
        rules.suppress = [...rules.suppress, (data.lines.find((l) => l.page === p.page && l.line_index === p.line_index)?.text || p.title).trim()];
        _rulesToForm(rules);
        await _saveRulesAndRepropose();
      });
      right.appendChild(sup);
    }
    row.appendChild(cb);
    row.appendChild(body);
    row.appendChild(right);
    list.appendChild(row);
  });
}

/**
 * 해제와 본문의 짧은 행에서 표제 어휘 후보를 뽑는다 (D-092 남은 것).
 *
 * 왜 바로 넣지 않는가: 규칙은 이 문헌의 편집 정책이고 판단은 사람의 것이다(D-080 계열).
 * 후보를 칩으로 보여 주고, 누른 것만 표제 어휘 칸에 붙는다.
 */
async function _suggestRules() {
  const out = document.getElementById("comp-rules-suggest-out");
  const btn = document.getElementById("comp-rules-suggest-btn");
  if (!out || !viewerState.docId || !viewerState.partId) {
    if (out) out.textContent = "문헌과 권을 먼저 고르세요.";
    return;
  }
  const sel = document.getElementById("comp-llm-model-select");
  const [provider, model] = (sel && sel.value ? sel.value : "").split(":");
  out.textContent = "해제와 본문을 보는 중…";
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation-rules/suggest`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          part_id: viewerState.partId,
          reference_text: document.getElementById("comp-rules-reference")?.value ?? null,
          force_provider: provider || null,
          force_model: model || null,
        }),
      },
    );
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    _renderRuleCandidates(d);
  } catch (e) {
    out.textContent = `뽑기 실패: ${e.message}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _renderRuleCandidates(d) {
  const out = document.getElementById("comp-rules-suggest-out");
  out.textContent = "";
  const words = d.title_words || [];
  const sup = d.suppress || [];
  if (!words.length && !sup.length) {
    out.textContent = d.error
      ? `뽑지 못했습니다: ${d.error}`
      : `후보가 없습니다 (표본 ${d.sample_count || 0}행). 손으로 적으세요.`;
    return;
  }
  const line = document.createElement("div");
  line.textContent = `표본 ${d.sample_count || 0}행${d.model ? ` · ${d.model}` : ""} — 누르면 넣습니다`;
  out.appendChild(line);
  const add = (text, targetId, isList) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "comp-rules-cand";
    b.textContent = text;
    b.title = isList ? "억제 목록에 넣기" : "신호 목록에 표제 어휘로 넣기 (켜진 채로)";
    b.addEventListener("click", () => {
      if (isList) {
        const el = document.getElementById(targetId);
        if (!el) return;
        const cur = el.value.trim();
        const has = cur.split(/\n/).some((x) => x.trim() === text);
        if (!has) el.value = cur ? cur + "\n" + text : text;
      } else {
        _addWordRow(`title_word:${text}`, "title_words", `「${text}」로 끝남 (LLM 후보)`);
      }
      b.disabled = true;
    });
    out.appendChild(b);
  };
  for (const w of words) add(w, null, false);
  for (const s of sup) add(s, "comp-rules-suppress", true);
  if (d.note) {
    const n = document.createElement("div");
    n.textContent = d.note;
    out.appendChild(n);
  }
}

async function _saveRulesAndRepropose() {
  if (!_signalsCurrent()) {
    await _openProposePanel(); // 다른 문헌·권의 신호를 이 문헌에 저장하면 안 된다
    return;
  }
  const rules = _rulesFromForm();
  try {
    const res = await fetch(`/api/documents/${viewerState.docId}/segmentation-rules`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const saved = await res.json().catch(() => null);
    _markRulesSaved(saved?.segmentation_rules || rules);
  } catch (e) {
    showToast(`규칙 저장 실패: ${e.message}`, "error");
    return;
  }
  // 목차 줄의 LLM 스위치가 켜져 있으면 여기서 한 번 모델을 부른다(목차 쪽 텍스트만)
  if (document.getElementById("comp-toc-use")?.checked !== false) {
    await _detectToc(!!rules.toc_llm);
  }
  await _proposeBoundaries();
}

function _closeProposePanel() {
  _renderCurrentBoundaries(); // 제안을 접으면 «지금 경계»로 돌아온다
  const panel = document.getElementById("comp-propose-panel");
  if (panel) panel.style.display = "none";
}

/**
 * 승인한 제안 사이의 구간을 단위으로 만든다.
 * 구간은 화면에서 다시 계산한다 — 사용자가 체크를 바꾸면 서버의 spans와 달라지기 때문이다.
 */
async function _applyProposals() {
  const data = proposeState.data;
  if (!data) return;
  const idx = [...proposeState.checked].sort((a, b) => a - b);
  if (!idx.length) {
    showToast("승인한 경계가 없습니다.", "warning");
    return;
  }
  const lines = data.lines;
  const keyOf = (l) => `${l.page}:${l.line_index}`;
  const pos = new Map(lines.map((l, i) => [keyOf(l), i]));
  // 경계 = (행, 행 안 글자 오프셋). 다음 경계가 행 중간이면 이 구간은 같은 행의 그 글자 앞에서 끝난다 (D-090 2단계)
  const starts = idx
    .map((i) => ({ i, li: pos.get(keyOf(data.proposals[i])), off: data.proposals[i].char_offset || 0, p: data.proposals[i] }))
    .sort((a, b) => a.li - b.li || a.off - b.off);
  const endBefore = (next) =>
    next.off > 0
      ? { page: lines[next.li].page, line_index: lines[next.li].line_index, char_end: next.off }
      : { page: lines[next.li - 1].page, line_index: lines[next.li - 1].line_index, char_end: null };
  const spans = [];
  if (starts[0].li > 0 || starts[0].off > 0) {
    spans.push({ title: lines[0].text.trim().slice(0, 20) || "(앞부분)", kind: "front", level: 2, role: "article",
      start: { page: lines[0].page, line_index: lines[0].line_index, char_offset: 0 },
      end: endBefore(starts[0]) });
  }
  starts.forEach((s, k) => {
    const end = k + 1 < starts.length
      ? endBefore(starts[k + 1])
      : { page: lines[lines.length - 1].page, line_index: lines[lines.length - 1].line_index, char_end: null };
    spans.push({ title: s.p.title, kind: s.p.kind || "",
      level: proposeState.levels.get(s.i) ?? s.p.level ?? 2,
      role: proposeState.roles.get(s.i) ?? s.p.role ?? "article",
      start: { page: lines[s.li].page, line_index: lines[s.li].line_index, char_offset: s.off },
      end });
  });
  if (!confirm(`체크한 ${starts.length}개로 단위를 다시 세웁니다(전에 제안으로 만든 경계 중 체크가 빠진 것은 지워지고, 손으로 넣은 경계는 남습니다). 계속할까요?`)) return;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part_id: viewerState.partId,
        spans,
        pages: data.pages || null,
        replace: "proposal", // 체크 상태가 곧 트리 — 전에 제안으로 만든 경계 중 빠진 것은 지운다
      }),
    });
    const result = await res.json();
    if (!res.ok) throw new Error(result.error || `HTTP ${res.status}`);
    showToast(`단위 ${result.created.length}개 적용` + (result.removed ? ` · 이전 제안 경계 ${result.removed}개 정리` : "") + (result.errors.length ? ` · 실패 ${result.errors.length}` : ""),
      result.errors.length ? "warning" : "success");
    _closeProposePanel();
    await _loadCompositionData();
    if (typeof refreshContentsTree === "function") refreshContentsTree();
  } catch (e) {
    showToast(`적용 실패: ${e.message}`, "error");
  }
}
