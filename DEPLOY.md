# MediRoute 배포 가이드

## 아키텍처

```
[사용자 브라우저]
    │
    ├── 프론트엔드 (Vercel / Netlify)
    │   └── mediroute.html (정적 파일)
    │
    └── 백엔드 API (Railway / Render)
        ├── FastAPI 서버
        ├── SQLite / PostgreSQL
        └── 외부 API 연동
            ├── Claude API (증상 분석)
            ├── 심평원 API (병원 검색, 진료비)
            └── 카카오맵 API (좌표 변환)
```

---

## 방법 A: Railway 배포 (추천, 가장 빠름)

### 1. GitHub에 Push

```bash
cd mediroute-backend
git init
git add .
git commit -m "MediRoute backend STEP 1~4"
git remote add origin https://github.com/PxPreMED25/mediroute-backend.git
git push -u origin main
```

### 2. Railway 프로젝트 생성

1. https://railway.app 로그인 (GitHub 계정)
2. "New Project" → "Deploy from GitHub repo" 선택
3. mediroute-backend 리포 선택
4. Dockerfile 자동 감지됨 → 바로 빌드 시작

### 3. 환경변수 설정

Railway 대시보드 → Variables 탭에 아래 추가:

```
ANTHROPIC_API_KEY=sk-ant-실제키
DATA_GO_KR_API_KEY=공공데이터포털키
KAKAO_REST_API_KEY=카카오REST키
JWT_SECRET_KEY=랜덤32자이상문자열
FRONTEND_URL=https://your-frontend.vercel.app
DATABASE_URL=sqlite:///./mediroute.db
```

> JWT_SECRET_KEY 생성: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

### 4. 도메인 확인

배포 완료 후 Railway가 `*.up.railway.app` 도메인을 자동 부여.
커스텀 도메인도 Settings → Domains에서 추가 가능.

### 5. 배포 확인

```bash
curl https://your-app.up.railway.app/health
# {"status":"ok","version":"0.1.0","claude_api":true,...}
```

---

## 방법 B: Render 배포 (무료 tier)

### 1. GitHub에 Push (위와 동일)

### 2. Render 서비스 생성

1. https://render.com → New → Web Service
2. GitHub 리포 연결
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

### 3. 환경변수 (Railway와 동일)

### 4. PostgreSQL 추가 (선택)

Render에서 무료 PostgreSQL DB 생성 가능:
1. New → PostgreSQL → Free plan
2. Internal Database URL 복사
3. 환경변수에 `DATABASE_URL=복사한URL` 추가

---

## 프론트엔드 배포

### 방법 1: Vercel (추천)

```bash
# vercel CLI 설치
npm i -g vercel

# 프론트엔드 폴더에서
cd frontend
vercel
```

또는 Vercel 대시보드에서 GitHub 리포 연결.

### 방법 2: Netlify

1. https://netlify.com → "Add new site" → "Deploy manually"
2. mediroute.html이 있는 폴더를 드래그 앤 드롭
3. 자동 배포됨

### 방법 3: GitHub Pages (가장 간단)

1. GitHub 리포에 mediroute.html Push
2. Settings → Pages → Source: main / root
3. 자동으로 `username.github.io/repo-name` 에 배포

### 프론트엔드 수정 사항

배포 후 `frontend-patch.js`의 API_BASE_URL을 실제 백엔드 URL로 변경:

```javascript
const API_BASE_URL = 'https://your-app.up.railway.app';
```

---

## 환경변수 체크리스트

| 변수 | 필수 | 용도 | 발급처 |
|------|------|------|--------|
| ANTHROPIC_API_KEY | ✅ | Claude API | console.anthropic.com |
| DATA_GO_KR_API_KEY | ✅ | 심평원 병원 검색 | data.go.kr |
| KAKAO_REST_API_KEY | 권장 | 좌표 변환/거리 계산 | developers.kakao.com |
| JWT_SECRET_KEY | ✅ | 로그인 토큰 암호화 | 직접 생성 |
| FRONTEND_URL | ✅ | CORS 허용 | 프론트 배포 URL |
| DATABASE_URL | 선택 | PostgreSQL 연결 | Railway/Render DB |
| KAKAO_CLIENT_ID | 선택 | 카카오 로그인 | developers.kakao.com |
| NAVER_CLIENT_ID | 선택 | 네이버 로그인 | developers.naver.com |
| NAVER_CLIENT_SECRET | 선택 | 네이버 로그인 | developers.naver.com |

---

## API 키 발급 가이드

### 1. 공공데이터포털 (data.go.kr)

1. 회원가입/로그인
2. "건강보험심사평가원 병원정보서비스" 검색
3. "활용신청" 클릭 → 자동 승인 (즉시)
4. 마이페이지 → 인증키 복사

### 2. 카카오 REST API

1. https://developers.kakao.com 로그인
2. "내 애플리케이션" → 앱 추가
3. 앱 키 → REST API 키 복사
4. (로그인 기능 사용 시) 카카오 로그인 활성화 + Redirect URI 등록

### 3. 네이버 OAuth

1. https://developers.naver.com 로그인
2. "Application" → 앱 등록
3. 네이버 로그인 API 선택
4. Client ID / Secret 복사

---

## 배포 후 모니터링

```bash
# 헬스체크
curl https://your-api.railway.app/health

# API 문서
open https://your-api.railway.app/docs

# 로그 확인 (Railway)
railway logs
```

---

## 트러블슈팅

**Q: Railway에서 빌드 실패**
→ requirements.txt의 패키지 버전 확인. Python 3.12 호환 여부 체크.

**Q: CORS 에러**
→ 환경변수 FRONTEND_URL이 프론트엔드 URL과 정확히 일치하는지 확인 (끝에 / 없이).

**Q: Claude API 429 Too Many Requests**
→ RATE_LIMIT_PER_MINUTE 값 조정 또는 API 요금제 확인.

**Q: 심평원 API 응답 없음**
→ data.go.kr에서 일일 트래픽 한도 확인. 개발계정은 1,000건/일.

**Q: SQLite → PostgreSQL 전환**
→ DATABASE_URL만 변경. `sqlite:///./mediroute.db` → `postgresql://user:pass@host:5432/mediroute`
→ requirements.txt에 `asyncpg==0.30.0` 추가 필요.
