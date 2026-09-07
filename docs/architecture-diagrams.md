# 아키텍처 다이어그램

> **2026-07-26 기준 — v1.2.0(D-055 ~ D-069)에 맞춰 갱신.** Mermaid 문법으로 작성.
> GitHub, VSCode (Mermaid 확장), [Mermaid Live Editor](https://mermaid.live)에서 렌더링 가능.
>
> **구성**: 13개 다이어그램 — 데이터 모델·스키마(1·3·7), 시스템·모듈(2·8),
> 처리 엔진(4·5), 워크플로우(6·12·13), 저장소·의존(9·10), 화면(11)
>
> **이번 판에서 손댄 곳**
>
> | 다이어그램 | 무엇이 바뀌었나 |
> |---|---|
> | 6 · 11 · 13 | 추출 모드와 화면 구조 — 전면 개작. 13번은 새로 만든 것 |
> | 1 · 5 · 9 · 10 | 텍스트 레이어 PDF 산출, 쪽 전면 1블록, 부분 재-OCR, 되돌리기 |
> | 2 · 8 | 라우트 217개 · JS 모듈 32개 · API 캐시 금지 미들웨어 |
> | 4 | LLM 사용량을 화면에 표시(D-056), LLM Vision OCR을 소비자로 추가 |
> | 3 · 7 · 12 | **v1.3.0에서 바뀜** — 코어 엔티티가 6종으로(경계 목록 추가 D-092, Work 삭제 D-099), 경계 목록은 **원본 저장소**에 산다(D-097). 스키마 19개, L7 주석 4단계는 그대로 |
>
> 그림과 코드가 어긋나면 **코드가 기준**이다.

---

## 1. 8층 데이터 모델

원본 저장소(L1-L4, 단일 정본)와 해석 저장소(L5-L8, 다수 병존)의 구조.
저장소 경계에서 `dependency.json`이 변경을 추적한다.

**여기서 읽어야 할 것**: v1.2.0은 층을 더하지 않았다. 8층은 그대로이고,
L2·L4를 읽어 `exports/`로 나가는 **출구**가 하나 생겼을 뿐이다.
추출 모드도 새 층이 아니라 화면 표시 방식일 뿐이다.

```mermaid
flowchart TB
    subgraph SOURCE["원본 저장소 (L1-L4) -- 단일 정본, 정답이 있는 층"]
        direction LR
        L1["<b>L1 이미지/PDF</b><br/>불변 원본 · 수정 금지<br/><i>manifest · bibliography</i>"]
        L2["<b>L2 OCR 글자해독</b><br/>글자 + 좌표 + 신뢰도<br/><i>ocr_page</i>"]
        L3["<b>L3 레이아웃 분석</b><br/>본문/주석/서문 구분 · 읽기 순서<br/><i>layout_page (LayoutBlock)</i>"]
        L4["<b>L4 사람 수정</b><br/>OCR 교정 · 이체자 확인 · 확정본<br/><i>corrections</i>"]
        L1 --> L2 --> L3 --> L4
    end

    subgraph OUT["산출물 -- 저장소 밖으로 나가는 출구 (v1.2.0)"]
        EXPDF["<b>텍스트 레이어 PDF</b><br/>원본 이미지 위에 보이지 않는 글자<br/><i>exports/권ID_text.pdf · L1은 읽기만</i>"]
    end

    subgraph BOUNDARY["저장소 경계"]
        DEP["<b>dependency.json</b><br/>파일 해시 · 커밋 추적 · 변경 경고"]
    end

    subgraph INTERP["해석 저장소 (L5-L8) -- 다수 해석 병존, 정답 없음"]
        direction LR
        L5["<b>L5 표점 · 현토</b><br/>句讀 삽입 · 懸吐 달기<br/><i>punctuation_page · hyeonto_page</i>"]
        L6["<b>L6 번역</b><br/>현대어역 · 다국어<br/><i>translation_page</i>"]
        L7["<b>L7 주석 · 사전</b><br/>인물/지명 태깅 · 사전형 주석 · 인용마크<br/><i>annotation_page v2 · citation_mark_page</i>"]
        L8["<b>L8 외부연계</b><br/>DB · API · 학술 네트워크<br/><i>relation (코어)</i>"]
        L5 --> L6 --> L7 --> L8
    end

    subgraph CORE["코어 스키마 엔티티"]
        BND["경계 목록<br/><i>원본 저장소</i>"]
        Unit["단위(unit)"]
        Tag["Tag"]
        PROMO["승격 (선택적)"]
        Concept["Concept"]
        Agent["Agent"]
        Relation["Relation"]
        BND --- Unit --- Tag
        Tag -.-> PROMO -.-> Concept
        Concept --- Relation
        Agent --- Relation
    end

    SOURCE --> BOUNDARY --> INTERP
    INTERP --- CORE
    SOURCE -->|"L2 또는 L4를 읽어 새 파일로"| OUT

    style OUT fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style EXPDF fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style SOURCE fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style L1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style L2 fill:#e8f5e9,stroke:#2e7d32
    style L3 fill:#e8f5e9,stroke:#2e7d32
    style L4 fill:#e8f5e9,stroke:#2e7d32
    style BOUNDARY fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style DEP fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style INTERP fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style L5 fill:#e3f2fd,stroke:#1565c0
    style L6 fill:#e3f2fd,stroke:#1565c0
    style L7 fill:#e3f2fd,stroke:#1565c0
    style L8 fill:#e3f2fd,stroke:#1565c0
    style CORE fill:#e1bee7,stroke:#6a2d6a,stroke-width:2px
    style BND fill:#fef3c7,stroke:#b45309
    style Unit fill:#fef3c7,stroke:#b45309
    style Tag fill:#fef3c7,stroke:#b45309
    style Concept fill:#fef3c7,stroke:#b45309
    style Agent fill:#fef3c7,stroke:#b45309
    style Relation fill:#fef3c7,stroke:#b45309
    style PROMO fill:#f3e5f5,stroke:#7b1fa2
```

**핵심 원칙:**
- 원본 저장소는 **단일 정본**으로 수렴 (정답이 있다)
- 해석 저장소는 **다수 병존** (해석은 연구자마다 다르다)
- L4 확정 → `dependency.json` → 해석 저장소 시작점 (저장소 경계)
- 코어 스키마 6개(단위(unit — 보기), Boundaries(경계 목록 — 단위의 정본, D-092), Tag, Concept, Agent, Relation).
  경계 목록은 원본 저장소에 살고(D-097) 나머지는 해석 저장소 안에 있다
- **추출 모드는 층이 아니라 표시 프로필**이다 — 어떤 탭을 보여 줄지만 바뀌고,
  그 상태는 브라우저 `localStorage`(`ctb.profile.<문헌ID>`)에만 남는다.
  `manifest.json`에는 아무것도 기록되지 않으므로 저장되는 데이터는 교감 모드와 완전히 같다 (D-055 · D-060)
- 산출물 `exports/`는 **새 파일**이다 — `L1_source/`는 어떤 경우에도 수정하지 않는다 (D-062 · D-068)

---

## 2. 전체 시스템 아키텍처

프론트엔드(32개 JS 모듈) · 백엔드(FastAPI + 9 라우터, 라우트 217개) ·
처리 엔진(OCR 5종 + LLM 5단 + 산출·검출 보조) · Git 저장소 · 외부 서비스.

**여기서 읽어야 할 것**: 화면과 서버 사이에는 REST API 하나뿐이고 빌드 도구도
프레임워크도 없다. v1.2.0에서 서버가 API 응답에 캐시 금지를 붙여
「고쳤는데 화면이 그대로」를 원천 차단한다 — 호출부 47곳이 아니라 미들웨어 한 곳에서다(D-066).

```mermaid
flowchart TB
    subgraph GIT["Git 저장소 (로컬)"]
        GIT_SRC["원본 저장소<br/>L1-L4"]
        GIT_INT["해석 저장소<br/>L5-L8 (다수)"]
        GIT_MAN["library_manifest.json<br/>서고 전체 지도"]
    end

    subgraph FE["프론트엔드 (Vanilla JS · 빌드 도구 없음)"]
        direction TB
        subgraph FE_CORE["코어 UI"]
            direction LR
            WS["workspace.js<br/>메인 오케스트레이션 · 작업 프로필"]
            PDF["pdf-renderer.js<br/>PDF.js 뷰어"]
            TREE["sidebar-tree.js<br/>문헌/권/페이지 탐색"]
            DD["drag-drop.js<br/>파일 끌어다 놓기 · 첫 서고 자동 생성"]
            CD["create-document.js<br/>URL/파일에서 문헌 생성"]
        end
        subgraph FE_SRC["원본 작업 (L1-L4)"]
            direction LR
            LE["layout-editor.js<br/>L3 영역 편집 · 전면 1블록 자동 생성"]
            CE["correction-editor.js<br/>L4 교정 대조"]
            TXE["text-editor.js<br/>L4 텍스트 편집"]
            BC["batch-correction.js<br/>일괄 이체자 교정"]
            COMP["composition-editor.js<br/>LayoutBlock을 단위로"]
        end
        subgraph FE_EXT["추출 모드 전용 (v1.2.0)"]
            EXP["extract-panel.js<br/>진단 · 권 일괄 OCR · 쪽별 검수 · PDF 산출"]
        end
        subgraph FE_INT["해석 작업 (L5-L8)"]
            direction LR
            PE["punctuation-editor.js<br/>L5 표점"]
            HE["hyeonto-editor.js<br/>L5 현토"]
            TE["translation-editor.js<br/>L6 번역"]
            AE["annotation-editor.js<br/>L7 주석"]
            CIE["citation-editor.js<br/>L7 인용마크"]
        end
        subgraph FE_SUP["지원 모듈"]
            direction LR
            INTJS["interpretation.js"]
            ENT["entity-manager.js"]
            GG["git-graph.js"]
            OP["ocr-panel.js"]
            BIB["bibliography.js"]
            NP["notes-panel.js"]
            HWP["hwp-import.js"]
            AV["alignment-view.js"]
            VM["variant-manager.js"]
            CFM["cite-format-manager.js"]
            RL["reader-line.js"]
            KLD["koten-layout-detector.js"]
            TOAST["toast.js"]
        end
    end

    FE <-->|"REST API"| BE

    subgraph BE["백엔드 (Python · FastAPI)"]
        direction TB
        SRV["server.py<br/>앱 생성 + 라우터 마운트 + 캐시 금지 (152줄)"]
        ST["_state.py<br/>공유 상태 · 헬퍼 · LLM/OCR 캐시"]
        MW["미들웨어<br/>API 응답에 Cache-Control no-store<br/>정적 파일에는 no-cache + ETag (D-066)"]
        subgraph ROUTERS["9개 도메인 라우터 (라우트 217개)"]
            direction LR
            R1["library <b>29</b>"]
            R2["documents <b>43</b>"]
            R3["interpretations <b>22</b>"]
            R9["composition <b>14</b>"]
            R4["llm_ocr <b>24</b>"]
            R5["alignment <b>20</b>"]
            R6["reading <b>24</b>"]
            R7["annotation <b>34</b>"]
            R8["version <b>7</b>"]
        end
    end

    BE --> ENGINE

    subgraph ENGINE["처리 엔진"]
        direction LR
        subgraph OCR_ENG["OCR 엔진 (registry.py)"]
            O1["NDL古典籍OCR Full (TrOCR)"]
            O2["NDL古典籍OCR-Lite (ONNX)"]
            O3["NDLOCR-Lite"]
            O4["LLM Vision OCR"]
            O5["PaddleOCR"]
        end
        subgraph LLM_ENG["LLM 라우터 (router.py)"]
            LR1["1. Ollama (텍스트 gemma4:e4b · 비전 gemma4:cloud)"]
            LR2["2. OpenAI OAuth"]
            LR3["3. Gemini"]
            LR4["4. OpenAI"]
            LR5["5. Anthropic"]
        end
        subgraph OUT_ENG["산출 · 보조 (v1.2.0)"]
            X1["export/text_layer_pdf.py<br/>검색되는 PDF 산출 + 다시 열어 재기"]
            X2["ocr/line_detector.py<br/>줄 위치 검출"]
            X3["ocr/full_page_block.py<br/>쪽 전면 1블록 L3 생성"]
            X4["ocr/layout_staleness.py<br/>다시 돌릴 쪽 판정"]
            X5["ocr/page_backup.py<br/>직전 상태 백업 (되돌리기)"]
        end
        subgraph ETC_ENG["기타"]
            JS_VAL["jsonschema 검증"]
            HWP_P["HWP/HWPX 파서"]
            BIB_P["서지 파서<br/>(NDL · KORCIS · Archives.JP)"]
        end
    end

    subgraph EXT["외부 서비스"]
        EXT_LLM["OpenAI OAuth · Gemini · OpenAI · Anthropic"]
        EXT_OLL["Ollama Server"]
        EXT_PUNCT["punctuation-service<br/>(SikuRoBERTa)"]
        EXT_GIT["GitHub · GitLab<br/>(백업/동기화)"]
        EXT_BIB["NDL · KORCIS<br/>(서지 API)"]
    end

    ENGINE -.-> EXT

    style GIT fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style GIT_SRC fill:#fef3c7,stroke:#b45309
    style GIT_INT fill:#fef3c7,stroke:#b45309
    style FE fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style BE fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style SRV fill:#fef3c7,stroke:#b45309
    style ST fill:#fef3c7,stroke:#b45309
    style MW fill:#fef3c7,stroke:#b45309
    style FE_EXT fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style EXP fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style OUT_ENG fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style X1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style ENGINE fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style EXT fill:#fce4ec,stroke:#c62828,stroke-width:2px,stroke-dasharray: 5 5
    style EXT_LLM fill:#fef3c7,stroke:#b45309
    style EXT_PUNCT fill:#e0f2fe,stroke:#0369a1
```

**역할 분리:**
- **Git**: 저장, 이력, 버전, diff → 이미 있는 인프라
- **앱**: 관계, 의미, 경고, UI → 만들어야 할 것
- **원격 호스팅**: 백업, 동기화 → 교체 가능
- **오프라인 퍼스트**: 핵심 작업(교정, 열람, 커밋)은 인터넷 없이 완전히 동작

---

## 3. 코어 스키마 엔티티 관계 (ER Diagram)

해석 저장소 내부의 6개 엔티티 모델. core-schema-v1.3 기준.
모든 엔티티는 `draft → active → deprecated → archived` 상태 전이를 따른다 (삭제 금지).

```mermaid
flowchart TB
    subgraph SCHEMA["코어 5 + 경계 목록"]
        direction TB
        subgraph ROW1[" "]
            direction LR
            BND2["<b>경계 목록</b> <i>(원본 저장소)</i><br/>document_id / part_id<br/>boundaries[]: id · level · role<br/>start: page/line/offset<br/>anchor_text · l4_commit<br/><i>권마다 파일 하나 — D-092·D-097</i>"]
            TB_NODE["<b>단위(unit)</b> <i>(읽기 보기)</i><br/>id = 시작 경계의 id<br/>sequence_index: 권 안의 차례<br/>original_text: L4에서 잘라 온다<br/>normalized_text: 정규화<br/>source_ref: 출처 추적 JSON<br/>status: draft|active|..."]
            TAG["<b>Tag</b><br/>id: UUID (PK)<br/>block_id: FK → 단위<br/>surface: 표면 텍스트 (필수)<br/>core_category: person|place|...<br/>confidence: 신뢰도 0-1<br/>extractor: llm|rule|human<br/>status: draft|active|..."]
        end
        subgraph ROW2[" "]
            direction LR
            CONCEPT["<b>Concept</b><br/>id: UUID (PK)<br/>label: 대표 이름 (필수)<br/>scope_document: 범위 문헌 (선택)<br/>description: 학술 설명<br/>concept_features: 자유 확장<br/>status: draft|active|..."]
            AGENT["<b>Agent</b><br/>id: UUID (PK)<br/>name: 이름 (필수)<br/>period: 활동 시대<br/>biography_note: 약전<br/>status: draft|active|..."]
            RELATION["<b>Relation</b><br/>id: UUID (PK)<br/>subject_id / subject_type<br/>predicate: snake_case (필수)<br/>object_id / object_type<br/>object_value: 자유 텍스트<br/>evidence_blocks: 단위 ID[]<br/>confidence / status"]
        end

        BND2 -->|"만든다"| TB_NODE
        TB_NODE -->|"has tags"| TAG
        TAG -.->|"승격"| CONCEPT
        AGENT -->|"subject/object"| RELATION
        CONCEPT -->|"subject/object"| RELATION
        TB_NODE -->|"evidence"| RELATION
    end

    style SCHEMA fill:#f3e5f5,stroke:#6a2d6a,stroke-width:2px
    style BND2 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style TB_NODE fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style TAG fill:#e3f2fd,stroke:#1565c0
    style CONCEPT fill:#e3f2fd,stroke:#1565c0
    style AGENT fill:#e3f2fd,stroke:#1565c0
    style RELATION fill:#e3f2fd,stroke:#1565c0
    style ROW1 fill:transparent,stroke:none
    style ROW2 fill:transparent,stroke:none
```

**설계 보장:**
- 구조(Structure) ≠ 해석(Interpretation) — 코어는 구조만 저장
- 온톨로지 잠금 없음 — Concept의 `concept_features`는 자유 확장
- Tag → Concept 승격은 연구자 판단 (선택적, Promotion Flow)
- Predicate는 snake_case, 구조적 행위만 (해석 배제)
- `source_ref`로 원본 저장소 역참조 (document_id + page + layout_block_id + git commit)

---

## 4. LLM 5단 폴백 아키텍처

전체 프로젝트 공용 LLM 연동. `src/llm/router.py`가 단일 진입점.
자동으로 1순위부터 시도, 실패 시 다음으로 폴백.

**여기서 읽어야 할 것**: 순서는 무료 → 저렴 → 최후 폴백이다.
v1.2.0에서 달라진 것은 폴백 구조가 아니라 **투명성**이다 —
어느 모델로 몇 번 불렀고 얼마가 나갔는지가 화면에 표시된다(D-056).

```mermaid
flowchart TB
    ENTRY["<b>src/llm/router.py</b><br/>LLMRouter -- 단일 진입점 · 자동 폴백"]

    ENTRY -->|"시도"| TIER1

    subgraph TIER1_GROUP["1순위: Ollama (로컬)"]
        TIER1["127.0.0.1:11434 -- 비전 gemma4:cloud · 텍스트 gemma4:e4b"]
    end

    TIER1_GROUP -->|"실패 시"| TIER2
    TIER2["<b>2순위: OpenAI OAuth</b><br/>start_server.bat/openai-oauth · 무료 · 비전 포함"]
    TIER2 -->|"실패 시"| TIER3
    TIER3["<b>3순위: Gemini (Google AI)</b><br/>GOOGLE_API_KEY · 저렴 · 비전 포함"]
    TIER3 -->|"실패 시"| TIER4
    TIER4["<b>4순위: OpenAI API</b><br/>OPENAI_API_KEY · 중간 비용 · 비전 포함"]
    TIER4 -->|"실패 시"| TIER5
    TIER5["<b>5순위: Anthropic (Claude)</b><br/>ANTHROPIC_API_KEY · 최후 폴백"]

    subgraph CONSUMERS["LLM 소비자 (src/core/)"]
        direction LR
        C1["punctuation_llm.py<br/>L5 표점 초안"]
        C2["hyeonto.py<br/>L5 현토 초안"]
        C3["translation_llm.py<br/>L6 번역 초안"]
        C4["annotation_llm.py<br/>L7 주석 자동생성"]
        C5["annotation_dict_llm.py<br/>L7 사전 생성"]
        C6["draft.py<br/>범용 LLM 초안"]
        C7["ocr/llm_ocr_engine.py<br/>LLM Vision OCR"]
    end

    CONSUMERS --> ENTRY

    subgraph CONFIG["설정 (config.py)"]
        CF1["환경변수"]
        CF2[".env 파일<br/>(프로젝트 · 서고)"]
        CF3["기본값"]
        CF1 --> CF2 --> CF3
    end

    subgraph USAGE["사용량 추적 (D-056)"]
        direction TB
        UT["usage_tracker.py<br/>토큰 · 비용 · 모델별 집계"]
        UT2["추출 패널에 표시<br/>어느 모델로 몇 번, 얼마"]
        UT --> UT2
    end

    ENTRY --> USAGE

    style UT2 fill:#fef3c7,stroke:#b45309
    style C7 fill:#e0f2fe,stroke:#0369a1
    style ENTRY fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style TIER1 fill:#e8f5e9,stroke:#2e7d32
    style TIER2 fill:#e0f2fe,stroke:#0369a1
    style TIER3 fill:#fff3e0,stroke:#e65100
    style TIER4 fill:#f3e5f5,stroke:#7b1fa2
    style TIER5 fill:#fce4ec,stroke:#c62828
    style CONSUMERS fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style CONFIG fill:#e8f5e9,stroke:#2e7d32
    style CF1 fill:#fef3c7,stroke:#b45309
    style CF2 fill:#fef3c7,stroke:#b45309
    style CF3 fill:#fef3c7,stroke:#b45309
    style USAGE fill:#eceff1,stroke:#546e7a
```

**LLM 협업 패턴 (2-8층 공통):**
1. LLM이 draft 생성
2. 사람이 review
3. 사람이 commit (Git 자동 저장)

---

## 5. OCR 엔진 파이프라인

5개 OCR 엔진의 레지스트리 기반 자동 선택. LayoutBlock 단위로 이미지 크롭 → 전처리 → 인식 → 후처리.

**여기서 읽어야 할 것**: 파이프라인의 입구는 언제나 L3 LayoutBlock이다.
v1.2.0은 그 계약을 바꾸지 않았다 — 대신 입구를 자동으로 채우고(쪽 전면 1블록),
다시 돌릴 쪽을 골라내고(재-OCR 판정), 덮어쓰기 직전 상태를 남기는(되돌리기)
보조 모듈을 그 앞뒤에 붙였다. 출구에는 텍스트 레이어 PDF 산출이 새로 붙었다.

```mermaid
flowchart LR
    subgraph INPUT["입력"]
        IN1["L1 이미지/PDF<br/>페이지 단위"]
        IN2["L3 LayoutBlock<br/>영역 · 읽기순서 · block_type"]
    end

    subgraph PREP["입구 채우기 (v1.2.0)"]
        direction TB
        PR1["full_page_block.py<br/>L3가 없으면 쪽 전면 1블록을 만든다<br/><i>사람이 사각형을 그리지 않는다 -- D-067</i>"]
        PR2["layout_staleness.py<br/>L3가 OCR 이후에 바뀐 쪽만 골라낸다<br/><i>부분 재-OCR -- D-057</i>"]
    end

    subgraph REGISTRY["OCR 레지스트리 (registry.py)"]
        REG["자동 등록<br/>우선순위 기반 선택<br/>엔진 불가시 폴백"]
    end

    subgraph ENGINES["OCR 엔진 (우선순위순)"]
        direction TB
        E1["<b>1.</b> NDL古典籍OCR Full<br/><i>TrOCR · RTMDet · GPU 권장</i>"]
        E2["<b>2.</b> NDL古典籍OCR-Lite<br/><i>ONNX 경량 · CPU 가능</i>"]
        E3["<b>3.</b> NDLOCR-Lite<br/><i>현대/인쇄 · ParseQ · DEIM</i>"]
        E4["<b>4.</b> LLM Vision OCR<br/><i>LLM 비전 모델 활용</i>"]
        E5["<b>5.</b> PaddleOCR<br/><i>다국어 · 멀티라인</i>"]
    end

    subgraph PIPELINE["파이프라인 (pipeline.py)"]
        direction TB
        P1["이미지 크롭<br/>(LayoutBlock bbox)"]
        P2["전처리<br/>(BGR/RGB · 리사이즈)"]
        P3["글자 인식<br/>(엔진별 추론)"]
        P4["후처리<br/>(신뢰도 필터 · 좌표 매핑)"]
        P1 --> P2 --> P3 --> P4
    end

    subgraph OUTPUT["출력"]
        OUT1["<b>L2 OcrResult</b><br/>ocr_page.json"]
        OUT2["OcrLine → OcrCharacter<br/>char · bbox · confidence"]
    end

    subgraph ORDERING["읽기 순서"]
        OR1["XY-Cut 알고리즘"]
        OR2["Smooth Ordering"]
        OR3["割注 블록 감지"]
    end

    subgraph SAFE["되돌리기 (v1.2.0 · D-065)"]
        SF1["page_backup.py<br/>덮어쓰기 직전 L2 + L4를 한 벌 남긴다<br/><i>문헌/.page_backup/ · Git 이력과 무관 · 한 세대만</i>"]
    end

    subgraph DOWN["산출 (v1.2.0)"]
        direction TB
        DN2["line_detector.py<br/>엔진이 좌표를 안 주면 줄 위치를 검출"]
        DN1["export/text_layer_pdf.py<br/>보이지 않는 텍스트 + 폰트 임베드<br/>만든 뒤 다시 열어 잰다"]
        DN2 --> DN1
    end

    INPUT --> PREP --> REGISTRY --> ENGINES --> PIPELINE --> OUTPUT
    OUTPUT --- ORDERING
    OUTPUT --- SAFE
    OUTPUT --> DOWN

    style INPUT fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style REGISTRY fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style REG fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style ENGINES fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style PIPELINE fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style OUTPUT fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style OUT1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style ORDERING fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style PREP fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style SAFE fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style DOWN fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style DN1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
```

---

## 6. 사용자 워크플로우

연구자의 작업 흐름은 v1.2.0부터 **두 갈래**다. 문헌을 열 때 고르는 작업 모드가
그 갈래를 정한다 — 고서를 한 글자씩 다루는 「교감 모드」와,
근현대 논문에서 텍스트만 뽑는 「추출 모드」다.

**여기서 읽어야 할 것**: 두 갈래는 **같은 데이터**를 쓴다. 추출 모드는 저장 형식을
바꾸지 않고 거쳐야 할 단계와 화면을 줄일 뿐이므로, 언제든 모드를 바꿔
반대쪽 흐름을 이어서 할 수 있다. 어느 쪽으로 가든 관리 기능은 공통이다.

```mermaid
flowchart TB
    START["<b>문헌 등록</b><br/>창에 파일 끌어다 놓기 · URL · 폴더<br/>또는 명령 한 줄 -- ctb ocr 논문.pdf"]
    BIBP["서지정보 파싱<br/>NDL · KORCIS · Archives.JP"]
    MODE{"작업 모드를 고른다<br/>문헌마다 기억된다"}
    START --> BIBP --> MODE

    MODE -->|"교감 모드 -- 탭 10개 모두"| C1
    MODE -->|"추출 모드 -- 탭 3개"| E1

    subgraph COLLATE["교감 모드 -- 고서를 한 글자씩"]
        direction TB
        C1["<b>1. 열람</b><br/>PDF.js 뷰어 · 확대/축소 · 읽기 보조선"]
        C2["<b>2. 레이아웃 (L3)</b><br/>본문 · 주석 · 판심제 영역 나누기<br/>자동감지 또는 손으로 · 읽기 순서 지정"]
        C3["<b>3. OCR (L2)</b><br/>엔진 선택 · LayoutBlock별 인식"]
        C4["<b>4. 교정 (L4)</b><br/>OCR 대조 · 이체자 확인 · 확정"]
        C5["<b>5. 편성</b><br/>LayoutBlock을 단위로 · source_ref 추적"]
        C6["<b>6. 해석 (L5-L7)</b><br/>표점 · 현토 · 번역 · 주석 · 인용마크<br/><i>저장소 경계를 넘는다</i>"]
        C1 --> C2 --> C3 --> C4 --> C5 --> C6
    end

    subgraph EXTRACT["추출 모드 -- 논문 한 편에서 텍스트만"]
        direction TB
        E1["<b>1. 진단</b><br/>이 PDF에 텍스트 레이어가 이미 있는가"]
        E2A["<b>있다 → 바로 가져오기</b><br/>OCR 없이 L4로 · 비용 0"]
        E2B["<b>없다, 스캔본 → 권 전체 OCR</b><br/>L3는 쪽 전면 1블록으로 자동 생성<br/>끝난 쪽은 건너뛴다 -- 중단해도 이어서"]
        E3["<b>2. 쪽별 검수</b><br/>글자 수 · 줄 수 · 미리보기<br/>빈 쪽/짧은 쪽 표시 · 확인한 쪽 체크<br/>LLM 사용량 표시"]
        E4["<b>3. 검색되는 PDF 만들기</b><br/>exports/로 새 파일 · 만든 뒤 다시 열어 잰다"]
        E5["<b>4. 내려받기</b><br/>파일 이름은 원본 논문 그대로"]
        E1 --> E2A --> E3
        E1 --> E2B --> E3
        E3 --> E4 --> E5
    end

    FIX["<b>나쁜 쪽만 손본다</b><br/>레이아웃을 고친 쪽을 기계가 찾아 그 쪽만 다시 OCR<br/>새 결과가 이전만 못하면 직전 상태로 되돌리기<br/><i>L2와 L4를 함께 -- D-057 · D-065</i>"]
    E3 -.-> FIX
    C4 -.-> FIX
    FIX -.-> E3

    subgraph MANAGE["관리 -- 두 갈래 공통"]
        direction LR
        M1["Git 이력<br/>커밋 · diff · 되돌리기"]
        M2["스냅샷<br/>JSON 내보내기/가져오기"]
        M3["권 추가<br/>문헌은 만든 뒤에도 자란다"]
        M4["이체자 사전 · 일괄 교정"]
        M5["백업 · 휴지통"]
    end

    COLLATE --> MANAGE
    EXTRACT --> MANAGE

    subgraph LLM_PATTERN["LLM 협업 패턴 (2-8층 공통)"]
        direction TB
        LP1["LLM이 draft 생성"]
        LP2["사람이 review"]
        LP3["사람이 commit<br/>(Git 자동 저장)"]
        LP1 --> LP2 --> LP3
    end

    style START fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style MODE fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style COLLATE fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style EXTRACT fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style FIX fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style E4 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style MANAGE fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style LLM_PATTERN fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style LP1 fill:#fef3c7,stroke:#b45309
    style LP2 fill:#fef3c7,stroke:#b45309
    style LP3 fill:#fef3c7,stroke:#b45309
```

**두 갈래의 차이는 화면뿐이다:**
- 추출 모드에서 숨는 것은 상단 탭 7개(편성 · 표점 · 현토 · 번역 · 주석 · 인용 · 이체자)와
  사이드바 패널 6개(Git 이력 · 검증 · 의존 · 엔티티 · 비고 · 인용 양식)다
- 숨기는 것은 표시뿐이고 모드 · 패널 · 데이터는 그대로 남는다
- 프로필은 문헌마다 브라우저에 기억된다 — 한 서고에 고서와 논문이 섞여도 된다

---

## 7. 스키마 간 참조 관계도

19개 스키마(원본 7 + 해석 5 + 코어 6 + 교환 1)의 연결 구조.
화살표는 참조 방향: A → B = 「A가 B를 참조」.

```mermaid
flowchart TB
    subgraph SRC_SCHEMA["원본 저장소 스키마 (7개)"]
        direction TB
        S_MAN["<b>manifest</b><br/><i>document_id, parts, completeness_status</i>"]
        S_BIB["<b>bibliography</b><br/><i>서지정보, raw_metadata, _mapping_info</i>"]
        S_OCR["<b>ocr_page</b><br/><i>OcrResult · char, bbox, confidence</i>"]
        S_LAY["<b>layout_page</b><br/><i>LayoutBlock · block_id, bbox, reading_order</i>"]
        S_COR["<b>corrections</b><br/><i>Correction · type, original_ocr, corrected</i>"]
        S_IMP["<b>interp_manifest</b><br/><i>interpretation_id, source_document_id</i>"]
        S_DEP["<b>dependency</b><br/><i>source.base_commit, tracked_files, status</i>"]
    end

    subgraph INT_SCHEMA["해석 저장소 스키마 (5개)"]
        direction TB
        I_PUN["<b>punctuation_page</b><br/><i>block_id, marks, target, before/after</i>"]
        I_HYE["<b>hyeonto_page</b><br/><i>block_id, annotations, position, text</i>"]
        I_TRA["<b>translation_page</b><br/><i>source, translations, status, annotation_context</i>"]
        I_ANN["<b>annotation_page v2</b><br/><i>blocks, annotations, dictionary, generation_history</i>"]
        I_CIT["<b>citation_mark_page</b><br/><i>marks, source, marked_from, citation_override</i>"]
    end

    subgraph CORE_SCHEMA["코어 스키마 (6개)"]
        direction TB
        C_BND["<b>경계 목록</b><br/><i>원본 저장소 · 권마다 하나</i>"]
        C_TB["<b>단위(unit)</b><br/><i>경계의 읽기 보기 · original_text는 L4에서</i>"]
        C_TAG["<b>Tag</b><br/><i>block_id, surface, core_category</i>"]
        C_CON["<b>Concept</b><br/><i>label, concept_features</i>"]
        C_AGE["<b>Agent</b><br/><i>name, period</i>"]
        C_REL["<b>Relation</b><br/><i>subject, predicate, object, evidence_blocks</i>"]
    end

    subgraph EXCHANGE["교환 형식 (1개)"]
        EX["<b>exchange</b><br/><i>단일 JSON 스냅샷 · 내보내기/가져오기</i>"]
    end

    S_LAY --> S_MAN
    S_OCR --> S_MAN
    S_OCR --> S_LAY
    S_COR --> S_LAY
    S_IMP --> S_MAN
    S_DEP --> S_MAN
    I_PUN --> S_LAY
    I_HYE --> S_LAY
    I_TRA --> S_LAY
    I_ANN --> S_LAY
    I_CIT --> S_LAY
    I_TRA <-->|"annotation_context"| I_ANN
    C_TB --> C_WOR
    C_TAG --> C_TB
    C_TAG -.->|"승격"| C_CON
    C_REL --> C_AGE
    C_REL --> C_CON
    C_REL --> C_TB
    C_TB -.->|"source_ref 역참조"| S_MAN

    style SRC_SCHEMA fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style S_MAN fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style INT_SCHEMA fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style CORE_SCHEMA fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style EXCHANGE fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style EX fill:#fef3c7,stroke:#b45309,stroke-width:2px
```

**참조 패턴 요약:**
- 원본 내부: `layout_page/ocr_page` → `manifest`, `ocr_page/corrections` → `layout_page`
- 저장소 간: `interp_manifest/dependency` → `manifest` (document_id + base_commit)
- 해석→원본: 모든 해석 스키마 → `layout_page` (block_id로 연결)
- 해석 내부: `translation_page` ↔ `annotation_page` (annotation_context)
- 코어→원본: `단위(unit).source_ref` → `manifest` (역참조)

---

## 8. 백엔드 모듈 의존 구조

`server.py`(조립) → 9개 라우터 → `_state.py`(공유 상태) → core/llm/ocr/export 모듈.
라우터 간 직접 import 금지. `_state.py`가 lazy import로 순환 방지.

**여기서 읽어야 할 것**: 새 기능은 새 라우터를 만들지 않고 기존 도메인 라우터에 붙었다.
v1.2.0에서 늘어난 27개 라우트는 `documents`(텍스트 레이어 진단 · 가져오기 · PDF 산출 · 권 추가)와
`llm_ocr`(권 단위 일괄 OCR · 되돌리기 · 훑어보기)에 몰려 있다.

```mermaid
flowchart TB
    subgraph APP["src/app/ -- API 레이어"]
        direction TB
        MAIN["__main__.py<br/>CLI 진입점"]
        SRV["<b>server.py</b><br/>FastAPI 앱 생성 · 라우터 마운트 · 캐시 금지 (152줄)"]
        STATE["<b>_state.py</b><br/>공유 상태, 헬퍼 · LLM 캐시, 토큰 계산"]
        subgraph ROUTERS["routers/ -- 9개 도메인 · 라우트 217개"]
            direction LR
            R1["library <b>29</b>"]
            R2["documents <b>43</b>"]
            R3["interpretations <b>22</b>"]
            R9["composition <b>14</b>"]
            R4["llm_ocr <b>24</b>"]
            R5["alignment <b>20</b>"]
            R6["reading <b>24</b>"]
            R7["annotation <b>34</b>"]
            R8["version <b>7</b>"]
        end
        MAIN --> SRV --> ROUTERS --> STATE
    end

    APP -->|"lazy import"| CORE_MOD
    APP -->|"lazy import"| LLM_MOD
    APP -->|"lazy import"| OCR_MOD
    APP -->|"lazy import"| EXPORT_MOD

    subgraph CORE_MOD["src/core/ -- 비즈니스 로직"]
        direction TB
        CM1["library"]
        CM2["document"]
        CM3["interpretation"]
        CM4["entity"]
        CM5["punctuation / punctuation_llm"]
        CM6["hyeonto"]
        CM7["translation / translation_llm"]
        CM8["annotation / annotation_llm<br/>annotation_dict_llm / annotation_dict_match"]
        CM9["citation_mark"]
        CM10["alignment"]
        CM11["git_graph"]
        CM12["snapshot / snapshot_validator"]
        CM13["backup"]
        CM14["layout_analyzer"]
    end

    subgraph LLM_MOD["src/llm/ -- LLM 통합"]
        direction TB
        LM1["<b>router.py -- 5단 폴백</b>"]
        LM2["config.py"]
        LM3["draft.py"]
        LM4["usage_tracker.py"]
        subgraph PROVIDERS["providers/"]
            LP1["ollama (텍스트 gemma4:e4b · 비전 gemma4:cloud)"]
            LP2["openai_oauth"]
            LP3["gemini"]
            LP4["openai"]
            LP5["anthropic"]
        end
    end

    subgraph OCR_MOD["src/ocr/ -- OCR 엔진 + 보조"]
        direction TB
        OM1["registry.py"]
        OM2["pipeline.py"]
        OM3["ndlkotenocr_full"]
        OM4["ndlkotenocr_lite"]
        OM5["ndlocr_lite"]
        OM6["llm_ocr"]
        OM7["paddleocr"]
        OM8["<b>full_page_block.py</b> (v1.2.0)"]
        OM9["<b>layout_staleness.py</b> (v1.2.0)"]
        OM10["<b>page_backup.py</b> (v1.2.0)"]
        OM11["<b>line_detector.py</b> (v1.2.0)"]
    end

    subgraph EXPORT_MOD["src/export/ -- 산출물 (v1.2.0)"]
        EM1["<b>text_layer_pdf.py</b><br/>검색되는 PDF 산출 + 다시 열어 재기"]
    end

    subgraph MISC["기타 모듈"]
        direction TB
        MI1["src/parsers/<br/>ndl, korcis, archives_jp"]
        MI2["src/hwp/<br/>reader, text_cleaner"]
        MI3["src/text_import/<br/>pdf_extractor, text_separator"]
        MI4["src/cli/<br/>ctb 진입점 · embed_folder<br/>models(모델 고르기) · config(기본값 기억)"]
    end

    CORE_MOD --> LLM_MOD
    CORE_MOD --> OCR_MOD
    OCR_MOD --> EXPORT_MOD

    style APP fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style SRV fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style STATE fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style CORE_MOD fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style LLM_MOD fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style LM1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style OCR_MOD fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style EXPORT_MOD fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style EM1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style MISC fill:#eceff1,stroke:#546e7a,stroke-width:2px
```

**규칙:**
- 라우터 간 직접 import 금지 → 공유 로직은 `_state.py`에 배치
- `_state.py`는 core/llm/ocr/export 모듈을 lazy import (순환 방지)
- Pydantic 모델은 사용하는 라우터 파일 내부에 정의
- JSON 저장은 반드시 `core.document.write_json_atomic()` — 저장 도중 죽으면
  `manifest.json`이 빈 파일이 되어 문헌이 통째로 열리지 않는다 (D-069)

---

## 9. Git 저장소 모델

하나의 원본 저장소 위에 여러 해석 저장소가 독립 Git 리포로 병존.
`library_manifest.json`이 서고 전체 지도 역할.

**여기서 읽어야 할 것**: v1.2.0에서 문헌 폴더 안에 두 개가 늘었다 —
밖으로 내보낼 산출물을 담는 `exports/`와, 되돌리기용 `.page_backup/`이다.
둘 다 **Git 이력과는 다른 층위**다. 되돌리기는 커밋을 되감는 것이 아니라
직전 파일 한 벌을 되돌려 놓는 것이고, 남기는 세대는 하나뿐이다.

```mermaid
flowchart TB
    subgraph LIBRARY["서고 (library_manifest.json)"]
        LIB["<b>library_manifest.json</b><br/>서고 전체 지도 · 문헌 목록, 해석 목록"]
    end

    subgraph SRC_REPO["원본 저장소 (Git repo)"]
        direction TB
        SR_MAN["manifest.json<br/>document_id, parts (권은 나중에 더할 수 있다)"]
        SR_L1["<b>L1_source/</b><br/>PDF, 이미지 (불변)"]
        SR_L2["L2_ocr/<br/>ocr_page JSON"]
        SR_L3["L3_layout/<br/>layout_page JSON"]
        SR_L4["L4_text/<br/>corrections JSON · 교정 텍스트"]
        SR_BIB["bibliography.json"]
        SR_EXP["<b>exports/</b> (v1.2.0)<br/>텍스트 레이어 PDF -- 밖으로 내보내는 산출물"]
        SR_BAK["<b>.page_backup/</b> (v1.2.0)<br/>덮어쓰기 직전 L2 + L4 한 벌<br/><i>로컬 되돌리기 · Git 이력과 무관 · 한 세대만</i>"]
        SR_GIT["Git 이력: commit, diff, log"]
    end

    subgraph REMOTE["원격 호스팅 (선택)"]
        REM["GitHub / GitLab / Gitea<br/>← push/pull →"]
    end

    subgraph INTERP_A["해석 A (연구자 김, Git repo)"]
        direction TB
        IA_MAN["interp_manifest.json -- interpreter: 김"]
        IA_DEP["<b>dependency.json -- base_commit 추적</b>"]
        IA_L5["L5/<br/>punctuation, hyeonto"]
        IA_L6["L6/<br/>translation"]
        IA_L7["L7/<br/>annotation, citation"]
        IA_CORE["core/ -- 경계 목록, 단위(unit), Tag, Concept, Agent, Relation"]
    end

    subgraph INTERP_B["해석 B (LLM draft, Git repo)"]
        direction TB
        IB_MAN["interp_manifest.json -- interpreter: LLM"]
        IB_DEP["dependency.json"]
        IB_L5["L5/"]
        IB_L6["L6/ LLM 번역"]
    end

    subgraph INTERP_C["해석 C (공동연구, Git repo)"]
        direction TB
        IC_MAN["interp_manifest.json -- interpreter: 팀"]
        IC_DEP["dependency.json"]
        IC_L5["L5/"]
        IC_L6["L6/"]
        IC_L7["L7/"]
    end

    LIBRARY --> SRC_REPO
    LIBRARY --> INTERP_A
    LIBRARY --> INTERP_B
    LIBRARY --> INTERP_C
    IA_DEP -.->|"base_commit"| SRC_REPO
    IB_DEP -.->|"base_commit"| SRC_REPO
    IC_DEP -.->|"base_commit"| SRC_REPO
    SRC_REPO <-->|"push/pull"| REMOTE

    style LIBRARY fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style LIB fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style SRC_REPO fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style SR_L1 fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style SR_EXP fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style SR_BAK fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style REMOTE fill:#eceff1,stroke:#546e7a,stroke-width:2px,stroke-dasharray: 5 5
    style INTERP_A fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style IA_DEP fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style INTERP_B fill:#e8eaf6,stroke:#3f51b5
    style INTERP_C fill:#ede7f6,stroke:#5e35b1
    style IA_CORE fill:#f3e5f5,stroke:#7b1fa2
```

---

## 10. 층별 의존 관계

하위층 변경이 상위층에 미치는 영향. `dependency.json`의 `dependency_status` 상태 전이.

**여기서 읽어야 할 것**: 원본 저장소 안에는 v1.2.0에서 **거꾸로 가는 화살표**가 하나 생겼다.
레이아웃(L3)을 고치면 그 쪽의 OCR(L2)이 낡은 것이 되는데, 예전에는 사용자가
그것을 기억해 쪽 번호를 입력해야 했다. 이제 기계가 찾아낸다.

```mermaid
flowchart LR
    subgraph SRC_DEP["원본 저장소 내부"]
        direction TB
        D_L1["L1 이미지 (불변)"]
        D_L2["L2 OCR"]
        D_L3["L3 레이아웃"]
        D_L4["L4 교정"]
        D_L1 -->|"거의 없음"| D_L2
        D_L2 -->|"OCR 재실행 필요"| D_L3
        D_L3 -->|"블록 재분류 필요"| D_L4
        D_L3 -.->|"레이아웃을 고치면 그 쪽만 다시 OCR<br/>layout_staleness.py가 찾아낸다 (v1.2.0)"| D_L2
    end

    subgraph BOUNDARY_DEP["저장소 경계"]
        direction TB
        BD["<b>경고 발생</b><br/>dependency.json<br/>tracked_files hash 비교"]
        BD_NOTE["모든 해석에 경고 전파"]
    end

    subgraph INT_DEP["해석 저장소 내부"]
        direction TB
        I_L5["L5 표점/현토"]
        I_L6["L6 번역"]
        I_L7["L7 주석"]
        I_L8["L8 외부연계"]
        I_L5 -->|"표점 변경시 번역 재검토"| I_L6
        I_L6 -->|"번역 변경시 주석 재검토"| I_L7
        I_L7 -->|"주석 변경시"| I_L8
    end

    subgraph STATUS["dependency_status 상태"]
        direction TB
        ST_SYNC["<b>synced</b><br/>일치"]
        ST_STALE["<b>stale</b><br/>변경 감지"]
        ST_ACK["<b>acknowledged</b><br/>확인 완료"]
        ST_SYNC -->|"변경 발생"| ST_STALE
        ST_STALE -->|"확인"| ST_ACK
        ST_ACK -->|"다시 synced"| ST_SYNC
    end

    SRC_DEP ==> BOUNDARY_DEP ==> INT_DEP

    style SRC_DEP fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style BOUNDARY_DEP fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style BD fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style INT_DEP fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style STATUS fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style ST_SYNC fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style ST_STALE fill:#fce4ec,stroke:#c62828
    style ST_ACK fill:#fff3e0,stroke:#e65100
```

---

## 11. 프론트엔드 UI 구조

VSCode 스타일 화면. 왼쪽(액티비티 바 + 사이드바) · 가운데(원본 뷰어) ·
오른쪽(작업 패널), 그리고 그 위에 상단 작업 탭 10개.

**여기서 읽어야 할 것**: v1.2.0에서 **하단 패널이 사라졌다.** 거기 있던
Git 이력 · 검증 · 의존 추적 · 엔티티 · 비고는 모두 왼쪽 액티비티 바의
사이드바로 옮겨졌다. 그리고 추출 모드에서는 「추출 모드에서 숨음」이라고
적힌 것들이 통째로 숨는다 — 탭 7개와 사이드바 패널 6개다.
숨는 것은 표시뿐이고, 데이터와 API는 그대로 남아 있다.

```mermaid
flowchart TB
    subgraph TABS["상단 작업 탭 -- 모두 10개"]
        direction LR
        T_KEEP["<b>추출 모드에도 남는 셋</b><br/>열람 · 레이아웃 · 교정<br/><i>원본을 보고, 드물게 영역을 나누고, 결과를 손본다</i>"]
        T_HIDE["<b>추출 모드에서 숨는 일곱</b><br/>편성 · 표점 · 현토 · 번역 · 주석 · 인용 · 이체자"]
    end

    subgraph LAYOUT["3영역 레이아웃"]
        direction LR
        subgraph LEFT["왼쪽: 액티비티 바 + 사이드바"]
            direction TB
            ACT["<b>액티비티 바</b><br/>아이콘으로 사이드바 패널 전환"]
            A1["서고 브라우저 -- sidebar-tree.js<br/>문헌 · 권 · 쪽 트리 · 새 문헌 · 권 추가"]
            A2["Git 이력 -- git-graph.js<br/><i>추출 모드에서 숨음</i>"]
            A3["검증 결과<br/><i>추출 모드에서 숨음</i>"]
            A4["의존 추적 -- interpretation.js<br/>해석 저장소 목록 · dependency 상태<br/><i>추출 모드에서 숨음</i>"]
            A5["엔티티 -- entity-manager.js<br/><i>추출 모드에서 숨음</i>"]
            A6["비고 -- notes-panel.js<br/><i>추출 모드에서 숨음</i>"]
            A7["인용 양식 -- cite-format-manager.js<br/><i>추출 모드에서 숨음</i>"]
            A8["설정 · 서지정보 · 테마<br/><i>원격 저장소 절만 추출 모드에서 숨음</i>"]
            ACT --> A1
            ACT --> A2
            ACT --> A3
            ACT --> A4
            ACT --> A5
            ACT --> A6
            ACT --> A7
            ACT --> A8
        end
        subgraph CENTER["가운데: 원본 뷰어"]
            direction TB
            PDF_R["<b>pdf-renderer.js</b><br/>PDF.js 통합 · 확대/축소/회전"]
            LAY_E["layout-editor.js<br/>LayoutBlock 오버레이 · 영역 편집 · 읽기순서<br/>블록이 없으면 쪽 전면 1블록을 만들어 바로 저장 (D-067)"]
            RL["reader-line.js<br/>읽기 보조선 -- 가로/세로"]
            EXPANEL["<b>extract-panel.js</b><br/>열람 탭 안에 놓인 추출 패널<br/>진단 · 권 일괄 OCR · 쪽별 검수 · PDF 산출<br/><i>추출 모드에서만 보인다</i>"]
        end
        subgraph RIGHT["오른쪽: 작업 패널 -- 탭으로 전환"]
            direction TB
            TAB0["교정 -- correction-editor.js · text-editor.js"]
            TAB1["편성 -- composition-editor.js"]
            TAB2["표점 -- punctuation-editor.js"]
            TAB3["현토 -- hyeonto-editor.js"]
            TAB4["번역 -- translation-editor.js"]
            TAB5["주석 -- annotation-editor.js"]
            TAB6["인용 -- citation-editor.js"]
            TAB7["이체자 -- variant-manager.js · alignment-view.js"]
        end
    end

    TABS --> LAYOUT

    subgraph PROFILE["작업 프로필 전환 (D-055 · D-060)"]
        direction TB
        PF1["workspace.js -- applyWorkspaceProfile()"]
        PF2["data-profile 표시가 붙은 요소에 hidden을 건다"]
        PF3["숨은 탭이나 패널을 보고 있었다면<br/>열람 탭 · 서고 브라우저로 되돌린다<br/><i>돌아올 방법이 없는 화면에 갇히지 않게</i>"]
        PF4["상태는 브라우저 localStorage에만<br/>ctb.profile.문헌ID -- manifest에는 남지 않는다"]
        PF1 --> PF2 --> PF3 --> PF4
    end

    PROFILE -.->|"탭 7개 · 사이드바 패널 6개를 숨긴다"| LAYOUT

    GONE["<b>하단 패널은 제거됐다 (v1.2.0)</b><br/>높이 드래그 · 접기/펴기와 함께 사라졌고<br/>내용은 전부 왼쪽 사이드바로 옮겨졌다"]
    GONE -.-> LEFT

    subgraph POPUP["다이얼로그 · 전역"]
        direction LR
        P1["toast.js -- 알림"]
        P2["drag-drop.js -- 창 어디에나 끌어다 놓기"]
        P3["create-document.js -- 새 문헌"]
        P4["bibliography.js -- 서지정보"]
        P5["ocr-panel.js -- 쪽 단위 OCR 실행"]
        P6["batch-correction.js -- 일괄 교정"]
        P7["hwp-import.js -- D-037로 잠김"]
    end

    style TABS fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style T_KEEP fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style T_HIDE fill:#eceff1,stroke:#546e7a
    style LAYOUT fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style LEFT fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style A1 fill:#fef3c7,stroke:#b45309
    style CENTER fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style PDF_R fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style EXPANEL fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style RIGHT fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style PROFILE fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style GONE fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style POPUP fill:#eceff1,stroke:#546e7a,stroke-width:2px
```

---

## 12. L7 주석 4단계 누적 생성 워크플로우

annotation_page v2의 4단계 `current_stage` 전이. 각 단계마다 `generation_history`에 스냅샷 저장.

```mermaid
flowchart TB
    subgraph STAGE1["Stage 1: from_original"]
        direction LR
        S1A["L4 교정 텍스트<br/>(원문)"]
        S1B["<b>LLM 분석</b><br/>인물/지명/용어 추출"]
        S1C["기본 주석 생성<br/>type, label, description"]
        S1A --> S1B --> S1C
    end

    STAGE1 -->|"Stage 1 스냅샷 저장"| STAGE2

    subgraph STAGE2["Stage 2: from_translation"]
        direction LR
        S2A["L6 번역문<br/>(현대어)"]
        S2B["<b>LLM 보강</b><br/>번역 맥락 반영"]
        S2C["사전 의미 보강<br/>dict_meaning, ctx_meaning"]
        S2A --> S2B --> S2C
    end

    STAGE2 -->|"Stage 2 스냅샷 저장"| STAGE3

    subgraph STAGE3["Stage 3: from_both"]
        direction LR
        S3A["원문 + 번역<br/>(양쪽 참조)"]
        S3B["<b>LLM 교차 검증</b><br/>누락 보완"]
        S3C["교차 검증 완료<br/>sources, related 추가"]
        S3A --> S3B --> S3C
    end

    STAGE3 -->|"Stage 3 스냅샷 저장"| STAGE4

    subgraph STAGE4["Stage 4: reviewed"]
        direction LR
        S4A["연구자 검토"]
        S4B["<b>수동 편집</b><br/>추가/삭제/수정"]
        S4C["최종 확정<br/>status: accepted"]
        S4A --> S4B --> S4C
    end

    subgraph DICT["사전형 주석 (DictionaryEntry)"]
        direction TB
        D1["<b>headword</b>: 표제어"]
        D2["reading: 독음"]
        D3["dict_meaning: 사전 의미"]
        D4["ctx_meaning: 문맥 의미"]
        D5["sources: 출처"]
        D6["related: 관련 항목"]
    end

    subgraph HIST["generation_history"]
        direction TB
        H1["Stage 1 스냅샷"]
        H2["Stage 2 스냅샷"]
        H3["Stage 3 스냅샷"]
    end

    style STAGE1 fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style S1B fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style STAGE2 fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style S2B fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style STAGE3 fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style S3B fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style STAGE4 fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style S4B fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style S4C fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style DICT fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style D1 fill:#fef3c7,stroke:#b45309
    style HIST fill:#eceff1,stroke:#546e7a,stroke-width:2px
```

---

## 13. 추출 모드 — 논문 한 편의 경로

근현대 논문 스캔본에서 텍스트만 뽑아 **검색되는 PDF**로 내보내는 흐름.
D-055 · D-057 · D-062 · D-065 · D-067 · D-068이 여기에 모인다.

**여기서 읽어야 할 것**: 이 경로는 8층 모델을 우회하지 않는다.
L3(레이아웃)와 L2(OCR)를 그대로 거치되, 사람이 손대야 하던 자리를 기계가 채운다.
그리고 마지막에 「만든 것을 다시 열어 재는」 단계가 있다 —
글자가 조용히 사라지거나 엉뚱한 크기로 박히는 사고를 여기서 잡는다.

```mermaid
flowchart TB
    IN["<b>L1 원본 PDF</b><br/>끌어다 놓기 · URL · ctb ocr 명령<br/><i>L1_source/ -- 절대 수정하지 않는다</i>"]

    DIAG{"텍스트 레이어가<br/>이미 있는가"}
    IN --> DIAG

    DIAG -->|"있다"| IMP["<b>바로 가져오기</b><br/>PDF에 박힌 글자를 L4로<br/><i>OCR 비용 0</i>"]

    DIAG -->|"없다 -- 스캔본"| L3AUTO["<b>L3를 앱이 만든다</b><br/>full_page_block.py<br/>쪽 전면을 덮는 LayoutBlock 하나<br/><i>사람이 사각형을 그리지 않는다</i>"]

    L3AUTO --> BATCH["<b>권 전체 OCR</b><br/>끝난 쪽은 건너뛴다 -- 중단해도 이어서<br/><i>덮어쓰기 직전 상태를 page_backup.py가 남긴다</i>"]
    BATCH --> L2["<b>L2 OCR 결과</b><br/>글자 · 좌표 · 신뢰도"]
    L2 --> L4["<b>L4 교정 텍스트</b><br/>배치가 함께 채운다 -- fill_text_layer"]
    IMP --> L4

    L4 --> REVIEW["<b>쪽별 검수</b><br/>글자 수 · 줄 수 · 미리보기<br/>빈 쪽/짧은 쪽 표시 · 확인한 쪽 체크<br/>LLM 사용량 -- 어느 모델로 몇 번, 얼마 (D-056)"]

    REVIEW -->|"나쁜 쪽이 있다"| REDO
    REVIEW -->|"충분하다"| LINE

    subgraph REDO_G["다시 돌리기 · 되돌리기 (D-057 · D-064 · D-065)"]
        direction TB
        REDO["레이아웃 탭에서 영역을 나눈다"]
        STALE["layout_staleness.py<br/>L2의 layout_block_id 집합과 현재 L3를 비교<br/>바뀐 쪽만 골라낸다"]
        UNDO["되돌리기 -- 새 결과가 이전만 못할 때<br/>직전 L2 + L4를 함께 복원<br/><i>문헌/.page_backup/ · Git 이력과 무관</i>"]
        REDO --> STALE
        STALE --> UNDO
    end

    STALE -->|"그 쪽만 다시"| BATCH
    UNDO -.-> REVIEW

    subgraph MAKE_G["검색되는 PDF 만들기 -- export/text_layer_pdf.py"]
        direction TB
        LINE["<b>줄 위치 검출</b><br/>line_detector.py<br/>엔진이 좌표를 안 주면 여기서 자리를 찾는다<br/><i>실패하면 page-approximated로 기록하고 알린다</i>"]
        CONV["<b>좌표 환산</b><br/>L2의 픽셀 좌표를 PDF 포인트로<br/>배율은 L3의 image_width에서 구한다"]
        WRAP["<b>원본이 남긴 좌표 변환을 끊는다</b><br/>page.wrap_contents<br/><i>이것을 빠뜨려 0.24배로 박혔다 -- D-068</i>"]
        FONT["<b>폰트를 임베드한다</b><br/>표준 CID 폰트에 없는 한자가 사라지지 않게<br/><i>실측 51종 130자 누락 → 2종 2자 -- D-062</i>"]
        INVIS["<b>보이지 않는 텍스트로 써넣는다</b><br/>render_mode 3 -- 이미지는 그대로 보인다"]
        LINE --> CONV --> WRAP --> FONT --> INVIS
    end

    INVIS --> VERIFY["<b>만든 것을 다시 열어 잰다</b><br/>글자 크기 · 텍스트가 덮은 넓이<br/>표본 3쪽은 잉크 밀도까지<br/><i>이상하면 내려받기 버튼 옆에 남는다</i>"]

    VERIFY --> OUTPDF["<b>exports/권ID_text.pdf</b><br/>내려받는 이름은 원본 논문 파일명 그대로<br/><i>L1_source/는 그대로 남는다</i>"]

    style IN fill:#fef3c7,stroke:#b45309,stroke-width:2px
    style DIAG fill:#ffe0b2,stroke:#e65100,stroke-width:2px
    style L3AUTO fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style BATCH fill:#e8f5e9,stroke:#2e7d32
    style L2 fill:#fce4ec,stroke:#c62828
    style L4 fill:#fce4ec,stroke:#c62828
    style REVIEW fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style REDO_G fill:#eceff1,stroke:#546e7a,stroke-width:2px
    style MAKE_G fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style WRAP fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style FONT fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style VERIFY fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    style OUTPDF fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
```

**이 경로가 지키는 약속:**
- 원본은 읽기만 한다 — `L1_source/`는 어떤 단계에서도 수정되지 않는다
- 만든 것은 믿지 않고 잰다 — 산출물을 다시 열어 글자 크기 · 덮개 · 잉크 밀도를 확인한다
- 되돌릴 수 있다 — 저장할 때마다 직전 한 벌을 남기므로 되돌리기는 언제나 「방금 저장한 것 취소」다
- 중단할 수 있다 — 끝난 쪽은 건너뛰므로 권 전체 OCR을 언제 멈춰도 이어서 돌아간다

**알려진 한계** (v1.2.0 릴리스 노트와 같음):
- 줄 위치 검출이 실패한 쪽은 텍스트가 원문 자리가 아닌 곳에 순서대로 놓인다.
  검색은 되지만 형광이 엉뚱한 데 뜬다
- 앱 안의 PDF 뷰어는 텍스트 레이어를 그리지 않는다.
  Ctrl+F는 내보낸 파일을 외부 뷰어에서 열 때 동작한다

---

## 부록: 설계 원칙 요약

| 원칙 | 설명 |
|------|------|
| **원본 불변** | L1 파일, raw_metadata, original_text — 수정 금지 |
| **모든 필드 Nullable** | 소스에 없는 필드는 비워두고 나중에 채운다 |
| **삭제 금지, 상태 전이만** | `draft → active → deprecated → archived` |
| **원문 비변형** | 표점/현토/번역은 글자 인덱스 오버레이. 원문은 그대로 |
| **매핑 투명성** | `_mapping_info`에 출처/신뢰도 기록 |
| **출처 추적** | `source_ref`로 원본 저장소 역참조 |
| **온톨로지 비강제** | Concept 자유 확장. 부재 = 미지정 |
| **Promotion Flow** | Tag(잠정) → Concept(확정), 연구자 판단 |
| **용어 규칙** | LayoutBlock / OcrResult / 단위(unit). 「Block」 단독 사용 금지 |
| **오프라인 퍼스트** | 핵심 작업(교정, 열람, 커밋)은 인터넷 없이 동작 |
| **작업 모드는 표시만 바꾼다** | 추출 모드는 탭·패널을 숨길 뿐, 저장 형식과 데이터는 교감 모드와 같다 (D-055) |
| **산출물은 다시 열어 잰다** | 만든 PDF를 앱이 열어 글자 크기·덮개·잉크 밀도를 확인한다 (D-068) |
| **덮어쓰기 전에 한 벌 남긴다** | 되돌리기는 언제나 「방금 저장한 것 취소」. 세대는 하나만 (D-065) |
| **JSON은 원자적으로 저장** | 임시 파일에 다 쓴 뒤 갈아 끼운다. 중간에 죽어도 빈 파일이 남지 않는다 (D-069) |

---
