# charlotte

---------------------------


## 샬럿울먹콘
discord.py를 이용한 디스코드 보이스 채널 음악 재생기

![](https://private-user-images.githubusercontent.com/140505972/418364767-186ec966-6f0e-40d4-953c-fb032ca7c675.png?jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NDA5MzgwNDMsIm5iZiI6MTc0MDkzNzc0MywicGF0aCI6Ii8xNDA1MDU5NzIvNDE4MzY0NzY3LTE4NmVjOTY2LTZmMGUtNDBkNC05NTNjLWZiMDMyY2E3YzY3NS5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjUwMzAyJTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI1MDMwMlQxNzQ5MDNaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT1mODA3YTM4ODFhNGI4YjlhYzNjZDU1NzgxNjM1ZTAyNGU3NDhiMjdjMWM3MTVlOWUyNzQxM2U5OWI1M2M0ZjZhJlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.QwXMQ8QFnZrYyH9UzSSUCuaXw43-sfRIWaL2f9gOoiU)

## 설정 방법

### 1. 환경변수 설정
`.env` 파일을 생성하고 다음 값들을 설정하세요:

```env
SPOTIFY_CLIENT_ID=your_spotify_client_id_here
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret_here
SPOTIFY_CREDENTIALS_PATH=./credentials.json
```

### 2. Spotify 인증 설정
`credentials.json.template`을 복사하여 `credentials.json`을 만들고 Spotify 로그인 정보를 입력하세요:

```json
{
    "username": "your_spotify_username",
    "password": "your_spotify_password",
    "credentials": {
        "stored_credentials": "your_stored_credentials_blob_here"
    }
}
```

### 3. 의존성 설치
```bash
pip install -r requirements.txt
cd ../deezspot
pip install -e .
```

## 기능
- YouTube 음악 재생
- Spotify 음악 재생 (deezspot을 통한 버퍼 스트리밍)
- 파일 업로드 재생
- Discord 음성 채널 관리

할 일
- ~~spotify / 파일 첨부 인식~~ 완료
- 샬럿울먹콘이 음성 채널에 접속중이지 않을 때에도 보이스 킥 시키기
- detached mode에서 log 볼 수 있도록
