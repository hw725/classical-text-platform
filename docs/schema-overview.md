# 전체 스키마 개요도

> 2026-03-14 작성 · **2026-09-04 확인: 19개**
> (원본 7 + 해석 5 + 코어 6 + 교환 1). v1.3.0에서 코어가 바뀌었다 —
> 경계 목록이 더해지고(D-092) Work가 빠졌으며(D-099), 경계 파일은 해석 저장소가 아니라
> **원본 저장소**에 산다(D-097). 교환 형식은 정본이 스키마 하나가 되었다(D-100).

---

## 1. 8층 모델과 스키마 배치

| 층 | 이름 | 설명 | 스키마 |
|:---:|------|------|--------|
| **L8** | 연결 | 엔티티 간 관계, 외부 DB 연동 | `relation` (코어) |
| **L7** | 주석 | 주석(v2 사전형), 인용 마크, 인물/지명 태깅 | `annotation_page` v2, `citation_mark_page` (해석) / `tag`, `concept`, `agent` (코어) |
| **L6** | 번역 | 현대어 번역문 | `translation_page` (해석) |
| **L5** | 표점/현토 | 구두점(句讀) 부여, 한국어 현토(懸吐) | `punctuation_page`, `hyeonto_page` (해석) / `unit`, `work` (코어) |
| | **경계** | **해석 저장소 ↑ dependency.json ↓ 원본 저장소** | |
| **L4** | 사람 수정 | OCR 교정, 확정 텍스트 | `corrections` (원본) |
| **L3** | 레이아웃 | 페이지 영역 분할, 읽기 순서 지정 | `layout_page` (원본) |
| **L2** | OCR | 글자 인식 결과 + 좌표 + 신뢰도 | `ocr_page` (원본) |
| **L1** | 원본 | PDF/이미지 원본 파일 (수정 금지) | `manifest`, `bibliography` (원본) |

**범례:**
- 원본 저장소 스키마 (7개)
- 해석 저장소 스키마 (5개)
- 코어 스키마 엔티티 (6개)
- 교환 형식 (1개)

---

## 2. 파일 구조

```
schemas/
├── source_repo/          ─ 원본 저장소 (L1-L4)
│   ├── manifest.schema.json         문헌 매니페스트
│   ├── bibliography.schema.json     서지정보
│   ├── ocr_page.schema.json         L2 OCR 결과
│   ├── layout_page.schema.json      L3 레이아웃
│   ├── corrections.schema.json      L4 교정 기록
│   ├── interp_manifest.schema.json  해석 저장소 매니페스트
│   └── dependency.schema.json       저장소 간 의존 추적
│
├── interp/               ─ 해석 저장소 (L5-L7)
│   ├── punctuation_page.schema.json L5 표점 (句讀)
│   ├── hyeonto_page.schema.json     L5 현토 (懸吐)
│   ├── translation_page.schema.json L6 번역
│   ├── annotation_page.schema.json  L7 주석 (v2 사전형)
│   └── citation_mark_page.schema.json L7 인용 마크
│
├── core/                 ─ 코어 스키마 엔티티 (L5-L8)
│   ├── work.schema.json              작품
│   ├── unit.schema.json             글 단위
│   ├── tag.schema.json               표면 태그
│   ├── concept.schema.json           승격된 의미 엔티티
│   ├── agent.schema.json             역사적 행위자
│   └── relation.schema.json          엔티티 간 관계
│
└── exchange.schema.json       ─ 내보내기/가져오기 교환 형식
```

---

## 3. 스키마 간 참조 관계

### 원본 저장소 내부

| 참조 대상 | 참조 원본 | 필드 |
|-----------|----------|------|
| `manifest`.parts[].part_id | `layout_page`.part_id | ◄─ 참조 |
| `manifest`.parts[].part_id | `ocr_page`.part_id | ◄─ 참조 |
| `layout_page`.blocks[].block_id | `ocr_page`.ocr_results[].layout_block_id | ◄─ 참조 |
| `layout_page`.blocks[].block_id | `corrections`.corrections[].block_id | ◄─ 참조 |

### 저장소 간 의존

| 참조 원본 | 참조 대상 | 필드 |
|-----------|----------|------|
| `interp_manifest`.source_document_id | `manifest`.document_id | ──▶ |
| `dependency`.source.document_id | `manifest`.document_id | ──▶ |
| `dependency`.source.base_commit | 원본 저장소 git commit hash | ──▶ |

### 해석 저장소 → 원본 저장소

| 참조 원본 | 참조 대상 | 필드 |
|-----------|----------|------|
| `punctuation_page`.block_id | `layout_page`.blocks[].block_id | ──▶ |
| `hyeonto_page`.block_id | `layout_page`.blocks[].block_id | ──▶ |
| `translation_page`.source.block_id | `layout_page`.blocks[].block_id | ──▶ |
| `annotation_page`.blocks[].block_id | `layout_page`.blocks[].block_id | ──▶ |
| `citation_mark_page`.source.block_id | `layout_page`.blocks[].block_id | ──▶ |

### 해석 저장소 내부 (L6↔L7)

| 참조 원본 | 참조 대상 | 설명 |
|-----------|----------|------|
| `translation_page`.annotation_context.used_annotation_ids[] | `annotation_page`.annotations[].id | 번역 시 참조한 주석 |

### 코어 스키마 내부

| 참조 원본 | 참조 대상 | 필드 |
|-----------|----------|------|
| `tag`.block_id | `unit`.id | ──▶ (이름은 v1.2의 것 — D-093·B-003) |
| `concept`.scope_document | `manifest`.document_id | ──▶ (선택. v1.2의 `scope_work`는 D-099에서 옮김) |
| `relation`.subject_id | `agent`.id \| `concept`.id | ──▶ |
| `relation`.object_id | `agent`.id \| `concept`.id \| `unit`.id | ──▶ |
| `relation`.evidence_blocks[] | `unit`.id | ──▶ |

### 코어 → 원본 역참조

| 참조 원본 | 참조 대상 | 설명 |
|-----------|----------|------|
| `unit`.source_ref | `manifest`.document_id + page + `layout_page`.block_id + git commit | 원본 출처 추적 |

---

## 4. 원본 저장소 스키마 (7개)

### manifest

> `source_repo/manifest.schema.json`

문헌 1건의 신원 정보. 문헌 ID, 제목, 물리 파일(권) 목록, 작업 진행 상태.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*document_id** | string | 영문 식별자 |
| **\*title** | string | 원어 표기 |
| title_ko | string? | 한국어 제목 |
| **\*parts[]** | array | {part_id, label, file, page_count} |
| **\*completeness_status** | enum | file_only → text_imported → bibliography_added → ocr_done → layout_analyzed → correcting → correction_done → finalized |
| segmentation_rules | object? | 편성 규칙(D-088·D-116) — 신호 스위치 `signals`, `title_words`·`head_words`, 판심 `furniture`, `suppress`, `reference_text`, `toc_llm`, `origin`(빈 값=아직 없음·induced·manual). 프로그램이 전문에서 찾아 채우고 사람이 고친다 |
| ocr_guidance | string? | LLM OCR 판독 지침(D-081) |

### bibliography

> `source_repo/bibliography.schema.json`

서지정보. 모든 필드 nullable. raw_metadata로 원본 보존, _mapping_info로 매핑 투명성.

| 그룹 | 필드 |
|------|------|
| 기본 정보 | title, title_reading, alternative_titles, creator ({name, role, period}), contributors[], date_created, edition_type |
| 분류/형태 | physical_description, subject, classification, material_type |
| 소장/접근 | repository ({name, country, call_number}), digital_source ({platform, source_url, permanent_uri, system_ids, license}) |
| 파서 투명성 | raw_metadata, _mapping_info |

### layout_page

> `source_repo/layout_page.schema.json`

L3 레이아웃 분석. LayoutBlock 단위로 분할, 읽기 순서/구조적 역할 부여.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*part_id, page_number** | string, int | 페이지 식별 |
| **\*blocks[]** | LayoutBlock[] | 페이지 영역 배열 |
| ↳ block_id | string | 블록 식별자 |
| ↳ block_type | string | main_text, annotation, preface 등 |
| ↳ bbox | [x,y,w,h] | 좌표 |
| ↳ reading_order | int | 읽기 순서 |
| ↳ writing_direction | string | vertical_rtl 등 |
| ↳ line_style | string | single_line 등 |
| ↳ refers_to_block | string? | 참조 블록 |
| ↳ skip | boolean | 건너뛰기 |

### ocr_page

> `source_repo/ocr_page.schema.json`

L2 OCR 결과. 글자 + 좌표 + 신뢰도. layout_block_id로 LayoutBlock 연결.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*part_id, page_number** | string, int | 페이지 식별 |
| **\*ocr_results[]** | OcrResult[] | 인식 결과 배열 |
| ↳ OcrResult → OcrLine → OcrCharacter | | char, bbox, confidence |

### corrections

> `source_repo/corrections.schema.json`

L4 교정 기록. OCR 오류, 이체자, 판본 이문 등 유형별 교정.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*corrections[]** | Correction[] | 교정 배열 |
| ↳ type | enum | 6종 (ocr_error, variant_reading ...) |
| ↳ original_ocr | string | 원래 OCR 결과 |
| ↳ corrected | string | 교정된 텍스트 |
| ↳ common_reading | string | 통용 독음 |

### interp_manifest

> `source_repo/interp_manifest.schema.json`

해석 저장소 매니페스트. 누가, 어떤 원본 기반으로 해석하는지.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*interpretation_id** | string | 해석 식별자 |
| **\*source_document_id** | string | 원본 문헌 ID |
| **\*interpreter** | object | {type, name} |

### dependency

> `source_repo/dependency.schema.json`

저장소 간 의존 추적. 파일 단위 해시 비교, 변경 경고.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*source** | object | {document_id, base_commit} |
| **\*tracked_files[]** | array | {path, hash_at_base, status} |
| **\*dependency_status** | enum | synced → stale → acknowledged |

---

## 5. 해석 저장소 스키마 (5개)

**공통 원칙: 원문 비변형** — 글자 인덱스 기반 오버레이 방식

> Target: {start, end} — 0-based inclusive 글자 인덱스. 원문은 그대로 두고 부호/토/번역/주석을 덧붙인다.

### punctuation_page

> `interp/punctuation_page.schema.json`

L5 표점(句讀). 원문 글자 인덱스에 삽입할 구두점 기록. before/after 통합 방식.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*block_id** | string | 대상 블록 (L3/L4) |
| **\*marks[]** | Mark[] | 표점 배열 |
| ↳ id | string | 마크 식별자 |
| ↳ target | {start, end} | 글자 인덱스 범위 |
| ↳ before | string? | 범위 앞 삽입 부호 |
| ↳ after | string? | 범위 뒤 삽입 부호 |

### hyeonto_page

> `interp/hyeonto_page.schema.json`

L5 현토(懸吐). 원문 글자에 붙이는 한글 토. position(after/before/over/under) 지정.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*block_id** | string | 대상 블록 (L3/L4) |
| **\*annotations[]** | Annotation[] | 현토 배열 |
| ↳ id | string | 현토 식별자 |
| ↳ target | {start, end} | 글자 인덱스 범위 |
| ↳ **\*position** | enum | after \| before \| over \| under |
| ↳ **\*text** | string | 현토 텍스트 |
| ↳ category | string? | 현토 분류 |

### translation_page

> `interp/translation_page.schema.json`

L6 번역. 문장 단위 번역 기록. LLM draft → 사람 review → accepted 워크플로우. 주석 참조 추적(annotation_context).

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*part_id, page_number** | string, int | 페이지 식별 |
| **\*translations[]** | Translation[] | 번역 배열 |
| ↳ id | string | 번역 식별자 |
| ↳ source | {block_id, start, end} | 원문 범위 |
| ↳ **\*source_text** | string | 원문 스냅샷 |
| ↳ hyeonto_text | string? | 현토 적용 텍스트 |
| ↳ **\*target_language** | string | 대상 언어 |
| ↳ **\*translation** | string | 번역문 |
| ↳ **\*translator** | object | {type: llm\|human, model?, draft_id?} |
| ↳ **\*status** | enum | draft → reviewed → accepted |
| ↳ annotation_context | AnnotationContext? | {used_annotation_ids[], reference_dict_filenames[]} |

### annotation_page (v2)

> `interp/annotation_page.schema.json`

L7 주석 v2. 기존 태깅 + 사전형 주석(DictionaryEntry) + 4단계 누적 생성 이력(GenerationStage). LLM 자동 태깅 + 수동 편집.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*part_id, page_number** | string, int | 페이지 식별 |
| schema_version | string | "2.0" (v1 하위호환) |
| **\*blocks[]** | AnnotatedBlock[] | 블록별 주석 |
| ↳ block_id | string | 대상 블록 |
| ↳ annotations[] | Annotation[] | 주석 배열 |
| &nbsp;&nbsp;↳ id, target | string, {start, end} | 식별/범위 |
| &nbsp;&nbsp;↳ **\*type** | string | person, place, term 등 |
| &nbsp;&nbsp;↳ **\*content** | object | {label, description, references[]} |
| &nbsp;&nbsp;↳ dictionary | DictionaryEntry? | {headword, reading, dict_meaning, ctx_meaning, sources[], related[]} |
| &nbsp;&nbsp;↳ current_stage | enum | none → from_original → from_translation → from_both → reviewed |
| &nbsp;&nbsp;↳ generation_history[] | GenerationStage[] | 단계별 스냅샷 |
| &nbsp;&nbsp;↳ source_text_snapshot | string? | 원문 스냅샷 |
| &nbsp;&nbsp;↳ translation_snapshot | string? | 번역 스냅샷 |
| &nbsp;&nbsp;↳ **\*annotator** | object | {type: llm\|human, model?} |
| &nbsp;&nbsp;↳ **\*status** | enum | draft → reviewed → accepted |

### citation_mark_page

> `interp/citation_mark_page.schema.json`

L7 인용 마크. 논문 인용을 위한 텍스트 구절 마크업. L4→L5→L6→L7 교차 레이어 해소(resolve). 학술 인용 형식 내보내기.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*part_id, page_number** | string, int | 페이지 식별 |
| **\*marks[]** | CitationMark[] | 인용 마크 배열 |
| ↳ **\*id** | string | 예: cite_a1b2c3 |
| ↳ **\*source** | object | {block_id, start, end} |
| ↳ **\*marked_from** | enum | original \| translation |
| ↳ **\*source_text_snapshot** | string | L4 변경 감지용 |
| ↳ label | string? | 인용 라벨 |
| ↳ tags[] | string[]? | 태그 |
| ↳ citation_override | CitationOverride? | {work_title, page_ref, supplementary} |
| ↳ **\*status** | enum | active → used → archived |

---

## 6. 코어 스키마 엔티티 (5개 + 경계 목록)

> Work는 D-099에서 없앴다. 경계 목록(`core/boundaries.schema.json`, D-092)은 단위의 정본이며
> 원본 저장소에 산다(D-097) — 아래 「단위」는 그 목록에서 만든 읽기 보기다.

**공통 상태 전이 (삭제 금지):**

`draft` → `active` → `deprecated` → `archived`

**공통 필드:** id (UUID), status (enum), metadata (object? 자유 확장)

### 단위 (unit)

> `core/unit.schema.json`

최소 해석 단위. original_text 불변. source_ref로 원본 출처 추적.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*sequence_index** | int | 권 안의 차례 |
| **\*original_text** | string | 원문 (불변) |
| source_ref | object? | {document_id, page, layout_block_id, commit} |

### Tag

> `core/tag.schema.json`

잠정적 태그. LLM/자동 추출 → Concept 승격 가능.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*block_id** | uuid | → 단위(unit) |
| **\*surface** | string | 표면 텍스트 |
| **\*core_category** | enum | person \| place \| book \| office \| object \| concept \| event \| other |
| confidence | float? | 신뢰도 0-1 |
| extractor | string? | llm \| rule \| human |

### Concept

> `core/concept.schema.json`

승격된 의미 엔티티. concept_features 자유 확장, 온톨로지 비강제.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*label** | string | 대표 이름 |
| scope_document | string? | 범위 문헌(비우면 전역) |
| description | string? | 학술 설명 |
| concept_features | json? | 자유 확장 (온톨로지 비강제) |

### Agent

> `core/agent.schema.json`

역사적 인물 / 서사적 행위자.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*name** | string | 이름 |
| period | string? | 활동 시대 |
| biography_note | string? | 약전 |

### Relation

> `core/relation.schema.json`

Agent/Concept/단위 간 관계. predicate는 구조적 동사만.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*subject_id** | uuid | 주어 ID |
| **\*subject_type** | enum | agent \| concept |
| **\*predicate** | string | 구조적 동사 (snake_case) |
| object_id | uuid? | 목적어 ID |
| object_type | enum? | agent \| concept \| block \| null |
| object_value | string? | 자유 텍스트 (object_id 없을 때) |
| evidence_blocks[] | uuid[]? | 근거 단위 ID 배열 |

---

## 7. 교환 형식

### exchange

> `exchange.schema.json`

단일 JSON 스냅샷(문헌 하나 + 해석 하나의 현재 HEAD). 내보내기/가져오기용.
**교환 형식의 정본은 이 스키마 하나다**(B-005) — `build_snapshot()`이 이 모양으로 쓰고,
`snapshot_validator.py`가 이것으로 검증하며, 시험이 내보내기 결과를 이것에 맞춰 본다.

| 필드 | 타입 | 설명 |
|------|------|------|
| **\*schema_version** | string | `"1.0"` |
| export_timestamp | string? | 내보낸 일시(ISO 8601) |
| platform_version | string? | 내보낸 앱의 판 |
| **\*source_info** | object | 어느 문헌·해석의 것인가(제목·권·서지·해석자). v1.3까지 이름은 `work` |
| **\*original** | object | 원본 층 — `head_hash` + `layers.L1_source`(경로 참조)·`L3_layout`·`L4_text` |
| interpretation | object? | 해석 층 — `head_hash`·`dependency`·`layers.L5~L7`·`core_entities` |
| variant_characters | object? | 이체자 사전 |
| annotation_types[] | array? | 주석 유형(기본 + 서고 커스텀) |

담지 않는 것: L1 바이너리(경로만) · L2 OCR(L4에서 다시 만든다) · git 이력 ·
단위(편성은 원본 저장소의 경계 목록이다 — D-092·D-097).

---

## 8. 설계 원칙 요약

| 원칙 | 설명 |
|------|------|
| **모든 필드 Nullable** | 소스에 없는 필드는 비워두고 나중에 채운다 |
| **원본 불변** | L1 파일, raw_metadata, original_text — 수정 금지 |
| **삭제 금지, 상태 전이만** | draft → active → deprecated → archived |
| **매핑 투명성** | _mapping_info에 출처/신뢰도 기록 |
| **출처 추적** | source_ref로 원본 저장소 역참조 |
| **원문 비변형** | 표점/현토/번역은 글자 인덱스 오버레이. 원문은 그대로 |
| **온톨로지 비강제** | 자유 확장. 부재 = 미지정 |
| **Promotion Flow** | Tag(잠정) → Concept(확정) |
| **용어 규칙** | LayoutBlock / OcrResult / 단위(unit). "Block" 단독 사용 금지 |

---

> 생성: 2026-03-14 · schemas/ 디렉토리 기준 · 19개 스키마 (원본 7 + 해석 5 + 코어 6 + 교환 1)
