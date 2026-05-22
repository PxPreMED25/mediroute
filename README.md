# MediRoute 백엔드 API

증상 기반 진료과 추천 및 주변 의료기관 안내 서비스의 백엔드입니다.

## 프로젝트 구조

```
mediroute-backend/
├── app/
│   ├── main.py              # FastAPI 앱 진입점
│   ├── core/
│   │   └── config.py         # 환경변수 & 설정
│   ├── models/
│   │   └── schemas.py        # 요청/응답 스키마
│   ├── routers/
│   │   └── analyze.py        # POST /api/analyze
│   └── services/
│       └── claude_service.py # Claude API 프록시
├── .env.example              # 환경변수 템플릿
├── requirements.txt          # Python 의존성
├── Dockerfile                # 컨테이너 배포용
└── frontend-patch.js         # 프론트엔드 수정 가이드
```

## 빠른 시작 (로컬)

### 1. 프로젝트 클론 & 환경 설정

```bash
cd mediroute-backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열고 실제 API 키를 입력:

```
ANTHROPIC_API_KEY=sk-ant-여기에-실제-키-입력
```

### 3. 서버 실행

```bash
uvicorn app.main:app --reload --port 8000
```

### 4. API 확인

- Swagger UI: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health

### 5. 프론트엔드 수정

`mediroute_260515.html`에서 `callClaude()` 함수를
`frontend-patch.js`의 코드로 교체합니다.

## API 엔드포인트

### POST /api/analyze

증상을 분석하고 진료과, 예상 질환, 주변 병원을 추천합니다.

**요청:**
```json
{
  "symptom": "무릎이 아프고 계단 오를 때 힘들어요",
  "areas": ["무릎"],
  "age": "55",
  "gender": "여성",
  "region": "대전 서구",
  "duration": "3개월 전부터",
  "meds": "",
  "disease": "고혈압",
  "checks": [],
  "is_urgent": false
}
```

**응답:** 프론트엔드의 `applyResult()`가 기대하는 JSON 구조 그대로 반환

## 배포

### Railway (추천)

1. GitHub에 push
2. railway.app에서 프로젝트 생성
3. 환경변수에 `ANTHROPIC_API_KEY` 추가
4. 자동 배포됨 (Dockerfile 감지)

### Render

1. render.com에서 Web Service 생성
2. GitHub 리포 연결
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. 환경변수 추가

배포 후 프론트엔드의 `API_BASE_URL`을 배포된 URL로 변경하세요.
