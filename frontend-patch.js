/**
 * ═══════════════════════════════════════════════════
 *  MediRoute 프론트엔드 수정 가이드 (STEP 2 업데이트)
 *  mediroute_260515.html 에서 변경할 부분
 * ═══════════════════════════════════════════════════
 *
 *  변경 1: callClaude() 함수를 아래 코드로 교체
 *  변경 2: analyze() 함수 시작 부분에 GPS 수집 추가
 */

// ── 백엔드 API URL ──
const API_BASE_URL = 'http://localhost:8000';
// 배포 후: const API_BASE_URL = 'https://your-backend.railway.app';

// ── GPS 좌표 저장 (전역) ──
let userLat = null;
let userLng = null;

// 페이지 로드 시 GPS 수집 (한 번만)
if (navigator.geolocation) {
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      userLat = pos.coords.latitude;
      userLng = pos.coords.longitude;
      console.log(`GPS 확인: ${userLat}, ${userLng}`);
    },
    (err) => console.log('GPS 미허용:', err.message),
    { enableHighAccuracy: true, timeout: 5000 }
  );
}

// ── callClaude() 교체 ──
async function callClaude() {
  const resp = await fetch(`${API_BASE_URL}/api/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symptom: state.symptom || '',
      areas: state.areas || [],
      age: state.age || '',
      gender: state.gender || '',
      region: state.region || '',
      duration: state.duration || '',
      meds: state.meds || '',
      disease: state.disease || '',
      checks: state.checks || [],
      is_urgent: state.isUrgent || false,
      lat: userLat,
      lng: userLng
    })
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `서버 오류 (${resp.status})`);
  }

  return await resp.json();
}
