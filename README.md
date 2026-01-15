# 샬럿울먹콘(Charlotte)

**샬럿울먹콘**은 Discord 서버에서 사용할 수 있는 봇입니다. 음악 재생부터 전적 검색, 이모지 확대 기능을 제공합니다.

---

## 🌟 주요 기능 (Key Features)

### 🎵 뮤직 플레이어
`FFmpeg` 기반의 음악 재생 기능을 제공합니다.
- **다양한 소스 지원**: YouTube URL, Spotify 등 다양한 소스의 음악 재생과, **오디오 파일 직접 업로드** 재생을 지원합니다.
> 현재 Spotify의 지원은 중단되었습니다.
- **재생 제어**: 재생, 일시정지, 건너뛰기, 대기열 확인 등의 커맨드 기반의 제어가 가능합니다.

### 🎮 이터널 리턴 (Eternal Return) 전적 검색
게임 '이터널 리턴'의 플레이어 전적을 조회할 수 있습니다.
> 현재 이터널 리턴 전적 검색 기능은 일시 중단되었습니다.
- **간편한 조회**: `?er [닉네임]` 명령어로 티어, 랭킹, 승률, 평균 딜량 등 핵심 지표를 한눈에 볼 수 있습니다.
- **시각화**: MMR 변동 그래프를 포함한 상세한 정보를 시각적으로 제공합니다.

### 😊 이모지 확대 (Emoji Enlarger)
- 텍스트 채널에 전송된 이모지를 자동으로 감지하여 봇의 임베드로 확대 전송합니다.

---

## 🚀 시작하기 (Getting Started)

### 전제 조건 (Prerequisites)
- **Python 3.8** 이상
- **FFmpeg**: 음악 재생을 위해 시스템에 FFmpeg가 설치되어 있어야 합니다.

### 설치 및 실행 (Installation & Usage)

#### 1. 레포지토리 클론
```bash
git clone https://github.com/Start-Here-To-Serve/charlotte.git
cd charlotte
```

#### 2. 환경 변수 설정
프로젝트 루트에 `.env` 파일을 생성하고 디스코드 봇 토큰을 입력하세요.
```
DISCORD_TOKEN=
```

#### 3. 라이브러리 설치
```bash
pip install -r requirements.txt
```

#### 4. 봇 실행
```bash
python charlotte_bot.py
```

---

## 🐳 Docker로 실행하기

Docker를 이용하면 더욱 간편하게 봇을 호스팅할 수 있습니다.

```bash
# 컨테이너 실행 (백그라운드)
docker compose up -d

# 로그 확인
docker compose logs -f
```

---

## 💬 명령어 목록 (Commands)

모든 명령어는 기본 접두사 `?`를 사용합니다.

### 음악 (Music)
| 명령어 | 설명 |
| :--- | :--- |
| `?play [URL]` | YouTube/Spotify URL을 재생하거나, 오디오 파일을 첨부하여 재생합니다. |
| `?skip` | 현재 재생 중인 곡을 건너뜁니다. |
| `?pause` / `?resume` | 재생을 일시정지하거나 다시 시작합니다. |
| `?stop` | 재생을 멈추고 대기열을 초기화합니다. |
| `?queue` | 현재 대기 중인 곡 목록을 보여줍니다. |
| `?leave` | 봇을 음성 채널에서 내보냅니다. |

### 유틸리티 (Utility)
| 명령어 | 설명 |
| :--- | :--- |
| `?er [닉네임]` | 이터널 리턴 플레이어의 시즌 전적을 검색합니다. |
| `?help` | 도움말을 표시합니다. |

---

## 🛠️ 개발 및 기여 (Development)

이 프로젝트는 오픈 소스입니다. 버그 제보나 기능 제안은 [Issue](https://github.com/Start-Here-To-Serve/charlotte/issues)를 통해 남겨주세요.

### 📁 프로젝트 구조 (Project Structure)

```
.
├── charlotte_bot.py       # 봇 진입점 (Entry Point) 및 메인 로직
├── AudioScheduler.py      # 음악 재생 대기열(Queue) 관리 클래스
├── Modules/               # 주요 기능 모듈 디렉토리
│   ├── ServerClient.py    # 디스코드 서버별 봇 클라이언트 상태 관리
│   ├── TrackFactory.py    # 다양한 소스(URL, 파일)로부터 트랙 객체 생성
│   ├── features/          # 개별 기능 구현
│   │   ├── emoji_enlarger/   # 이모지 확대 기능
│   │   ├── eternal_return/   # 이터널 리턴 전적 검색
│   │   ├── konglish/         # 한영 전환 등 텍스트 유틸리티
│   │   └── language_research/# 언어 감지 및 분석
│   └── track_sources/     # 음악 소스 처리
│       ├── base.py        # 트랙 소스 핸들러 베이스 클래스
│       ├── config.py      # 관련 설정
│       └── providers/     # 플랫폼별 구현체
│           ├── youtube/   # YouTube 관련 처리
│           ├── spotify/   # Spotify 관련 처리
│           └── upload.py  # 파일 업로드 처리
├── Dockerfile             # Docker 이미지 빌드 설정
├── docker-compose.yml     # Docker 컨테이너 오케스트레이션 설정
└── requirements.txt       # 프로젝트 의존성 목록
```
