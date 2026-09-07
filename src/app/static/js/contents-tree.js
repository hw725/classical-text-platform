/**
 * 내용 트리 — 사이드바에서 문헌 > 권 > 단위로 훑고, 단위를 누르면 그 내용이 있는
 * 쪽(PDF)으로 가며 해석 편집기 다섯이 그 단위로 맞춰진다 (D-085 → D-096 → D-098).
 *
 * 왜 필요한가:
 *   원본 층(L1~L4)은 쪽 단위일 수밖에 없다. 그런데 교감이 끝난 뒤에는 연구자가
 *   쪽을 넘기며 내용을 찾는 게 아니라 **내용에서 쪽으로** 가야 한다. 단위에는
 *   이미 source_refs(쪽·레이아웃 블록)가 있는데, 지금까지는 「이 쪽에 어떤 블록이
 *   있나」(하단 엔티티 탭)만 있고 그 반대 방향이 없었다. 이 파일이 그 반대 방향이다.
 *
 * `GET /api/documents/{문헌}/contents`가 경계 목록에서 만들어 준 것을 그리기만 한다.
 * 편성은 문헌의 것이므로(D-097) 해석 저장소가 없어도 트리는 선다.
 *
 * 의존성:
 *   viewerState (sidebar-tree.js) · goToPage (sidebar-tree.js)
 *   interpState (interpretation.js) · layoutState/_selectBlock (layout-editor.js)
 *   _treeEscHtml (sidebar-tree.js)
 */

const contentsState = {
  selectedUnitId: null, // 「내용」에서 고른 단위 — 해석 편집기 다섯이 이것을 따라간다
  insertOpen: false, // «경계 넣기» 폼을 펼쳤는가 (기본 접힘)
  picking: false, // «찍기» 모드 — 원본 이미지를 눌러 경계 자리를 정하는 중 (B-002)
  pickForm: null,
  interpId: null, // 마지막으로 그린 해석 저장소
  data: null, // /contents 응답
  collapsedParts: new Set(), // 접어 둔 권(part) id
  openFragments: new Set(), // (구) 조각 접기 — 중첩 개요로 대체
  closedNodes: new Set(), // 접어 둔 단위 id (중첩 개요)
  anchorHighlight: null, // 시작 행 점선 {bbox, imageWidth, imageHeight, page}
};

/**
 * 「내용」 섹션을 보이거나 숨긴다.
 *
 * 왜 따로 있는가: 사이드바 섹션은 모두 HTML에서 display:none으로 시작하고 JS가
 * 모드에 따라 켠다. 이 섹션은 처음 만들 때 켜는 코드가 없어 **어디서도 보이지
 * 않았다** (v1.2.3 첫 배포에서 발견). 해석 섹션(interp-section)을 켜고 끄는 네 자리가
 * 이 함수를 같이 부른다 — 내용 트리는 해석 저장소가 있어야 뜨는 것이므로 같은 조건이다.
 * 추출 프로필(data-profile="collation" → hidden)에서는 hidden 속성이 우선하므로
 * 여기서 display를 비워도 보이지 않는다.
 */
function setContentsSectionVisible(visible) {
  const section = document.getElementById("contents-section");
  if (!section) return;
  section.style.display = visible ? "" : "none";
}

/**
 * 내용 트리를 다시 불러 그린다. 문헌을 고르거나 편성이 바뀔 때 부른다.
 *
 * 편성은 문헌의 것이므로(D-097) 해석 저장소가 없어도 그린다 — 문헌만 있으면 된다.
 */
async function refreshContentsTree() {
  const container = document.getElementById("contents-tree");
  if (!container) return;

  const docId = typeof viewerState !== "undefined" ? viewerState.docId : null;
  if (!docId) {
    contentsState.data = null;
    container.innerHTML =
      '<div class="placeholder">문헌을 고르면 편성된 내용이 표시됩니다</div>';
    return;
  }
  contentsState.interpId = typeof interpState !== "undefined" ? interpState.interpId : null;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(docId)}/contents`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      container.innerHTML = `<div class="placeholder">${_treeEscHtml(err.error || "내용 트리를 불러오지 못했습니다")}</div>`;
      return;
    }
    contentsState.data = await res.json();
    _renderContentsTree(container);
    if (contentsState.selectedUnitId) {
      const row = document.querySelector(
        `#contents-tree .contents-block[data-block-id="${CSS.escape(contentsState.selectedUnitId)}"]`,
      );
      if (row) row.classList.add("unit-selected");
    }
    _updateExportLink(docId);
    highlightContentsForPage(viewerState.pageNum);
  } catch (e) {
    container.innerHTML = `<div class="placeholder">내용 트리 오류: ${_treeEscHtml(e.message)}</div>`;
  }
}

function _renderContentsTree(container) {
  const data = contentsState.data;
  container.innerHTML = "";
  container.appendChild(_renderInsertForm());
  if (!data || data.total_units === 0) {
    const ph = document.createElement("div");
    ph.className = "placeholder";
    ph.textContent = "단위(권·기사)가 없습니다. 위의 «경계 넣기»로 첫 경계를 놓거나, 「자동 트리」·편성 인덱스의 「경계 제안」을 쓰세요.";
    container.appendChild(ph);
    return;
  }

  // 문헌 > 권 > 단위 (B-004). 전에는 Work로 묶었는데, Work는 해석 저장소의 엔티티라
  // 편성이 문헌으로 내려온 뒤로는 가리킬 수 없다(D-097). 저작이 여럿인 문집은 층위 1의
  // «묶음» 경계가 나타낸다 — 아래 개요의 첫 단계가 그것이다.
  const docNode = document.createElement("div");
  docNode.className = "tree-node contents-doc";
  const docHead = document.createElement("div");
  docHead.className = "tree-node-header";
  // 문헌 머리를 누르면 트리 전체가 접힌다(전에 Work 머리가 하던 일).
  const docClosed = contentsState.collapsedParts.has("__doc__");
  docHead.innerHTML =
    `<span class="tree-toggle">${docClosed ? "▶" : "▼"}</span>` +
    `<span class="tree-label" title="${_treeEscHtml(data.title)}">${_treeEscHtml(data.title)}</span>` +
    `<span class="contents-count">${data.total_units}</span>`;
  docHead.addEventListener("click", () => {
    if (docClosed) contentsState.collapsedParts.delete("__doc__");
    else contentsState.collapsedParts.add("__doc__");
    _renderContentsTree(container);
    highlightContentsForPage(viewerState.pageNum);
  });
  docNode.appendChild(docHead);
  container.appendChild(docNode);
  if (docClosed) return;

  // 권이 하나뿐인 문헌에서는 권 머리를 두지 않는다 — 한 단계가 헛돈다.
  const parts = (data.parts || []).filter((p) => p.unit_count);
  const single = parts.length === 1;
  for (const part of parts) {
    const node = document.createElement("div");
    node.className = "tree-node contents-part";

    const collapsed = contentsState.collapsedParts.has(part.part_id);
    const children = document.createElement("div");
    children.className = "tree-children";
    children.style.display = collapsed ? "none" : "";

    if (!single) {
      const header = document.createElement("div");
      header.className = "tree-node-header";
      header.innerHTML =
        `<span class="tree-toggle">${collapsed ? "▶" : "▼"}</span>` +
        `<span class="tree-label" title="${_treeEscHtml(part.title)}">${_treeEscHtml(part.title)}</span>` +
        `<span class="contents-count">${part.unit_count}</span>`;
      header.addEventListener("click", () => {
        if (contentsState.collapsedParts.has(part.part_id))
          contentsState.collapsedParts.delete(part.part_id);
        else contentsState.collapsedParts.add(part.part_id);
        _renderContentsTree(container);
        highlightContentsForPage(viewerState.pageNum);
      });
      node.appendChild(header);
    }

    // Workflowy식 중첩 개요: 층위가 깊은 행은 바로 앞의 얕은 행 아래에 들어간다.
    // 깊이(level)대로 중첩한다. 자식이 있는 행은 ▸/▾로 접힌다 — 접힘 상태는 contentsState.closedNodes.
    _renderOutline(children, part.units, container);
    node.appendChild(children);
    container.appendChild(node);
  }
}

/** 이 문헌의 모든 단위를 한 줄로 (권 순서 → 권 안의 차례). */
function _allUnits() {
  return (contentsState.data?.parts || []).flatMap((p) => p.units || []);
}

/**
 * 평평한(위치순) 단위 목록을 층위로 중첩해 그린다. 스택에 «열린 조상»을 두고, 각 행은
 * 자기보다 얕은 마지막 조상의 자식 컨테이너에 붙는다.
 */
function _renderOutline(root, blocks, container) {
  const stack = []; // {level, kids}
  for (const b of blocks) {
    const lv = Number(b.level) || 2;
    while (stack.length && stack[stack.length - 1].level >= lv) stack.pop();
    const parent = stack.length ? stack[stack.length - 1].kids : root;
    const row = _createBlockRow(b);
    const kids = document.createElement("div");
    kids.className = "tree-children contents-kids";
    const closed = contentsState.closedNodes.has(b.id);
    kids.style.display = closed ? "none" : "";
    const tg = document.createElement("span");
    tg.className = "tree-toggle contents-node-toggle";
    tg.textContent = closed ? "▸" : "▾";
    tg.title = "접기/펼치기";
    tg.style.visibility = "hidden"; // 자식이 생기면 보인다
    tg.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (contentsState.closedNodes.has(b.id)) contentsState.closedNodes.delete(b.id);
      else contentsState.closedNodes.add(b.id);
      _renderContentsTree(container);
      highlightContentsForPage(viewerState.pageNum);
    });
    row.prepend(tg);
    parent.appendChild(row);
    parent.appendChild(kids);
    if (stack.length) {
      const pt = stack[stack.length - 1];
      if (pt.toggle) pt.toggle.style.visibility = "";
      if (pt.count) pt.count.textContent = String(Number(pt.count.textContent || 0) + 1);
    }
    stack.push({ level: lv, kids, toggle: tg });
  }
}

/**
 * 블록 한 줄: [순번] 미리보기 … 쪽 배지들.
 * 줄을 누르면 첫 쪽으로, 배지를 누르면 그 쪽으로 간다. 두 쪽에 걸친 블록은 배지가 둘이다.
 */
const ROLE_NAME = { container: "묶음", article: "기사", fragment: "조각" };
const ROLE_MARK = { container: "▣ ", article: "", fragment: "· " };

/** 역할이 비어 있으면 깊이로 추정한다 (옛 데이터 호환 — 서버의 role_for_level과 같은 규칙). */
function _roleOf(block) {
  const lv = Number(block.level) || 2;
  return block.role || (lv <= 1 ? "container" : lv === 2 ? "article" : "fragment");
}

/** 한 행의 역할 표시(클래스·머리표·이름표·설명)를 다시 칠한다. 트리를 다시 그리지 않는다. */
function _paintRole(row, block, role) {
  row.classList.remove(`contents-role-${row.dataset.role}`);
  row.classList.add(`contents-role-${role}`);
  row.dataset.role = role;
  block.role = role;
  block.role_estimated = false; // 사람이 정했다
  if (row._roleBtn) row._roleBtn.textContent = ROLE_NAME[role];
  if (row._label) row._label.textContent = _rowLabel(block, role);
  row.title = _rowTitle(block, role);
}

function _rowLabel(block, role) {
  const seq = block.sequence_index != null ? `${block.sequence_index}. ` : "";
  const head = block.title ? `${block.title} · ` : "";
  const stale = block.anchor && block.anchor.status === "stale";
  return `${stale ? "⚠ " : ""}${ROLE_MARK[role] || ""}${seq}${head}${block.preview || "(비어있음)"}`;
}

function _rowTitle(block, role) {
  const seq = block.sequence_index != null ? `${block.sequence_index}. ` : "";
  const level = Number(block.level) || 2;
  const stale = block.anchor && block.anchor.status === "stale";
  return (
    `${seq}${block.preview}  (${block.char_count}자, ${ROLE_NAME[role]} · 깊이 ${level}${block.status ? ", " + block.status : ""})` +
    (stale ? "\n⚠ 확정본이 바뀐 뒤 자리를 못 찾았습니다 — ▲▼로 옮겨 주세요" : "")
  );
}

function _createBlockRow(block) {
  const row = document.createElement("div");
  row.className = "tree-page contents-block";
  row.dataset.blockId = block.id || "";
  row.dataset.pages = (block.pages || []).map((p) => p.page).join(",");
  const level = Number(block.level) || 2;
  // 깊이는 중첩이 보여 준다. 역할(뜻)은 따로 — container 묶음 / article 기사 / fragment 조각
  const role = _roleOf(block);
  row.classList.add(`contents-role-${role}`);
  row.dataset.role = role;
  row.title = _rowTitle(block, role);

  const label = document.createElement("span");
  label.className = "tree-label contents-preview";
  label.textContent = _rowLabel(block, role);
  row._label = label;
  row.appendChild(label);

  // 쪽 배지는 하나만: 시작 쪽(여러 쪽에 걸치면 「14~16쪽」). 쪽마다 배지를 달면 번잡하다.
  const badges = document.createElement("span");
  badges.className = "contents-badges";
  const pages = block.pages || [];
  if (pages.length) {
    const first = pages[0];
    const last = pages[pages.length - 1];
    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "contents-page-badge";
    badge.textContent = pages.length > 1 ? `${first.page}~${last.page}쪽` : `${first.page}쪽`;
    badge.title = `${first.page}쪽으로 이동` + (block.anchor?.start ? ` · ${block.anchor.start.line + 1}행` : "");
    badge.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _jumpToBlockPage(block, first);
    });
    badges.appendChild(badge);
  }
  row.appendChild(badges);

  // 경계 색인에서 파생된 블록: 시작 행을 한 행씩 옮기는 단추 (D-090)
  if (block.anchor) {
    const tools = document.createElement("span");
    tools.className = "contents-boundary-tools";
    for (const [label, delta, tip] of [["▲", -1, "시작을 한 행 앞으로"], ["▼", 1, "시작을 한 행 뒤로"]]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "contents-shift-btn";
      b.textContent = label;
      b.title = tip;
      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        _shiftBoundary(block, delta);
      });
      tools.appendChild(b);
    }
    // 내어쓰기·들여쓰기 = 층위 바꾸기 (D-092). 층위 n을 바꿔도 더 얕은 층위의 id는 그대로다.
    for (const [label, delta, tip] of [["⇤", -1, "내어쓰기 — 한 단 위로"], ["⇥", 1, "들여쓰기 — 한 단 아래로 (깊이는 제한 없음)"]]) {
      const lv = document.createElement("button");
      lv.type = "button";
      lv.className = "contents-shift-btn";
      lv.textContent = label;
      lv.title = tip;
      lv.disabled = delta < 0 && level <= 1; // 깊이는 위로만 제한(1), 아래는 무제한
      lv.addEventListener("click", (ev) => {
        ev.stopPropagation();
        _setBoundaryLevel(block, level + delta);
      });
      tools.appendChild(lv);
    }
    // 역할 바꾸기 — 묶음 → 기사 → 조각 → 묶음. 깊이와 무관하다(3단에 오는 기사도 있다)
    const rl = document.createElement("button");
    rl.type = "button";
    rl.className = "contents-shift-btn";
    rl.textContent = ROLE_NAME[role];
    // 파일에 실제 값이 없어 깊이로 어림한 역할은 흐리게 — 사람이 정한 것과 구별되어야 한다.
    // 단추를 한 번 누르면 그때 실제 값이 저장되므로 표시가 저절로 사라진다.
    rl.classList.toggle("is-estimated", !!block.role_estimated);
    rl.title = block.role_estimated
      ? "역할이 아직 정해지지 않아 깊이로 어림한 것입니다 — 누르면 정해집니다 (묶음/기사/조각)"
      : "역할 바꾸기 — 묶음(卷·集·編) / 기사(번역·주석 단위) / 조각(기사 안 문단·문답)";
    rl.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const order = ["container", "article", "fragment"];
      _setBoundaryRole(row, block, order[(order.indexOf(_roleOf(block)) + 1) % 3]);
    });
    row._roleBtn = rl;
    tools.appendChild(rl);
    // 지우기 = 앞 단위에 합치기 (앞 단위의 id가 남는다)
    const del = document.createElement("button");
    del.type = "button";
    del.className = "contents-shift-btn";
    del.textContent = "×";
    del.title = "이 경계를 지워 앞 단위에 합치기";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      _deleteBoundary(block);
    });
    tools.appendChild(del);
    row.appendChild(tools);
  }

  row.addEventListener("click", () => {
    selectUnit(block); // 표점·현토·번역·주석·인용이 이 단위를 따라간다
    const first = (block.pages || [])[0];
    if (first) _jumpToBlockPage(block, first);
    else showToast("이 블록에는 출처 쪽 정보가 없습니다.", "warning");
  });
  return row;
}

/**
 * 블록이 있는 쪽으로 이동하고, 레이아웃이 뜨면 해당 LayoutBlock을 선택(강조)한다.
 *
 * 권(part)이 참조에 있고 지금 보는 권과 다르면 권 선택기로 먼저 바꾼다.
 * 예전 참조에는 part_id가 없으므로 그때는 현재 권으로 간다.
 */
async function _jumpToBlockPage(block, pageRef) {
  if (pageRef.part_id && pageRef.part_id !== viewerState.partId) {
    // 다른 권이다. 트리 노드 클릭 경로(goToPage)는 현재 권 안에서만 찾으므로
    // 권·쪽을 함께 바꾸는 _selectPage(sidebar-tree.js)를 직접 부른다.
    if (typeof _selectPage === "function") {
      _selectPage(viewerState.docId, pageRef.part_id, pageRef.page, viewerState.documentInfo, null);
      if (typeof updatePartSelector === "function" && viewerState.documentInfo?.parts) {
        updatePartSelector(viewerState.documentInfo.parts, pageRef.part_id);
      }
    } else {
      showToast(`이 블록은 다른 권(${pageRef.part_id})에 있습니다. 권을 먼저 바꾸세요.`, "warning");
      return;
    }
  } else if (Number(viewerState.pageNum) !== Number(pageRef.page)) {
    if (typeof goToPage !== "function" || !goToPage(pageRef.page)) {
      showToast(`${pageRef.page}쪽으로 이동할 수 없습니다.`, "error");
      return;
    }
  }
  _selectLayoutBlocksWhenLoaded(pageRef.layout_block_ids || []);
  _markActiveBlock(block.id);
  _highlightAnchor(block, pageRef);
}

/**
 * 경계 색인의 시작 행을 이미지 위에 점선으로 표시한다 (D-090).
 * 시작 쪽으로 갔을 때만. 다른 쪽 배지를 눌렀으면 강조를 지운다.
 */
function _highlightAnchor(block, pageRef) {
  // anchor.bbox는 적용·삽입 때 캐시한 L2 행 좌표 {start_line, image_width, image_height}
  const a = block.anchor;
  const bb = a && a.bbox;
  const firstPage = (block.pages || [])[0];
  if (bb && bb.start_line && firstPage && Number(firstPage.page) === Number(pageRef.page)) {
    contentsState.anchorHighlight = {
      bbox: bb.start_line,
      imageWidth: bb.image_width,
      imageHeight: bb.image_height,
      page: Number(firstPage.page),
    };
  } else {
    contentsState.anchorHighlight = null;
  }
  // 레이아웃 모드의 오버레이에도 같은 것을 준다(그 모드에서는 블록 상자와 함께 그린다)
  if (typeof layoutState !== "undefined") {
    layoutState.anchorHighlight = contentsState.anchorHighlight
      ? { bbox: contentsState.anchorHighlight.bbox, imageWidth: contentsState.anchorHighlight.imageWidth, page: contentsState.anchorHighlight.page }
      : null;
  }
  // PDF가 비동기로 그려지므로 잠깐 뒤까지 다시 그린다
  const started = Date.now();
  const tick = () => {
    _drawAnchorCanvas();
    if (typeof _redrawOverlay === "function" && typeof layoutState !== "undefined" && layoutState.active) _redrawOverlay();
    if (Date.now() - started < 1500) setTimeout(tick, 300);
  };
  setTimeout(tick, 100);
}

/**
 * 시작 행 점선을 **항상 보이는** 캔버스에 그린다 (D-090·D-092).
 *
 * 왜 따로 그리는가: 레이아웃 오버레이(layout-overlay)는 레이아웃 모드에서만 display:block이라
 * 교정·편성·표점 탭에서 내용 트리를 눌러도 점선이 보이지 않았다(실측 2026-09-03). 이 캔버스는
 * PDF 캔버스 위에 같은 크기로 겹치고 마우스를 받지 않는다(pointer-events: none).
 * 좌표: L2 픽셀 → PDF 캔버스 픽셀은 «캔버스 폭 / L2 image_width» 비율로 옮긴다(쪽 전체를
 * 렌더한 것이라 가로·세로 비율이 같다). 회전은 다루지 않는다.
 */
function _ensureAnchorCanvas() {
  const pdfCanvas = document.getElementById("pdf-canvas");
  if (!pdfCanvas || !pdfCanvas.parentElement) return null;
  let c = document.getElementById("anchor-overlay");
  if (!c) {
    c = document.createElement("canvas");
    c.id = "anchor-overlay";
    c.className = "anchor-overlay";
    pdfCanvas.parentElement.appendChild(c);
  }
  c.style.width = pdfCanvas.style.width;
  c.style.height = pdfCanvas.style.height;
  c.width = pdfCanvas.width;
  c.height = pdfCanvas.height;
  return c;
}

/**
 * PDF 캔버스가 커지거나 작아지면 점선을 다시 그린다.
 *
 * 왜 필요한가: 패널을 접거나 펴면 «가로·세로 맞춤»이 다시 돌아 PDF 캔버스 크기가 바뀌는데,
 * 점선 캔버스는 고를 때 한 번 맞춰 둔 크기 그대로였다. 그래서 네모가 자동 맞춤을 따라가지
 * 못하고 옛 자리에 남았다(사용자 지적 2026-09-04). 크기가 바뀌는 길이 여럿이라
 * (줌·맞춤·회전·패널 접기·창 크기) 원인마다 손대는 대신 «바뀌면 다시 그린다»로 잡는다.
 */
function _watchPdfCanvasResize() {
  const pdfCanvas = document.getElementById("pdf-canvas");
  if (!pdfCanvas || pdfCanvas.dataset.anchorWatch || typeof ResizeObserver === "undefined") return;
  pdfCanvas.dataset.anchorWatch = "1";
  new ResizeObserver(() => _drawAnchorCanvas()).observe(pdfCanvas);
}

document.addEventListener("DOMContentLoaded", _watchPdfCanvasResize);

function _drawAnchorCanvas() {
  _watchPdfCanvasResize(); // 캔버스가 나중에 생겼으면 그때 붙인다
  const c = _ensureAnchorCanvas();
  if (!c) return;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const a = contentsState.anchorHighlight;
  if (!a || !a.bbox || Number(a.page) !== Number(viewerState.pageNum) || !a.imageWidth) return;
  const f = c.width / a.imageWidth;
  const [x1, y1, x2, y2] = a.bbox.map((v) => v * f);
  ctx.save();
  ctx.strokeStyle = "#d33";
  ctx.lineWidth = 3;
  ctx.setLineDash([8, 5]);
  ctx.strokeRect(x1 - 3, y1 - 3, x2 - x1 + 6, y2 - y1 + 6);
  ctx.fillStyle = "rgba(221, 51, 51, 0.12)";
  ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
  ctx.restore();
}

/**
 * «경계 넣기» 폼 (D-092) — 임의 쪽·행·글자에 경계를 놓아 단위를 나눈다. 새 id는 뒤 단위에 붙는다.
 * 쪽은 지금 보는 쪽이 기본값이다. 행·글자는 확정본(교정 탭) 기준 0부터.
 */
function _renderInsertForm() {
  // 펼치면 곧바로 «찍기»가 켜진다 — 경계 자리는 숫자로 세는 것이 아니라 눈으로 찍는 것이다.
  // 쪽·행·글자 칸은 「숫자로 적기」 안으로 넣었다: OCR 행 좌표가 없어 찍을 수 없는 쪽에서만 쓴다.
  const box = document.createElement("details");
  box.className = "contents-insert-box";
  box.open = !!contentsState.insertOpen;
  const sum = document.createElement("summary");
  sum.textContent = "＋ 경계 넣기";
  sum.title = "펼치면 «찍기»가 켜집니다. 원본 이미지나 교정 텍스트에서 자리를 누르세요";
  box.appendChild(sum);

  const form = document.createElement("div");
  form.className = "contents-insert-form";
  const page = Number(viewerState.pageNum) || 1;
  form.innerHTML =
    `<select title="역할 — 묶음(卷·集·編) / 기사(번역·주석 단위) / 조각(기사 안 문단)" data-k="role">` +
    `<option value="container">묶음</option><option value="article" selected>기사</option>` +
    `<option value="fragment">조각</option></select>` +
    `<input type="number" min="1" value="2" title="깊이(중첩 단계, 1부터)" class="contents-insert-num" data-k="level">단` +
    `<input type="text" placeholder="제목(선택)" class="contents-insert-title" data-k="title">` +
    `<button type="button" class="contents-shift-btn contents-insert-btn" disabled title="찍은 자리에서 단위를 나눕니다">넣기</button>` +
    `<span class="contents-pick-hint"></span>` +
    `<details class="contents-insert-manual"><summary>숫자로 적기</summary>` +
    `<input type="number" min="1" value="${page}" title="쪽" class="contents-insert-num" data-k="page">쪽` +
    `<input type="number" min="0" value="0" title="행 (0부터)" class="contents-insert-num" data-k="line">행` +
    `<input type="number" min="0" value="0" title="글자 (0 = 행 첫머리)" class="contents-insert-num" data-k="offset">자` +
    `</details>`;
  box.appendChild(form);

  // 숫자를 손으로 고쳐도 넣을 수 있어야 한다
  form.querySelectorAll('[data-k="page"], [data-k="line"], [data-k="offset"]').forEach((el) => {
    el.addEventListener("input", () => _markPicked(form, true));
  });
  form.querySelector(".contents-insert-btn").addEventListener("click", async () => {
    const v = (k) => form.querySelector(`[data-k="${k}"]`).value;
    await _insertBoundary({
      start: { page: Number(v("page")), line: Number(v("line")), offset: Number(v("offset")) },
      level: Math.max(1, Number(v("level")) || 2),
      role: v("role"),
      title: v("title").trim() || null,
    });
  });
  // 펼치면 찍기 켜고, 접으면 끈다
  box.addEventListener("toggle", () => {
    contentsState.insertOpen = box.open;
    _setPickMode(form, box.open);
  });
  if (box.open) setTimeout(() => _setPickMode(form, true), 0);
  return box;
}

/** 자리를 찍었는가에 따라 「넣기」를 켜고 끈다. 안 찍고 누르면 엉뚱한 자리에 들어간다. */
function _markPicked(form, picked) {
  const btn = form.querySelector(".contents-insert-btn");
  if (btn) btn.disabled = !picked;
}

/**
 * «찍기» 모드를 켜고 끈다 (B-002·D-094).
 *
 * 왜 찍는 것이 기본인가: 고서는 이미지로 읽는다. 확정본의 몇 번째 행인지 세는 것은
 * 도구가 할 일을 사람에게 미루는 것이다. 그래서 「경계 넣기」를 펼치면 곧바로 켜진다.
 * 시작 행 점선을 그리는 캔버스(anchor-overlay)가 이미 PDF 위에 꼭 맞게 겹쳐 있으므로,
 * 그 캔버스에 마우스를 받게 하고 누른 자리를 서버에 물어 폼을 채운다.
 * 바로 넣지 않는 이유: 글자 번호는 «행 길이 × 비율»의 추정이라 협주가 섞인 행에서
 * 한두 자 어긋난다. 찍은 자리의 글자를 보여 주고 「넣기」는 사람이 누른다.
 */
function _setPickMode(form, on) {
  on = !!on;
  contentsState.picking = on;
  contentsState.pickForm = on ? form : null;
  if (!on) _markPicked(form, false);
  const hint = form.querySelector(".contents-pick-hint");
  if (hint) hint.textContent = on ? "원본 이미지나 교정 텍스트에서 자리를 누르세요" : "";
  const c = _ensureAnchorCanvas();
  if (c) {
    c.style.pointerEvents = on ? "auto" : "none";
    c.style.cursor = on ? "crosshair" : "";
    if (on && !c._pickBound) {
      c.addEventListener("click", _onPickClick);
      c._pickBound = true;
    }
  }
}

/**
 * 교정 텍스트(오른쪽 패널)에서 글자를 눌렀을 때 그 자리를 «경계 넣기»에 채운다 (D-094 다음).
 *
 * 입력: globalIdx — correctionState.pageText 안의 글자 번호(교정 편집기의 data-idx).
 * 출력: 자리를 채웠으면 true. «찍기»가 꺼져 있으면 false — 그때는 교정 대화상자가 열린다.
 *
 * 왜 서버에 묻지 않는가: 이미지와 달리 여기서는 행·글자를 바로 셀 수 있다(D-094의 이미지
 * 경로는 좌표를 행으로 옮겨야 해서 서버가 필요했다).
 *
 * 교정으로 글자 수가 달라진 만큼은 더해 준다: 확정본(L4)은 교정을 적용한 것이고
 * data-idx는 적용 전 텍스트의 번호다. 같은 행에서 앞선 교정의 길이 차이를 더하면 맞는다.
 */
function pickBoundaryFromCorrectionChar(globalIdx) {
  if (!contentsState.picking || !contentsState.pickForm) return false;
  if (typeof correctionState === "undefined" || !correctionState.pageText) return false;
  const raw = correctionState.pageText;
  const idx = Math.max(0, Math.min(raw.length, Number(globalIdx) || 0));
  const before = raw.slice(0, idx);
  const line = (before.match(/\n/g) || []).length;
  const lineStart = before.lastIndexOf("\n") + 1;
  let offset = idx - lineStart;
  // 같은 행에서 이 글자보다 앞에 있는 교정의 길이 차이
  for (const c of correctionState.corrections || []) {
    const ci = Number(c.char_index);
    if (!Number.isFinite(ci) || ci >= idx) continue;
    if ((raw.slice(0, ci).match(/\n/g) || []).length !== line) continue;
    offset += String(c.corrected ?? "").length - String(c.original_ocr ?? "").length;
  }
  offset = Math.max(0, offset);
  const form = contentsState.pickForm;
  form.querySelector('[data-k="page"]').value = String(viewerState.pageNum);
  form.querySelector('[data-k="line"]').value = String(line);
  form.querySelector('[data-k="offset"]').value = String(offset);
  const hint = form.querySelector(".contents-pick-hint");
  if (hint) {
    const lineText = (correctionState.correctedText || raw).split("\n")[line] || "";
    hint.textContent = `${line}행 ${offset}자 「${lineText.slice(offset, offset + 8) || "?"}」 (교정 텍스트)`;
  }
  contentsState.anchorHighlight = null; // 이미지 점선은 이 경로에서 그리지 않는다
  _drawAnchorCanvas();
  _markPicked(form, true);
  return true;
}

async function _onPickClick(ev) {
  const form = contentsState.pickForm;
  if (!contentsState.picking || !form) return;
  ev.stopPropagation();
  const c = ev.currentTarget;
  const r = c.getBoundingClientRect();
  // 화면 좌표 → 캔버스 픽셀. 캔버스 폭을 그대로 넘기면 서버가 L2 폭으로 환산한다
  const x = ((ev.clientX - r.left) / r.width) * c.width;
  const y = ((ev.clientY - r.top) / r.height) * c.height;
  const hint = form.querySelector(".contents-pick-hint");
  if (hint) hint.textContent = "찾는 중…";
  try {
    const q = new URLSearchParams({
      part_id: viewerState.partId,
      x: String(Math.round(x)),
      y: String(Math.round(y)),
      image_width: String(c.width),
    });
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/pages/${viewerState.pageNum}/position-at?${q}`,
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    form.querySelector('[data-k="page"]').value = String(data.page);
    form.querySelector('[data-k="line"]').value = String(data.line);
    form.querySelector('[data-k="offset"]').value = String(data.offset);
    if (hint) {
      hint.textContent =
        `${data.inside ? "" : "(행 밖 — 가장 가까운 행) "}${data.line}행 ${data.offset}자 「${data.anchor_text || "?"}」`;
    }
    // 찍은 자리를 점선으로 보여 준다 (넣기 전 확인)
    contentsState.anchorHighlight = {
      bbox: data.bbox,
      imageWidth: data.image_width,
      imageHeight: data.image_height,
      page: Number(data.page),
    };
    _drawAnchorCanvas();
    _markPicked(form, true);
  } catch (e) {
    if (hint) hint.textContent = `찍기 실패: ${e.message}`;
  }
}

async function _insertBoundary(spec) {
  if (contentsState.picking && contentsState.pickForm) _setPickMode(contentsState.pickForm, false);
  if (!viewerState.docId || !viewerState.partId) {
    showToast("문헌·권이 정해져야 경계를 넣을 수 있습니다.", "warning");
    return;
  }
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/boundaries`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part_id: viewerState.partId, ...spec }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    showToast(`경계를 넣었습니다: ${data.boundary?.title || ""}`, "success");
    await refreshContentsTree();
  } catch (e) {
    showToast(`경계 넣기 실패: ${e.message}`, "error");
  }
}

async function _deleteBoundary(block) {
  if (!viewerState.docId) return;
  if (!confirm("이 경계를 지우면 이 단위가 앞 단위에 합쳐집니다. 계속할까요?")) return;
  try {
    const res = await fetch(
      `${_boundaryUrl(block.id)}`,
      { method: "DELETE" },
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    const warn = data.dangling_tags?.length ? ` (이 단위를 가리키던 태그 ${data.dangling_tags.length}개는 그대로 남았습니다)` : "";
    showToast(`앞 단위에 합쳤습니다.${warn}`, data.dangling_tags?.length ? "warning" : "success");
    await refreshContentsTree();
  } catch (e) {
    showToast(`경계 지우기 실패: ${e.message}`, "error");
  }
}

/**
 * 경계 하나의 API 주소. 편성은 문헌의 것이므로 해석 저장소가 끼지 않는다 (D-097).
 * part_id를 붙이면 서버가 그 권부터 찾는다(없으면 문헌의 모든 권을 훑는다).
 */
function _boundaryUrl(boundaryId) {
  const q = viewerState.partId ? `?part_id=${encodeURIComponent(viewerState.partId)}` : "";
  return `/api/documents/${encodeURIComponent(viewerState.docId)}/boundaries/${encodeURIComponent(boundaryId)}${q}`;
}

// 저장 중인 역할 변경 (경계 id → {desired}). 연타하면 마지막 값 하나만 더 보낸다.
const _rolePending = new Map();

/**
 * 역할을 바꾼다. 화면은 곧바로, 저장은 뒤에서.
 *
 * 왜 이렇게 하는가: 역할은 단위의 끝도 중첩도 바꾸지 않는다. 그런데 저장(경계 파일 쓰기 +
 * git 커밋)과 트리 다시 읽기가 합쳐 1초 넘게 걸린다 — 운양집 1책(경계 90)에서 PUT 0.9초 +
 * 내용 0.3초 실측. 구조가 안 바뀌는 변경까지 서버 왕복을 기다릴 이유가 없어, 그 행만 다시
 * 칠하고 저장은 뒤로 보낸다. 실패하면 트리를 다시 읽어 서버의 사실로 되돌린다.
 */
/**
 * 「내용」 트리에서 단위을 고른다 — 해석 편집기 다섯의 «지금 단위»가 된다.
 *
 * 왜 트리인가: 편집기마다 드롭다운을 두면 같은 것을 다섯 번 고르게 되고, 「#3 기사 …」라는
 * 이름만으로는 어느 대목인지 알기 어렵다. 트리는 전체 구조를 보며 고르는 자리다
 * (사용자 요청 2026-09-04 — «드롭다운 없애고 사이드바 트리에서 고르면 따라가게»).
 *
 * 알림은 DOM 이벤트로 보낸다: 편집기 다섯이 서로를 모르고, 불러오는 순서에도 매이지 않는다.
 */
function selectUnit(block) {
  contentsState.selectedUnitId = block && block.id ? block.id : null;
  document.querySelectorAll("#contents-tree .contents-block.unit-selected").forEach((el) => {
    el.classList.remove("unit-selected");
  });
  if (!block || !block.id) return;
  const row = document.querySelector(
    `#contents-tree .contents-block[data-block-id="${CSS.escape(block.id)}"]`,
  );
  if (row) row.classList.add("unit-selected");
  document.dispatchEvent(
    new CustomEvent("unit-selected", {
      detail: {
        id: block.id,
        title: block.title || "",
        role: _roleOf(block),
        level: Number(block.level) || 2,
        sequence_index: block.sequence_index,
      },
    }),
  );
}

/** 지금 고른 단위의 id (없으면 null). 편집기가 목록을 채운 뒤 물어본다. */
// 해석 편집기 다섯의 «블록 선택». 화면에서는 감춰 두고 트리가 값을 넣는다(D-096).
const UNIT_SELECT_IDS = [
  "punct-block-select",
  "hyeonto-block-select",
  "trans-block-select",
  "ann-block-select",
  "cite-block-select",
];

/**
 * 고른 단위를 편집기 다섯에 흘려 보낸다.
 *
 * 왜 select에 값을 넣고 change를 쏘는가: 편집기마다 이미 «고르면 불러온다»는 길이 있다.
 * 그 길을 그대로 쓰면 다섯 곳을 새로 짤 필요가 없고, 저장·되돌리기 같은 뒷일도 그대로다.
 * 목록이 아직 안 채워졌을 수 있어 잠깐 뒤 한 번 더 시도한다(탭을 옮기며 고르는 경우).
 */
function _applyUnitToEditors(unitId, tries = 0) {
  let hit = 0;
  for (const id of UNIT_SELECT_IDS) {
    const sel = document.getElementById(id);
    if (!sel) continue;
    const opt = sel.querySelector(`option[value="unit:${unitId}"]`);
    if (!opt) continue;
    hit++;
    if (sel.value === opt.value) continue;
    sel.value = opt.value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }
  // 지금 열린 편집기의 목록이 아직 안 채워졌으면 잠깐 뒤에 다시 (최대 세 번)
  if (hit === 0 && tries < 3) setTimeout(() => _applyUnitToEditors(unitId, tries + 1), 400);
  _renderUnitLabels();
}

/** 편집기 머리줄의 «지금 단위» 표시를 갱신한다. */
function _renderUnitLabels() {
  const u = currentUnit();
  const text = u
    ? `${u.sequence_index != null ? "#" + u.sequence_index + " " : ""}${ROLE_NAME[_roleOf(u)]}${u.title ? " · " + u.title : ""}`
    : "";
  document.querySelectorAll(".unit-pick-label").forEach((el) => {
    el.textContent = text || "사이드바 「내용」에서 기사를 고르세요";
    el.classList.toggle("is-empty", !text);
    el.title = text ? "사이드바 「내용」에서 다른 단위를 고르면 바뀝니다" : "";
  });
}

document.addEventListener("unit-selected", (ev) => {
  _applyUnitToEditors(ev.detail.id);
});

function currentUnitId() {
  return contentsState.selectedUnitId || null;
}

/** 지금 고른 단위 (없으면 null). 편집기가 처음 열릴 때 물어본다. */
function currentUnit() {
  const id = contentsState.selectedUnitId;
  if (!id || !contentsState.data) return null;
  return _allUnits().find((b) => b.id === id) || null;
}

function _setBoundaryRole(row, block, role) {
  if (!contentsState.interpId) return;
  _paintRole(row, block, role);
  _saveRole(block.id, role);
}

async function _saveRole(boundaryId, role) {
  const running = _rolePending.get(boundaryId);
  if (running) {
    running.desired = role; // 진행 중이면 마지막 값만 남긴다 (연타로 요청이 쌓이지 않게)
    return;
  }
  const state = { desired: role };
  _rolePending.set(boundaryId, state);
  try {
    while (state.desired) {
      const next = state.desired;
      state.desired = null;
      const res = await fetch(
        `${_boundaryUrl(boundaryId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: next }),
        },
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${res.status}`);
      }
    }
  } catch (e) {
    showToast(`역할 변경 실패: ${e.message}`, "error");
    await refreshContentsTree(); // 화면을 서버의 사실로 되돌린다
  } finally {
    _rolePending.delete(boundaryId);
  }
}

async function _setBoundaryLevel(block, level) {
  if (!viewerState.docId) return;
  try {
    const res = await fetch(
      `${_boundaryUrl(block.id)}`,
      { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ level }) },
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    await refreshContentsTree();
  } catch (e) {
    showToast(`층위 변경 실패: ${e.message}`, "error");
  }
}

/**
 * 경계의 시작 행을 ±1 행 옮긴다 (D-090). 앞 경계의 끝도 함께 조정되고 단위 본문이 다시 이어진다.
 */
/**
 * 옮기면 어디로 가는지 미리 보여 준다. 「14쪽 3행 「…」 → 14쪽 2행 「…」」.
 *
 * 입력: block(단위 보기), delta(±1 행). 출력: 사람이 읽을 두 줄.
 * 시작 행 번호는 내용 트리에 없다(트리는 쪽과 미리보기만 안다) — 경계 색인에서 가져온다.
 * 확인 대화 한 번에 두 번 물어보는 값이지만, 무엇이 어디로 가는지 모른 채 누르는 것보다 낫다.
 */
async function _shiftPreview(block, delta) {
  const line = (txt) => (txt || "").trim().slice(0, 14);
  try {
    const q = `part_id=${encodeURIComponent(viewerState.partId)}`;
    const bs = await (
      await fetch(
        `/api/documents/${encodeURIComponent(viewerState.docId)}/boundaries?${q}`,
      )
    ).json();
    const row = (bs.boundaries || []).find((b) => b.id === block.id);
    if (!row || !row.start) return "";
    const { page, line: ln } = row.start;
    const to = Number(ln) + delta;
    let now = line(block.preview);
    let next = "";
    const res = await fetch(
      `/api/documents/${encodeURIComponent(viewerState.docId)}/pages/${page}/corrected-text?part_id=${encodeURIComponent(viewerState.partId)}`,
    );
    if (res.ok) {
      const lines = ((await res.json()).corrected_text || "").split("\n");
      now = line(lines[ln]) || now;
      next = line(lines[to]);
    }
    if (to < 0) return `${page}쪽 ${ln}행이 이 쪽의 첫 행입니다 — 더 앞으로는 못 갑니다.`;
    return (
      `지금:   ${page}쪽 ${ln}행${now ? " 「" + now + "」" : ""}` +
      `\n옮기면: ${page}쪽 ${to}행${next ? " 「" + next + "」" : ""}`
    );
  } catch (e) {
    return ""; // 미리보기를 못 만들어도 물어보기는 한다
  }
}

async function _shiftBoundary(block, delta) {
  if (!block.anchor || !contentsState.interpId) return;
  // 옮기기 전에 묻는다.
  //
  // 왜: 이 단추들은 행 오른쪽에 겹쳐 떠 있어(마우스를 올렸을 때만) 스치듯 눌리기 쉽다.
  // 그런데 시작 행이 한 줄만 밀려도 «그 기사가 어디서 시작하는가»가 바뀌고, 붙어 있던
  // 표점·번역이 딴 대목을 가리키게 된다. 되돌리려면 어느 경계가 밀렸는지 먼저 알아내야
  // 하는데, 조용히 바뀌면 그것부터 어렵다(실측 2026-09-04 — 시험이 모르고 밀었다).
  // 역할·깊이는 화면에서 곧바로 보이고 되돌리기 쉬워 묻지 않는다.
  const where = await _shiftPreview(block, delta);
  const arrow = delta > 0 ? "한 행 뒤로" : "한 행 앞으로";
  if (!confirm(`이 단위의 시작을 ${arrow} 옮깁니다.\n\n${where}\n\n계속할까요?`)) return;
  try {
    const res = await fetch(
      `${_boundaryUrl(block.id)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shift_start: delta }),
      },
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    showToast(`경계를 ${delta > 0 ? "한 행 뒤로" : "한 행 앞으로"} 옮겼습니다.`, "success");
    await refreshContentsTree();
    const b = _allUnits().find((x) => x.id === block.id);
    if (b && b.pages?.[0]) _jumpToBlockPage(b, b.pages[0]);
  } catch (e) {
    showToast(`경계 이동 실패: ${e.message}`, "error");
  }
}

/**
 * 레이아웃(L3)이 비동기로 로드되므로 블록이 나타날 때까지 잠깐 기다린 뒤 선택한다.
 * 3초 안에 안 뜨면 조용히 포기한다 — 쪽 이동 자체는 이미 끝났다.
 */
function _selectLayoutBlocksWhenLoaded(blockIds) {
  if (!blockIds.length || typeof layoutState === "undefined") return;
  const target = blockIds[0];
  const started = Date.now();
  const tick = () => {
    const found = (layoutState.blocks || []).some((b) => b.block_id === target);
    if (found) {
      if (typeof _selectBlock === "function") _selectBlock(target);
      return;
    }
    if (Date.now() - started < 3000) setTimeout(tick, 150);
  };
  setTimeout(tick, 150);
}

/** 경계 색인 CSV 내보내기 링크 (D-090). 경계가 하나라도 있을 때만 보인다. */
function _updateExportLink(docId) {
  const a = document.getElementById("contents-export-csv");
  if (!a) return;
  const has = _allUnits().some((b) => b.anchor);
  a.hidden = !has;
  if (!docId) { a.hidden = true; return; }
  const qs = viewerState.partId ? `?part_id=${encodeURIComponent(viewerState.partId)}` : "";
  a.href = `/api/documents/${encodeURIComponent(docId)}/boundaries/export.csv${qs}`;
}

function _markActiveBlock(blockId) {
  document.querySelectorAll(".contents-block.active").forEach((el) => el.classList.remove("active"));
  const row = document.querySelector(`.contents-block[data-block-id="${blockId}"]`);
  if (row) {
    row.classList.add("active");
    row.scrollIntoView({ block: "nearest" });
  }
}

/**
 * 현재 쪽에 있는 블록들을 트리에서 표시한다 (쪽 → 내용 방향의 동기화).
 * workspace.js의 onPageChanged에서 부른다.
 */
function highlightContentsForPage(pageNum) {
  const page = Number(pageNum);
  // 쪽이 바뀌면 점선도 다시 판단한다(다른 쪽이면 지워진다)
  setTimeout(_drawAnchorCanvas, 150);
  document.querySelectorAll(".contents-block").forEach((el) => {
    const pages = (el.dataset.pages || "").split(",").filter(Boolean).map(Number);
    el.classList.toggle("on-page", pages.includes(page));
  });
}


/**
 * 「자동 트리」: 편성 탭 「전부 적용해 새로 세우기」의 바로 가기 (D-092 후속, D-116).
 * 이 문헌에 저장된 규칙(신호 설정)으로 목차·卷頭·날짜·○권점·어휘를 찾아 개요를 한 번에 세운다.
 * 규칙이 아직 없으면 서버가 확정본 전문에서 먼저 찾아 문헌 설정에 저장한다.
 * 이 권의 기존 경계는 새로 세워진다(Git으로 되돌릴 수 있다). 손으로 고친 것이 많으면 편성 탭의
 * 제안 패널에서 체크해 「골라 적용」하는 쪽이 낫다 — 그쪽은 제안 경계만 바꿔치기한다.
 */
async function _autoTree() {
  const interpId = typeof interpState !== "undefined" ? interpState.interpId : null;
  if (!viewerState.docId || !viewerState.partId) {
    showToast("문헌·권이 정해져야 트리를 세울 수 있습니다.", "warning");
    return;
  }
  const n = contentsState.data?.total_units || 0;
  // 확정본(L4)만 읽는다는 말을 여기서 한다 — OCR만 돌린 쪽은 규칙이 보지 못한다.
  const how = "확정본(L4)이 있는 쪽에서 이 책의 규약(목차 → 기호·내려쓰기 → 날짜·어휘)으로 개요를 세웁니다 (규칙만, LLM 안 씀).\n신호 설정이 아직 없으면 전문에서 찾아 문헌 설정에 저장합니다 (편성 인덱스 「경계 제안」에서 고칠 수 있음).";
  const msg = n ? `이 권의 단위 ${n}개를 지우고 개요를 다시 세웁니다.\n${how} 계속할까요?` : `${how} 계속할까요?`;
  if (!confirm(msg)) return;
  // 이 단추는 LLM을 부르지 않는다(D-117 ③-B). 배지 없는 단추가 모델을 부르면 «표시 없는 것은
  // LLM을 안 쓴다»(D-115)에 예외가 생긴다. 목차 항목 구조화에 모델을 쓰려면 스위치가 보이는
  // 편성 탭에서 「전부 적용해 새로 세우기」를 누른다. 경계 찾기(층계 1~3단)는 언제나 규칙이다.
  const btn = document.getElementById("contents-auto-btn");
  if (btn) { btn.disabled = true; btn.textContent = "세우는 중…"; }
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(viewerState.docId)}/segmentation/auto`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        part_id: viewerState.partId,
        use_llm_toc: false, // 사이드바는 규칙만 — 저장된 toc_llm이 켜져 있어도 부르지 않는다
        replace: "all",
      }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
    showToast(describeAutoTreeResult(d, false), "success");
    // 일부 쪽에만 확정본이 있으면 나머지는 규칙이 보지도 못했다 — 「후보 0」의 흔한 원인.
    if (d.pages_total && d.pages_with_text < d.pages_total) {
      showToast(
        `확정본(L4)이 있는 쪽은 ${d.pages_with_text}/${d.pages_total}쪽입니다. 나머지 쪽은 규칙이 읽지 못했습니다. ` +
          "교정 인덱스의 「OCR 채우기」로 확정본을 만든 뒤 다시 세우세요.",
        "warning",
      );
    }
    await refreshContentsTree();
  } catch (e) {
    showToast(`자동 트리 실패: ${e.message}`, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "자동 트리"; }
  }
}

/**
 * 자동 트리 응답 → 토스트 한 줄. 편성 탭의 「전부 적용해 새로 세우기」도 같은 말을 쓴다.
 * 목차가 없으면 LLM은 아예 불리지 않는다 — 스위치가 켜져 있어도 «규칙만»으로 적는다.
 */
function describeAutoTreeResult(d, useLlm) {
  const llmUsed = useLlm && d.toc_pages?.length;
  const how = llmUsed ? `LLM 구조화${d.toc_meta?.model ? `(${d.toc_meta.model})` : ""} · ` : "규칙만 · ";
  const toc = d.toc_pages?.length ? `목차 ${d.toc_pages.join(",")}쪽 · ` : "목차 없음 · ";
  let text = `${how}${toc}후보 ${d.proposals} 중 ${d.applied}개로 개요를 세웠습니다`;
  if (d.removed) text += ` (이전 ${d.removed}개 정리)`;
  if (d.unmatched_toc?.length) text += ` · 목차에만 있는 항목 ${d.unmatched_toc.length}`;
  if (d.stage && d.stage.summary) text += ` · 이 책의 규약: ${d.stage.summary}`;
  if (d.induced) {
    // 이번에 전문에서 규약을 새로 찾아 저장했다 — 무엇을 켰는지 말한다(D-116)
    const names = d.induced
      .filter((id) => !["short_line", "after_short", "indent"].includes(id))
      .map((id) => (typeof _signalLabel === "function" ? _signalLabel(id) : id));
    text += names.length
      ? ` · 이 책의 규약을 찾아 저장했습니다: ${names.join(", ")}`
      : " · 되풀이되는 표지를 찾지 못해 기본 신호로 세웠습니다";
  }
  return text;
}

document.addEventListener("DOMContentLoaded", () => {
  const b = document.getElementById("contents-auto-btn");
  if (b && !b.dataset.bound) {
    b.dataset.bound = "1";
    b.addEventListener("click", _autoTree);
  }
});
