# 유지보수 안내

> 이 저장소를 **고칠 때** 지켜야 하는 것들. 무엇을 만들지가 아니라
> 무엇을 깨뜨리지 않을지에 대한 문서다.
> 대상: 이 코드를 손대는 사람(사람이든 에이전트든).
> 설계 근거는 [DECISIONS.md](DECISIONS.md), 사용법은 [user-guide.md](user-guide.md).
> 최종 확인: 2026-09-04 (v1.3.0)

---

## 0. 되돌릴 수 없는 것 넷

먼저 이것부터. 나머지는 고치면 되지만 아래는 고칠 수 없다.

| 무엇 | 왜 되돌릴 수 없나 |
|---|---|
| `L1_source/` 안의 원본 파일 | 사용자가 스캔한 논문·고서 그 자체다. 덮어쓰면 끝이다. **읽기만 한다.** |
| `manifest.json` | 깨지면 그 문헌이 통째로 열리지 않는다. git 커밋 이전이면 복구 경로도 없다. |
| 저장 형식·스키마 변경 | 기존 서고를 열 수 없게 되는 변경은 마이그레이션 경로 없이 내보내면 안 된다. |
| 영구 삭제 | 이 저장소의 삭제는 **전부 휴지통 이동**이다. `rm`·`Remove-Item` 금지. |

---

## 1. 파일을 다룰 때 — 되풀이하지 말 것

여섯 가지 모두 **실제로 사고가 난 뒤** 적힌 것이다. 각각의 사고 기록이 괄호 안에 있다.

### 1.1 JSON 저장은 `write_json_atomic()` (D-069)

```python
from core.document import write_json_atomic
write_json_atomic(path, data)          # ✅
path.write_text(json.dumps(data))      # ❌ 절대 금지
```

`Path.write_text()`는 **먼저 파일을 0바이트로 자르고** 쓴다. 그 사이에 정전·강제종료·
디스크 부족이 나면 `manifest.json`이 빈 파일로 남는다. `write_json_atomic()`은
임시 파일에 다 쓰고 `fsync` 한 뒤 `os.replace`로 갈아 끼우므로, 실패해도 예전
내용이 그대로 남는다.

다섯 모듈(`document`·`entity`·`interpretation`·`library`·`snapshot`)의 `_write_json`이
전부 이 하나를 부른다. **새 모듈에서 또 복제하지 말 것.**

### 1.2 PDF는 `resolve_part_pdf(doc_path, part_id)`로 연다 (D-069)

```python
from ocr.image_utils import resolve_part_pdf
pdf = resolve_part_pdf(doc_path, part_id)   # ✅
pdf = list(source_dir.glob("*.pdf"))[0]      # ❌
```

`glob()`은 순서를 보장하지 않을뿐더러 **`part_id`를 아예 보지 않는다.**
卷上·卷下가 함께 있는 문헌에서 卷下 5쪽을 OCR 하면 卷上 5쪽 이미지가 엔진에
넘어가고, **오류 없이** 그럴듯한 결과가 저장된다. 원본과 텍스트의 대응이
조용히 끊어지는 것이라 나중에 발견하기가 가장 어렵다.

### 1.3 `fitz.open()`은 `with`로

```python
with fitz.open(str(pdf_path)) as doc:   # ✅
    ...
```

예외 경로에서 핸들이 남으면 **Windows가 그 PDF를 잠근다.** 이후 문헌 삭제·이동이
부분 실패한다. 이 저장소는 Windows가 기본이고 같은 PDF를 반복해서 연다.

### 1.4 기존 PDF에 덧쓸 때는 `page.wrap_contents()` 먼저 (D-068)

스캔 PDF는 픽셀 단위로 작업하려고 내용 스트림 첫 줄에 배율을 걸어 두고
되돌리지 않는 일이 흔하다.

```
0.24 0 0 0.24 0 0 cm      ← q 없이, 되돌리는 Q도 없다
q 2064 0 0 2893 0 0 cm /I0 Do Q
```

그 뒤에 덧붙이는 **모든 것이 0.24배로 줄어든다.** 실제로 텍스트 레이어가
495×694pt 쪽의 왼쪽 아래 구석에 2.9pt 크기로 박혔다. `page.insert_text()`는
자기 출력을 `q…Q`로 감싸 무사하지만 `TextWriter.write_text()`는 감싸지 않는다.

### 1.5 화면에 넣는 외부 문자열은 이스케이프 (D-069)

신뢰 경계 **밖**인 것: 드롭한 파일명(→ 문헌 제목), OCR 원문, 외부 사전·표점
임포트, 서버 오류 메시지. `innerHTML`에 넣기 전에 각 파일의 이스케이프 헬퍼를 쓴다.

`<img src=x onerror=…>.pdf`라는 이름의 파일을 끌어다 놓으면 스크립트가 돌았다.

### 1.6 배치 파일(`.bat`)에는 ASCII만 (2026-09-03)

`chcp 65001` 상태의 cmd.exe는 다중바이트 문자가 든 배치 파일에서 **자기 위치를
잘못 센다.** 한글 REM 주석이 쌓이자 `start_server.bat`이 새 콘솔에서
`'3개처럼' is not recognized as an internal or external command`를 찍고 계속 돌았다
— 뒤쪽 주석 줄의 중간부터 다시 읽은 것이다. 주석을 영어로 바꾸니 사라졌다.

- 주석·메시지 모두 영어로 적는다. 한국어 설명은 `docs/DECISIONS.md`(D-078·D-091)에 둔다.
- 하네스·파이프 안에서는 콘솔이 없어 `chcp`가 실패하고 cp949로 읽히므로 **재현되지
  않는다.** 더블클릭(새 콘솔)에서만 난다. 그래서 `tests/test_doc_drift.py::
  test_batch_files_are_ascii`가 바이트로 검사한다.
- `install.bat`도 ASCII 껍데기가 됐고 한글 안내는 `install.ps1`에 있다. 세 `.bat` 모두 같은 테스트가 검사한다.
- **서버가 떠 있는 동안 `start_server.bat`을 편집하지 않는다.** cmd는 배치 파일을 실행하면서
  디스크에서 다시 읽는다. 서버를 끄면 그 cmd가 «편집된» 파일의 엉뚱한 줄(else 분기
  `--library ""`)을 이어서 실행해 유령 서버가 8000에 떴다(2026-09-03 실측). 편집 전에 서버를
  끄고, 끈 뒤 `netstat -ano | findstr :8000`으로 남은 것이 없는지 본다.

---

## 2. 의존성을 올릴 때

OCR 스택 셋(**paddlepaddle+paddleocr** / **onnxruntime+opencv** / **torch+transformers**)이
전이 의존을 공유한다 — `numpy`·`protobuf`·`pyyaml`·`typing-extensions`·`setuptools`·
`networkx`·`pillow`.

**하나를 올리면 다른 스택이 조용히 죽는다.** 엔진 등록 실패는 예외가 아니라
`available=False`로 나타나고, 라우터는 말없이 다음 엔진으로 넘어간다(D-044·D-056).
즉 **화면에는 아무 일도 없어 보이는데 결과만 나빠진다.**

### 이미 겪은 결합 지점

| 무엇 | 결과 |
|---|---|
| `paddleocr` 2.x → 3.x | `show_log`·`use_angle_cls`·`use_gpu` 제거, `ocr.ocr()` → `ocr.predict()` |
| `paddleocr` 3.7이 `pyyaml==6.0.2` 고정 | 이 저장소의 하한도 6.0.2로 내림 |
| `paddlepaddle` 휠이 cp312까지 | `requires-python`에 `<3.13` (D-059) |
| Windows + paddlepaddle 3.x | OneDNN이 PIR 속성 변환 미지원 → `FLAGS_use_mkldnn=0` 회피 |
| `torch`는 전용 인덱스(Windows cu124·Linux cu126 분기) | CUDA 버전을 바꾸면 `[[tool.uv.index]]` URL도 함께 |
| `opencv-contrib-python` ↔ `opencv-python-headless` | **같은 `cv2`를 두 배포판이 제공.** 한쪽을 지우면 공유 디렉터리가 사라져 남은 쪽까지 깨진다 |

### 절차

```bash
uv lock --upgrade-package <이름>   # 전체 갱신 금지 — 무엇이 깼는지 가려진다
uv run python -m pytest
# 그리고 실제 이미지로 OCR 1쪽 ← 아래 3장 참조
```

---

## 3. 자동 테스트가 못 잡는 것

**테스트가 전부 통과해도 안심할 수 없는 자리들이다.** 전부 실제로 사고가 났다.

(여기 «655개»라고 건수를 박아 두었다가 실제 671건과 어긋났다. 건수는 pytest를
실제로 돌려야 알 수 있어 정적 검사로 지킬 수 없으므로, **지킬 수 없는 수치는
문서에 적지 않는다.**)

| 사각지대 | 왜 못 잡나 | 무엇으로 대신하나 |
|---|---|---|
| **파일 형식을 다루는 코드** | 시험용 PDF를 PyMuPDF로 만들면 실제 스캐너 출력의 특성이 없다. D-068이 정확히 여기서 났다 | **실제 스캔본 1쪽**을 태우고 산출물을 직접 열어 본다 |
| **OCR 엔진 API** | 검출(`line_detector`)은 순수 함수만 검증하고, 배치·파이프라인은 더미 엔진을 쓴다 | 엔진을 올린 뒤 **실제 이미지로 1쪽** |
| **다권본** | 시험 문헌이 대부분 단권이다 | 2권짜리로 **같은 쪽 번호의 결과가 다른지** 확인 |
| **프론트엔드 전체** | 테스트 0개, CI 없음 | jsdom 일회성 하네스. **정식 스모크 테스트는 미결**(D-053) |
| **「없다」의 증명** | 정적 검사는 «볼 곳»만 좁혀 준다 | 오탐을 사람이 하나씩 걸러야 한다 |

### 특히 — 침묵하는 실패

이 저장소에서 나온 심각한 결함은 **전부 같은 모양**이었다.
오류를 던지지 않고, 그럴듯한 답을 내고, 테스트는 초록이었다.

- `if (!x) return` — 화면 요소가 사라져도 조용히 넘어간다. 죽은 코드 1,000줄이
  이렇게 살아남았다(D-069).
- `typeof f === "function"` — 없는 함수를 삼킨다. 버튼이 그냥 안 눌렸다(D-063).
- `except Exception: pass` — 부분 결과가 «전부»로 반환된다.
- `available=False` — 엔진이 죽어도 다음 것으로 넘어간다.

**null 가드와 폴백은 실패를 침묵으로 바꾼다.** 정말 없어도 되는 것에는 맞고,
반드시 있어야 하는 것에는 틀리다. 새로 쓸 때 «이게 없으면 화면이 잘못된
결과를 보여주는가»를 물어보고, 그렇다면 가드 대신 **경고를 남긴다.**

### 그래서 넣은 방어

| 무엇 | 어디 | 잡는 것 |
|---|---|---|
| 산출물 재검사 | `text_layer_pdf._audit_output()` | 만든 PDF를 다시 열어 글자 크기·덮은 넓이·**그 자리의 잉크 밀도**를 잰다 |
| 원자적 쓰기 | `core.document.write_json_atomic()` | 저장 중 중단으로 인한 파일 손상 |
| 응답 경합 가드 | 쪽 로더 3종 | 늦게 온 응답이 새 쪽을 덮는 것 |
| `Cache-Control: no-store` | `server.py` 미들웨어 | 고쳤는데 화면에 반영 안 되는 것 |

**한계를 분명히 해 둔다** — 이것들은 **이미 아는 모양**만 잡는다.
새로운 종류의 침묵하는 실패는 여전히 사람이 산출물을 봐야 안다.

---

## 4. 데이터가 상했을 때

| 증상 | 어디를 보나 |
|---|---|
| 문헌이 안 열린다 | `manifest.json`이 빈 파일인지 확인. 그 문헌의 `.git`에서 직전 커밋을 꺼낸다 |
| OCR 결과가 갑자기 나빠졌다 | 추출 모드 「되돌리기」는 **다시 돌리기 직전**으로만 간다(D-065). 그 전 차수는 `.page_backup/`에 없다 |
| 레이아웃이 화면과 어긋난다 | L3의 `image_width`와 PDF 뷰포트의 비율을 본다. 데이터를 고치지 말고 **환산**해야 한다(D-067) |
| 텍스트 레이어 PDF가 이상하다 | 앱이 이미 재서 경고를 띄운다. 없으면 `_audit_output()`을 직접 호출해 본다 |
| 서고 폴더가 안 지워진다 | Windows가 PDF 핸들을 잡고 있다. `fitz.open()`이 `with` 밖에서 열린 곳을 찾는다 |

원본 저장소·해석 저장소 모두 git이다. **커밋된 것은 되돌릴 수 있다.**
커밋되지 않은 것은 되돌릴 수 없으므로, 위험한 작업 전에는 커밋이 있는지 본다.

---

## 5. 릴리스 절차

1. `uv run python -m pytest` — 전부 통과
2. `uv run ruff check src/ tests/`
3. JS 문법: `for f in src/app/static/js/*.js; do node --check "$f"; done`
3-1. **그림**: `uv run python scripts/check_doc_drift.py --screenshots` — 문서가 가리키는
   그림 가운데 화면 코드(`src/app/static`)보다 오래된 것을 짚는다. v1.3.0에서 사용자
   가이드 그림 일곱 장이 넉 달 반 전 화면인 채로 나갔다. 수치 검사는 셀 수 있는 것만
   보고, 그림은 아무도 세지 않았다. 낡은 것은 다시 찍거나 문서에서 뺀다
4. **실제 문헌으로 E2E** — 등록 → OCR → 검수 → PDF → 내려받기, 그리고
   **산출물을 열어서 본다.** 숫자만 보지 않는다.
4-1. **화면 전체 훑기 — 사용자가 켜는 환경으로.** GPU PC는 아이콘이 `.venv-gpu`를 고르므로
   `.venv-gpu\Scripts\python.exe -m app serve --port 8179`로 띄우고
   `uv run --with playwright python scripts/ui_sweep.py 8179`. 버전 표시·패널 전부·콘솔 오류·4xx/5xx·
   처음 설정 마법사를 한 번에 본다(오류가 있으면 종료 코드 1). v1.3.0에서 diff 리뷰와 pytest를 다
   통과하고도 화면 아래 «v1.2.1»이 남았다 — 검증은 `uv run`(.venv)으로, 실행은 `.venv-gpu`로 해서
   생긴 일이다. 판마다 `uv pip install --python .venv-gpu/Scripts/python.exe --no-deps -e .`로
   GPU 환경 메타데이터도 맞춘다
5. `docs/DECISIONS.md`에 결정 카드(다음 번호)
6. `docs/releases/vX.Y.Z.md` — 되돌릴 수 없는 변화는 **맨 위에** 적는다. 표제 문구
   «되돌릴 수 없는 변화 — 있음»을 바꾸면 앱의 새 판 경고(`core/updater.py`)가 안 뜬다
7. 버전 올리기: **`pyproject.toml` 한 곳뿐이다.** `server.py`와 화면 아래
   상태바는 `core.updater.current_version()`으로 pyproject를 직접 읽는다(`/api/app/version`).
   설치 메타데이터(dist-info)는 실행 환경마다 달라 `.venv-gpu`에서 옛 판이 보였다(2026-09-06).
   **여기에 버전을 새로 적지 말 것** — 적는 곳이 둘 이상이면 반드시 어긋난다
8. `/doc-sync` (Release/Range Mode, base = 직전 태그)
9. 커밋 → 푸시 → `git tag -a vX.Y.Z` → `git push origin vX.Y.Z`
9-1. 설치 파일: `installer/ctb_setup.py`의 `ZIP_URL` 태그를 새 판으로 바꾸고
    `powershell -File scripts/build_installer.ps1` → `dist/CTB-Setup.exe`를 릴리스 자산으로
    `gh release upload vX.Y.Z dist/CTB-Setup.exe --clobber`. 가이드 0장의 링크는 `releases/latest/download/CTB-Setup.exe`라
    바꿀 것 없다(D-113)
10. GitHub 릴리스(`gh release create`) 본문은 **하드랩을 푼 변환본**으로 게시한다 —
    릴리스 본문은 문단 안 개행을 그대로 렌더링해서, 저장소의 72자 랩 그대로 올리면
    문장이 중간에 끊겨 보인다 (v1.2.2에서 실측). **첫 줄 H1은 뺀다** — 릴리스 제목이
    이미 그것이라 두 번 보인다(v1.3.0에서 실측). 같은 판 번호로 태그를 옮기면 앱의
    「새 판 확인」은 원격 main보다 뒤진 커밋 수로 새 판을 판정한다

`release`·`feat`·`refactor` 커밋은 doc-sync 게이트가 걸린다.
`--no-verify`로 우회하지 않는다.

### 태그를 이미 낸 뒤에 옮겨야 할 때

**GitHub 릴리스는 태그에 매달려 있다.** 태그를 지우면 릴리스가 조용히
**초안(draft)** 으로 떨어지고, 목록에서는 그 전 판이 다시 «Latest»가 된다.
오류도 경고도 없다 — 사람이 릴리스 목록을 봐야 안다.

순서를 지킨다. **릴리스는 언제나 마지막이다.**

```bash
git tag -d vX.Y.Z                      # 로컬
git push origin :refs/tags/vX.Y.Z      # 원격
git tag -a vX.Y.Z -m "..."             # 새 커밋에 다시
git push origin vX.Y.Z
gh release edit vX.Y.Z --draft=false   # ← 초안으로 떨어진 것을 게시
```

확인은 SHA만 보지 말고 **목록의 «Latest» 표시**까지 본다.

```bash
gh release list --limit 3              # v X.Y.Z 가 Latest 인가
git rev-parse refs/tags/vX.Y.Z^{}      # HEAD와 같은가
```

---

## 6. 구조 규칙

- **라우터 간 직접 import 금지.** 공유 상태는 `_state.py`를 통해서만.
- 새 엔드포인트는 해당 도메인의 라우터 파일에(현재 9개, 216 라우트).
  **문서의 라우트 수는 손으로 적은 것이라 어긋난다.** 세는 명령:

  ```bash
  grep -c "^@router\.\(get\|post\|put\|patch\|delete\)(" src/app/routers/*.py
  ```

  실제로 `server.py` 머리말이 documents 34·interpretations 23·llm_ocr 14로
  오래 어긋나 있었다(실제 40·25·20). 문서와 코드가 다르면 **코드가 기준**이다.

  이 대조는 이제 기계가 한다(D-079) — `scripts/check_doc_drift.py`가 라우트 수·
  JS 모듈 수·스키마 수·테스트 파일 수를 실측해 README.md·AGENTS.md·CLAUDE.md·
  이 문서·architecture-diagrams.md·`server.py` 머리말의 수치와 대조하고,
  `tests/test_doc_drift.py`로 pytest에 편입되어 있어 릴리스 절차 1단계에서
  어긋나면 빨갛게 떨어진다. 붙이자마자 `AGENTS.md`가 `reading.py`의 라우트 수를
  둘 많게 적어 둔 것을 잡았다 — 같은 사고가 다른 문서에서 재발해 있었다.
  (틀렸던 값 자체는 D-079에 적혀 있다. **여기에 그 숫자를 인용해 쓰면
  검사기가 그것을 현재 주장으로 읽고 다시 걸린다** — 실제로 걸렸다.)

  ```bash
  uv run python scripts/check_doc_drift.py   # 단독 실행도 가능
  ```

  **줄 수·테스트 건수는 일부러 세지 않는다.** 한 줄만 고쳐도 바뀌거나
  pytest를 실제로 돌려야 알 수 있어서, 게이트로 삼으면 거의 매번 빨개진다.
  자주 틀리는 게이트는 곧 무시된다. 지킬 수 없는 수치는 문서에 적지 않는다.
- Pydantic 모델은 쓰는 라우터 파일 안에 정의.
- JSON 파일은 `jsonschema`로 검증(스키마 19개).
- 코드 주석은 한국어로, **왜 그렇게 했는지**를 담는다. 이 저장소의
  사용자는 비개발자 연구자이고, 주석이 유일한 설명이다.
- 용어: `LayoutBlock`(L3 영역) / `OcrResult`(L2 인식 결과) / `unit`(단위 — 해석용, D-093).
  **「Block」이라고만 쓰지 않는다.**

---

## 7. 디렉터리 지도

```
src/
├── core/         # 핵심 로직 (표점, 번역, 주석 등)
├── hwp/          # HWP/HWPX 처리 (hwp-hwpx-parser)
├── text_import/  # 텍스트 가져오기 (HWP 표점분리 + PDF 참조텍스트)
├── llm/          # LLM 라우터 + 프로바이더
├── ocr/          # OCR 엔진 (NDL古典籍OCR Full/Lite + NDLOCR + LLM 비전 + PaddleOCR)
│              #  + line_detector: 인식 없이 줄 위치만 찾는다 (텍스트 레이어 배치용)
├── export/       # 연구 산출물 내보내기 (텍스트 레이어 PDF — 검색되는 PDF)
├── parsers/      # 서지정보 파서 (NDL, 국립공문서관, KORCIS, KOSTMA, 장서각, 규장각 + 범용 LLM)
├── cli/          # CLI 도구
└── app/          # 웹 앱 (FastAPI + static)
schemas/
├── source_repo/  # 원본 저장소 스키마 (7개)
├── interp/       # 해석 저장소 스키마 (5개)
└── core/         # 코어 엔티티 스키마 (6개 — 경계 목록 포함, D-092) + schemas/exchange.schema.json 1개
```

---

## 관련 문서

| 문서 | 언제 보나 |
|---|---|
| [DECISIONS.md](DECISIONS.md) | 왜 이렇게 되어 있는지 — **고치기 전에 반드시** |
| [architecture-diagrams.md](architecture-diagrams.md) | 전체 그림이 필요할 때 |
| [../AGENTS.md](../AGENTS.md) | 인지 부채 지도 — 어디가 위험한지 |
| [../CLAUDE.md](../CLAUDE.md) | 작업 규칙 요약 — 의존성 결합 지점·파일 다루기 표는 그쪽이 더 자세하다 |
| [core-schema-v1.3.md](core-schema-v1.3.md) · [operation-rules-v1.0.md](operation-rules-v1.0.md) | 스키마를 건드릴 때 |
