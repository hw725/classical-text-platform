# 프로젝트: 고전서지 통합 브라우저 (Classical Text Browser)

## 프로젝트 비전
물리적 원본(PDF/이미지)과 디지털 텍스트의 연결이 끊어지지 않는,
사람과 LLM이 함께 고전 텍스트를 읽고 번역하고 연구하는 통합 작업 환경.

개발자의 VSCode처럼, 연구자가 이 안에서 이미지 열람, 레이아웃 분석,
OCR, 교정, 번역, 주석 작업을 모두 수행한다.

## 설계 문서
- docs/platform-v7.md — 8층 모델, Git 저장소, 전체 아키텍처
- docs/core-schema-v1.3.md — 해석 저장소의 엔티티 모델
- docs/operation-rules-v1.0.md — 코어 스키마 운영 규약
- docs/DECISIONS.md — 설계 결정 기록 (반드시 읽을 것)
- docs/observability-roadmap.md — 관측 가능성(OpenTelemetry) 점진적 도입 로드맵
- docs/retrospective/ — 회고용 뷰 (원본 무수정). 결정·세션·패턴·하네스 권고 + 인터랙티브 뷰어

## 기술 스택
- 백엔드: Python + FastAPI
- 프론트엔드: HTML + vanilla JS + CSS (빌드 도구 없음)
- PDF 렌더링: PDF.js
- 버전관리: GitPython
- 스키마 검증: jsonschema
- 패키지 관리: uv (pip 사용 금지)
  - 패키지 추가: uv add <패키지명>
  - 개발 의존성: uv add --dev <패키지명>
  - 실행: uv run python -m <모듈>
  - uv.lock은 git에 포함

## 백엔드 모듈 구조 (src/app/)
server.py는 FastAPI 앱 생성 + 라우터 마운트만 담당하는 조립 파일.
실제 API 엔드포인트는 9개 라우터 모듈에 분산 (2026-09-06 기준 실측):

```
src/app/
├── server.py            ← 앱 생성 + 라우터 마운트 + configure()
├── _state.py            ← 공유 상태 + 헬퍼 + LLM 프롬프트/캐시/동적 토큰 계산
├── __main__.py          ← CLI 진입점 (python -m app serve)
└── routers/
    ├── library.py       ← 서고/설정/백업/휴지통 + 스키마 검증 + 연결 설정·앱 업데이트·엔진 추가 설치·OAuth 프록시·Ollama 로그인·모델 골라 받기 (29 라우트)
    ├── documents.py     ← 문헌 CRUD/페이지/교정/서지/파서 + 텍스트레이어 진단·가져오기·입히기 + 권 추가 + 찍은 자리·규칙 제안 (43 라우트)
    ├── composition.py   ← 편성 — 내용 트리·경계 색인·넣기·옮기기·지우기 + 제안·목차·적용·자동 트리·신호 도출 + 쪼개기·리셋 (13 라우트)
    ├── interpretations.py ← 해석 CRUD/레이어/의존/엔티티/관계·태그 (22 라우트)
    ├── llm_ocr.py       ← LLM 상태·분석·초안 + OCR 엔진·실행·권단위 일괄·백업 되돌리기·판독 지침·LLM 교정 패스 (24 라우트)
    ├── alignment.py     ← 이체자 사전/정렬/일괄교정/문헌별 승인 (20 라우트)
    ├── reading.py       ← L5 표점·현토 + L6 번역 + 비고 + AI보조 (24 라우트)
    ├── annotation.py    ← L7 주석·사전형·인용마크 + AI보조 (34 라우트)
    └── version.py       ← Git 그래프/되돌리기/스냅샷/가져오기 (7 라우트)
```

- 라우터 간 직접 import 금지. 공유 상태는 반드시 _state.py를 통해 접근.
- 새 엔드포인트 추가 시 해당 도메인의 라우터 파일에 추가할 것.
- Pydantic 모델은 사용하는 라우터 파일 내부에 정의.

## 코딩 규칙
- 이 프로젝트의 사용자는 비개발자 인문학 연구자다
- 코드 주석은 한국어로, 상세하게, "왜 이렇게 하는지" 포함
- 함수마다 docstring에 입력/출력/목적 설명
- UTF-8 인코딩, LF 줄바꿈
- JSON 파일은 jsonschema로 검증
- 에러 메시지는 한국어로, 원인과 해결책 포함
- primary_data/ 또는 L1_source/ 내의 원본 파일은 절대 수정 금지

## 용어 규칙 (혼동 방지)
- LayoutBlock: 원본 저장소 L3의 페이지 영역 (OCR 읽기 순서 단위)
- OcrResult: 원본 저장소 L2의 OCR 인식 결과
- 단위(unit): 코어 스키마의 해석용 텍스트 단위 (source_ref로 원본 추적). v1.3부터 경계 목록에서 만든 읽기 보기이고 이름도 `unit`이다(D-092·D-093)
- 편성(composition): 경계를 정하는 일. **원본 저장소의 것**(D-097). 사이드바 「내용」 트리는 문헌 > 권 > 단위(D-098) — Work 엔티티는 D-099에서 없앴다
- "Block"이라고만 쓰지 말고 항상 위 세 이름 중 하나를 사용할 것

## 작업 방식: CLI를 적극 활용할 것
- 코드를 작성한 뒤 반드시 실행해서 확인하라. 작성만 하고 검증 없이 넘어가지 마라.
- API 엔드포인트를 만들면 curl이나 테스트 스크립트로 직접 호출해서 응답을 확인하라.
- 웹 스크래핑 파서를 작성할 때는 대상 사이트의 HTML 구조를 먼저 curl/wget으로 가져와서 확인하라.
- JSON 파일을 생성하면 jsonschema로 검증하라.
- 테스트를 작성했으면 실행해서 통과하는지 확인하라.
- "될 것 같다"로 끝내지 말고, 실제로 동작하는 것을 보여줘라.

## Git 커밋 규칙
형식: <타입>: <설명>
타입: feat / fix / data / docs / refactor / test
예시: feat: Phase 2 — 서고 초기화 CLI 구현

## 의존성 업그레이드 — 먼저 볼 것

OCR 스택 셋(**paddlepaddle+paddleocr** / **onnxruntime+opencv** / **torch+transformers**)이
전이 의존을 공유한다 — `numpy`·`protobuf`·`pyyaml`·`typing-extensions`·`setuptools`·
`networkx`·`pillow`. 하나를 올리면 다른 스택이 **조용히** 죽는다: 엔진 등록 실패는
예외가 아니라 `available=False`로 나타나고 라우터는 다음 엔진으로 넘어간다(D-044·D-056).

**이미 겪은 결합 지점**

| 무엇 | 결과 |
|---|---|
| `paddleocr` 2.x → 3.x | `show_log`·`use_angle_cls`·`use_gpu` 제거, `ocr.ocr()` → `ocr.predict()` (a3894c2) |
| `paddleocr` 3.7이 `pyyaml==6.0.2` 고정 | 이 저장소의 하한도 6.0.2로 내림 |
| `paddlepaddle` 휠이 cp312까지 | `requires-python`에 `<3.13` (D-059) |
| Windows + paddlepaddle 3.x | OneDNN이 PIR 속성 변환 미지원 → `FLAGS_use_mkldnn=0` 회피 |
| `torch`는 전용 인덱스(플랫폼 분기: Windows `pytorch-cu124`·Linux `pytorch-cu126`, 2026-08-20 실측) | CUDA 버전을 바꾸면 `[[tool.uv.index]]` URL도 함께 고쳐야 한다 |
| `ndl-lab/ndlocr-lite` **master**에서 모델 받기 | 원본이 v1.2.0에서 PARSeq 셋을 바꿔 셋이 404. 모델 URL은 **태그 1.1.3**에 고정(`src/ocr/ndlocr/__init__.py`) |
| `torch`(cu124) ↔ `paddlepaddle-gpu` **같은 프로세스** | cuDNN 9 DLL을 따로 들고 와 먼저 뜬 쪽이 이긴다 → PaddleOCR 사용 불가. 해법은 프로세스 분리(`CTB_PADDLE_PYTHON`, D-091). `doctor.bat`이 판정한다 |
| `opencv-contrib-python`(paddlex) ↔ `opencv-python-headless`(extras) | **같은 `cv2`를 두 배포판이 제공.** 한쪽을 지우면 공유 디렉터리가 사라져 남은 쪽까지 깨진다 — `module 'cv2' has no attribute 'IMREAD_COLOR'`. extras도 contrib판으로 통일했다 |

GPU 스택은 `.venv`에 설치하지 않는다 — 별도 환경 `.venv-gpu`가 정본(D-078). 환경이 이상하면 먼저
`doctor.bat`. **파일 다루기 규칙**(`write_json_atomic`·`resolve_part_pdf`·`fitz.open` with·L2 배율·
로컬 서비스는 `127.0.0.1`·`.bat`은 ASCII만)은 `CLAUDE.md`「파일 다루기」표와 `docs/maintenance.md`가 정본이다.

**올릴 때 절차**

1. `uv lock --upgrade-package <이름>` — **전체 갱신은 하지 않는다.** 한꺼번에 올리면
   무엇이 깼는지 가릴 수 없다.
2. `uv run python -m pytest`
3. **실제 이미지로 OCR 1쪽.** 자동 테스트의 사각지대가 여기다 —
   `test_ocr_paddle.py::test_recognize_real`이 PaddleOCR `recognize()`를 실제로
   부르지만(설치 시에만), **검출(`line_detector`)은 순수 함수만 검증하고
   배치·파이프라인 경로는 더미 엔진을 쓴다.** 엔진 API가 바뀌어도 초록으로 통과한다.
4. 스키마·저장 형식이 바뀌면 `docs/DECISIONS.md`에 마이그레이션 경로를 남긴다.
   기존 서고를 열 수 없게 되는 변경은 **되돌릴 수 없다.**

> 고치기 전에 **[docs/maintenance.md](docs/maintenance.md)**를 먼저 본다 —
> 되돌릴 수 없는 것, 되풀이하지 말 것, 자동 테스트가 못 잡는 자리.

## 인지 부채 지도

> 2026-07-04 최초 감사 · 2026-09-04 3차 감사(v1.3.0). AI 작성 코드와 사용자 이해의 간극 요약.
> 상세·퀴즈: docs/cognitive-debt-audit.html — **9절이 v1.3.0에서 «검증이 없어서 몰랐던 것» 넷을 적는다**
> (비고 패널이 죽어 있었다 · 시험 픽스처가 스키마를 어기고 있었다 · 교환 형식이 두 벌이었다 ·
> 스냅샷의 앱 판이 멈춰 있었다). 넷 다 예외를 던지지 않았다.

### 실제 하는 일 (문서에 없는 층위)
- "서버 시작" = 최대 3개 프로세스: start_server.bat가 uvicorn 외에 OpenAI OAuth 프록시
  (`npx -y openai-oauth`, 포트 10531–10540 스캔, Bearer 토큰 `oauth-proxy` 하드코딩)와
  SikuRoBERTa 표점 Docker(punctuation-service/.env 존재 시)를 자동 기동.
- 프론트(static/)가 약 4.2만 줄 — index.html 약 4.9천 줄 단일 파일, workspace.css 약 7.9천 줄,
  JS 32개. 테스트 59파일은 전부 백엔드, **프론트 테스트 0, CI 없음.**
  (2026-09-06 재실측. 2026-07-26 v1.2.0 감사 때 직전 대비 프론트가 줄어든 것은
  D-069에서 죽은 코드 약 1,000줄을 걷어냈기 때문이다.)

### 안다고 착각하기 쉬운 지점
1. ~~`src/app/_state.py`에 `_parse_llm_json`이 **두 번 정의**~~ → **해소됨(ee8db13)**, 아래
   "인지부채 해소 반영" 참조. 현재는 단일 정의다.
2. ~~LLM 응답이 잘리면 조용히 부분 결과 반환~~ → **해소됨(56000f8·b91f3ef)**: `_truncated`
   플래그가 UI 경고로 노출된다. 아래 "인지부채 해소 반영" 참조.
3. LLM 결과 캐시(TTL 600초·최대 256건, `_state.py:360`) — 같은 텍스트 재요청 시 10분간
   옛 결과가 돌아올 수 있음.
4. "가져오기" 버튼의 "준비중"은 UI만 봉인(D-037). hwp-import.js 약 1천 줄과 백엔드
   엔드포인트(`/api/documents/import-hwp` 등)는 살아 있음 — 재구현하지 말고 복원할 것.
   **단 D-055 이후 이 버튼은 프로필에 따라 동작이 갈린다**: 「추출」 모드에서는
   hwp-import 다이얼로그가 아니라 `POST .../text-import/from-text-layer`(단순 추출)에
   연결된다. hwp-import 봉인 자체는 그대로다.
7. **기본 OCR 엔진은 문헌 성격을 보지 않는다** — "설치된 것 중 첫 번째"(`registry.py`).
   torch가 있으면 `ndlkotenocr-full`(고전적 전용, **한글 인식 불가**)이 기본이 된다.
   근현대 한글 문헌을 아무 설정 없이 OCR하면 결과가 깨진다. 배치 라우트는 시작 시점에
   경고하지만(D-055), 페이지 단위 라우트에는 그 경고가 없다.
8. **OCR은 L3 레이아웃이 없으면 조용히 실패한다** — 200 OK에 `status: "partial"`,
   `errors: ["L3 레이아웃을 찾을 수 없습니다"]`, 결과 0건. 예외가 아니므로
   호출부가 `ocr_results`를 확인하지 않으면 성공으로 오인한다.
9. **`el.hidden = true`만으로는 안 숨는다** — `workspace.css`가 `.mode-tab`,
   `.extract-panel` 등에 `display: flex`를 지정하고 있어, 작성자 스타일시트가
   브라우저 기본 `[hidden] { display: none }`을 우선순위로 덮어쓴다. 지금은 파일
   맨 위의 `[hidden] { display: none !important }`가 막고 있으니 **그 줄을 지우면
   숨김이 전부 깨진다.** jsdom은 이 결함을 재현하지 못하므로 브라우저 확인이 필요하다.
10. **정적 파일은 `_NoCacheStaticFiles`로 서빙된다**(server.py) — 기본 `StaticFiles`는
   `Cache-Control`을 붙이지 않아 브라우저가 고친 JS·CSS를 다시 받지 않는 사고가 있었다.
   `?v=` 쿼리에 의존하지 말 것.
12. **추출 패널에서 탭을 열 때는 `_switchMode()`를 직접 부르지 말 것**(D-057) —
   탭 하이라이트 처리가 그 함수가 아니라 `initModeBar()`의 클릭 핸들러 안에 있다.
   직접 부르면 화면은 바뀌는데 탭 표시가 그대로여서 «지금 어느 탭인가»가 어긋난다.
   `.mode-tab[data-mode="..."]`를 찾아 `.click()`하면 기존 경로를 그대로 탄다.
   또한 검수 목록의 확인 기록은 **localStorage**(`ctb.reviewed.<doc>.<part>`)에
   글자 수와 함께 저장된다 — 다시 OCR로 내용이 바뀌면 확인이 저절로 풀리게 하기 위함.
11. **배치 OCR의 «건너뛴다»는 두 조건이다**(D-057) — L2 결과가 있고 **그 결과가 지금
   L3와 맞을 때**만 건너뛴다. `ocr/layout_staleness.py`가 L2의 `layout_block_id` 집합과
   L3의 `block_id` 집합을 비교한다. 스키마에 타임스탬프가 없어서 이 방식을 쓴다
   (`layout_page`·`ocr_page` 둘 다 `additionalProperties: false`). 판정이 애매하면
   «안 바뀌었다»로 둔다 — 오판하면 쪽마다 LLM 호출이 다시 나가기 때문이다.
5. 서지 파서 5종(`src/parsers/` korcis 1,807줄·archives_jp·kyujanggak·kostma·ndl)은 외부
   사이트 HTML 구조 의존 — 사이트 개편 시 침묵 파손. 테스트가 실제 네트워크를 안 타면 못 잡음.
6. `llm_usage_log.jsonl`은 서고 루트에, 서고 미설정 시 `~/.classical-text-browser/`에 기록 —
   예산(MONTHLY_BUDGET_USD) 추적이 서고별로 분산됨.

### 인지 부채 핫스팟 (위험 순)
| 순위 | 경로 | 위험 |
|---|---|---|
| 1 | `src/app/_state.py` (922줄) | 전역 상태+프롬프트+캐시+JSON 파서 응집, 중복 정의 잠복 |
| 2 | `src/app/static/` 프론트 모놀리스 | 테스트 0·CI 없음, index.html 4,826줄 수정 회귀 감지 불가. D-055·D-067·D-069의 화면 검증은 매번 **jsdom 일회성 하네스**로 했고 **그 하네스들은 저장소에 없다** — 정식 프론트 스모크 테스트는 여전히 미결(D-053 착수 조건). 이 부채 때문에 D-069의 죽은 코드 1,000줄이 오래 살아남았다 |
| 3 | `start_server.bat` | 암묵 부수효과 3종(프록시·Docker·포트 스캔), 실패 시 원인 추적 곤란 |
| 4 | `src/parsers/` 5종 | 외부 사이트 의존 침묵 파손 |
| 5 | ~~**비교 모드 백엔드 잔재**~~ | v1.3.0에서 정리됨 — `l5_compare`·`get_l5_compare*`는 코드에 없다(2026-09-06 grep 0건). D-069에서 프론트 880줄을 걷어낸 뒤 남았던 백엔드 라우트 |
| 6 | 봉인된 「외부 파일 → 새 문헌」 가져오기 | 존재를 모르면 중복 재구현, 알면 진입점 복원만으로 재개(D-037). 백엔드는 살아 있고 `_openHwpImportDialog()` 호출자가 0건일 뿐이다. **추출 모드의 「텍스트 바로 가져오기」(`/text-import/from-text-layer`)와 혼동하지 말 것** — 그쪽은 정상 동작하는 별개 경로다 |
| 7 | **합성 입력만으로 검증하는 습관** | D-068이 여기서 났다 — 시험용 PDF를 PyMuPDF로 만들어 써서, 실제 스캔본에만 있는 좌표 변환을 자동 테스트가 영영 만나지 못했다. 파일 형식·외부 엔진을 다루는 코드는 **실물로 한 번 돌려야** 한다 |

## ✅ 인지부채 해소 반영 (2026-07-07)

> 위 "인지 부채 지도"의 일부 항목이 실제 코드 수정으로 해소되었다. 아래는 문제 → 해소, 커밋해시.

- `_state.py`의 `_parse_llm_json` 이중 정의(앞 정의가 `# type: ignore[no-redef]` 뒤 정의에 shadow되던 호출 불가 죽은 코드) → 앞 정의 제거, AST로 단일 정의 확인. 커밋 `ee8db13`. (위 "안다고 착각하기 쉬운 지점" 1번 → 해소됨. 단, 지도 본문의 행번호 775·906은 수정 이전 스냅샷 기준.)
- 잘린 LLM 응답 복구 시 `_truncated` 플래그로 조용한 부분 결과 누락 방지 → 플래그 도입. 커밋 `56000f8`. (위 "안다고 착각하기 쉬운 지점" 2번의 조용한 부분 반환 위험 완화.)
